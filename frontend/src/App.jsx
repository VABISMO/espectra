import { useCallback, useEffect, useState } from 'react'
import { Logo } from './components/Logo.jsx'
import { api } from './api.js'
import { LangProvider, useLang } from './lang.jsx'
import Analyze from './pages/Analyze.jsx'
import Calibrate from './pages/Calibrate.jsx'
import Library from './pages/Library.jsx'
import Guide from './pages/Guide.jsx'

const PAGES = [
  { id: 'analyze', ico: '◎', nav: 'nav.analyze', kicker: 'page.analyze.kicker', title: 'page.analyze.title' },
  { id: 'calibrate', ico: '◮', nav: 'nav.calibrate', kicker: 'page.calibrate.kicker', title: 'page.calibrate.title' },
  { id: 'library', ico: '▤', nav: 'nav.library', kicker: 'page.library.kicker', title: 'page.library.title' },
  { id: 'guide', ico: '✦', nav: 'nav.guide', kicker: 'page.guide.kicker', title: 'page.guide.title' },
]

function Shell() {
  const { lang, setLang, t } = useLang()
  const [page, setPage] = useState('analyze')
  const [status, setStatus] = useState(null)
  const [err, setErr] = useState(null)

  const refresh = useCallback(() => {
    api.status()
      .then((s) => { setStatus(s); setErr(null) })
      .catch((e) => setErr(e.message))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const calMode = status?.calibration?.mode
  const calLabel = calMode ? t(`mode.${calMode}`) : null

  const Body = { analyze: Analyze, calibrate: Calibrate, library: Library, guide: Guide }[page]
  const meta = PAGES.find((p) => p.id === page)

  return (
    <div className="app">
      <aside className="sidebar">
        <Logo />
        <nav className="nav">
          {PAGES.map((p) => (
            <button key={p.id} className={`nav-btn ${page === p.id ? 'active' : ''}`}
              onClick={() => setPage(p.id)}>
              <span className="ico">{p.ico}</span> {t(p.nav)}
            </button>
          ))}
        </nav>

        <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 9, paddingTop: 18 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 2, color: '#7c8aa8' }}>
            {t('status.title')}
          </div>
          {err && <div className="alert error" style={{ margin: 0 }}>
            {t('status.apiError', { msg: err })}</div>}
          {status?.calibration
            ? <span className="chip ok">✔ {t('status.calibrated', { mode: calLabel })}</span>
            : <span className="chip warn">⚠ {t('status.noCal')}</span>}
          {status && (
            <span className="chip info">▤ {t('status.materials', { n: status.library.total })}</span>
          )}
          {status && status.cameras.length > 0 && (
            <span className="chip violet">◉ {t('status.cameras', { n: status.cameras.length })}</span>
          )}
          <button className="btn sm ghost" onClick={refresh}>↻ {t('status.refresh')}</button>

          {/* selector de idioma discreto */}
          <div style={{
            display: 'flex', gap: 6, alignItems: 'center', marginTop: 6,
            fontSize: 11, color: '#7c8aa8',
          }} title={t('lang.label')}>
            <span style={{ letterSpacing: 1 }}>EN·ES</span>
            {['en', 'es'].map((l) => (
              <button key={l} onClick={() => setLang(l)}
                style={{
                  padding: '2px 9px', borderRadius: 7, cursor: 'pointer',
                  font: '700 11.5px "JetBrains Mono", monospace',
                  border: lang === l ? '1px solid #4cc9f0' : '1px solid rgba(148,163,184,.3)',
                  background: lang === l ? 'rgba(76,201,240,.16)' : 'transparent',
                  color: lang === l ? '#e9eefb' : '#7c8aa8',
                }}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="section-head">
          <div>
            <div className="kicker">{t(meta.kicker)}</div>
            <h1>{t(meta.title)}</h1>
          </div>
        </div>
        <Body status={status} refresh={refresh} go={setPage} />
      </main>
    </div>
  )
}

export default function App() {
  return (
    <LangProvider>
      <Shell />
    </LangProvider>
  )
}
