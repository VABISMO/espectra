"""Conversion to pseudo-reflectance and re-sampling.

With a reference bulb the sample spectrum is divided by the bulb's, which
cancels the lamp, the tube's optical transmission and the sensor
response.  The result is NOT absolute reflectance (there is no white
reference), but it keeps the spectral shape, which is what we use to
compare against the libraries.

Two important details:

* **Gamma**: if the 8-bit camera delivers gamma-encoded values they must
  be linearized BEFORE dividing (`gamma=2.2`); otherwise the division
  returns (s/l)^γ and the absorption bands come out compressed.
  Disabled by default (`gamma=1.0`).

* **Lamp-less zones**: where the bulb is weak the ratio is meaningless
  and NaN is returned, never 0.  An artificial zero would create a fake
  absorption band in the red and shift the color.  The NaN travels to
  `match()`, which only evaluates the valid points.
"""
from __future__ import annotations

import numpy as np


def linearize(y: np.ndarray, gamma: float = 1.0,
              full_scale: float | None = None) -> np.ndarray:
    """Undo the gamma encoding of an 8-bit camera.

    gamma      : exponent applied by the camera (1.0 = no effect).
    full_scale : DN that corresponds to saturated white (255 on one channel,
                 765 on R+G+B). Defaults to the maximum of `y`.
    """
    if gamma is None or abs(float(gamma) - 1.0) < 1e-9:
        return np.asarray(y, dtype=float)
    g = float(gamma)
    if g <= 0:
        raise ValueError("gamma must be > 0")
    y = np.asarray(y, dtype=float)
    fs = float(full_scale) if full_scale else max(float(np.nanmax(y)), 1e-12)
    return np.power(np.clip(y, 0.0, fs) / fs, 1.0 / g) * fs


