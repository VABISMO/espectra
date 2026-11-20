"""Loading of spectral reference libraries.

Supported formats
-----------------
* Generic CSV/TSV  : 2 columns (wavelength, reflectance), header optional;
  detects µm vs nm and 0-1 vs 0-100 automatically.
* USGS splib05a .asc (or the CSV already converted by scripts/fetch_usgs.py)
* ECOSTRESS .spectrum.txt (key:value header + Wavelength/Reflectance blocks)

Every library is normalized to: name, category, nm, reflectance 0-1.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .color import lab_of_spectrum
from .reflectance import band_depths, continuum_removal

BAD_VALUES = re.compile(r"^\*+$")


@dataclass
class Spectrum:
    name: str
    nm: np.ndarray
    reflectance: np.ndarray
    category: str = ""
    source: str = ""
    path: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def visible(self) -> bool:
        """Does it cover any part of the visible (400-700 nm)?"""
        return bool(self.nm.size and self.nm.max() >= 400 and self.nm.min() <= 700)


@dataclass(eq=False)
class Prepared:
    """Spectrum resampled to the grid with its derived values precomputed.

    `lab`, `cr` and `depth` only depend on (y, grid), so they are computed
    once in `to_grid` instead of on every `match()`: measured with
    cProfile, the candidate's color (25 %) and continuum (24 %) were about
    half of the crossing time.

    `eq=False` so dataclass comparison does not try to equal numpy arrays.
    It is iterable as a `(spectrum, y)` pair, so code doing
    `for sp, y in paired` keeps working.
    """
    spectrum: Spectrum
    y: np.ndarray
    lab: tuple[float, float, float]
    cr: np.ndarray        # R / continuo
    depth: np.ndarray     # 1 - cr (band depth)

    def __iter__(self):
        yield self.spectrum
        yield self.y


def _normalize_y(nm: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Normalize units: nm and reflectance in 0-1."""
    nm = np.asarray(nm, dtype=float)
    y = np.asarray(y, dtype=float)
    if nm.size and np.nanmax(nm) < 50:          # estaba en micras
        nm = nm * 1000.0
    finite = np.isfinite(y)
    if finite.any() and np.nanmax(y[finite]) > 1.5:
        y = y / 100.0                            # estaba en %
    good = np.isfinite(nm) & np.isfinite(y) & (nm > 50) & (nm < 25000)
    order = np.argsort(nm[good])
    return nm[good][order], y[good][order]


