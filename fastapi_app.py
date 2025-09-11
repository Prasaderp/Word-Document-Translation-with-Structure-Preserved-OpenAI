import os
import uuid
import time
import asyncio
from typing import Dict, List, Optional, Set
from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from translator import EnhancedTranslator
from dotenv import load_dotenv
import json
import http.client

class Job:
    def __init__(self, job_id: str, input_path: str, output_path: str, target_language: str, retain_terms: List[str]):
        self.job_id = job_id
        self.input_path = input_path
        self.output_path = output_path
        self.target_language = target_language
        self.retain_terms = retain_terms
        self.status = "pending"
        self.progress = 0.0
        self.avg_quality = 0.0
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.error: Optional[str] = None
        self.clients: Set[WebSocket] = set()

class JobManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self.lock = asyncio.Lock()

    async def create_job(self, input_dir: str, filename: str, data: bytes, target_language: str, retain_terms_raw: Optional[str]) -> Job:
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(input_dir, job_id)
        os.makedirs(job_dir, exist_ok=True)
        input_path = os.path.join(job_dir, filename)
        with open(input_path, "wb") as f:
            f.write(data)
        base, ext = os.path.splitext(filename)
        output_path = os.path.join(job_dir, f"{base}_{target_language}_enhanced{ext}")
        retain_terms = []
        if retain_terms_raw:
            parts = [p.strip() for p in retain_terms_raw.replace("\r", "").split("\n")]
            flat = [s.strip() for p in parts for s in (p.split(",") if "," in p else [p])]
            retain_terms = [t for t in flat if t]
        job = Job(job_id, input_path, output_path, target_language, retain_terms)
        async with self.lock:
            self.jobs[job_id] = job
        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self.lock:
            return self.jobs.get(job_id)

    async def add_client(self, job_id: str, ws: WebSocket):
        job = await self.get_job(job_id)
        if job is not None:
            job.clients.add(ws)

    async def remove_client(self, job_id: str, ws: WebSocket):
        job = await self.get_job(job_id)
        if job is not None and ws in job.clients:
            job.clients.remove(ws)

    async def broadcast(self, job: Job, payload: dict):
        stale: List[WebSocket] = []
        for ws in list(job.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.remove_client(job.job_id, ws)

job_manager = JobManager()

app = FastAPI()
load_dotenv()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = os.path.join(os.path.dirname(__file__), "web")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.post("/api/translate")
async def start_translation(file: UploadFile = File(...), target_language: str = Form(...), retain_terms: Optional[str] = Form(None), api_key: Optional[str] = Form(None)):
    env_api_key = os.getenv("OPENAI_API_KEY")
    effective_api_key = (api_key or "").strip() or env_api_key
    if not effective_api_key:
        return JSONResponse({"error": "API key is missing"}, status_code=400)
    try:
        now = time.time()
        age = now - float(connectivity.last_checked or 0)
        if api_key:
            pass
        else:
            if not connectivity.last_ok or age > 6.0:
                return JSONResponse({"error": "OpenAI API is not reachable"}, status_code=503)
    except NameError:
        pass
    if not file.filename.lower().endswith(".docx"):
        return JSONResponse({"error": "Only .docx files are supported"}, status_code=400)
    data_root = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_root, exist_ok=True)
    file_bytes = await file.read()
    job = await job_manager.create_job(data_root, file.filename, file_bytes, target_language, retain_terms)
    asyncio.create_task(run_job(job.job_id, effective_api_key))
    return {"job_id": job.job_id}

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = await job_manager.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    elapsed = 0.0
    if job.started_at:
        end = job.completed_at or time.time()
        elapsed = max(0.0, end - job.started_at)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": round(job.progress, 2),
        "avg_quality": round(job.avg_quality, 2),
        "elapsed_seconds": round(elapsed, 1)
    }

