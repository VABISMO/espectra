/* Gráfico de líneas SVG con hover, área degradada y leyenda.
   series: [{ name, color, data:[y], width?, dashed? }]
   x: [números]  (mismo tamaño que cada data) */
import { useEffect, useMemo, useRef, useState } from 'react'

const PALETTE = ['#38bdf8', '#a78bfa', '#fb7185', '#fbbf24',
  '#34d399', '#f472b6', '#22d3ee', '#facc15']

export default function Chart({
  x = [], series = [], xLabel = '', yLabel = '',
  height = 300, yDomain = null, areaFirst = true, formatX = (v) => `${v}`,
  formatY = (v) => `${v}`,
}) {
  const wrapRef = useRef(null)
  const [hover, setHover] = useState(null)
  const [w, setW] = useState(760)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const measure = () => setW(Math.max(360, el.clientWidth))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const P = { l: 54, r: 16, t: 14, b: 40 }
  const iw = w - P.l - P.r
  const ih = height - P.t - P.b

  const { yMin, yMax, ticksY, ticksX } = useMemo(() => {
    let lo = Infinity, hi = -Infinity
    for (const s of series) for (const v of s.data) {
      if (v < lo) lo = v
      if (v > hi) hi = v
    }
    if (!isFinite(lo)) { lo = 0; hi = 1 }
    if (yDomain) [lo, hi] = yDomain
    const pad = (hi - lo) * 0.08 || 0.1
    lo -= pad; hi += pad
    const n = 5
    const step = (hi - lo) / n
    const t = Array.from({ length: n + 1 }, (_, i) => lo + i * step)
    const nx = 6
    const xt = Array.from({ length: nx }, (_, i) =>
      x.length ? x[Math.round((i * (x.length - 1)) / (nx - 1))] : 0)
    return { yMin: lo, yMax: hi, ticksY: t, ticksX: xt }
  }, [series, x, yDomain])

  const sx = (i) => P.l + (x.length <= 1 ? 0 : (i / (x.length - 1)) * iw)
  const sy = (v) => P.t + ih - ((v - yMin) / (yMax - yMin || 1)) * ih

  const path = (data) =>
    data.map((v, i) => `${i ? 'L' : 'M'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join('')

  const onMove = (e) => {
    const rect = wrapRef.current.getBoundingClientRect()
    const px = e.clientX - rect.left
    const frac = (px - P.l) / iw
    const idx = Math.round(frac * (x.length - 1))
    if (idx >= 0 && idx < x.length) setHover(idx)
  }

  const legend = series.filter((s) => s.name)

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <svg width={w} height={height} onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4cc9f0" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#4cc9f0" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {/* rejilla */}
        {ticksY.map((t, i) => (
          <g key={i}>
            <line x1={P.l} x2={w - P.r} y1={sy(t)} y2={sy(t)}
              stroke="rgba(148,163,184,.13)" strokeDasharray="3 5" />
            <text x={P.l - 9} y={sy(t) + 4} textAnchor="end"
              fill="#7c8aa8" fontSize="11.5" fontFamily="JetBrains Mono, monospace">
              {formatY(t)}
            </text>
          </g>
        ))}
        {ticksX.map((t, i) => (
          <text key={i} x={sx(Math.round((i * (x.length - 1)) / (ticksX.length - 1)))}
            y={height - P.b + 20} textAnchor="middle" fill="#7c8aa8"
            fontSize="11.5" fontFamily="JetBrains Mono, monospace">
            {formatX(t)}
          </text>
        ))}
        {/* ejes */}
        <line x1={P.l} x2={P.l} y1={P.t} y2={P.t + ih}
          stroke="rgba(148,163,184,.4)" />
        <line x1={P.l} x2={w - P.r} y1={P.t + ih} y2={P.t + ih}
          stroke="rgba(148,163,184,.4)" />
        {xLabel && (
          <text x={P.l + iw / 2} y={height - 4} textAnchor="middle"
            fill="#a3b0cc" fontSize="12" fontWeight="600">{xLabel}</text>
        )}
        {yLabel && (
          <text x={14} y={P.t + ih / 2} textAnchor="middle" fill="#a3b0cc"
            fontSize="12" fontWeight="600"
            transform={`rotate(-90 14 ${P.t + ih / 2})`}>{yLabel}</text>
        )}
        {/* área de la muestra */}
        {areaFirst && series[0] && (
          <path d={`${path(series[0].data)} L${sx(x.length - 1)},${P.t + ih} L${P.l},${P.t + ih} Z`}
            fill="url(#areaGrad)" />
        )}
        {/* curvas */}
        {series.map((s, i) => (
          <path key={i} d={path(s.data)} fill="none" stroke={s.color}
            strokeWidth={s.width || (i === 0 ? 2.6 : 1.7)}
            strokeDasharray={s.dashed ? '5 4' : undefined}
            strokeLinejoin="round" strokeLinecap="round"
            opacity={i === 0 ? 1 : 0.92} />
        ))}
        {/* cursor */}
        {hover != null && (
          <g>
            <line x1={sx(hover)} x2={sx(hover)} y1={P.t} y2={P.t + ih}
              stroke="rgba(233,238,251,.45)" strokeDasharray="4 4" />
            {series.map((s, i) => (
              <circle key={i} cx={sx(hover)} cy={sy(s.data[hover])} r="4"
                fill="#0b1020" stroke={s.color} strokeWidth="2.4" />
            ))}
          </g>
        )}
      </svg>
      {/* tooltip */}
      {hover != null && (
        <div style={{
          position: 'absolute', left: Math.min(Math.max(sx(hover) + 14, 8), w - 210),
          top: 8, background: 'rgba(10,15,30,.94)', border: '1px solid rgba(148,163,184,.3)',
          borderRadius: 10, padding: '9px 12px', pointerEvents: 'none',
          fontSize: 12.4, minWidth: 175, boxShadow: '0 14px 34px -14px #000',
        }}>
          <div style={{
            fontFamily: 'JetBrains Mono, monospace', color: '#e9eefb',
            fontWeight: 600, marginBottom: 5,
          }}>{formatX(x[hover])}{xLabel ? ` ${xLabel}` : ''}</div>
          {series.map((s, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', gap: 14,
              color: '#a3b0cc', lineHeight: 1.6,
            }}>
              <span><span style={{
                display: 'inline-block', width: 9, height: 9, borderRadius: 3,
                background: s.color, marginRight: 6,
              }} />{s.name || (i === 0 ? 'muestra' : '')}</span>
              <b style={{ fontFamily: 'JetBrains Mono, monospace', color: '#e9eefb' }}>
                {formatY(s.data[hover])}
              </b>
            </div>
          ))}
        </div>
      )}
      {/* leyenda */}
      {legend.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', marginTop: 10 }}>
          {legend.map((s, i) => (
            <span key={i} style={{
              display: 'inline-flex', alignItems: 'center', gap: 7,
              fontSize: 12.4, color: '#a3b0cc',
            }}>
              <span style={{ width: 14, height: 3.5, borderRadius: 3, background: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export { PALETTE }
