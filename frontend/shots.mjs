/* Captures the UI screenshots for visual review.
   W/H = viewport; adds search detail and switches to ES. */
import { chromium } from 'playwright'

const pages = ['Analyze', 'Calibration', 'Libraries', 'Guide']
const errors = []
const W = Number(process.env.W || 1440)
const H = Number(process.env.H || 960)
const TS = Date.now()

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: W, height: H } })
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)

for (let i = 0; i < pages.length; i++) {
  if (i > 0) {
    await page.click(`.nav-btn:has-text("${pages[i]}")`)
    await page.waitForTimeout(900)
  }
  await page.screenshot({ path: `/tmp/shots/${TS}_${i}_${pages[i]}.png`, fullPage: true })
}

// --- buscador con autocompletado en Bibliotecas
await page.click('.nav-btn:has-text("Libraries")')
await page.waitForTimeout(500)
const search = page.locator('.ac input')
await search.click()
await search.type('azur', { delay: 60 })
await page.waitForTimeout(700)
await page.screenshot({ path: `/tmp/shots/${TS}_4_autocomplete.png` })
await page.keyboard.press('Enter')
await page.waitForSelector('.statgrid', { timeout: 8000 })
await page.waitForTimeout(700)
await page.screenshot({ path: `/tmp/shots/${TS}_5_ficha.png`, fullPage: true })

// --- idioma ES
await page.getByRole('button', { name: 'ES', exact: true }).click()
await page.waitForTimeout(500)
await page.locator('.nav-btn').first().click()   // 1ª entrada: Analizar (EN/ES)
await page.waitForTimeout(600)
await page.screenshot({ path: `/tmp/shots/${TS}_6_espanol.png`, fullPage: true })

console.log('console errors:', errors.length ? errors : 'ninguno')
await browser.close()
