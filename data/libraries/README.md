# Bibliotecas de espectros

CSV con formato `wavelength_nm,reflectance` (también se aceptan µm, %,
`;`/tab, o cabeceras `wavelength/reflectance`). Cabecera recomendada:

```
# name: Hematite HS22
# category: mineral
# source: USGS splib05a
wavelength_nm,reflectance
400.0,0.0421
...
```

## Contenido actual

- `usgs/` — USGS Digital Spectral Library splib05a, 740 espectros
  (475 minerales, 110 vegetación, 104 suelos/mezclas, 39 artificiales,
  12 recubrimientos). Dominio público. Generado por `scripts/fetch_usgs.py`
  a partir de <https://pubs.usgs.gov/of/2003/ofr-03-395/>.
- `pigments/` — 116 mediciones de 24 pigmentos minerales y sintéticos
  (ASD FieldSpec 4 Hi-Res NG, 350-2500 nm). Carrino et al. 2025,
  DOI 10.5281/zenodo.14906872. Generado por `scripts/fetch_pigments.py`.

## Añadir tus propios espectros

Cualquier CSV de dos columnas basta. Si tienes un espectrómetro de
laboratorio, un plugin de espectroscopía o descargas de ECOSTRESS/CSIRO,
conviértelo a `wavelength_nm,reflectance` y déjalo aquí (subcarpeta
propia). El comando `match` los carga recursivamente.

Los `.spectrum.txt` de ECOSTRESS se leen tal cual (parser incluido).

## Descargas manuales que no funcionan por script

- **ECOSTRESS/ASTER** (JPL, 3400+ espectros 0.4-25 µm): solicitud en
  <https://speclib.jpl.nasa.gov/download> → descomprimir y convertir, o
  dejar los `.spectrum.txt` en una subcarpeta.
- **USGS Spectral Library v7** (ScienceBase, tras Cloudflare):
  <https://www.sciencebase.gov/catalog/item/586e8c88e4b0f5ce109fccae>
  → `ASCIIdata_splib07a.zip`. Formato: 1 columna de reflectancia, las
  longitudes de onda en un fichero aparte (`splib07a_Wavelengths_*.txt`),
  en micras; unir ambas columnas al convertir.
