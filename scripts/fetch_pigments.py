#!/usr/bin/env python3
"""Download the Carrino et al. 2025 pigment library (Zenodo).

24 synthetic and mineral pigments measured with an ASD FieldSpec-4
Hi-Res NG (350-2500 nm, 2151 channels).  Real reflectance data, ideal
for lamp-illuminated samples.

DOI: 10.5281/zenodo.14906872

Output: data/libraries/pigments/*.csv  (wavelength_nm,reflectance)
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = "https://zenodo.org/api/records/14906872"
OUT = ROOT / "data" / "libraries" / "pigments"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def convert(text: str, stem: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    rows = []
    for ln in lines[1:]:                      # primera fila = cabecera
        parts = ln.split("\t") if "\t" in ln else ln.split()
        if len(parts) < 2:
            continue
        try:
            nm, y = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        rows.append((nm, y))
    if len(rows) < 100:
        raise ValueError(f"{stem}: only {len(rows)} rows")
    out = [f"# name: {stem}", "# category: pigmento",
           "# source: Carrino et al. 2025, Zenodo 10.5281/zenodo.14906872",
           "wavelength_nm,reflectance"]
    out += [f"{nm:.1f},{y:.6f}" for nm, y in rows]
    return "\n".join(out) + "\n"


def main() -> int:
    import json
    print("Consultando Zenodo 10.5281/zenodo.14906872 ...")
    meta = json.loads(get(RECORD))
    url = next(f["links"]["self"] for f in meta["files"]
               if f["key"].endswith(".zip"))
    print(f"Descargando {url} ...")
    blob = get(url)

    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".txt"):
                continue
            stem = Path(info.filename).stem
            text = zf.read(info).decode("utf-8", errors="replace")
            (OUT / f"{stem}.csv").write_text(convert(text, stem),
                                             encoding="utf-8")
            n += 1
    print(f"Listo: {n} pigmentos en {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
