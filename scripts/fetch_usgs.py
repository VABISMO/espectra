#!/usr/bin/env python3
"""Descarga la USGS Digital Spectral Library (splib05a) y la convierte a CSV.

Fuente: Clark, R.N. et al. 2003, USGS Open-File Report 03-395
        https://pubs.usgs.gov/of/2003/ofr-03-395/datatable.html

Uso:
    python scripts/fetch_usgs.py                 # todo (M, S, C, A, V)
    python scripts/fetch_usgs.py --chapters M    # solo minerales
    python scripts/fetch_usgs.py --limit 50      # prueba rápida

Salida:
    data/libraries/usgs_splib05a/   archivos .asc originales (crudo)
    data/libraries/usgs/            un CSV por espectro: wavelength_nm,reflectance

Los .asc tienen 3 columnas: longitud de onda (micras), reflectancia,
desviación estándar.  `***********` marca canales borrados.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://pubs.usgs.gov/of/2003/ofr-03-395"
INDEX = f"{BASE}/datatable.html"
RAW = ROOT / "data" / "raw" / "usgs_splib05a"
OUT = ROOT / "data" / "libraries" / "usgs"

CHAPTER_NAMES = {
    "M": "Minerals",
    "S": "Soils and Mixtures",
    "C": "Coatings",
    "A": "Artificial materials",
    "L": "Liquids",
    "O": "Organic compounds",
    "V": "Vegetation",
}
DEFAULT_CHAPTERS = "MSCAV"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url: str, timeout: float = 60.0, retries: int = 5) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def parse_index(html: str) -> list[tuple[str, str]]:
    """Devuelve [(capitulo, href relativo), ...] sin duplicados."""
    out, seen = [], set()
    for href in re.findall(r'href=["\']([^"\']+\.asc)["\']', html, re.IGNORECASE):
        if href in seen:
            continue
        seen.add(href)
        m = re.match(r"ASCII/([A-Za-z])/", href)
        if m:
            out.append((m.group(1).upper(), href))
    return out


def parse_asc(text: str, href: str) -> tuple[str, list[tuple[float, float]]]:
    """Extrae (nombre, [(nm, reflectancia), ...]) de un archivo .asc."""
    lines = text.splitlines()
    # línea 15 (índice 14) = título; los datos empiezan en la línea 17 (índice 16)
    title = lines[14].strip() if len(lines) > 14 else Path(href).stem
    name = title.split("  ")[0].strip() or Path(href).stem
    name = re.sub(r"\s+", " ", name)

    pts: list[tuple[float, float]] = []
    for line in lines[16:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            wl_um = float(parts[0])
        except ValueError:
            continue
        if "*" in parts[1]:          # canal borrado
            continue
        try:
            refl = float(parts[1])
        except ValueError:
            continue
        if not (0.0 < refl < 2.0):   # basura / fuera de escala
            continue
        pts.append((wl_um * 1000.0, refl))
    return name, pts


def save_csv(path: Path, name: str, pts: list[tuple[float, float]],
             category: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# name: {name}\n")
        if category:
            fh.write(f"# category: {category}\n")
        fh.write("# source: USGS splib05a (Clark et al. 2003, OFR 03-395)\n")
        fh.write("wavelength_nm,reflectance\n")
        for nm, r in pts:
            fh.write(f"{nm:.1f},{r:.6f}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chapters", default=DEFAULT_CHAPTERS,
                    help=f"capítulos a descargar ({''.join(CHAPTER_NAMES)})")
    ap.add_argument("--limit", type=int, default=0,
                    help="máximo de archivos (0 = todos)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    wanted = set(args.chapters.upper())
    print("Descargando índice de datos USGS splib05a ...")
    index = fetch(INDEX).decode("utf-8", "replace")
    entries = [(c, h) for c, h in parse_index(index) if c in wanted]
    if args.limit:
        entries = entries[: args.limit]
    print(f"  {len(entries)} archivos .asc en capítulos {sorted(wanted)}")

    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    ok = fail = 0

    def work(item: tuple[str, str]) -> str | None:
        chapter, href = item
        dest = RAW / href.split("/", 2)[-1]
        if dest.exists() and dest.stat().st_size > 0:
            raw = dest.read_bytes()
        else:
            raw = fetch(f"{BASE}/{href}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        name, pts = parse_asc(raw.decode("utf-8", "replace"), href)
        if len(pts) < 10:
            return None
        csv_name = href.split("/")[-1].replace(".asc", ".csv")
        cat = CHAPTER_NAMES.get(chapter, chapter)
        save_csv(OUT / f"{chapter}_{csv_name}", f"{name} [{cat}]", pts, cat)
        return f"{chapter} {name}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(work, e): e for e in entries}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception as exc:          # noqa: BLE001
                fail += 1
                print(f"  FALLO {futs[fut][1]}: {exc}", file=sys.stderr)
                continue
            if res:
                ok += 1
            if (ok + fail) % 50 == 0:
                print(f"  ... {ok + fail}/{len(entries)}")

    print(f"Listo: {ok} espectros convertidos en {OUT}  (fallos: {fail})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
