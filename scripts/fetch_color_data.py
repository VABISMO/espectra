#!/usr/bin/env python3
"""Download the standard colorimetry data and bundle it with the project.

* CIE 1931 2° color matching functions (x̄, ȳ, z̄)
* CIE D65 illuminant (relative power spectrum)

Sources: colour-science (public domain / BSD-3), which packages the
official CIE data.  Saved as CSV so the program works offline.

Output:
    espectro/data/cmf_cie1931_2deg.csv   wavelength_nm,x_bar,y_bar,z_bar
    espectro/data/illuminant_d65.csv     wavelength_nm,spd
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "espectro" / "data"
BASE = ("https://raw.githubusercontent.com/colour-science/colour/develop/"
        "colour/colorimetry/datasets")
CMFS_URL = f"{BASE}/cmfs.py"
SDS_URL = f"{BASE}/illuminants/sds.py"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def extract_dict(src: str, key: str) -> dict[float, tuple[float, ...]]:
    """Extrae el primer dict anidado cuya clave sea `key`.

    Soporta dicts con tuplas como valor, p. ej.
        "CIE 1931 2 Degree Standard Observer": {360: (0.001368, ...), ...}
    """
    i = src.find(f'"{key}"')
    if i < 0:
        i = src.find(f"'{key}'")
    if i < 0:
        raise KeyError(f"clave no encontrada: {key}")
    start = src.find("{", i)
    depth = 0
    end = start
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                end = j
                break
    body = src[start:end + 1]
    out: dict[float, tuple[float, ...]] = {}
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*:\s*\(([^)]*)\)", body):
        wl = float(m.group(1))
        vals = tuple(float(v) for v in m.group(2).split(","))
        out[wl] = vals
    if not out:                       # dict escalar (iluminantes)
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*:\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)",
                             body):
            out[float(m.group(1))] = (float(m.group(2)),)
    if not out:
        raise ValueError(f"empty dict for {key}")
    return out


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)

    print("Downloading CIE 1931 color matching functions ...")
    cmfs_src = get(CMFS_URL)
    cmf = extract_dict(cmfs_src, "CIE 1931 2 Degree Standard Observer")
    p = DEST / "cmf_cie1931_2deg.csv"
    with p.open("w", encoding="utf-8") as fh:
        fh.write("# CIE 1931 2 Degree Standard Observer (CIE 1931)\n")
        fh.write("# source: colour-science (official CIE data)\n")
        fh.write("# wavelength_nm,x_bar,y_bar,z_bar\n")
        for wl in sorted(cmf):
            x, y, z = cmf[wl][:3]
            fh.write(f"{wl:.0f},{x:.6f},{y:.6f},{z:.6f}\n")
    print(f"  {len(cmf)} points written to {p}")

    print("Downloading D65 illuminant ...")
    sds_src = get(SDS_URL)
    d65 = extract_dict(sds_src, "D65")
    p = DEST / "illuminant_d65.csv"
    with p.open("w", encoding="utf-8") as fh:
        fh.write("# CIE Illuminant D65 (SPD relativo)\n")
        fh.write("# source: colour-science (official CIE data)\n")
        fh.write("# wavelength_nm,spd\n")
        for wl in sorted(d65):
            fh.write(f"{wl:.0f},{d65[wl][0]:.6f}\n")
    print(f"  {len(d65)} points written to {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
