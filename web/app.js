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
const setKeyBtn = document.getElementById('setKey')
const clearKeyBtn = document.getElementById('clearKey')
const cancelBtn = document.getElementById('cancel')
startBtn.disabled = true

let ws
let wsHealth
let lastHealth = { ok: false, present: false, age: Infinity }
let userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
let lastJobId
let userHealthIntervalId
const FRESH_WINDOW_SECONDS = 300

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
  const fresh = age <= FRESH_WINDOW_SECONDS
  const healthy = ok && present && fresh
  apiDot.style.background = healthy ? '#12b76a' : present ? '#f59e0b' : '#ef4444'
  apiText.textContent = present ? (healthy ? 'API OK' : 'API not working') : 'API key missing'
  startBtn.disabled = !healthy
}

function nowSeconds(){ return Date.now() / 1000 }
function getCombinedHealth(){
  const freshServer = lastHealth.age <= FRESH_WINDOW_SECONDS && lastHealth.present && lastHealth.ok
  const ageUser = userHealth.present ? Math.max(0, nowSeconds() - (userHealth.checkedAt || 0)) : Infinity
  const freshUser = ageUser <= FRESH_WINDOW_SECONDS && userHealth.present && userHealth.ok
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

function reasonToMessage(r){
  if(r === 'ok') return 'API key saved'
  if(r === 'invalid') return 'API key invalid'
  if(r === 'expired') return 'API key expired'
  if(r === 'exhausted') return 'API quota exhausted'
  if(r === 'unreachable') return 'OpenAI API unreachable'
  if(r === 'missing') return 'API key missing'
  return 'API key error'
}

async function doValidateKey(key){
  const trimmed = (key || '').trim()
  if(!trimmed){ return { ok:false, reason:'missing' } }
  try{
    const fd = new FormData()
    fd.append('api_key', trimmed)
    const res = await fetch('/api/validate_key', { method: 'POST', body: fd })
    const data = await res.json().catch(()=>({ ok:false, reason:'invalid' }))
    const ok = Boolean(data && data.ok)
    return { ok, reason: (data && data.reason) || (ok ? 'ok' : 'invalid') }
  }catch(e){
    return { ok:false, reason:'unreachable' }
  }
}

function stopUserHealthLoop(){
  if(userHealthIntervalId){
    clearTimeout(userHealthIntervalId)
    userHealthIntervalId = undefined
  }
}

function startUserHealthLoop(){
  stopUserHealthLoop()
  let nextDelaySec = 15
  async function tick(){
    const stored = getCookie('user_api_key') || ''
    if(!stored){ stopUserHealthLoop(); return }
    const res = await doValidateKey(stored)
    const ok = Boolean(res.ok)
    userHealth = { ok, present: true, age: 0, checkedAt: nowSeconds(), reason: res.reason || (ok ? 'ok' : 'invalid') }
    updateCombinedStatus()
    if(ok){
      // Slow down checks when healthy; between 60s and 300s
      nextDelaySec = Math.min(300, Math.max(60, nextDelaySec * 2))
    }else{
      // Retry faster on failure; between 15s and 120s
      nextDelaySec = Math.min(120, Math.max(15, Math.floor(nextDelaySec * 1.5)))
    }
    userHealthIntervalId = setTimeout(tick, nextDelaySec * 1000)
  }
  // Start immediately, then backoff
  tick()
}

async function applyApiKey(key, opts){
  const options = opts || { silent: false }
  const res = await doValidateKey(key)
  if(res.ok){
    setCookie('user_api_key', (key || '').trim(), 30)
    userHealth = { ok: true, present: true, age: 0, checkedAt: nowSeconds(), reason: 'ok' }
    startUserHealthLoop()
    if(!options.silent){ setStatus('API key saved') }
  }else{
    userHealth = { ok: false, present: true, age: Infinity, checkedAt: 0, reason: res.reason }
    stopUserHealthLoop()
    if(!options.silent){ setStatus(reasonToMessage(res.reason)) }
  }
  updateCombinedStatus()
}

function setCookie(name, value, days){
  const d = new Date()
  d.setTime(d.getTime() + (days*24*60*60*1000))
  const expires = 'expires=' + d.toUTCString()
  const secure = location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = name + '=' + encodeURIComponent(value) + ';' + expires + '; path=/; SameSite=Strict' + secure
}
function getCookie(name){
  const n = name + '='
  const ca = document.cookie.split(';')
  for(let i=0;i<ca.length;i++){
    let c = ca[i]
    while(c.charAt(0) === ' '){ c = c.substring(1) }
    if(c.indexOf(n) === 0){ return decodeURIComponent(c.substring(n.length, c.length)) }
  }
  return ''
}
function deleteCookie(name){
  document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; SameSite=Strict'
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
  apiKeyInput.addEventListener('input', function(){
    // Mark as changed; require explicit Set
    stopUserHealthLoop()
    userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
    updateCombinedStatus()
  })
}

if(setKeyBtn){
  setKeyBtn.addEventListener('click', async function(){
    const key = (apiKeyInput && apiKeyInput.value || '').trim()
    if(!key){
      userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
      updateCombinedStatus()
      setStatus('API key missing')
      return
    }
    setKeyBtn.disabled = true
    try{ await applyApiKey(key, { silent: false }) } finally { setKeyBtn.disabled = false }
  })
}

if(clearKeyBtn){
  clearKeyBtn.addEventListener('click', function(){
    stopUserHealthLoop()
    deleteCookie('user_api_key')
    if(apiKeyInput){ apiKeyInput.value = '' }
    userHealth = { ok: false, present: false, age: Infinity, checkedAt: 0, reason: 'missing' }
    updateCombinedStatus()
    setStatus('API key cleared')
  })
}

// Load persisted key on startup
;(function(){
  const legacy = (typeof localStorage !== 'undefined') ? (localStorage.getItem('user_api_key') || '') : ''
  if(legacy){ setCookie('user_api_key', legacy, 30); try{ localStorage.removeItem('user_api_key') }catch(_){} }
  const stored = getCookie('user_api_key')
  if(stored && apiKeyInput){ apiKeyInput.value = stored; applyApiKey(stored, { silent: true }) }
})()


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
  if(startBtn.disabled || !(c.ok && c.present && c.age <= FRESH_WINDOW_SECONDS)){
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
  const cookieKey = getCookie('user_api_key')
  if(cookieKey && userHealth.present && userHealth.ok && (nowSeconds() - (userHealth.checkedAt||0)) <= FRESH_WINDOW_SECONDS){ fd.append('api_key', cookieKey) }
  const res = await fetch('/api/translate', { method: 'POST', body: fd })
  if(!res.ok){ setStatus('Error starting job'); return }
  const data = await res.json()
  lastJobId = data.job_id
  setStatus('Processing')
  if(cancelBtn){ cancelBtn.disabled = false }
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
      if(cancelBtn){ cancelBtn.disabled = true }
    } else if(msg.type === 'error'){
      setStatus('Error')
      closeWs()
      if(cancelBtn){ cancelBtn.disabled = true }
    } else if(msg.type === 'cancelled'){
      setStatus('Cancelled')
      closeWs()
      if(cancelBtn){ cancelBtn.disabled = true }
      downloadA.style.display = 'none'
    }
  }
  ws.onerror = ()=>{ setStatus('Connection error') }
})

if(cancelBtn){
  cancelBtn.addEventListener('click', async function(){
    if(!lastJobId){ return }
    try{
      const res = await fetch(`/api/cancel/${lastJobId}`, { method: 'POST' })
      if(res.ok){ setStatus('Cancelled'); if(cancelBtn){ cancelBtn.disabled = true }; downloadA.style.display = 'none'; closeWs() }
    }catch(e){ setStatus('Cancel failed') }
  })
}

