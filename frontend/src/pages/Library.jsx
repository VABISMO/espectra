/* Libraries page: autocomplete search + material card,
   stats, downloads and CSV upload */
import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import Chart from '../components/Chart.jsx'
import { Html, useLang } from '../lang.jsx'

const COLLECTIONS = [
  { id: 'usgs', label: 'col.usgs', desc: 'col.usgs.d' },
  { id: 'pigments', label: 'col.pigments', desc: 'col.pigments.d' },
  { id: 'revigo', label: 'col.revigo', desc: 'col.revigo.d' },
]

export default function Library({ status, refresh }) {
  const { t, cat } = useLang()
  const [lib, setLib] = useState(null)
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const fetchStatus = status?.fetch

  const load = () => api.library().then(setLib).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!fetchStatus?.running) return
    const id = setInterval(refresh, 2500)
    return () => clearInterval(id)
  }, [fetchStatus?.running, refresh])

  useEffect(() => {
    if (fetchStatus?.done && !fetchStatus.running) load()
  }, [fetchStatus?.done, fetchStatus?.running])

  // ---------------- buscador con autocompletado
  const [q, setQ] = useState('')
  const [hits, setHits] = useState(null)
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)
  const [searching, setSearching] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [detailErr, setDetailErr] = useState(null)
  const boxRef = useRef(null)

  useEffect(() => {
    if (q.trim().length < 2) { setHits(null); setOpen(false); return }
    setSearching(true)
    const id = setTimeout(() => {
      api.search(q)
        .then((r) => { setHits(r); setOpen(true); setHi(-1) })
        .catch((e) => setError(e.message))
        .finally(() => setSearching(false))
    }, 180)
    return () => clearTimeout(id)
  }, [q])

  useEffect(() => {
    const h = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const choose = (name) => {
    setQ(name); setOpen(false); setDetail(null); setDetailErr(null)
    setLoading(true)
    api.spectrum(name)
      .then(setDetail)
      .catch((e) => setDetailErr(e.message))
      .finally(() => setLoading(false))
  }

  const onKey = (e) => {
    const list = hits?.results || []
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, list.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
    else if (e.key === 'Enter') {
      const pick = list[hi] || list[0]     // Enter selecciona el primero si no hay resalte
      if (pick) { e.preventDefault(); choose(pick.name) }
    } else if (e.key === 'Escape') setOpen(false)
  }

  // ---------------- descargas / subida
  const startFetch = async (id) => {
    setError(null); setMsg(t('lib.downloading', { id }))
    try { await api.fetchCollection(id); refresh() }
    catch (e) { setError(e.message); setMsg(null) }
  }

  const onUpload = async (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    try {
      await api.upload(f)
      setMsg(t('lib.saved', { name: f.name }))
      load(); refresh()
    } catch (err) { setError(err.message) }
  }

  const cats = lib?.categories || status?.library?.categories || {}
  const total = lib?.total || status?.library?.total || 0
  const maxCat = Math.max(1, ...Object.values(cats))

  return (
    <>
      {/* ---------- buscador */}
      <div className="card">
        <div className="kicker">{t('lib.search')}</div>
        <h2>{t('lib.selectHint')}</h2>
        <div className="ac searchbar" ref={boxRef} style={{ maxWidth: 720, marginTop: 14 }}>
          <span className="sico">🔍</span>
          <input
            value={q}
            placeholder={t('lib.searchPh')}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            onFocus={() => hits && setOpen(true)}
            autoComplete="off"
            spellCheck={false}
            aria-label={t('lib.search')}
          />
          {open && hits && hits.results.length > 0 && (
            <div className="ac-list">
              {hits.results.map((r, i) => (
                <div key={r.name} className={`ac-item ${i === hi ? 'hi' : ''}`}
                  onMouseEnter={() => setHi(i)}
                  onMouseDown={(e) => { e.preventDefault(); choose(r.name) }}>
                  <span>{r.name}</span>
                  <span className="cat">{cat(r.category) || '—'}</span>
                </div>
              ))}
            </div>
          )}
          {open && hits && hits.results.length === 0 && (
            <div className="ac-list">
              <div className="ac-item" style={{ color: '#7c8aa8', cursor: 'default' }}>
                {t('lib.noResults')}
              </div>
            </div>
          )}
        </div>
        {hits && (
          <div className="ac-count">
            {t('lib.results', { n: hits.total })}{searching ? ` · ${t('lib.searching')}` : ''}
          </div>
        )}
        {!hits && (
          <div className="tryrow">
            <span>{t('lib.try')}</span>
            {['hematite', 'azurite', 'malachite', 'goethite', 'quartz'].map((s) => (
              <span key={s} className="chip" role="button"
                onClick={() => { setQ(s); setOpen(true) }}>{s}</span>
            ))}
          </div>
        )}

        {detailErr && <div className="alert error" style={{ marginTop: 14 }}>{detailErr}</div>}
        {loading && <div className="alert info" style={{ marginTop: 14 }}>
          <span className="spin" /> {t('lib.searching')}</div>}

        {detail && !loading && (
          <div style={{ marginTop: 18 }}>
            <div className="section-head" style={{ marginBottom: 12 }}>
              <div>
                <div className="kicker">{t('lib.spectrum')}</div>
                <h2 style={{ fontSize: 23 }}>{detail.name}</h2>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                  <span className="chip">{cat(detail.category) || t('an.noCategory')}</span>
                  <span className="chip info">{detail.source}</span>
                  <span className="chip violet">
                    {t('lib.detail.points', { n: detail.stats.points })}
                  </span>
                  <span className="chip">
                    {t('lib.detail.range')}: {detail.stats.range_nm[0]}–
                    {detail.stats.range_nm[1]} nm
                  </span>
                </div>
              </div>
              <div className="swatch">
                <div className="box" style={{ background: detail.color }} />
                <div className="lbl">{detail.color}</div>
              </div>
            </div>

            <div className="statgrid" style={{ marginBottom: 16 }}>
              <div className="statbox"><span>{t('lib.detail.min')}</span>
                <b>{detail.stats.min.toFixed(3)}</b></div>
              <div className="statbox"><span>{t('lib.detail.max')}</span>
                <b>{detail.stats.max.toFixed(3)}</b></div>
              <div className="statbox"><span>{t('lib.detail.mean')}</span>
                <b>{detail.stats.mean.toFixed(3)}</b></div>
              <div className="statbox"><span>{t('lib.detail.color')}</span>
                <b style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <i style={{
                    width: 26, height: 16, borderRadius: 5, display: 'inline-block',
                    background: detail.color, border: '1px solid rgba(255,255,255,.2)',
                  }} />
                  {detail.color}
                </b></div>
            </div>

            <Chart
              x={detail.nm}
              height={430}
              areaFirst
              series={[{
                name: detail.name.split(' [')[0],
                color: '#4cc9f0',
                data: detail.reflectance,
                width: 2.5,
              }]}
              xLabel={t('an.xnm')}
              yLabel={t('lib.y')}
              formatX={(v) => `${Math.round(v)}`}
              formatY={(v) => v.toFixed(2)}
            />
          </div>
        )}
      </div>

      {/* ---------- stats */}
      <div className="row c3" style={{ marginBottom: 18 }}>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="stat"><b>{total}</b><span>{t('lib.spectra')}</span></div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="stat"><b>{Object.keys(cats).length}</b>
            <span>{t('lib.categories')}</span></div>
        </div>
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="stat"><b>{Object.keys(lib?.sources || status?.library?.sources || {}).length}</b>
            <span>{t('lib.collections')}</span></div>
        </div>
      </div>

      {/* ---------- composition */}
      <div className="card">
        <div className="kicker">{t('lib.composition')}</div>
        <h2>{t('lib.byCategory')}</h2>
        <div className="barlist" style={{ marginTop: 16 }}>
          {Object.entries(cats).map(([name, n]) => (
            <div className="barline" key={name}>
              <span className="name">{name}</span>
              <div className="track"><i style={{ width: `${(n / maxCat) * 100}%` }} /></div>
              <span className="n">{n}</span>
            </div>
          ))}
          {!Object.keys(cats).length && <span className="sub">{t('lib.loading')}</span>}
        </div>
      </div>

      {/* ---------- descargas */}
      <div className="card">
        <div className="kicker">{t('lib.expand')}</div>
        <h2>{t('lib.downloadCollections')}</h2>
        <div className="row c3" style={{ marginTop: 14 }}>
          {COLLECTIONS.map((c) => (
            <div key={c.id} className="card pad-sm"
              style={{ marginBottom: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <b style={{ fontFamily: 'Space Grotesk, sans-serif', fontSize: 16.5 }}>
                {t(c.label)}</b>
              <span style={{ fontSize: 13, color: '#a3b0cc', flex: 1 }}>{t(c.desc)}</span>
              <button className="btn sm" disabled={fetchStatus?.running}
                onClick={() => startFetch(c.id)}>
                {fetchStatus?.running ? t('lib.inProgress') : `⬇ ${t('lib.dl')}`}
              </button>
            </div>
          ))}
        </div>

        {fetchStatus?.running && (
          <div className="alert info" style={{ marginTop: 14 }}>
            <span className="spin" /> {t('lib.running')}
          </div>
        )}
        {fetchStatus?.log && !fetchStatus.running && (
          <div className="log" style={{ marginTop: 14 }}>{fetchStatus.log}</div>
        )}
        {error && <div className="alert error" style={{ marginTop: 14 }}>{error}</div>}
        {msg && !error && <div className="alert ok" style={{ marginTop: 14 }}>{msg}</div>}
      </div>

      {/* ---------- subida */}
      <div className="card">
        <div className="kicker">{t('lib.own')}</div>
        <h2>{t('lib.uploadH')}</h2>
        <p className="sub"><Html k="lib.uploadSub" /></p>
        <label className="btn" style={{ display: 'inline-flex' }}>
          📁 {t('lib.choose')}
          <input type="file" accept=".csv,.txt,.asc,.tsv" hidden onChange={onUpload} />
        </label>
      </div>
    </>
  )
}
