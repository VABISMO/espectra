"""Calibration from pixel to wavelength (nm).

Five modes, in order of reliability:

1. Mercury/CFL lines (`--lines`, and the first thing the automatic mode
   tries): the lines at **404.7 / 435.8 / 546.1 / 577-579 nm** are
   narrow, so they really fix both the position AND the curvature. With
   ≥3 lines the accuracy is ±1-2 nm. With only two peaks the classic
   435.8/546.1 pair is assumed (only if you state the lamp is Hg).

2. White LED (`--led`, the automatic fallback): its spectrum is broad and
   smooth (blue pump peak ~450 nm + phosphor hump ~560 nm), so it only
   gives an **affine** calibration (offset + scale) with ±5-10 nm error:
   the correlation does not constrain the curvature.

3. Anchors (`--anchor px nm ...`): marks you identify yourself. With2
   anchors a line, with3 a parabola. Most precise when you know which
   line each one is.

4. Assumed range (`--range 400 700`): linear between the first and last
   lit pixel. Fast but only indicative.

5. Blackbody (`--blackbody [T]`): ONLY for filament bulbs (tungsten
   2700-3000 K). Does not work for LED: an LED does not emit like a
   blackbody.

The result is saved as JSON: polynomial coefficients
nm(p) = c[0]*p^k + ... + c[-1].
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np


# Approximate quantum response of a CMOS sensor with an IR-cut filter:
# a broad Gaussian around550 nm.
def qe_model(nm: np.ndarray, center: float = 550.0, sigma: float = 150.0) -> np.ndarray:
    return np.exp(-0.5 * ((nm - center) / sigma) ** 2)


def planck(nm: np.ndarray, T: float) -> np.ndarray:
    """Curva de Planck normalizada (relativa), nm -> intensidad relativa."""
    lam = np.asarray(nm, dtype=float) * 1e-9          # m
    h, c, k = 6.62607015e-34, 2.99792458e8, 1.380649e-23
    with np.errstate(over="ignore"):
        b = (2.0 * h * c**2 / lam**5) / np.expm1(h * c / (lam * k * T))
    b = np.nan_to_num(b, nan=0.0, posinf=0.0, neginf=0.0)
    m = b.max()
    return b / m if m > 0 else b


def _gauss(nm: np.ndarray, center: float, sigma: float) -> np.ndarray:
    return np.exp(-0.5 * ((np.asarray(nm, dtype=float) - center) / sigma) ** 2)


def led_template(nm: np.ndarray) -> np.ndarray:
    """White LED template: InGaN blue pump + phosphor + red tail.

    Nominal peaks: 450 nm (pump) and 560 nm (phosphor). Real LEDs vary
    ±10-15 nm; the correlation refinement absorbs that variation.
    """
    nm = np.asarray(nm, dtype=float)
    blue = _gauss(nm, 450.0, 17.0)
    phosphor = 0.85 * _gauss(nm, 560.0, 75.0)
    red_tail = 0.30 * _gauss(nm, 645.0, 95.0)
    return blue + phosphor + red_tail


def led_model(nm: np.ndarray) -> np.ndarray:
    """LED as seen by the sensor: template · quantum response."""
    return led_template(nm) * qe_model(nm)


def poly_from_anchors(anchors: list[tuple[float, float]]) -> np.ndarray:
    """anchors = [(pixel, nm), ...] -> coeficientes np.polyfit."""
    if len(anchors) < 2:
        raise ValueError("at least 2 anchors are required (pixel, nm)")
    px = np.array([a[0] for a in anchors], dtype=float)
    nm = np.array([a[1] for a in anchors], dtype=float)
    deg = 1 if len(anchors) == 2 else 2
    return np.polyfit(px, nm, deg)


def linear_from_range(p0: float, p1: float, nm0: float, nm1: float) -> np.ndarray:
    if p1 <= p0:
        raise ValueError("invalid pixel range")
    a1 = (nm1 - nm0) / (p1 - p0)
    a0 = nm0 - a1 * p0
    return np.array([a1, a0])


def apply_cal(coeffs: np.ndarray, pixel: np.ndarray) -> np.ndarray:
    return np.polyval(coeffs, pixel)


def _prep(pixel, intensity):
    p = np.asarray(pixel, dtype=float)
    y = np.asarray(intensity, dtype=float)
    keep = y > 0.02 * y.max()
    if keep.sum() < 16:
        return None, None
    return p[keep], y[keep]


def _refine_shape(coeffs, p, y, model_fn,
                  da0_span=45.0, s_span=0.20, c2_span=0.0,
                  iters=2, n_da0=13, n_s=13, n_c2=9):
    """Maximise corr(y, model_fn(nm)) by fitting offset, scale and — only
    if asked — curvature of the map nm(p) = a0 + a1·p + c2·u².

    `c2_span` defaults to 0 (affine map) and it is not a whim: measured
    on synthetic data, a broad smooth spectrum (LED, Planck) does not
    constrain the curvature. Widening the range to ±60 nm makes noise the
    decider (Δr ~ 1e-6) and a perfectly linear calibration goes from 0.44
    nm to 10 nm of error. The real curvature is fixed by the narrow
    mercury lines with ≥3 anchors (`fit_hg_lines` / `_fit_poly`), where
    there is enough information.
    """
    mid = 0.5 * (p.min() + p.max())
    span = max(p.max() - p.min(), 1.0)
    u = (p - mid) / span
    base = np.atleast_1d(np.asarray(coeffs, dtype=float))
    a0 = float(base[-1])
    a1 = float(base[-2]) if base.size >= 2 else 1.0
    best = (-np.inf, a0, a1, 0.0)
    for _ in range(iters):
        cand = best
        for da0 in np.linspace(-da0_span, da0_span, n_da0):
            a0c = best[1] + da0
            for s in np.linspace(1.0 - s_span, 1.0 + s_span, n_s):
                a1c = best[2] * s
                for c2 in np.linspace(-c2_span, c2_span, n_c2):
                    c2c = best[3] + c2
                    nm = a0c + a1c * p + c2c * u**2
                    if np.any(nm <= 0):
                        continue
                    model = model_fn(nm)
                    if model.std() == 0 or y.std() == 0:
                        continue
                    r = float(np.corrcoef(y, model)[0, 1])
                    if np.isfinite(r) and r > cand[0]:
                        cand = (r, a0c, a1c, c2c)
        best = cand
        # geometric mesh: each iteration refines around the best
        da0_span = max(1.0, da0_span * 0.35)
        s_span = max(0.004, s_span * 0.35)
        c2_span = abs(c2_span) * 0.35      # (0 se queda en 0)
    r, a0f, a1f, c2f = best
    if r <= 0:
        return np.asarray(coeffs, dtype=float), float("-inf")
    d = c2f / span**2
    return np.array([d, a1f - 2.0 * d * mid, a0f + d * mid**2]), float(r)


# ---------------------------------------------------- mercury lamp
# Visible mercury lines: present in any mercury lamp and in CFLs too
# (mercury + phosphor). They are NARROW, unlike a white LED's phosphor
# hump, so they fix the curvature.
HG_LINES: tuple[tuple[float, str], ...] = (
    (404.66, "Hg 404.7"),
    (435.83, "Hg 435.8"),
    (546.07, "Hg 546.1"),
    (576.96, "Hg 577.0"),
    (579.07, "Hg 579.1"),
)
# The two most intense visible lines: the pair used when there are only
# two peaks and the user states the lamp is mercury/CFL.
HG_PRIMARY = (435.83, 546.07)


def hg_model(nm: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """Mercury lamp template as seen by the sensor (lines only)."""
    nm = np.asarray(nm, dtype=float)
    y = np.zeros_like(nm)
    for center, _ in HG_LINES:
        y = y + _gauss(nm, center, sigma)
    return y * qe_model(nm)


def _subpixel_peak(y: np.ndarray, i: int) -> float:
    """Sub-pixel centre of a maximum by fitting a parabola to 3 points."""
    if i <= 0 or i >= y.size - 1:
        return float(i)
    y0, y1, y2 = float(y[i - 1]), float(y[i]), float(y[i + 1])
    den = y0 - 2.0 * y1 + y2
    if den >= 0:                       # not a concave maximum
        return float(i)
    d = 0.5 * (y0 - y2) / den
    return float(i) + float(np.clip(d, -1.0, 1.0))


def _half_max_width(y: np.ndarray, i: int, win: int) -> float:
    """Half-maximum width (pixels) of peak `i`, measured over the
    local minimum of ±`win` pixels."""
    n = y.size
    lo, hi = max(0, i - win), min(n, i + win + 1)
    base = float(np.min(y[lo:hi]))
    half = base + 0.5 * (float(y[i]) - base)
    l = i
    while l > lo and y[l - 1] >= half:
        l -= 1
    r = i
    while r + 1 < hi and y[r + 1] >= half:
        r += 1
    return float(r - l + 1)


def measure_fwhm_nm(pixel: np.ndarray, intensity: np.ndarray,
                    coeffs: np.ndarray,
                    peaks: list[tuple[float, float]] | None = None,
                    smooth: int = 5) -> float | None:
    """Instrument spectral resolution (FWHM in nm), measured.

    A mercury lamp emits lines MUCH narrower than any
    tube, so the width we see them at IS the instrument line shape:
    with this the system measures itself instead of assuming
    a FWHM of 7 nm.

    The median of the widths is returned; an unresolved doublet
    (577/579 nm) comes out wider and is dropped for being >2× the median.
    """
    from .image import moving_average

    p = np.asarray(pixel, dtype=float)
    raw = np.asarray(intensity, dtype=float)
    if p.size != raw.size or p.size < 8:
        return None
    if peaks is None:
        peaks = detect_narrow_peaks(p, raw)
    if not peaks:
        return None
    y = moving_average(raw, smooth)
    win = max(4, int(0.05 * y.size))
    deriv = np.polyder(np.asarray(coeffs, dtype=float))
    widths: list[float] = []
    for q, _h in peaks:
        i = int(np.argmin(np.abs(p - q)))
        disp = float(np.polyval(deriv, p[i]))        # nm per pixel
        if disp > 0:
            widths.append(_half_max_width(y, i, win) * disp)
    if not widths:
        return None
    med = float(np.median(widths))
    if med <= 0:
        return None
    keep = [w for w in widths if w <= 2.0 * med]     # fuera el doblete
    return float(np.median(keep))


def detect_narrow_peaks(pixel: np.ndarray, intensity: np.ndarray,
                        max_peaks: int = 8, width_frac: float = 0.08,
                        min_prom: float = 0.06, smooth: int = 5
                        ) -> list[tuple[float, float]]:
    """NARROW maxima (lines) of a spectrum → [(pixel, height), ...].

    Tells a mercury line (FWHM ~ instrument resolution,
    <8 % of the band width) of the LED blue pump (~40 nm) and of the
    phosphor hump (~180 nm)) apart, which are dropped by width.
    """
    from .image import moving_average

    y = np.asarray(intensity, dtype=float)
    p = np.asarray(pixel, dtype=float)
    if y.size < 32 or p.size != y.size:
        return []
    ys = moving_average(y, smooth)
    n = ys.size
    ymax = float(np.nanmax(ys))
    if not np.isfinite(ymax) or ymax <= 0:
        return []
    win = max(4, int(0.05 * n))
    found: list[tuple[float, float]] = []
    for i in range(1, n - 1):
        if not (ys[i] >= ys[i - 1] and ys[i] > ys[i + 1]):
            continue
        lo, hi = max(0, i - win), min(n, i + win + 1)
        base = float(np.min(ys[lo:hi]))
        prom = float(ys[i]) - base
        if prom < min_prom * ymax:
            continue
        if _half_max_width(ys, i, win) > width_frac * n:
            continue                   # wide hump: LED or phosphor, not a line
        found.append((float(p[i]) + (_subpixel_peak(ys, i) - i), float(ys[i])))
    found.sort(key=lambda t: -t[1])
    found = found[:max(1, int(max_peaks))]
    found.sort(key=lambda t: t[0])
    return found


def _plausible(coeffs: np.ndarray, pixel: np.ndarray,
               disp_range: tuple[float, float] = (0.03, 2.0),
               span_nm: tuple[float, float, float, float] = (
                   250.0, 520.0, 580.0, 1000.0)) -> bool:
    """Sanity of the map: increasing, physical dispersion and the whole sensor
    falling inside a reasonable visible window. It avoids accepting a pair of
    peaks that are really not mercury lines."""
    p = np.asarray(pixel, dtype=float)
    if p.size < 2:
        return False
    p0, p1 = float(p.min()), float(p.max())
    if p1 <= p0:
        return False
    n0, n1 = float(np.polyval(coeffs, p0)), float(np.polyval(coeffs, p1))
    if not (np.isfinite(n0) and np.isfinite(n1)) or n1 <= n0:
        return False
    if not (disp_range[0] <= (n1 - n0) / (p1 - p0) <= disp_range[1]):
        return False
    return span_nm[0] <= n0 <= span_nm[1] and span_nm[2] <= n1 <= span_nm[3]


def _fit_poly(px: np.ndarray, nm: np.ndarray,
              pixel: np.ndarray) -> np.ndarray | None:
    """Robust pixel → nm fit of the identified lines.

    1. least-squares line;
    2. lines that do not agree with it are dropped (e.g. the
       577/579 the instrument cannot resolve and that ends up on one);
    3. curvature is only allowed if it also greatly reduces the error
       blows up when extrapolating to the other end of the sensor.
    """
    px = np.asarray(px, dtype=float)
    nm = np.asarray(nm, dtype=float)
    if px.size < 2 or px.size != nm.size:
        return None
    lin = np.polyfit(px, nm, 1)
    res = nm - np.polyval(lin, px)
    mad = float(np.median(np.abs(res - np.median(res))))
    keep = np.abs(res - np.median(res)) <= max(1.0, 3.0 * mad)
    if keep.sum() >= 2 and not bool(keep.all()):
        px, nm = px[keep], nm[keep]
        lin = np.polyfit(px, nm, 1)
        res = nm - np.polyval(lin, px)
    if px.size >= 4:
        quad = np.polyfit(px, nm, 2)
        rms_lin = float(np.sqrt(np.mean(res ** 2)))
        rms_quad = float(np.sqrt(np.mean((nm - np.polyval(quad, px)) ** 2)))
        # line/parabola disagreement over the WHOLE sensor: if curvature
        # dispara al extrapolar, no sirve (mejor la recta)
        drift = float(np.max(np.abs(np.polyval(quad, pixel)
                                    - np.polyval(lin, pixel))))
        if rms_quad < 0.5 * rms_lin and drift <= 10.0:
            return np.asarray(quad, dtype=float)
    return np.asarray(lin, dtype=float)


# RMS differences below this are floating point noise (the
# fit goes through the same points): ties are broken by priority — the
# classic 435.8/546.1 comes first— so the result does NOT depend on the
# numpy version. Without this, a 1e-13 nm RMS difference used to
# make a wrong line pair win (43 nm of error in CI).
RMS_TIE = 1e-6


def _better_assignment(cand: tuple, best: tuple) -> bool:
    """Does `cand` explain the lamp better than `best`?  (see RMS_TIE)."""
    if cand[0] != best[0]:
        return cand[0] < best[0]          # more peaks explained
    if abs(cand[1] - best[1]) <= RMS_TIE:
        return cand[2] < best[2]          # tie-break: pair priority
    return cand[1] < best[1]              # menor error


def fit_hg_lines(pixel: np.ndarray, intensity: np.ndarray,
                 tol_nm: float = 3.0, min_lines: int = 3,
                 allow_pair: bool = False
                 ) -> tuple[np.ndarray, dict] | None:
    """Calibration from narrow mercury / CFL lines.

    Tries every peak ↔ mercury line assignment, keeps the one
    that explains the most peaks (tie-break: lower error, then the
    classic 435.8/546.1 pair) and refits a polynomial through them.

    With `allow_pair=False` `min_lines` explained peaks are required (the
    the automatic path cannot afford mistakes); with `allow_pair=True`
    only two peaks are accepted assuming the classic pair — use it only if
    YOU KNOW the lamp is mercury/CFL.

    Devuelve (coeffs, meta) o None.
    """
    peaks = detect_narrow_peaks(pixel, intensity)
    if len(peaks) < 2:
        return None
    need = 2 if allow_pair else max(2, int(min_lines))
    if len(peaks) < need:
        return None
    px = np.array([q for q, _ in peaks], dtype=float)

    line_pairs = [HG_PRIMARY]
    for (na, _), (nb, _) in itertools.combinations(HG_LINES, 2):
        if (na, nb) != HG_PRIMARY:
            line_pairs.append((na, nb))

    best: tuple | None = None
    for i in range(px.size):
        for j in range(i + 1, px.size):
            if px[j] <= px[i]:
                continue
            for pri, (na, nb) in enumerate(line_pairs):
                coeffs = np.polyfit([px[i], px[j]], [na, nb], 1)
                if not _plausible(coeffs, pixel):
                    continue
                used, rms = _explain(px, coeffs, tol_nm)
                if len(used) < need:
                    continue
                cand = (-len(used), rms, pri, coeffs, used)
                if best is None or _better_assignment(cand, best):
                    best = cand
    if best is None:
        return None

    used = best[4]
    order = np.argsort([u[0] for u in used])
    used = [used[k] for k in order]
    coeffs = _fit_poly(np.array([u[0] for u in used]),
                       np.array([u[2] for u in used]), pixel)
    if coeffs is None or not _plausible(coeffs, pixel):
        return None
    meta = {
        "lines": [{"pixel": round(float(u[0]), 3), "nm": u[2],
                   "error_nm": round(float(u[3]), 3)} for u in used],
        "lines_explained": len(used),
        "lines_tol_nm": tol_nm,
        "lines_classic_pair": len(used) == 2,
    }
    fwhm = measure_fwhm_nm(pixel, intensity, coeffs, peaks)
    if fwhm:
        meta["fwhm_nm"] = round(float(fwhm), 2)
    return np.asarray(coeffs, dtype=float), meta


def _explain(px: np.ndarray, coeffs: np.ndarray,
             tol_nm: float
             ) -> tuple[list[tuple[float, float, float, float]], float]:
    """Peaks that land on a mercury line and their RMS error (nm).

    * A peak within `tol_nm` of TWO lines narrower than the
      instrument resolution (577/579 nm doublet) is a blend: its
      position is the centroid of both, not a line, and cannot anchor.
    * Each line is paired with a single peak (the closest one): so
      noise cannot "create" two peaks for the same line.
    """
    cands: list[tuple[float, int, float, float, float]] = []
    for idx, (q, nm) in enumerate(zip(px, np.polyval(coeffs, px))):
        near = sorted(((abs(float(nm) - c), c) for c, _ in HG_LINES),
                      key=lambda t: t[0])
        if (len(near) > 1 and near[0][0] <= tol_nm and near[1][0] <= tol_nm
                and abs(near[0][1] - near[1][1]) <= 2.0 * tol_nm):
            continue                   # doblete irresoluble: no es un ancla
        err, line = near[0]
        if err <= tol_nm:
            cands.append((err, idx, float(q), float(nm), line))
    cands.sort()

    used: list[tuple[float, float, float, float]] = []
    errs: list[float] = []
    seen_peak: set[int] = set()
    seen_line: set[float] = set()
    for err, idx, q, nm, line in cands:
        if idx in seen_peak or line in seen_line:
            continue
        seen_peak.add(idx)
        seen_line.add(line)
        used.append((q, nm, line, err))
        errs.append(err)
    rms = float(np.sqrt(np.mean(np.square(errs)))) if errs else float("inf")
    return used, rms


def detect_led_peaks(pixel: np.ndarray, intensity: np.ndarray
                     ) -> tuple[float, float] | None:
    """Locates (blue peak, phosphor peak) in pixels.

    Strategy: global peak → mask → second peak → check that
    there is a valley between them (otherwise they are lobes of one hump).
    """
    from .image import moving_average
    y = moving_average(np.asarray(intensity, dtype=float),
                       max(9, int(np.asarray(intensity).size // 60) * 2 + 1))
    n = y.size
    if n < 32:
        return None
    ymax = y.max()
    if ymax <= 0:
        return None
    p1 = int(np.argmax(y))
    guard = max(2, int(0.04 * n))
    lo, hi = max(0, p1 - guard), min(n, p1 + guard + 1)
    masked = y.copy()
    masked[lo:hi] = -np.inf
    p2 = int(np.argmax(masked))
    if p1 > p2:
        p1, p2 = p2, p1
    # validaciones
    if y[p2] < 0.25 * ymax:
        return None
    if abs(p2 - p1) < 0.04 * n:
        return None
    valley = float(y[p1:p2 + 1].min())
    if valley > 0.95 * min(y[p1], y[p2]):
        return None                      # no valley: it is a single hump
    return float(pixel[p1]), float(pixel[p2])


def refine_led(coeffs: np.ndarray, pixel: np.ndarray,
               intensity: np.ndarray) -> tuple[np.ndarray, float]:
    """Refines the calibration against the white LED template.

    Fits **offset and scale** (affine map): an LED has a
    broad, smooth spectrum, so the correlation cannot constrain the
    curvatura (ver `_refine_shape`).  Si necesitas curvatura, usa la
    calibration from mercury/CFL lines with ≥3 anchors.
    """
    p, y = _prep(pixel, intensity)
    if p is None:
        return coeffs, float("nan")
    return _refine_shape(coeffs, p, y, led_model, iters=4)


def refine_blackbody(
    coeffs: np.ndarray,
    pixel: np.ndarray,
    intensity: np.ndarray,
    T_range: tuple[float, float] = (2300.0, 3500.0),
    c2_span: float = 0.0,
    steps: int = 61,
) -> tuple[np.ndarray, float, float]:
    """Refine with the Planck·QE curve: try temperatures and for each one
    fit offset/scale (and curvature only if `c2_span` is asked for, because
    correlation with a broad spectrum does not constrain it — see `_refine_shape`).
    ONLY for filament bulbs.

    Returns (new coeffs, T, correlation).
    """
    p, y = _prep(pixel, intensity)
    if p is None:
        return coeffs, float("nan"), float("nan")
    best_r, best_poly, best_T = -np.inf, np.asarray(coeffs, float), float("nan")
    for T in np.linspace(T_range[0], T_range[1], max(5, min(11, steps // 5))):
        poly, r = _refine_shape(
            coeffs, p, y, lambda nm, T=T: planck(nm, T) * qe_model(nm),
            c2_span=c2_span, iters=1)
        if r > best_r:
            best_r, best_poly, best_T = r, poly, T
    poly, r = _refine_shape(best_poly, p, y,
                            lambda nm: planck(nm, best_T) * qe_model(nm),
                            c2_span=c2_span, iters=1)
    if r >= best_r:
        best_r, best_poly = r, poly
    if best_r <= 0:
        return coeffs, float("nan"), float("nan")
    return best_poly, float(best_T), float(best_r)


def save_cal(path: str | Path, coeffs: np.ndarray, meta: dict | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "coefficients": [float(c) for c in np.atleast_1d(coeffs)],
        "model": "nm = poly(pixel)",
        **(meta or {}),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_cal(path: str | Path) -> tuple[np.ndarray, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return np.array(data["coefficients"], dtype=float), data
