/* Anillo de puntuación circular (SVG) */
export default function ScoreRing({ score = 0, size = 132 }) {
  const r = (size - 14) / 2
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(1, score))
  return (
    <div className="ringwrap" style={{ width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
        <defs>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#4cc9f0" />
            <stop offset="55%" stopColor="#8b5cf6" />
            <stop offset="100%" stopColor="#f472b6" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(148,163,184,.18)"
          strokeWidth="9" fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke="url(#ringGrad)"
          strokeWidth="9" fill="none" strokeLinecap="round"
          strokeDasharray={`${pct * c} ${c}`}
          style={{ transition: 'stroke-dasharray .8s cubic-bezier(.2,.8,.2,1)' }} />
      </svg>
      <div className="val">
        <b>{(score * 100).toFixed(0)}</b>
        <span>SCORE</span>
      </div>
    </div>
  )
}
