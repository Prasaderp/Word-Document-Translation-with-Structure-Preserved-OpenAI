const form = document.getElementById('form')
const fileInput = document.getElementById('file')
const lang = document.getElementById('lang')
const terms = document.getElementById('terms')
const dropzone = document.getElementById('dropzone')
const fileName = document.getElementById('file-name')
const ring = document.getElementById('ring')
const percentEl = document.getElementById('percent')
const timeEl = document.getElementById('time')
const statusEl = document.getElementById('status')
const downloadA = document.getElementById('download')
const apiDot = document.getElementById('api-dot')
const apiText = document.getElementById('api-text')
const startBtn = document.getElementById('start')
const apiKeyInput = document.getElementById('apiKey')
startBtn.disabled = true

let ws
let wsHealth
let lastHealth = { ok: false, present: false, age: Infinity }
let userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
let lastJobId

function setProgress(p){
  const clamped = Math.max(0, Math.min(100, p))
  ring.style.background = `conic-gradient(#ffffff ${clamped}%, #444 ${clamped}% 100%)`
  percentEl.textContent = `${clamped.toFixed(0)}%`
}

function setTime(seconds){
  timeEl.textContent = `${seconds.toFixed(1)}s`
}

function setStatus(t){
  statusEl.textContent = t
}
function setApiStatus(ok, present, age){
  const fresh = age <= 6
  const healthy = ok && present && fresh
  apiDot.style.background = healthy ? '#12b76a' : present ? '#f59e0b' : '#ef4444'
  apiText.textContent = present ? (healthy ? 'API OK' : 'API not working') : 'API key missing'
  startBtn.disabled = !healthy
}

function nowSeconds(){ return Date.now() / 1000 }
function getCombinedHealth(){
  const freshServer = lastHealth.age <= 6 && lastHealth.present && lastHealth.ok
  const ageUser = userHealth.present ? Math.max(0, nowSeconds() - (userHealth.checkedAt || 0)) : Infinity
  const freshUser = ageUser <= 6 && userHealth.present && userHealth.ok
  const present = Boolean(lastHealth.present || userHealth.present)
  const ok = Boolean(freshServer || freshUser)
  const age = Math.min(lastHealth.age, ageUser)
  return { ok, present, age }
}
function updateCombinedStatus(){
  const c = getCombinedHealth()
  setApiStatus(c.ok, c.present, c.age)
}

function debounce(fn, wait){
  let t
  return function(){
    const ctx = this, args = arguments
    clearTimeout(t)
    t = setTimeout(function(){ fn.apply(ctx, args) }, wait)
  }
}

async function validateUserKey(){
  const key = (apiKeyInput && apiKeyInput.value || '').trim()
  if(!key){
    userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
    updateCombinedStatus()
    return
  }
  try{
    const fd = new FormData()
    fd.append('api_key', key)
    const res = await fetch('/api/validate_key', { method: 'POST', body: fd })
    const data = await res.json().catch(()=>({ ok:false, reason:'invalid' }))
    const ok = Boolean(data && data.ok)
    userHealth = { ok, present: true, age: 0, checkedAt: nowSeconds(), reason: data && data.reason || (ok ? 'ok' : 'invalid') }
  }catch(e){
    userHealth = { ok: false, present: true, age: Infinity, checkedAt: 0, reason: 'unreachable' }
  }
  updateCombinedStatus()
}

function startHealth(){
  startBtn.disabled = true
  setApiStatus(false, false, Infinity)
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  wsHealth = new WebSocket(`${proto}://${location.host}/ws/health`)
  wsHealth.onmessage = (ev)=>{
    const msg = JSON.parse(ev.data)
    if(msg.type === 'health'){
      lastHealth = { ok: Boolean(msg.openai_reachable), present: Boolean(msg.api_key_present), age: Number(msg.age_seconds || Infinity) }
      updateCombinedStatus()
    }
  }
  wsHealth.onerror = ()=>{ setApiStatus(false, false, Infinity) }
}

startHealth()

if(apiKeyInput){
  apiKeyInput.addEventListener('input', debounce(validateUserKey, 500))
}
setInterval(function(){ if(apiKeyInput && (apiKeyInput.value || '').trim()){ validateUserKey() } }, 10000)


function closeWs(){
  if(ws){
    try{ ws.close() }catch(e){}
    ws = undefined
  }
}
dropzone.addEventListener('dragover', (e)=>{ e.preventDefault() })
dropzone.addEventListener('drop', (e)=>{
  e.preventDefault()
  if(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]){
    fileInput.files = e.dataTransfer.files
    fileName.textContent = e.dataTransfer.files[0].name
  }
})
fileInput.addEventListener('change', ()=>{
  const f = fileInput.files && fileInput.files[0]
  fileName.textContent = f ? f.name : 'No file selected'
})


form.addEventListener('submit', async (e)=>{
  e.preventDefault()
  closeWs()
  const c = getCombinedHealth()
  if(startBtn.disabled || !(c.ok && c.present && c.age <= 6)){
    setStatus('API key is not working')
    return
  }
  downloadA.style.display = 'none'
  setProgress(0)
  setTime(0)
  setStatus('Uploading')
  const file = fileInput.files[0]
  if(!file){ return }
  const fd = new FormData()
  fd.append('file', file)
  fd.append('target_language', lang.value)
  fd.append('retain_terms', terms.value || '')
  if(userHealth.present && userHealth.ok && (nowSeconds() - (userHealth.checkedAt||0)) <= 6){
    fd.append('api_key', (apiKeyInput.value || '').trim())
  }
  const res = await fetch('/api/translate', { method: 'POST', body: fd })
  if(!res.ok){ setStatus('Error starting job'); return }
  const data = await res.json()
  lastJobId = data.job_id
  setStatus('Processing')
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/progress/${lastJobId}`)
  ws.onmessage = (ev)=>{
    const msg = JSON.parse(ev.data)
    if(msg.type === 'progress'){
      setProgress(msg.progress || 0)
      setTime(Number(msg.elapsed_seconds || 0))
      setStatus('Processing')
    } else if(msg.type === 'completed'){
      setProgress(100)
      setTime(Number(msg.elapsed_seconds || 0))
      setStatus('Completed')
      if(msg.download_url){
        downloadA.href = msg.download_url
        downloadA.style.display = 'inline-block'
      }
      closeWs()
    } else if(msg.type === 'error'){
      setStatus('Error')
      closeWs()
    }
  }
  ws.onerror = ()=>{ setStatus('Connection error') }
})

