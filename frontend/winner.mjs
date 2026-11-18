/* Captura el resultado del análisis (tarjeta del material identificado) */
import { chromium } from 'playwright'

const errors = []
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle' })
await page.waitForTimeout(1000)
await page.click('button:has-text("Analyze and compare")')
await page.waitForSelector('.winner', { timeout: 30000 })
await page.waitForTimeout(1500)
await page.screenshot({ path: `/tmp/shots/winner_${Date.now()}.png`, fullPage: true })

console.log('console errors:', errors.length ? errors : 'ninguno')
await browser.close()
