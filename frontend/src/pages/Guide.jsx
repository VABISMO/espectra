/* Página Guía: cómo capturar e interpretar (EN/ES) */
import { Html, useLang } from '../lang.jsx'

export default function Guide() {
  const { t } = useLang()

  const DO_AVOID = [
    ['gd.r1', 'gd.x1'], ['gd.r2', 'gd.x2'], ['gd.r3', 'gd.x3'],
    ['gd.r4', 'gd.x4'], ['gd.r5', 'gd.x5'],
  ]
  const SIGNALS = [['gd.sig1', 'gd.sig1d'], ['gd.sig2', 'gd.sig2d'],
    ['gd.sig3', 'gd.sig3d']]
  const SCORES = [
    ['score ≥ 0.70 · margen ≥ 0.06', 'gd.sc1'],
    ['score ≥ 0.70 · margen < 0.06', 'gd.sc2'],
    ['score < 0.70', 'gd.sc3'],
  ]

  return (
    <>
      <div className="card">
        <div className="kicker">{t('gd.flow')}</div>
        <h2>{t('gd.three')}</h2>
        <ol className="steps" style={{ marginTop: 16 }}>
          <li><div><Html k="gd.s1" /></div></li>
          <li><div><Html k="gd.s2" /></div></li>
          <li><div><Html k="gd.s3" /></div></li>
        </ol>
      </div>

      <div className="card">
        <div className="kicker">{t('gd.capture')}</div>
        <h2>{t('gd.goodPhotos')}</h2>
        <table className="gtable" style={{ marginTop: 12 }}>
          <thead>
            <tr><th>{t('gd.hacer')}</th><th>{t('gd.evitar')}</th></tr>
          </thead>
          <tbody>
            {DO_AVOID.map(([good, bad]) => (
              <tr key={good}>
                <td className="good">{t(good)}</td>
                <td className="bad">{t(bad)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="kicker">{t('gd.interpret')}</div>
        <h2>{t('gd.whatResult')}</h2>
        <p className="sub"><Html k="gd.division" /></p>
        <table className="gtable" style={{ marginTop: 8 }}>
          <thead>
            <tr><th>{t('gd.sig')}</th><th>{t('gd.aport')}</th></tr>
          </thead>
          <tbody>
            {SIGNALS.map(([k, d]) => (
              <tr key={k}><td>{t(k)}</td><td>{t(d)}</td></tr>
            ))}
          </tbody>
        </table>
        <table className="gtable" style={{ marginTop: 16 }}>
          <thead>
            <tr><th>{t('gd.score')}</th><th>{t('gd.reading')}</th></tr>
          </thead>
          <tbody>
            {SCORES.map(([sc, k]) => (
              <tr key={k}>
                <td className="mono" style={{ fontFamily: 'JetBrains Mono, monospace' }}>
                  {sc}</td>
                <td>{t(k)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="kicker">{t('gd.limits')}</div>
        <h2>{t('gd.honest')}</h2>
        <div className="row c2" style={{ marginTop: 10 }}>
          <div className="alert info" style={{ margin: 0 }}><Html k="gd.l1" /></div>
          <div className="alert warn" style={{ margin: 0 }}><Html k="gd.l2" /></div>
          <div className="alert info" style={{ margin: 0 }}><Html k="gd.l3" /></div>
          <div className="alert ok" style={{ margin: 0 }}><Html k="gd.l4" /></div>
        </div>
      </div>
    </>
  )
}
