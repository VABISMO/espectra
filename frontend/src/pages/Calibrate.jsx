/* Calibration page: LED capture → pixel → nm */
import { useState } from 'react'
import { api } from '../api.js'
import CameraPanel from '../components/CameraPanel.jsx'
import { useLang } from '../lang.jsx'

const METHODS = [
  { id: 'auto', t: 'cal.m.auto.t', d: 'cal.m.auto.d' },
  { id: 'lines', t: 'cal.m.lines.t', d: 'cal.m.lines.d' },
  { id: 'led', t: 'cal.m.led.t', d: 'cal.m.led.d' },
  { id: 'anchors', t: 'cal.m.anchors.t', d: 'cal.m.anchors.d' },
  { id: 'range', t: 'cal.m.range.t', d: 'cal.m.range.d' },
  { id: 'blackbody', t: 'cal.m.bb.t', d: 'cal.m.bb.d' },
]

export default function Calibrate({ status, refresh }) {
  const { t } = useLang()
  const [method, setMethod] = useState('auto')
  const [anchors, setAnchors] = useState([[0, 450], [1000, 700]])
  const [range, setRange] = useState([400, 700])
  const [bb, setBb] = useState(3000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [out, setOut] = useState(null)

  const cal = status?.calibration

  const SLOTS = [
    { id: 'cal', label: `📸 ${t('cal.captureLed')}`, short: t('an.shortLamp'), primary: true },
  ]

  const run = async () => {
    setBusy(true); setError(null); setOut(null)
    try {
      const payload = { method }
      if (method === 'anchors') payload.anchors = anchors
      if (method === 'range') payload.range = range
      if (method === 'blackbody') payload.blackbody = bb
      const r = await api.calibrate(payload)
      setOut(r)
      refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const slotReady = status?.slots?.cal

  return (
    <>
      <div className="card">
        <div className="kicker">{t('cal.step1')}</div>
        <h2>{t('cal.h2')}</h2>
        <p className="sub" dangerouslySetInnerHTML={{ __html: t('cal.sub') }} />
        <CameraPanel slots={SLOTS} status={status} refresh={refresh}
          hint={t('cal.hint')} />
      </div>

      <div className="card">
        <div className="kicker">{t('cal.step2')}</div>
        <h2>{t('cal.h2b')}</h2>
        <div className="methods" style={{ margin: '14px 0 18px' }}>
          {METHODS.map((m) => (
            <button key={m.id} className={`method ${method === m.id ? 'active' : ''}`}
              onClick={() => setMethod(m.id)}>
              <b>{t(m.t)}</b><span>{t(m.d)}</span>
            </button>
          ))}
        </div>

        {method === 'anchors' && (
          <div className="card pad-sm" style={{ marginBottom: 16 }}>
            <label className="field">{t('cal.anchors')}</label>
            {anchors.map((a, i) => (
              <div key={i} className="row c3" style={{ marginBottom: 8 }}>
                <input type="number" value={a[0]}
                  onChange={(e) => setAnchors(anchors.map((x, j) =>
                    j === i ? [Number(e.target.value), x[1]] : x))} />
                <input type="number" value={a[1]}
                  onChange={(e) => setAnchors(anchors.map((x, j) =>
                    j === i ? [x[0], Number(e.target.value)] : x))} />
                <button className="btn sm danger"
                  onClick={() => setAnchors(anchors.filter((_, j) => j !== i))}>
                  ✖ {t('cal.remove')}
                </button>
              </div>
            ))}
            <button className="btn sm" onClick={() => setAnchors([...anchors, [500, 550]])}>
              {t('cal.add')}
            </button>
          </div>
        )}

        {method === 'range' && (
          <div className="row c2" style={{ marginBottom: 16, maxWidth: 460 }}>
            <div>
              <label className="field">{t('cal.nm0')}</label>
              <input type="number" value={range[0]}
                onChange={(e) => setRange([Number(e.target.value), range[1]])} />
            </div>
            <div>
              <label className="field">{t('cal.nm1')}</label>
              <input type="number" value={range[1]}
                onChange={(e) => setRange([range[0], Number(e.target.value)])} />
            </div>
          </div>
        )}

        {method === 'blackbody' && (
          <div style={{ marginBottom: 16, maxWidth: 460 }}>
            <label className="field">{t('cal.temp', { v: bb })}</label>
            <input type="range" min={2000} max={4000} step={50} value={bb}
              onChange={(e) => setBb(Number(e.target.value))} />
          </div>
        )}

        {error && <div className="alert error">{error}</div>}

        <button className="btn primary" onClick={run}
          disabled={busy || !slotReady}>
          {busy ? <span className="spin" /> : '⚙️'}
          {busy ? ` ${t('cal.running')}` : ` ${t('cal.run')}`}
        </button>
        {!slotReady && (
          <span style={{ marginLeft: 12, color: '#fcd77a', fontSize: 13 }}>
            ⚠ {t('cal.needLed')}
          </span>
        )}
      </div>

      {out && (
        <div className="card">
          <div className="kicker">{t('cal.resultKicker')}</div>
          <h2>{t('cal.ok')}</h2>
          <div className="log" style={{ marginTop: 14 }}>
{`${t('cal.log.mode', { v: t(`mode.${out.meta.mode}`) })}
${t('cal.log.coeffs', { v: out.coeffs.join(', ') })}
${t('cal.log.formula')}` +
              (out.meta.lines
                ? `\n${t('cal.log.lines', {
                  v: out.meta.lines.map((l) => `${l.nm} nm@p${l.pixel.toFixed(0)}`)
                    .join(' · '),
                })}` : '') +
              (out.meta.fwhm_nm != null
                ? `\n${t('cal.log.fwhm', { v: out.meta.fwhm_nm })}` : '') +
              (out.meta.led_peaks_pixel
                ? `\n${t('cal.log.peaks', {
                  a: out.meta.led_peaks_pixel[0],
                  b: out.meta.led_peaks_pixel[1],
                })}` : '') +
              (out.meta.led_corr != null
                ? `\n${t('cal.log.fit', {
                  v: Number(out.meta.led_corr).toFixed(4) })}` : '') +
              (out.meta.blackbody_T != null
                ? `\n${t('cal.log.T', { v: Math.round(out.meta.blackbody_T) })}` : '')}
          </div>
          {/* large calibration chart, full width */}
          {out.preview_b64 && (
            <div style={{ marginTop: 16 }}>
              <label className="field">{t('cal.preview')}</label>
              <img alt={t('cal.preview')}
                src={`data:image/png;base64,${out.preview_b64}`}
                style={{
                  width: '100%', display: 'block',
                  border: '1px solid rgba(148,163,184,.25)',
                  borderRadius: 14, background: '#fff',
                }} />
            </div>
          )}
        </div>
      )}

      {cal && !out && (
        <div className="card pad-sm">
          <span className="chip ok">✔ {t('cal.active', { mode: t(`mode.${cal.mode}`) })}</span>{' '}
          <span style={{
            fontFamily: 'JetBrains Mono, monospace', fontSize: 13, color: '#a3b0cc',
          }}>
            nm(p) = [{cal.coeffs.join(', ')}]
          </span>
          {cal.led_corr != null && (
            <span style={{ marginLeft: 10, color: '#7c8aa8', fontSize: 13 }}>
              r = {Number(cal.led_corr).toFixed(4)}
            </span>
          )}
          {cal.fwhm_nm != null && (
            <span style={{ marginLeft: 10, color: '#7c8aa8', fontSize: 13 }}>
              FWHM {cal.fwhm_nm} nm
            </span>
          )}
        </div>
      )}
    </>
  )
}
