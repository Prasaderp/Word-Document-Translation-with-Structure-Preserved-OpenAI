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
import tempfile
import shutil
import hashlib
import logging
from logging.handlers import TimedRotatingFileHandler

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
        self.task: Optional[asyncio.Task] = None
        self.temp_dir: Optional[str] = None

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

PROJECT_ROOT = os.path.dirname(__file__)
static_dir = os.path.join(PROJECT_ROOT, "web")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Data and logs directories
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
LOGS_ROOT = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(DATA_ROOT, exist_ok=True)
os.makedirs(LOGS_ROOT, exist_ok=True)

def setup_logging():
    try:
        handler = TimedRotatingFileHandler(os.path.join(LOGS_ROOT, "app.log"), when="midnight", utc=True, backupCount=7)
        formatter = logging.Formatter('%(asctime)sZ %(message)s')
        # Use UTC for timestamps
        logging.Formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        # Avoid duplicate handlers on reload
        exists = any(isinstance(h, TimedRotatingFileHandler) and getattr(h, 'baseFilename', '') == handler.baseFilename for h in root_logger.handlers)
        if not exists:
            root_logger.addHandler(handler)
    except Exception:
        pass

class DataCleaner:
    def __init__(self, root_dir: str, ttl_seconds: float = 12*3600, interval_seconds: float = 1800):
        self.root_dir = root_dir
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            try:
                await self._clean_once()
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    async def _clean_once(self):
        try:
            now_ts = time.time()
            if not os.path.isdir(self.root_dir):
                return
            for name in os.listdir(self.root_dir):
                path = os.path.join(self.root_dir, name)
                try:
                    if not os.path.isdir(path):
                        continue
                    # Skip active jobs
                    active = False
                    try:
                        for job in list(job_manager.jobs.values()):
                            jd = os.path.dirname(job.input_path)
                            if os.path.abspath(jd) == os.path.abspath(path) and job.status == "running":
                                active = True
                                break
                    except Exception:
                        active = False
                    if active:
                        continue
                    # Age threshold based on last modification time
                    age = now_ts - float(os.path.getmtime(path))
                    if age >= self.ttl_seconds:
                        try:
                            shutil.rmtree(path, ignore_errors=True)
                            logging.info(f"CLEAN status=deleted dir={name} age_seconds={int(age)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
                        except Exception as e:
                            logging.info(f"CLEAN status=error dir={name} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} error={str(e)}")
                except Exception:
                    continue
        except Exception:
            return

@app.post("/api/translate")
async def start_translation(file: UploadFile = File(...), target_language: str = Form(...), retain_terms: Optional[str] = Form(None), api_key: Optional[str] = Form(None)):
    effective_api_key = (api_key or "").strip()
    if not effective_api_key:
        return JSONResponse({"error": "API key is missing"}, status_code=400)
    if not file.filename.lower().endswith(".docx"):
        return JSONResponse({"error": "Only .docx files are supported"}, status_code=400)
    data_root = os.path.join(DATA_ROOT, str(uuid.uuid4()))
    os.makedirs(data_root, exist_ok=True)
    file_bytes = await file.read()
    job = await job_manager.create_job(data_root, file.filename, file_bytes, target_language, retain_terms)
    job.temp_dir = os.path.dirname(job.input_path)
    logging.info(f"JOB status=created id={job.job_id} file={os.path.basename(job.input_path)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    task = asyncio.create_task(run_job(job.job_id, effective_api_key))
    job.task = task
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

@app.post("/api/cancel/{job_id}")
async def cancel(job_id: str):
    job = await job_manager.get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if job.status in ("completed", "error", "cancelled"):
        return {"ok": True, "status": job.status}
    job.status = "cancelled"
    job.completed_at = time.time()
    try:
        if job.task:
            job.task.cancel()
    except Exception:
        pass
    try:
        await job_manager.broadcast(job, {"type": "cancelled", "progress": float(job.progress), "avg_quality": round(job.avg_quality, 2)})
    except Exception:
        pass
    try:
        if job.temp_dir and os.path.isdir(job.temp_dir):
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            job.temp_dir = None
    except Exception:
        pass
    logging.info(f"JOB status=cancelled id={job.job_id} file={os.path.basename(job.input_path)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    return {"ok": True, "status": "cancelled"}

async def run_job(job_id: str, effective_api_key: Optional[str] = None):
    job = await job_manager.get_job(job_id)
    if job is None:
        return
    job.status = "running"
    job.started_at = time.time()
    api_key = effective_api_key
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
        logging.info(f"JOB status=completed id={job.job_id} file={os.path.basename(job.input_path)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    except Exception as e:
        if isinstance(e, asyncio.CancelledError):
            job.status = "cancelled"
            job.error = None
            logging.info(f"JOB status=cancelled id={job.job_id} file={os.path.basename(job.input_path)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        else:
            job.status = "error"
            job.error = str(e)
            await job_manager.broadcast(job, {"type": "error", "message": job.error})
            logging.info(f"JOB status=error id={job.job_id} file={os.path.basename(job.input_path)} at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} error={job.error}")
        try:
            if job.temp_dir and os.path.isdir(job.temp_dir):
                shutil.rmtree(job.temp_dir, ignore_errors=True)
                job.temp_dir = None
        except Exception:
            pass

@app.get("/")
async def root_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.on_event("startup")
async def on_start():
    setup_logging()
    cleaner = DataCleaner(DATA_ROOT)
    await cleaner.start()

@app.websocket("/ws/health")
async def ws_health(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            now = time.time()
            payload = {
                "type": "health",
                "api_key_present": False,
                "openai_reachable": False,
                "checked_at": now,
                "age_seconds": 0.0,
                "reason": "client_key_only"
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
    # Simple in-memory cache to avoid hammering OpenAI on frequent validations
    # Cache by hash for privacy
    global _key_cache
    try:
        _key_cache
    except NameError:
        _key_cache = {}
    now_ts = time.time()
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cached = _key_cache.get(key_hash)
    if cached:
        last_ts, last_res = cached
        # Minimum interval between upstream checks
        if (now_ts - last_ts) < 15:
            return {"ok": bool(last_res.get("ok")), "reason": last_res.get("reason") or ("ok" if last_res.get("ok") else "invalid")}
        # Respect TTLs: 5 minutes for success, 30s for failure
        if (last_res.get("ok") and (now_ts - last_ts) < 300) or ((not last_res.get("ok")) and (now_ts - last_ts) < 30):
            return {"ok": bool(last_res.get("ok")), "reason": last_res.get("reason") or ("ok" if last_res.get("ok") else "invalid")}
    try:
        def do_req():
            try:
                # Use a lightweight endpoint that verifies key validity without billing-specific scopes
                conn = http.client.HTTPSConnection("api.openai.com", timeout=3)
                conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {key}"})
                r = conn.getresponse()
                s = r.status
                raw = r.read() or b""
                conn.close()
            except Exception:
                return {"ok": False, "reason": "unreachable"}
            # Interpret common statuses conservatively
            if s == 200:
                return {"ok": True, "reason": "ok"}
            if s == 429:
                # Key is valid but currently rate limited
                return {"ok": True, "reason": "ok"}
            if s in (401, 403):
                return {"ok": False, "reason": "invalid"}
            # Other statuses treated as temporary/unreachable
            return {"ok": False, "reason": "unreachable"}
        res = await asyncio.to_thread(do_req)
        ok = bool(res.get("ok"))
        reason = res.get("reason") or ("ok" if ok else "invalid")
        # Update cache
        _key_cache[key_hash] = (now_ts, {"ok": ok, "reason": reason})
        return {"ok": ok, "reason": reason}
    except Exception:
        return JSONResponse({"ok": False, "reason": "unreachable"}, status_code=200)