def _dedupe(nm: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Average values duplicated at the same wavelength."""
    if nm.size == 0:
        return nm, y
    out_nm, out_y = [], []
    i = 0
    while i < nm.size:
        j = i
        while j + 1 < nm.size and abs(nm[j + 1] - nm[i]) < 1e-6:
            j += 1
        out_nm.append(float(np.mean(nm[i:j + 1])))
        out_y.append(float(np.mean(y[i:j + 1])))
        i = j + 1
    return np.array(out_nm), np.array(out_y)


def _sniff(text: str) -> tuple[list[str], list[list[str]]]:
    """Detect the delimiter and return (header fields, rows)."""
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("#", "//"))]
    if not lines:
        return [], []
    sample = "\n".join(lines[:20])
    for delim in (",", ";", "\t", "|"):
        if delim in sample:
            break
    else:
        delim = None
    rows = [next(csv.reader([ln], delimiter=delim)) if delim else ln.split()
            for ln in lines]
    first = [c.strip() for c in rows[0]]
    try:
        float(first[0])
        float(first[1])
        header: list[str] = []
    except (ValueError, IndexError):
        header = first
        rows = rows[1:]
    return header, rows


def load_csv(path: str | Path) -> Spectrum:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    # "# name:" / "# category:" metadata from the CSV itself
    meta: dict = {}
    for ln in text.splitlines():
        if ln.startswith("#") and ":" in ln:
            k, _, v = ln[1:].partition(":")
            meta[k.strip().lower()] = v.strip()

    header, rows = _sniff(text)
    if not rows:
        raise ValueError(f"{path}: no data rows")

    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        hl = h.lower()
        if any(k in hl for k in ("wave", "lambda", "longitud", "x ")) and "wl" not in cols:
            cols["wl"] = i
        elif any(k in hl for k in ("reflect", "refl", "y ", "intens", "valor")):
            cols["y"] = i
    iwl = cols.get("wl", 0)
    iy = cols.get("y", 1 if len(rows[0]) > 1 else 0)

    nm, y = [], []
    for r in rows:
        if len(r) <= max(iwl, iy):
            continue
        if BAD_VALUES.match(r[iy].strip()):
            continue
        try:
            nm.append(float(r[iwl]))
            y.append(float(r[iy]))
        except ValueError:
            continue
    if len(nm) < 8:
        raise ValueError(f"{path}: only {len(nm)} valid points")

    nm, y = _normalize_y(np.array(nm), np.array(y))
    nm, y = _dedupe(nm, y)
    name = meta.get("name") or path.stem
    return Spectrum(name=name, nm=nm, reflectance=y,
                    category=meta.get("category", ""),
                    source=meta.get("source", "csv"), path=str(path), meta=meta)


def load_usgs_asc(path: str | Path) -> Spectrum:
    """Parser for the splib05a .asc format (3 columns, µm)."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    title = lines[14].strip() if len(lines) > 14 else path.stem
    nm, y = [], []
    for line in lines[16:]:
        parts = line.split()
        if len(parts) < 2 or "*" in parts[1]:
            continue
        try:
            nm.append(float(parts[0]) * 1000.0)
            y.append(float(parts[1]))
        except ValueError:
            continue
    if len(nm) < 8:
        raise ValueError(f"{path}: espectro demasiado corto")
    cat = {"M": "mineral", "S": "soil", "C": "coating",
           "A": "artificial", "L": "liquid", "O": "organic",
           "V": "vegetation"}.get(path.parent.name.split("_")[0][:1].upper(), "")
    return Spectrum(name=title, nm=np.array(nm), reflectance=np.array(y),
                    category=cat, source="USGS splib05a", path=str(path))


def load_ecostress(path: str | Path) -> Spectrum:
    """Tolerant parser for the ECOSTRESS/ASTER .spectrum.txt format."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    meta: dict = {}
    for ln in lines:
        if ":" in ln and not ln.strip()[0].isdigit():
            k, _, v = ln.partition(":")
            if len(k) < 40:
                meta[k.strip().lower()] = v.strip()

    def block_after(keyword: str) -> list[float]:
        vals: list[float] = []
        started = False
        for ln in lines:
            s = ln.strip()
            if not started:
                if s.lower() == keyword:
                    started = True
                continue
            if not s or s[0].isalpha():
                break
            try:
                vals.append(float(s.split(",")[0]))
            except ValueError:
                break
        return vals

    wl = block_after("wavelength")
    rf = block_after("reflectance")
    if len(wl) < 8 or len(rf) < 8:
        # comma/space separated two-column variant
        header, rows = _sniff(path.read_text(encoding="utf-8", errors="replace"))
        wl, rf = [], []
        for r in rows:
            try:
                wl.append(float(r[0]))
                rf.append(float(r[1]))
            except (ValueError, IndexError):
                continue
    if len(wl) < 8:
        raise ValueError(f"{path}: no se reconocieron datos")
    nm, y = _normalize_y(np.array(wl), np.array(rf))
    nm, y = _dedupe(nm, y)
    name = meta.get("name", path.stem.replace(".spectrum", ""))
    return Spectrum(name=name, nm=nm, reflectance=y,
                    category=meta.get("type", ""), source="ECOSTRESS",
                    path=str(path), meta=meta)


def _looks_usgs_asc(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return False
    return "USGS Digital Spectral Library" in head


def load_spectrum(path: str | Path) -> Spectrum:
    path = Path(path)
    if path.suffix.lower() == ".asc" or _looks_usgs_asc(path):
        return load_usgs_asc(path)
    if "spectrum" in path.name.lower() or path.suffix.lower() == ".txt":
        try:
            return load_ecostress(path)
        except ValueError:
            pass
    return load_csv(path)


def load_library(root: str | Path,
                 categories: set[str] | None = None,
                 min_points: int = 8) -> list[Spectrum]:
    """Recursively load every spectrum in a directory.

    categories: filter by substring in name or category
                (e.g. {"mineral", "vegeta"}).
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"library not found: {root}")
    out: list[Spectrum] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {
                ".csv", ".txt", ".asc", ".tsv", ".dat"}:
            continue
        if "readme" in path.name.lower():
            continue
        try:
            spec = load_spectrum(path)
        except (ValueError, IndexError, OSError):
            continue
        if spec.nm.size < min_points or not spec.visible:
            continue
        if categories is not None:
            blob = f"{spec.name} {spec.category} {path.as_posix()}".lower()
            if not any(c.lower() in blob for c in categories):
                continue
        out.append(spec)
    # no duplicates: the same title appears in several USGS chapters
    seen: dict[str, int] = {}
    unique: list[Spectrum] = []
    for s in out:
        key = s.name.strip().lower()
        if key in seen:
            continue
        seen[key] = 1
        unique.append(s)
    return unique