@app.websocket("/ws/progress/{job_id}")
async def progress_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    job = await job_manager.get_job(job_id)
    if job is None:
        await websocket.send_json({"error": "Not found"})
        await websocket.close()
        return
    await job_manager.add_client(job_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await job_manager.remove_client(job_id, websocket)

@app.get("/api/download/{job_id}")
async def download(job_id: str):
    job = await job_manager.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if job.status != "completed" or not os.path.exists(job.output_path):
        return JSONResponse({"error": "Not ready"}, status_code=400)
    filename = os.path.basename(job.output_path)
    return FileResponse(job.output_path, filename=filename, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

async def run_job(job_id: str, effective_api_key: Optional[str] = None):
    job = await job_manager.get_job(job_id)
    if job is None:
        return
    job.status = "running"
    job.started_at = time.time()
    api_key = effective_api_key or os.getenv("OPENAI_API_KEY")
    translator = EnhancedTranslator(api_key)
    try:
        async for progress, avg_quality in translator.process_enhanced_translation(job.input_path, job.output_path, job.target_language, job.retain_terms):
            job.progress = float(progress)
            job.avg_quality = float(avg_quality)
            elapsed = max(0.0, time.time() - (job.started_at or time.time()))
            await job_manager.broadcast(job, {"type": "progress", "progress": round(job.progress, 2), "avg_quality": round(job.avg_quality, 2), "elapsed_seconds": round(elapsed, 1)})
        job.status = "completed"
        job.completed_at = time.time()
        elapsed = max(0.0, job.completed_at - (job.started_at or job.completed_at))
        await job_manager.broadcast(job, {"type": "completed", "progress": 100.0, "avg_quality": round(job.avg_quality, 2), "elapsed_seconds": round(elapsed, 1), "download_url": f"/api/download/{job.job_id}"})
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        await job_manager.broadcast(job, {"type": "error", "message": job.error})

@app.get("/")
async def root_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

class OpenAIConnectivityMonitor:
    def __init__(self):
        self.last_ok = False
        self.last_checked = 0.0
        self.interval_seconds = 5.0
        self._task: Optional[asyncio.Task] = None
        self.last_reason = "unknown"

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            try:
                await self._check_once()
            except Exception:
                self.last_ok = False
                self.last_checked = time.time()
            await asyncio.sleep(self.interval_seconds)

    async def _check_once(self):
        api_key = os.getenv("OPENAI_API_KEY")
        ok = False
        reason = "missing_key" if not api_key else "unreachable"
        if api_key:
            def do_req():
                try:
                    conn = http.client.HTTPSConnection("api.openai.com", timeout=3)
                    conn.request("GET", "/v1/dashboard/billing/subscription", headers={"Authorization": f"Bearer {api_key}"})
                    r1 = conn.getresponse()
                    s1 = r1.status
                    raw1 = r1.read() or b""
                    conn.close()
                except Exception:
                    return {"ok": False, "reason": "unreachable"}
                if s1 != 200:
                    return {"ok": False, "reason": "unreachable"}
                try:
                    d1 = json.loads(raw1.decode("utf-8", errors="ignore"))
                except Exception:
                    return {"ok": False, "reason": "unreachable"}
                access_until = float(d1.get("access_until", 0) or 0)
                hard_limit_usd = float(d1.get("hard_limit_usd", 0) or 0)
                if access_until and access_until < time.time():
                    return {"ok": False, "reason": "expired"}
                if hard_limit_usd <= 0:
                    try:
                        conn2 = http.client.HTTPSConnection("api.openai.com", timeout=3)
                        conn2.request("GET", "/v1/dashboard/billing/credit_grants", headers={"Authorization": f"Bearer {api_key}"})
                        r2 = conn2.getresponse()
                        s2 = r2.status
                        raw2 = r2.read() or b""
                        conn2.close()
                        if s2 == 200:
                            d2 = json.loads(raw2.decode("utf-8", errors="ignore"))
                            ta = float(d2.get("total_available", 0) or 0)
                            return {"ok": ta > 0, "reason": "ok" if ta > 0 else "exhausted"}
                    except Exception:
                        return {"ok": False, "reason": "unreachable"}
                start_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30*24*3600))
                end_date = time.strftime("%Y-%m-%d", time.gmtime())
                try:
                    conn3 = http.client.HTTPSConnection("api.openai.com", timeout=3)
                    conn3.request("GET", f"/v1/dashboard/billing/usage?start_date={start_date}&end_date={end_date}", headers={"Authorization": f"Bearer {api_key}"})
                    r3 = conn3.getresponse()
                    s3 = r3.status
                    raw3 = r3.read() or b""
                    conn3.close()
                except Exception:
                    return {"ok": False, "reason": "unreachable"}
                if s3 != 200:
                    return {"ok": False, "reason": "unreachable"}
                try:
                    d3 = json.loads(raw3.decode("utf-8", errors="ignore"))
                    total_usage_cents = float(d3.get("total_usage", 0) or 0)
                except Exception:
                    return {"ok": False, "reason": "unreachable"}
                limit_cents = hard_limit_usd * 100
                if limit_cents <= 0:
                    return {"ok": False, "reason": "exhausted"}
                if total_usage_cents >= limit_cents:
                    return {"ok": False, "reason": "exhausted"}
                return {"ok": True, "reason": "ok"}
            try:
                res = await asyncio.to_thread(do_req)
                ok = bool(res.get("ok"))
                reason = res.get("reason") or ("ok" if ok else "unreachable")
            except Exception:
                ok = False
                reason = "unreachable"
        self.last_ok = bool(ok and api_key)
        self.last_reason = reason
        self.last_checked = time.time()

