/* Shared USB camera panel: live view + slot capture */
import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Html, useLang } from '../lang.jsx'

export default function CameraPanel({ slots, status, refresh, hint }) {
  const { t } = useLang()
  const cameras = status?.cameras || []
  const [live, setLive] = useState(false)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [camIdx, setCamIdx] = useState(null)
  const [expo, setExpo] = useState(-6)
  const [auto, setAuto] = useState(true)

  useEffect(() => {
    if (camIdx == null && cameras.length) {
      // ALWAYS prefers the external (free) USB one, never the laptop's
      const builtin = (c) => /integrated|internal|laptop|interno/i.test(c.name || '')
      const externals = cameras.filter((c) => !builtin(c))
      const pool = externals.length ? externals : cameras
      const free = pool.find((c) => c.openable !== false) || pool[0]
      setCamIdx(free.index)
    }
  }, [cameras, camIdx])

  const capture = async (id) => {
    setBusy(id); setError(null)
    try {
      await api.capture(id)
      refresh()
    } catch (e) { setError(e.message) } finally { setBusy(null) }
  }

  const upload = async (id, e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(id); setError(null)
    try {
      await api.uploadFrame(id, f)
      refresh()
    } catch (err) { setError(err.message) } finally { setBusy(null) }
  }

  const toggleLive = async (v) => {
    setError(null)
    if (v) {
      try {
        await api.camSettings({ index: camIdx, expo, auto })
        setLive(true)
      } catch (e) { setError(e.message) }
    } else {
      setLive(false)
      await api.camStop().catch(() => {})
    }
  }

  const settings = async (patch) => {
    try { await api.camSettings(patch) } catch (e) { setError(e.message) }
  }

  const clearSlot = async (id) => {
    await api.clearSlot(id).catch(() => {})
    refresh()
  }

  const captured = slots.filter((s) => status?.slots?.[s.id])

  return (
    <div className="row" style={{ gridTemplateColumns: 'minmax(0, 1.6fr) minmax(270px, 1fr)', alignItems: 'start' }}>
      <div>
        {live ? (
          <div className="live-wrap">
            <img src={api.streamUrl()} alt="live camera view" />
            <span className="live-dot">{t('cam.live').toUpperCase()}</span>
          </div>
        ) : (
          <div className="empty" style={{ padding: '34px 22px' }}>
            <div className="big">◉</div>
            <Html k="cam.off" />
          </div>
        )}
        {error && <div className="alert error" style={{ marginTop: 12 }}>{error}</div>}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
        <div>
          <label className="field">{t('cam.camera')}</label>
          <select value={camIdx ?? ''} disabled={!cameras.length}
            onChange={(e) => {
              const v = Number(e.target.value)
              setCamIdx(v)
              if (live) settings({ index: v })
            }}>
            {!cameras.length && <option value="">{t('cam.none')}</option>}
            {cameras.map((c) => {
              const builtin = /integrated|internal|laptop|interno/i.test(c.name || '')
              const short = (c.name || '').split(':')[0].slice(0, 30)
              return (
                <option key={c.index} value={c.index}>
                  /dev/video{c.index} · {c.w}×{c.h} {c.fourcc}
                  {short ? ` · ${short}` : ''}
                  {builtin ? ` ${t('cam.builtin')}` : ` ${t('cam.usb')}`}
                </option>
              )
            })}
          </select>
        </div>

        <label style={{
          display: 'flex', gap: 9, alignItems: 'center', cursor: 'pointer',
          fontSize: 15, fontWeight: 600, color: '#e9eefb',
        }}>
          <input type="checkbox" checked={live} style={{ width: 17, height: 17 }}
            onChange={(e) => toggleLive(e.target.checked)} />
          {t('cam.live')}
        </label>

        <details style={{ fontSize: 13 }}>
          <summary style={{ cursor: 'pointer', color: '#a3b0cc', fontWeight: 600 }}>
            {t('cam.exposure')}
          </summary>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: '#a3b0cc' }}>
              <input type="checkbox" checked={auto} style={{ width: 15, height: 15 }}
                onChange={(e) => { setAuto(e.target.checked); settings({ auto: e.target.checked }) }} />
              {t('cam.autoExposure')}
            </label>
            {!auto && (
              <>
                <input type="range" min={-30} max={0} value={expo}
                  onChange={(e) => setExpo(Number(e.target.value))}
                  onMouseUp={() => settings({ expo, auto: false })} />
                <span style={{ fontFamily: 'JetBrains Mono, monospace', color: '#7c8aa8' }}>
                  {t('cam.exposureValue', { v: expo })}
                </span>
              </>
            )}
          </div>
        </details>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          {slots.map((s) => (
            <div key={s.id} style={{ display: 'flex', gap: 8 }}>
              <button className={`btn ${s.primary ? 'primary' : ''}`}
                style={{ flex: 1, minWidth: 0 }}
                disabled={busy != null || !cameras.length}
                onClick={() => capture(s.id)}>
                {busy === s.id ? <span className="spin" /> : null}
                {s.label}
              </button>
              <label className="btn" title={t('cam.upload')}
                style={{ padding: '11px 13px', cursor: 'pointer' }}>
                📁
                <input type="file" hidden accept="image/*"
                  disabled={busy != null}
                  onChange={(e) => upload(s.id, e)} />
              </label>
            </div>
          ))}
        </div>
        {hint && <div style={{ fontSize: 12.6, color: '#7c8aa8' }}>{hint}</div>}
      </div>

      {captured.length > 0 && (
        <div style={{ gridColumn: '1 / -1' }}>
          <label className="field">{t('cam.captures')}</label>
          <div className="thumbs">
            {captured.map((s) => (
              <div className="thumb" key={s.id}>
                <img src={api.frameUrl(s.id)} alt={s.short || s.label} />
                <div className="meta">
                  <b>{s.short || s.label}</b>
                  <button className="x" title={t('cam.delete')}
                    onClick={() => clearSlot(s.id)}>✖</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