# Typical instrument spectral resolution (FWHM, nm). USGS is sampled at
# 0.5-5 nm and the camera resolves ~5-10 nm: without degrading, the
# references look "sharper" than the instrument can ever see.
FWHM_DEFAULT = 7.0


def degrade_resolution(nm: np.ndarray, y: np.ndarray,
                       fwhm_nm: float | None = FWHM_DEFAULT) -> np.ndarray:
    """Convolve a curve with a Gaussian of FWHM `fwhm_nm`.

    Approximates the instrument line shape (ILS) as a Gaussian.
    `fwhm_nm <= 0` or None returns the curve untouched; if the source
    sampling is already coarser than the instrument there is nothing
    to do either.
    """
    if not fwhm_nm or fwhm_nm <= 0:
        return np.asarray(y, dtype=float)
    nm = np.asarray(nm, dtype=float)
    y = np.asarray(y, dtype=float)
    if nm.size < 5 or nm.size != y.size:
        return y
    d = np.diff(nm)
    d = d[d > 0]
    if d.size == 0:
        return y
    step = float(np.median(d))
    if step <= 0 or step >= float(fwhm_nm):
        return y                      # already coarser than the instrument
    sigma = float(fwhm_nm) / 2.35482  # FWHM = 2·sqrt(2·ln2)·σ
    s_steps = sigma / step
    if s_steps < 0.25:
        return y
    r = int(np.ceil(3.0 * s_steps))
    k = np.exp(-0.5 * (np.arange(-r, r + 1, dtype=float) / s_steps) ** 2)
    k /= k.sum()
    padded = np.pad(y, r, mode="edge")   # no zeros: does not invent bands
    return np.convolve(padded, k, mode="valid")


def to_grid(specs: list[Spectrum], grid: np.ndarray,
            fwhm: float | None = FWHM_DEFAULT) -> list[Prepared]:
    """Resample every spectrum to the common grid; drops overlap < 60%.

    Before resampling, `fwhm` degrades each reference to the instrument's
    spectral resolution (Gaussian of FWHM `fwhm` nm): the sample already
    arrives measured through the tube's line shape, so comparing against
    undegraded references artificially favours whichever ones happen to
    have the right shape.  `fwhm=None` disables it.

    Returns `Prepared`: besides the curve it carries the CIELAB color and
    the removed continuum, which only depend on (curve, grid) and are
    therefore computed here exactly once.
    """
    res: list[Prepared] = []
    need = grid.size
    lo, hi = float(grid.min()), float(grid.max())
    for s in specs:
        m = (s.nm >= lo) & (s.nm <= hi)
        if m.sum() < max(8, int(0.6 * need)):
            continue
        y = degrade_resolution(s.nm, s.reflectance, fwhm) if fwhm \
            else s.reflectance
        yg = np.interp(grid, s.nm[m], y[m], left=np.nan, right=np.nan)
        if np.isnan(yg).mean() > 0.4:
            continue
        # gaps: extend the last measured value (filling with the mean
        # would invent a flat plateau in the middle of the curve)
        ok = np.isfinite(yg)
        if not ok.all():
            if not ok.any():
                continue
            gx = np.arange(need, dtype=float)
            yg = np.interp(gx, gx[ok], yg[ok])
        cr = continuum_removal(grid, yg)
        res.append(Prepared(s, yg, lab_of_spectrum(grid, yg),
                            cr, band_depths(cr)))
    return res