connectivity = OpenAIConnectivityMonitor()

@app.on_event("startup")
async def on_start():
    try:
        await connectivity._check_once()
    finally:
        await connectivity.start()

@app.websocket("/ws/health")
async def ws_health(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            api_key_present = bool(os.getenv("OPENAI_API_KEY"))
            now = time.time()
            age_seconds = max(0.0, now - float(connectivity.last_checked or 0))
            payload = {
                "type": "health",
                "api_key_present": api_key_present,
                "openai_reachable": bool(connectivity.last_ok),
                "checked_at": connectivity.last_checked,
                "age_seconds": round(age_seconds, 3),
                "reason": connectivity.last_reason
            }
            await websocket.send_json(payload)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return

@app.post("/api/validate_key")
async def validate_key(api_key: Optional[str] = Form(None)):
    key = (api_key or "").strip()
    if not key:
        return JSONResponse({"ok": False, "reason": "missing"}, status_code=200)
    try:
        def do_req():
            try:
                conn = http.client.HTTPSConnection("api.openai.com", timeout=3)
                conn.request("GET", "/v1/dashboard/billing/subscription", headers={"Authorization": f"Bearer {key}"})
                r = conn.getresponse()
                s = r.status
                raw = r.read() or b""
                conn.close()
            except Exception:
                return {"ok": False, "reason": "unreachable"}
            if s != 200:
                return {"ok": False, "reason": "invalid"}
            try:
                d = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                return {"ok": False, "reason": "invalid"}
            access_until = float(d.get("access_until", 0) or 0)
            hard_limit_usd = float(d.get("hard_limit_usd", 0) or 0)
            if access_until and access_until < time.time():
                return {"ok": False, "reason": "expired"}
            if hard_limit_usd <= 0:
                try:
                    conn2 = http.client.HTTPSConnection("api.openai.com", timeout=3)
                    conn2.request("GET", "/v1/dashboard/billing/credit_grants", headers={"Authorization": f"Bearer {key}"})
                    r2 = conn2.getresponse()
                    s2 = r2.status
                    raw2 = r2.read() or b""
                    conn2.close()
                    if s2 == 200:
                        d2 = json.loads(raw2.decode("utf-8", errors="ignore"))
                        ta = float(d2.get("total_available", 0) or 0)
                        return {"ok": ta > 0, "reason": "ok" if ta > 0 else "exhausted"}
                except Exception:
                    return {"ok": False, "reason": "unreachable"}
            start_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30*24*3600))
            end_date = time.strftime("%Y-%m-%d", time.gmtime())
            try:
                conn3 = http.client.HTTPSConnection("api.openai.com", timeout=3)
                conn3.request("GET", f"/v1/dashboard/billing/usage?start_date={start_date}&end_date={end_date}", headers={"Authorization": f"Bearer {key}"})
                r3 = conn3.getresponse()
                s3 = r3.status
                raw3 = r3.read() or b""
                conn3.close()
            except Exception:
                return {"ok": False, "reason": "unreachable"}
            if s3 != 200:
                return {"ok": False, "reason": "invalid"}
            try:
                d3 = json.loads(raw3.decode("utf-8", errors="ignore"))
                total_usage_cents = float(d3.get("total_usage", 0) or 0)
            except Exception:
                return {"ok": False, "reason": "invalid"}
            return {"ok": total_usage_cents >= 0, "reason": "ok"}
        res = await asyncio.to_thread(do_req)
        ok = bool(res.get("ok"))
        reason = res.get("reason") or ("ok" if ok else "invalid")
        return {"ok": ok, "reason": reason}
    except Exception:
        return JSONResponse({"ok": False, "reason": "unreachable"}, status_code=200)
