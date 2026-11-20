/* Spectrometer API client (sends the language on every request) */
let LANG = 'en'
export function setApiLang(l) { LANG = l }

const withLang = (u) => u + (u.includes('?') ? '&' : '?') + 'lang=' + LANG

const j = async (r) => {
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try { msg = (await r.json()).detail || msg } catch { /* empty */ }
    throw new Error(msg)
  }
  return r.json()
}

const post = (url, body) =>
  fetch(withLang(url), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lang: LANG, ...(body || {}) }),
  }).then(j)

export const api = {
  status: (hard = false) =>
    fetch(withLang(hard ? '/api/status?refresh_cameras=true' : '/api/status')).then(j),
  streamUrl: () => withLang('/api/stream'),
  frameUrl: (slot) => withLang(`/api/frame/${slot}`) + `&t=${Date.now()}`,
  capture: (slot) => fetch(withLang(`/api/capture/${slot}`), { method: 'POST' }).then(j),
  uploadFrame: (slot, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(withLang(`/api/frame/${slot}`), { method: 'POST', body: fd }).then(j)
  },
  clearSlot: (slot) => fetch(withLang(`/api/frame/${slot}`), { method: 'DELETE' }).then(j),
  analyze: (payload) => post('/api/analyze', payload),
  calibrate: (payload) => post('/api/calibrate', payload),
  calPreviewUrl: () => withLang('/api/calibration/preview') + `&t=${Date.now()}`,
  library: () => fetch(withLang('/api/library')).then(j),
  search: (q) =>
    fetch(withLang(`/api/library/search?q=${encodeURIComponent(q)}`)).then(j),
  spectrum: (name) =>
    fetch(withLang(`/api/library/spectrum?name=${encodeURIComponent(name)}`)).then(j),
  fetchCollection: (name) => post(`/api/library/fetch/${name}`),
  upload: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(withLang('/api/library/upload'), { method: 'POST', body: fd }).then(j)
  },
  camSettings: (payload) => post('/api/camera/settings', payload),
  camRefresh: () => post('/api/camera/refresh'),
  camStop: () => post('/api/camera/stop'),
}
