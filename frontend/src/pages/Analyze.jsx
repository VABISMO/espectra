/* Página Analizar: captura → ajustes → identificación */
import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import CameraPanel from '../components/CameraPanel.jsx'
import Chart, { PALETTE } from '../components/Chart.jsx'
import ScoreRing from '../components/ScoreRing.jsx'
import { Html, useLang } from '../lang.jsx'

export default function Analyze({ status, refresh, go }) {
  const { t } = useLang()
  const [cfg, setCfg] = useState({
    axis: 'auto', channel: 'sum', smooth: 5, frac: 0.25,
    rowsAuto: true, rows: [0, 400], gammaApply: false,
  })
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('curvas')
  const resultRef = useRef(null)

  // al terminar el análisis, lleva la vista al resultado
  useEffect(() => {
    if (!result) return
    const id = setTimeout(() => {
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 220)
    return () => clearTimeout(id)
  }, [result])

  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }))

  const SLOTS = [
    { id: 'sample', label: `📸 ${t('an.captureSample')}`, short: t('an.shortSample'), primary: true },
    { id: 'lamp', label: `💡 ${t('an.captureLamp')}`, short: t('an.shortLamp') },
    { id: 'dark', label: `🌑 ${t('an.captureDark')}`, short: t('an.shortDark') },
  ]

  const run = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.analyze({
        slot: 'sample',
        axis: cfg.axis, channel: cfg.channel, smooth: cfg.smooth,
        frac: cfg.frac, rows: cfg.rowsAuto ? null : cfg.rows, top: 10,
        gamma: cfg.gammaApply ? 2.2 : 1.0,
      })
      setResult(r)
    } catch (e) {
      setError(e.message); setResult(null)
    } finally { setBusy(false) }
  }

  const downloadCsv = () => {
    if (!result) return
    const head = ['material', 'category', 'score', 'color', 'delta_e',
      'sam_deg', 'pearson', 'continuum_r', 'delta_band']
    const rows = result.results.map((r) => [
      r.name, r.category, r.score, r.color, r.delta_e, r.sam_deg,
      r.r_raw, r.r_continuum, r.delta_banda,
    ])
    const csv = [head, ...rows]
      .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url; a.download = 'coincidencias.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  const hasCal = !!status?.calibration
  const noSample = !status?.slots?.sample
  const r0 = result?.rows?.[0] ?? 0
  const r1 = result?.rows?.[1] ?? 1
  const [ih, iw] = result?.image_shape || [1, 1]
  const bandStyle = result?.axis === 'y'
    ? { top: 0, bottom: 0, left: `${(r0 / iw) * 100}%`, width: `${((r1 - r0) / iw) * 100}%` }
    : { left: 0, right: 0, top: `${(r0 / ih) * 100}%`, height: `${((r1 - r0) / ih) * 100}%` }

  const top5 = result?.results?.slice(0, 5) || []
  const curveSeries = result
    ? [
        { name: t('an.legendSample'), color: '#e9eefb', data: result.curve.reflectance, width: 2.8 },
        ...top5.map((r, i) => ({ name: r.name.split(' [')[0], color: PALETTE[i], data: r.curve })),
      ]
    : []
  const contSeries = result
    ? [
        { name: t('an.legendSample'), color: '#e9eefb', data: result.continuum, width: 2.8 },
        ...top5.map((r, i) => ({ name: r.name.split(' [')[0], color: PALETTE[i], data: r.continuum })),
      ]
    : []

  const winner = result?.results?.[0]

  return (
    <>
      <div className="card">
        <div className="kicker">{t('an.step1')}</div>
        <h2>{t('an.h2')}</h2>
        <p className="sub"><Html k="an.sub" /></p>
        <CameraPanel slots={SLOTS} status={status} refresh={refresh}
          hint={t('an.hint')} />
      </div>

      <div className="card">
        <div className="kicker">{t('an.step2')}</div>
        <h2>{t('an.h2b')}</h2>

        {!hasCal && (
          <div className="alert warn">
            <Html k="an.needCal" />{' '}
            <button className="btn sm" onClick={() => go('calibrate')}>
              {t('an.goCal')}
            </button>
          </div>
        )}
        {hasCal && ['range', 'range_fallback'].includes(status.calibration.mode) && (
          <div className="alert warn"><Html k="an.weakCal" /></div>
        )}
        {error && <div className="alert error">{error}</div>}

        <div className="row c4" style={{ marginBottom: 16 }}>
          <div>
            <label className="field">{t('an.axis')}</label>
            <select value={cfg.axis} onChange={(e) => set('axis', e.target.value)}>
              <option value="auto">{t('an.axis.auto')}</option>
              <option value="x">{t('an.axis.x')}</option>
              <option value="y">{t('an.axis.y')}</option>
            </select>
          </div>
          <div>
            <label className="field">{t('an.channel')}</label>
            <select value={cfg.channel} onChange={(e) => set('channel', e.target.value)}>
              <option value="sum">{t('an.ch.sum')}</option>
              <option value="max">{t('an.ch.max')}</option>
              <option value="r">{t('an.ch.r')}</option>
              <option value="g">{t('an.ch.g')}</option>
              <option value="b">{t('an.ch.b')}</option>
              <option value="luma">{t('an.ch.luma')}</option>
            </select>
          </div>
          <div>
            <label className="field">{t('an.smooth', { v: cfg.smooth })}</label>
            <input type="range" min={1} max={41} step={2} value={cfg.smooth}
              onChange={(e) => set('smooth', Number(e.target.value))} />
          </div>
          <div>
            <label className="field">{t('an.frac', { v: cfg.frac.toFixed(2) })}</label>
            <input type="range" min={0.05} max={0.6} step={0.05} value={cfg.frac}
              onChange={(e) => set('frac', Number(e.target.value))} />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn primary" onClick={run}
            disabled={busy || !hasCal || noSample}>
            {busy ? <span className="spin" /> : '🔍'}
            {busy ? ` ${t('an.running')}` : ` ${t('an.run')}`}
          </button>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: '#a3b0cc', fontSize: 13 }}>
            <input type="checkbox" checked={cfg.rowsAuto} style={{ width: 15, height: 15 }}
              onChange={(e) => set('rowsAuto', e.target.checked)} />
            {t('an.autoRows')}
          </label>
          <label title="si la cámara de 8 bits aplica gamma, actívalo antes de dividir"
            style={{ display: 'flex', gap: 8, alignItems: 'center', color: '#a3b0cc', fontSize: 13 }}>
            <input type="checkbox" checked={cfg.gammaApply} style={{ width: 15, height: 15 }}
              onChange={(e) => set('gammaApply', e.target.checked)} />
            {t('an.gammaApply')}
          </label>
          {!cfg.rowsAuto && (
            <>
              <input type="number" value={cfg.rows[0]} style={{ width: 90 }}
                onChange={(e) => set('rows', [Number(e.target.value), cfg.rows[1]])} />
              <span style={{ color: '#7c8aa8' }}>…</span>
              <input type="number" value={cfg.rows[1]} style={{ width: 90 }}
                onChange={(e) => set('rows', [cfg.rows[0], Number(e.target.value)])} />
            </>
          )}
          {noSample && (
            <span style={{ color: '#fcd77a', fontSize: 13 }}>⚠ {t('cam.needSample')}</span>
          )}
        </div>
      </div>

      {!result && !busy && (
        <div className="empty">
          <div className="big">◎</div>
          <Html k="an.empty" />
        </div>
      )}

      {result && winner && (
        <>
          {result.brightness < 0.03 && (
            <div className="alert error"><Html k="an.darkImage" /></div>
          )}
          {result.verdict && result.verdict.label !== 'identified' && (
            <div className={`alert ${result.verdict.label === 'unknown' ? 'error' : 'warn'}`}>
              <Html k={`an.verdict.note.${result.verdict.label}`} v={{
                s: result.verdict.score.toFixed(3),
                ms: result.verdict.min_score.toFixed(2),
                m: result.verdict.margin.toFixed(3),
                mm: result.verdict.min_margin.toFixed(2),
              }} />
            </div>
          )}
          <div className="winner" ref={resultRef}>
            <div className="winner-inner">
              <ScoreRing score={winner.score} />
              <div style={{ minWidth: 0 }}>
                <div className="kicker">
                  {result.verdict?.label === 'identified'
                    ? t('an.identified')
                    : t(`an.verdict.${result.verdict?.label}`)}
                </div>
                <div className="winner-name">{winner.name}</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                  {result.verdict && (
                    <span className={`chip ${result.verdict.label === 'identified' ? 'ok'
                      : result.verdict.label === 'uncertain' ? 'warn' : 'error'}`}>
                      {t(`an.verdict.${result.verdict.label}`)}
                    </span>
                  )}
                  <span className="chip">{winner.category || t('an.noCategory')}</span>
                  <span className="chip info">{winner.source}</span>
                  <span className="chip violet">ΔE {winner.delta_e}</span>
                  <span className="chip">SAM {winner.sam_deg}°</span>
                  {result.has_lamp
                    ? <span className="chip ok">{t('an.withLamp')}</span>
                    : <span className="chip warn">{t('an.noLamp')}</span>}
                  {typeof result.coverage === 'number' && (
                    <span className={`chip ${result.coverage < 0.95 ? 'warn' : 'ok'}`}>
                      {t('an.coverage', { v: Math.round(result.coverage * 100) })}
                    </span>
                  )}
                </div>
                <div className="swatch-row" style={{ marginTop: 16 }}>
                  <div className="swatch">
                    <div className="box" style={{ background: result.sample_color }} />
                    <div className="lbl">{t('an.sampleSwatch', { hex: result.sample_color })}</div>
                  </div>
                  <div className="swatch">
                    <div className="box" style={{ background: winner.color }} />
                    <div className="lbl">{winner.color}</div>
                  </div>
                  <div style={{ alignSelf: 'center', color: '#a3b0cc', fontSize: 13 }}>
                    <Html k="an.colorNote" />
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center' }}>
                <span className="rank">№ 1</span>
                <button className="btn" onClick={downloadCsv}>⬇ {t('an.csv')}</button>
                <button className="btn ghost sm" onClick={() => setTab('tabla')}>
                  {t('an.viewAll')}
                </button>
              </div>

              {/* podio:2º-4º candidato de un vistazo */}
              {result.results.length > 1 && (
                <div className="winner-top3" style={{ gridColumn: '1 / -1' }}>
                  <div className="kicker" style={{ marginBottom: 6 }}>
                    {t('an.other')}
                  </div>
                  {result.results.slice(1, 4).map((r, i) => (
                    <div className="mini-row" key={r.name}>
                      <span className="pos">#{i + 2}</span>
                      <span className="colorchip" style={{ background: r.color, margin: 0 }} />
                      <span className="mini-name">{r.name}</span>
                      <div className="scorebar"><i style={{ width: `${r.score * 100}%` }} /></div>
                      <span className="mono">ΔE {r.delta_e.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="tabs">
              {[['extraccion', '◨', 'tab.extraction'], ['curves', '📈', 'tab.curves'],
                ['bands', '🪞', 'tab.bands'], ['tabla', '🧮', 'tab.table']].map(([id, ico, key]) => (
                <button key={id} className={`tab ${tab === id ? 'active' : ''}`}
                  onClick={() => setTab(id)}>{ico} {t(key)}</button>
              ))}
            </div>

            {tab === 'extraccion' && (
              <div className="row c2">
                <div>
                  <label className="field">
                    {t('an.band', { a: result.rows[0], b: result.rows[1], axis: result.axis })}
                  </label>
                  <div className="bandwrap">
                    <img src={api.frameUrl('sample')} alt="sample" />
                    <div className="band-band" style={bandStyle} />
                  </div>
                </div>
                <div>
                  <label className="field">
                    {t('an.profile', {
                      unit: t(result.domain === 'nm' ? 'an.unit.nm' : 'an.unit.px'),
                    })}
                  </label>
                  <Chart x={result.profile.pixel} height={400}
                    series={[{ name: t('an.yau'), color: '#4cc9f0', data: result.profile.intensity, width: 2 }]}
                    xLabel={t(result.domain === 'nm' ? 'an.xnm' : 'an.xpx')}
                    yLabel={t('an.yau')} formatX={(v) => `${Math.round(v)}`}
                    formatY={(v) => v.toFixed(0)} />
                </div>
              </div>
            )}

            {tab === 'curves' && (
              <>
                <Chart x={result.curve.nm} series={curveSeries} height={460}
                  xLabel={t('an.xnm')} yLabel={t('an.yrel')}
                  formatX={(v) => `${Math.round(v)}`}
                  formatY={(v) => v.toFixed(2)} />
                <p className="sub" style={{ marginTop: 8 }}>
                  <Html k="an.curveCaption" />
                </p>
              </>
            )}

            {tab === 'bands' && (
              <>
                <Chart x={result.curve.nm} series={contSeries} height={460}
                  xLabel={t('an.xnm')} yLabel={t('an.ycont')}
                  formatX={(v) => `${Math.round(v)}`}
                  formatY={(v) => v.toFixed(2)} />
                <p className="sub" style={{ marginTop: 8 }}>
                  <Html k="an.bandCaption" />
                </p>
              </>
            )}

            {tab === 'tabla' && (
              <div style={{ overflowX: 'auto' }}>
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t('th.material')}</th><th>{t('th.category')}</th>
                      <th>{t('th.score')}</th><th>{t('th.color')}</th>
                      <th>{t('th.deltaE')}</th><th>{t('th.sam')}</th>
                      <th>{t('th.pearson')}</th><th>{t('th.continuum')}</th>
                      <th>{t('th.deltaBand')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.results.map((r, i) => (
                      <tr key={i}>
                        <td>
                          <span className="colorchip" style={{ background: r.color }} />
                          {r.name}
                        </td>
                        <td style={{ color: '#a3b0cc' }}>{r.category || '—'}</td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                            <div className="scorebar" style={{ width: 74 }}>
                              <i style={{ width: `${r.score * 100}%` }} />
                            </div>
                            <span className="mono">{r.score.toFixed(3)}</span>
                          </div>
                        </td>
                        <td className="mono">{r.color}</td>
                        <td className="mono">{r.delta_e.toFixed(1)}</td>
                        <td className="mono">{r.sam_deg.toFixed(1)}</td>
                        <td className="mono">{r.r_raw.toFixed(3)}</td>
                        <td className="mono">{r.r_continuum.toFixed(3)}</td>
                        <td className="mono">{r.delta_banda.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
