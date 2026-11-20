# ESPECTRA · Espectrómetro USB

[![CI](https://github.com/VABISMO/espectra/actions/workflows/ci.yml/badge.svg)](https://github.com/VABISMO/espectra/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/Licencia-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)
[![React 18 + Vite](https://img.shields.io/badge/React-18-61dafb.svg)](frontend/)
[![Espectros](https://img.shields.io/badge/espectros%20de%20referencia-1024-8b5cf6.svg)](data/libraries/)

**Convierte una cámara USB + tubo dispersor en un espectrómetro web que
identifica materiales.** Vista en vivo → calibración (píxel → nm) →
pseudo-reflectancia → comparación contra **1.024 espectros de referencia**
con ángulo espectral, bandas de absorción y **color CIELAB (ΔE)**.

> 🇬🇧 English version: [README.md](README.md)

<p align="center">
  <img src="docs/screenshot-analyze.png" alt="Pantalla Analizar" width="820">
</p>

## Características

- 📷 **Captura en vivo por USB** (V4L2/OpenCV) — prioriza siempre la cámara
  USB externa y nunca la integrada del portátil
- ⚡ **Calibración por líneas de mercurio/CFL** — ajusta las líneas
  estrechas de Hg (404,7 / 435,8 / 546,1 / 577-579 nm): al ser estrechas
  fijan la posición **y la curvatura** (**±1-2 nm**). Es lo primero que
  intenta el modo automático; el LED blanco (bomba azul ~450 nm + hump de
  fósforo ~560 nm) es el respaldo y solo da un ajuste **afín**
  (**±5-10 nm** — un espectro ancho y suave no constriñe la curvatura;
  autoconsistencia **< 5 nm** con datos sintéticos). También anclas
  manuales, rango asumido y cuerpo negro
- 🛑 **Clase "desconocido"** — cada resultado lleva un veredicto
  (`identified` / `uncertain` / `unknown`) construido con un umbral de
  score y un margen entre familias. Si nada los alcanza, la API lo dice en
  vez de disfrazar al mejor candidato de respuesta
- 🎚 **Referencias a la resolución del instrumento** — cada espectro de la
  biblioteca se convoluciona con la función de línea del aparato
  (`--fwhm`, 7 nm por defecto) antes de comparar, para que una curva USGS
  de alta resolución no gane por ser más nítida de lo que tu tubo puede
  ver
- 🫥 **Salida honesta** — los puntos donde la lámpara no llega (cola roja
  del LED por encima de ~680 nm, valle azul-verde) se **enmascaran y no se
  rellenan con cero**: todas las métricas se evalúan solo en los puntos
  válidos
- 📐 **Linealización gamma opcional** — `gamma=2.2` deshace la gamma de 8
  bits de la cámara ANTES de dividir muestra ÷ LED, para que las bandas de
  absorción no queden comprimidas (`--gamma`, `POST /api/analyze
  {"gamma": ...}`)
- 🎯 **Identificación de materiales** —
  `score = 0.35·cos(SAM) + 0.40·bandas + 0.25·color(ΔE)` sobre rejilla
  400–700 nm
- 🎨 **Ciencia del color incluida** — observador CIE 1931 + iluminante D65
  empaquetados; muestras de color de la muestra y del candidato lado a lado
- 🔍 **Biblioteca con buscador** — autocompletado sobre 1.024 materiales con
  ficha: gráfica del espectro, mín/máx/media, rango y color
- 🌍 **Inglés (default) y español** — selector discreto, persistido
- 🖥️ **Interfaz React** (tema oscuro, centrado) servida por backend FastAPI
- 🧪 **70 tests end-to-end** + un **benchmark leave-one-out** con tasas
  top-1 / top-3 reales + CI en GitHub Actions

<p align="center">
  <img src="docs/screenshot-library.png" alt="Buscador" width="420">
  &nbsp;
  <img src="docs/screenshot-calibration.png" alt="Calibración" width="420">
  &nbsp;
  <img src="docs/screenshot-spanish.png" alt="Interfaz en español" width="420">
</p>

## Requisitos

- **Linux con V4L2** para la captura en vivo (el análisis, la calibración y
  la CLI también funcionan en macOS/Windows — solo la captura en vivo es
  exclusiva de Linux)
- Python **3.10+** · Node.js **18+** (compilación del frontend)
- Cualquier **cámara USB UVC** + tubo de espectrómetro o red de difracción
- **Recomendado para precisión:** lámpara de mercurio o fluorescente
  compacta (CFL) — sus líneas estrechas a **435,8 y 546,1 nm** son anclas
  mucho mejores que un LED blanco (±1-2 nm frente a ±5-10 nm)

## Arranque rápido

```bash
git clone https://github.com/VABISMO/espectra.git
cd espectra
./install.sh            # venv + dependencias + compilación de la interfaz
.venv/bin/python server.py
# → http://localhost:8000
```

Manual (o `make install` / `make run`):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python server.py
```

Verifica todo el pipeline:

```bash
.venv/bin/python -m espectro demo     # análisis sintético → PASS
.venv/bin/python tests/run_tests.py   # 70 comprobaciones: motor + API + cámara
.venv/bin/python scripts/benchmark.py # leave-one-out: tasas top-1 / top-3
```

## Cómo funciona

```
cámara USB ──► vista en vivo MJPEG ──► captura MUESTRA / LED / oscuro
                                              │
  calibración                                ▼           biblioteca (1024)
  líneas Hg 404,7/435,8/546,1 ──► nm ──► muestra ÷ LED ──► refs convolu-
  (±1-2 nm, con curvatura)        pseudo-reflectancia    cionadas a 7 nm
  si no, LED 450+560 (±5-10 nm)         │                      │
  + refinado de plantilla (r ≈ 1.000)    └────────┬─────────────┘
                                                 ▼
                               SAM + bandas + ΔE, solo en puntos válidos
                               (sin lámpara → NaN, promedio por bin de 5 nm)
                                                 ▼
                              mejor candidato + veredicto + top-3 + CSV
```

1. **Extracción** — detecta el eje de dispersión, promedia la banda encendida,
   resta la foto oscura (canal `R+G+B`, respuesta plana).
2. **Calibración** — polinomio `nm = P(píxel)`. El modo por defecto intenta
   primero las líneas estrechas de mercurio/CFL (≥3 líneas ⇒ posición *y*
   curvatura, ±1-2 nm) y si no las hay usa el LED blanco, que ajusta offset
   y escala contra `LED(λ)·QE(λ)` — un mapeo **afín**, porque un espectro
   ancho y suave no constriñe la curvatura (±5-10 nm).
3. **Pseudo-reflectancia** — `muestra ÷ LED` cancela lámpara, óptica y
   sensor. Conserva la forma espectral **y** el nivel absoluto, así que el
   ΔE sigue siendo significativo; no es reflectancia calibrada. Donde la
   lámpara no llega el valor queda en **NaN y se excluye de todas las
   métricas** (nunca se rellena con cero), y la curva se promedia dentro
   de cada bin de 5 nm de la rejilla en lugar de muestrear un solo punto,
   así el ruido de la cámara no pasa entero. Si la cámara aplica gamma de
   8 bits, pásala primero (`gamma`); si no, las bandas quedan comprimidas.
4. **Comparación** — ángulo espectral (SAM), bandas sin continuo y color
   CIELAB ΔE bajo D65, ponderados `0.35 / 0.40 / 0.25`, contra referencias
   previamente convolucionadas al FWHM del instrumento. El ranking termina
   en un **veredicto**: por debajo de `min_score` ⇒ *unknown*; por debajo
   de `min_margin` entre familias ⇒ *uncertain*; si no, *identified*.

## Selección de cámara

Los dispositivos se identifican por sysfs (`/sys/class/video4linux/*/name`):

| Dispositivo | Nombre | Por defecto |
|---|---|---|
| `/dev/video4` | `USB2.0 UVC PC Camera` | ✅ seleccionada (★ USB) |
| `/dev/video0/2` | `Integrated Camera` | ❌ marcada *(portátil)* |

La sonda usa TTL de 20 s, deduplica nodos de metadatos y lista las ocupadas
como `openable: false` — la cámara del espectrómetro nunca se pierde.

## Bibliotecas de referencia (1.024 espectros)

| Colección | Nº | Origen / licencia |
|---|---|---|
| USGS splib05a | 740 | Clark et al. 2003, OFR 03-395 — dominio público |
| Pigmentos Zenodo | 116 | Carrino et al. 2025, DOI [10.5281/zenodo.14906872](https://doi.org/10.5281/zenodo.14906872) |
| Pinturas Revigo | 168 | [fligt/revigo-spectra](https://github.com/fligt/revigo-spectra) — CC0 |

```bash
.venv/bin/python scripts/fetch_usgs.py       # actualizar USGS
.venv/bin/python scripts/fetch_pigments.py   # actualizar pigmentos
.venv/bin/python scripts/fetch_revigo.py     # actualizar pinturas
.venv/bin/python scripts/fetch_color_data.py # CIE 1931 + D65 (incluidos)
```

Añade los tuyos: cualquier CSV `wavelength_nm,reflectance` en
`data/libraries/` (µm y % se auto-detectan) o súbelo desde la pantalla
**Bibliotecas**. Los `.spectrum.txt` de ECOSTRESS se leen directamente.

## Línea de comandos

```bash
python -m espectro calibrate led.png                 # auto: líneas Hg → LED
python -m espectro calibrate hg.png --lines          # forzar líneas Hg/CFL
python -m espectro calibrate led.png --led           # forzar LED blanco
python -m espectro calibrate led.png --anchor 213 450 --anchor 682 560
python -m espectro extract muestra.png -o muestra.csv
python -m espectro match muestra.csv --lamp led.csv \
        --cal data/calibration/cal.json --top 5 --fwhm 7
python -m espectro match muestra.csv --lamp led.csv --gamma 2.2  # si hay gamma
python -m espectro info
python scripts/benchmark.py --n 200 --negatives 40 --sweep   # validación LOO
```

## API

| Método | Endpoint | Función |
|---|---|---|
| GET | `/api/status?refresh_cameras=true` | calibración, cámaras (fuerza re-sondeo), biblioteca |
| GET | `/api/stream` | vista en vivo MJPEG |
| POST | `/api/capture/{slot}` | capturar `sample`/`lamp`/`dark`/`cal` |
| GET/DELETE | `/api/frame/{slot}` | foto capturada / borrarla |
| POST | `/api/analyze` | identificación completa → JSON (curvas, top-10, color, `verdict`, `coverage`); `gamma` y `fwhm` opcionales |
| POST | `/api/calibrate` | píxel → nm (`method=auto\|lines\|led\|anchors\|range\|blackbody`) |
| GET | `/api/calibration/preview` | gráfica de calibración (PNG) |
| GET | `/api/library/search?q=` | autocompletado |
| GET | `/api/library/spectrum?name=` | ficha + datos de la gráfica |
| POST | `/api/library/fetch/{usgs\|pigments\|revigo}` | descargar colecciones |
| POST | `/api/library/upload` | subir CSV propio |
| POST | `/api/camera/{settings,refresh,stop}` | índice de cámara / exposición |

Todos los mensajes aceptan `lang=en|es`.

## Solución de problemas

| Síntoma | Solución |
|---|---|
| La imagen sale casi negra | Enciende el LED del espectrómetro (o sube **Exposición**); la app te avisa |
| Falta la cámara USB en el selector | Conéctala → **Refresh status** (el sondeo caduca a los 20 s) |
| Se eligió la cámara del portátil | Elige la entrada `★ USB` en el desplegable **Camera** |
| Los resultados parecen aleatorios | Recalibra con **Automático**, usa la foto de la lámpara como referencia, misma exposición en muestra y lámpara, y revisa el `coverage` devuelto |
| «Calibration only covers N points» | La calibración no cubre tu banda — captura el LED de nuevo |
| No se detecta la banda | Ajusta *Band threshold* / *Smoothing* o fija las filas a mano |

## Estructura

```
server.py            API FastAPI + estáticos (puerto 8000)
espectro/            motor: camara · image · calib · color · reflectance
                     · library · match · plotting · cli
espectro/data/       CMF CIE 1931 + iluminante D65
frontend/            React + Vite (src/pages, src/components, i18n.js)
scripts/             descargadores de bibliotecas + `benchmark.py` (LOO)
data/libraries/      1.024 espectros de referencia (CSV)
tests/run_tests.py   suite end-to-end (Python puro, sin pytest)
.github/workflows/   CI: lint + demo + tests + benchmark + build del frontend
```

## Validación (números honestos)

`scripts/benchmark.py` simula una **medición real de cada espectro de la
biblioteca**: LED blanco × reflectancia a través de la función de línea del
instrumento, una misma exposición para muestra y lámpara, cuantización de
8 bits, ruido de lectura y fotónico, gamma opcional y error de calibración
opcional; después recorre el pipeline entero y comprueba si el material
vuelve en el top-1 / top-3. Y añade **controles negativos** (plano, ruido,
rampa, LED sin dividir) que **no** deberían identificarse.

Valores por defecto (`--n 200 --negatives 40`, ruido 1 %, FWHM 7 nm,
semilla 7):

| métrica | valor |
|---|---|
| top-1, nombre exacto | **39,0 %** |
| top-3, nombre exacto | **60,0 %** |
| top-1, misma familia | **42,0 %** |
| top-3, misma familia | **62,5 %** |
| veredictos | identified 14 % · uncertain 77 % · unknown 10 % |
| acierto cuando responde `identified` | **100 % (27/27)** |
| falsos positivos en 40 controles negativos | **0 %** |

Los umbrales del veredicto (`min_score=0.70`, `min_margin=0.06`) se
eligieron con `--sweep`, que imprime precisión, recall y tasa de falsos
positivos para cada par: bajar el margen a 0,02 multiplica los falsos
positivos por ~5.

Si se empeoran las condiciones a 2 % de ruido **y** 5 nm de error de
calibración, la misma cae a top-1 24,5 % / top-3 39,5 % — la precisión de
la calibración pesa tanto como la biblioteca.

**Lo que esto NO es.** Es un benchmark sintético de bucle cerrado: mide
cuánto puede extraer el pipeline de 400–700 nm y de qué bien el veredicto
tapa las respuestas falsas, sobre datos generados con el mismo modelo
físico. **No** sustituye una validación con muestras reales (una carta de
color, pinturas conocidas, especímines minerales identificados) delante del
instrumento, y no se afirma ninguna exactitud en campo. Trata estas cifras
como referencia para condiciones de laboratorio.

## Límites honestos

- Solo el visible (**400–700 nm**): la mayoría de minerales son lisos o
  suaves ahí, y por eso el top-1 del benchmark ronda el 40 % — muchos
  materiales son genuinamente indistinguibles (calcita vs cuarzo). El
  infrarrojo requiere otro hardware.
- La pseudo-reflectancia identifica **familias** de materiales, no
  composiciones cuantitativas. El veredicto filtra la respuesta, pero
  `identified` es una afirmación de ranking, no una probabilidad: los
  umbrales se ajustaron sobre datos sintéticos (ver Validación).
- La calibración automática con **LED** es ≈ **±5-10 nm** y solo afín: el
  hump del fósforo es ancho y su posición cambia de un LED a otro, así que
  un espectro ancho y suave no puede constreñir la curvatura. Las líneas
  de mercurio/CFL (`--lines`, lo primero que intenta el modo automático)
  dan ≈ ±1-2 nm **con** curvatura y son la calibración recomendada.
- Cámaras de 8 bits con gamma/compresión: dispara sin saturar, misma
  exposición en muestra y LED, PNG sobre JPEG, y pasa `--gamma 2.2`
  (o `"gamma": 2.2`) si sabes que la cámara aplica gamma: dividir valores
  ya codificados comprime las bandas y deforma el ΔE.
- Las zonas donde la lámpara casi no aporta (por encima de ~680 nm, el
  valle 470-490 nm) se **enmascaran, no se rellenan con cero**; un
  `coverage < 1.0` en la respuesta significa que se excluyó parte de la
  banda de las métricas.
- **Los tests validan el pipeline y el benchmark lo valida con datos
  sintéticos.** Las 70 comprobaciones y la demo prueban que el código
  corre de extremo a extremo con datos generados por el mismo modelo
  (autoconsistencia < 5 nm); el benchmark leave-one-out da la exactitud
  en conjunto cerrado sobre medidas simuladas. Ninguno es una medición
  contra muestras físicas conocidas.

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md). Los textos de la interfaz están en
`frontend/src/i18n.js` (**añade cada clave a `en` y `es`**); los mensajes del
backend usan `_m(lang, es, en)`.

## Licencia

Código: [MIT](LICENSE). Datos: USGS (dominio público), Revigo (CC0),
pigmentos Zenodo (ver DOI). Cita las fuentes de datos al publicar resultados.

## Referencias

- Kokaly, R.F. et al. (2017). *USGS Spectral Library Version 7.* USGS DS 1035.
- Clark, R.N. et al. (2003). *USGS Digital Spectral Library splib05a.* OFR 03-395.
- Carrino, T.A. et al. (2025). Zenodo, DOI 10.5281/zenodo.14906872.
- Meerdink, S.K. et al. (2019). *ECOSTRESS spectral library v1.0.*
  Rem. Sens. Environ. 230, 111196.
- Funciones de igualación CIE 1931 e iluminante D65 vía colour-science.