def _smooth_nan(y: np.ndarray, window: int | None) -> np.ndarray:
    """Moving average that respects NaN (does not drag zeros into valid zones)."""
    y = np.asarray(y, dtype=float)
    if window is None or window <= 1:
        return y
    w = min(int(window), max(3, y.size // 2))
    if w % 2 == 0:
        w += 1
    valid = np.isfinite(y)
    if not valid.any():
        return y
    k = np.ones(w) / w
    num = np.convolve(np.where(valid, y, 0.0), k, mode="same")
    den = np.convolve(valid.astype(float), k, mode="same")
    out = np.full_like(y, np.nan)
    np.divide(num, den, out=out, where=den > 1e-6)
    return out


def pseudo_reflectance(sample: np.ndarray,
                       lamp: np.ndarray,
                       smooth: int = 5,
                       floor_frac: float = 0.03,
                       gamma: float = 1.0,
                       full_scale: float | None = None) -> np.ndarray:
    """R(p) = sample(p) / bulb(p), NaN where the bulb is weak.

    `floor_frac` is the fraction of the bulb maximum below which the ratio
    is pure noise (LED tails above ~680 nm, blue-green valley ~470-490 nm):
    those points are marked NaN instead of being divided or zero-filled.
    The mask is decided on the RAW bulb signal (S/N does not depend on
    gamma).
    """
    raw_l = np.asarray(lamp, dtype=float)
    thr = max(float(np.nanmax(raw_l)) * floor_frac, 1e-12)
    mask = raw_l > thr

    s = linearize(sample, gamma, full_scale)
    l = linearize(lamp, gamma, full_scale)
    if s.shape != l.shape:
        raise ValueError(f"shape mismatch: {s.shape} vs {l.shape}")
    ratio = np.divide(s, l, out=np.full_like(s, np.nan),
                      where=mask & (l > 0.0))
    ratio = np.clip(ratio, 0.0, 3.0)        # NaN propagates through clip
    return _smooth_nan(ratio, smooth)


def fill_nan(y: np.ndarray) -> np.ndarray:
    """Fill NaN by linear interpolation between the valid points.

    Only for display / JSON: the engine (`match`) works with the version
    that keeps the NaN and discards those points.
    """
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    if ok.all():
        return y
    if not ok.any():
        return np.zeros_like(y)
    x = np.arange(y.size, dtype=float)
    return np.interp(x, x[ok], y[ok])


# ---------------------------------------------------------------- continuo
# (these helpers live here and not in match.py so that library.py can
#  precompute every candidate's continuum without creating an import cycle)
def upper_hull(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Upper convex hull (monotone chain) interpolated over x."""
    pts: list[tuple[float, float]] = []
    for xi, yi in zip(x, y):
        pts.append((float(xi), float(yi)))
        while len(pts) >= 3:
            (x0, y0), (x1, y1), (x2, y2) = pts[-3:]
            # cross > 0 -> midpoint below the line = drop it
            cross = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
            if cross <= 0:
                break
            pts.pop(-2)
    hx = np.array([p[0] for p in pts])
    hy = np.array([p[1] for p in pts])
    return np.interp(x, hx, hy)


def continuum_removal(nm: np.ndarray, y: np.ndarray) -> np.ndarray:
    """R / continuum over the visible range (upper convex hull)."""
    hull = upper_hull(nm, y)
    hull = np.where(hull <= 1e-9, np.nan, hull)
    cr = y / hull
    if np.isnan(cr).all():
        return np.ones_like(y)
    return np.nan_to_num(cr, nan=float(np.nanmean(cr)))


def band_depths(cr: np.ndarray) -> np.ndarray:
    """Band depths (1 - CR) over a fine grid."""
    return 1.0 - np.asarray(cr, dtype=float)


def normalize(y: np.ndarray, mode: str = "max") -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if mode == "none":
        return y
    if mode == "max":
        m = np.nanmax(y)
        return y / m if m > 0 else y
    if mode == "unit":                       # mean0, norm1 (for SAM)
        m = np.nanmean(y)
        y = y - m
        n = np.linalg.norm(y)
        return y / n if n > 0 else y
    raise ValueError("mode must be 'none', 'max' or 'unit'")


def resample(nm: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Re-sample onto `grid`, averaging within ±half a step.

    Averaging (instead of sampling a single pixel) is what the detector
    does over the grid bin: without it the camera noise passes straight
    through into the curve and the crossing against the library suffers.
    NaN (weak lamp) are ignored inside the bin; if no valid point is
    left the result is NaN.
    """
    nm = np.asarray(nm, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(nm)
    nm, y = nm[order], y[order]
    if grid.size > 1:
        d = np.diff(np.asarray(grid, dtype=float))
        step = float(np.median(d[d > 0])) if np.any(d > 0) else 0.0
    else:
        step = 0.0
    out = np.full(np.asarray(grid).shape, np.nan, dtype=float)
    if step <= 0:
        return np.interp(grid, nm, y, left=np.nan, right=np.nan)
    half = 0.5 * step
    for i, g in enumerate(np.asarray(grid, dtype=float)):
        lo = int(np.searchsorted(nm, g - half))
        hi = int(np.searchsorted(nm, g + half, side="right"))
        if hi > lo:
            chunk = y[lo:hi]
            good = np.isfinite(chunk)
            out[i] = float(np.mean(chunk[good])) if good.any() else np.nan
            continue
        # outside the calibration range
        out[i] = float(np.interp(g, nm, y, left=np.nan, right=np.nan))
    return out


def resample_mean(nm: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Como resample pero promediando muestras duplicadas/ cercanas."""
    nm = np.asarray(nm, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(nm)
    nm, y = nm[order], y[order]
    out = np.full(grid.shape, np.nan)
    idx = np.clip(np.searchsorted(nm, grid), 0, nm.size - 1)
    for i, (g, j) in enumerate(zip(grid, idx)):
        # local window of ±1 grid step
        lo = np.searchsorted(nm, g - 1.0)
        hi = np.searchsorted(nm, g + 1.0, side="right")
        if hi <= lo:
            out[i] = y[j] if abs(nm[j] - g) < 50 else np.nan
        else:
            out[i] = float(np.mean(y[lo:hi]))
    return out
