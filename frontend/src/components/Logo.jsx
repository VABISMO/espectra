/* Logo ESPECTRA — prisma que descompone un haz en el espectro visible */
import { useLang } from '../lang.jsx'
export function PrismMark({ size = 44 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none"
      aria-label="logo ESPECTRA" role="img">
      <defs>
        <linearGradient id="prismFill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#4cc9f0" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.14" />
        </linearGradient>
        <filter id="prismGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="1.6" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      {/* haz de entrada */}
      <path d="M1.5 22 H15.4" stroke="#f8fafc" strokeWidth="2.6" strokeLinecap="round" />
      {/* prisma */}
      <path d="M24 6.5 L39.4 34.5 H8.6 Z" stroke="#dbe7ff" strokeWidth="2"
        strokeLinejoin="round" fill="url(#prismFill)" />
      {/* haz interno */}
      <path d="M15.6 22 H32.2" stroke="#f8fafc" strokeWidth="2" strokeLinecap="round"
        opacity="0.95" />
      {/* espectro dispersado */}
      <g strokeWidth="2.4" strokeLinecap="round" filter="url(#prismGlow)">
        <path d="M32.4 22 L46.5 19.6" stroke="#ff4d4d" />
        <path d="M32.4 22 L46.5 22.6" stroke="#ffb020" />
        <path d="M32.4 22 L46.5 25.6" stroke="#3ddc97" />
        <path d="M32.4 22 L46.5 28.6" stroke="#38bdf8" />
        <path d="M32.4 22 L46.5 31.6" stroke="#8b5cf6" />
      </g>
    </svg>
  )
}

export function Logo() {
  const { t } = useLang()
  return (
    <div className="logo">
      <PrismMark size={46} />
      <div>
        <div className="logo-name">ESPECTRA</div>
        <div className="logo-sub">{t('logo.sub')}</div>
      </div>
    </div>
  )
}
