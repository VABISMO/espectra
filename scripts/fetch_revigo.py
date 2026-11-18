#!/usr/bin/env python3
"""Descarga la biblioteca de pinturas del proyecto Revigo (CC0).

Mezclas de pigmentos históricos (Eosin, Cochineal, Minium, bermellón...)
medidas por reflectancia sobre fondo blanco y negro, 400-700 nm / 10 nm.
Fuente: https://github.com/fligt/revigo-spectra (CC0)

Salida: data/libraries/pinturas/*.csv
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "libraries" / "pinturas"
RAW = ("https://raw.githubusercontent.com/fligt/revigo-spectra/master/data/")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GROUNDS = {"_w": "fondo blanco", "_b": "fondo negro"}


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def main() -> int:
    print("Descargando espectros de pinturas Revigo ...")
    spec_txt = get(RAW + "revigo-mixture-specifications.txt")
    spec: dict[str, str] = {}
    for line in spec_txt.splitlines():
        if "\t" not in line:
            continue
        code, rest = line.split("\t", 1)
        if "|" in rest:
            mix, ratios = rest.split("|", 1)
            pretty = " + ".join(mix.split("-")) + f" ({ratios})"
        else:
            pretty = rest
        spec[code.strip()] = pretty

    data_txt = get(RAW + "revigo-aggregated-palette-spectral-data.txt")
    lines = [ln for ln in data_txt.splitlines() if ln.strip()]
    header = lines[0].split("\t")
    try:
        wls = [float(w) for w in header[1:]]
    except ValueError:
        wls = [float(w) for w in header[1:]]

    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in lines[1:]:
        parts = line.split("\t")
        key = parts[0].strip()
        suffix = next((s for s in GROUNDS if key.endswith(s)), None)
        if suffix is None:
            continue
        code = key[: -len(suffix)]
        base = spec.get(code, code)
        try:
            vals = [float(v) for v in parts[1:1 + len(wls)]]
        except ValueError:
            continue
        name = f"{base} · {GROUNDS[suffix]}"
        out = [f"# name: {name}", "# category: pintura",
               "# source: Revigo project (CC0), fligt/revigo-spectra",
               "wavelength_nm,reflectance"]
        out += [f"{w:.0f},{max(0.0, v / 100.0):.6f}"
                for w, v in zip(wls, vals)]
        (OUT / f"{key}.csv").write_text("\n".join(out) + "\n",
                                        encoding="utf-8")
        n += 1
    print(f"Listo: {n} espectros de pintura en {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
