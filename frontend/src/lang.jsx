/* Contexto de idioma: EN (default) / ES — persistido en localStorage */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { categoryLabel, translate } from './i18n.js'
import { setApiLang } from './api.js'

const Ctx = createContext(null)

export function LangProvider({ children }) {
  const [lang, setLang] = useState(() => {
    try { return localStorage.getItem('espectra_lang') || 'en' } catch { return 'en' }
  })

  useEffect(() => {
    try { localStorage.setItem('espectra_lang', lang) } catch { /* sin storage */ }
    document.documentElement.lang = lang
    setApiLang(lang)
  }, [lang])

  const t = useCallback((k, v) => translate(lang, k, v), [lang])
  const cat = useCallback((c) => categoryLabel(lang, c), [lang])
  return <Ctx.Provider value={{ lang, setLang, t, cat }}>{children}</Ctx.Provider>
}

export function useLang() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useLang fuera de LangProvider')
  return ctx
}

/** Texto con etiquetas HTML simples (<b>, <i>…) */
export function Html({ k, v, as: Tag = 'span' }) {
  const { t } = useLang()
  return <Tag dangerouslySetInnerHTML={{ __html: t(k, v) }} />
}
