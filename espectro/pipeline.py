"""Analysis pipeline: intensity profile → pseudo-reflectance → matching.

One single copy of the logic, shared by `server.py` (REST API) and
`app.py` (Streamlit UI). It used to be duplicated across both files and
every change had to be applied twice — which is exactly what happened
when the lamp mask and the gamma linearization were introduced.

The frontends still handle their own parts:

* extracting the profile from the photo → `espectro.image.extract_profile`
* locating and loading the calibration → `espectro.calib.load_cal`
* preparing the candidate pair          → `espectro.library.to_grid`
* presenting the result (JSON / Streamlit)

Only profiles already converted to intensity enter here.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .calib import apply_cal
from .color import lab_of_spectrum, lab_to_hex
from .match import MatchOutcome, match
from .reflectance import fill_nan, linearize, pseudo_reflectance, resample

# Common working grid: 400-700 nm every 5 nm (61 points).
GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)


class LowCoverage(ValueError):
    """The calibration or the lamp do not cover enough of the band."""

    def __init__(self, covered: int, total: int, min_coverage: float):
        super().__init__(
            f"only {covered}/{total} valid points "
            f"(minimum {int(min_coverage * 100)} %): recalibrate or "
            "capture the lamp again")
        self.covered = covered
        self.total = total
        self.min_coverage = min_coverage


@dataclass
class Analysis:
    """Pipeline result, ready for each frontend to present."""

    results: MatchOutcome       # top-N plus `.verdict`
    y: np.ndarray               # with NaN: what `match` crosses
    y_show: np.ndarray          # filled: charts, CSV and JSON
    nm: np.ndarray              # wavelength axis of each point of `y`
    coverage: float             # fraction of the band with valid points
    has_lamp: bool
    color: str                  # CIELAB color of the sample, as hex


def curves_of(results, paired) -> dict[str, np.ndarray]:
    """Curves of the winning candidates, by name.

    `paired` may be `list[Prepared]` or loose `(Spectrum, y)` pairs:
    both unpack the same way.
    """
    want = {r.spectrum.name for r in results}
    out: dict[str, np.ndarray] = {}
    for item in paired:
        spec, y = item
        if spec.name in want:
            out[spec.name] = y
    return out


def analyze(sample: np.ndarray,
            lamp: np.ndarray | None,
            pixel: np.ndarray,
            coeffs: np.ndarray,
            paired,
            *,
            gamma: float = 1.0,
            full_scale: float = 255.0,
            smooth: int = 5,
            top: int = 10,
            grid: np.ndarray = GRID,
            min_coverage: float = 0.4) -> Analysis:
    """Run the whole pipeline over already-extracted profiles.

    sample / lamp : mean intensity of the band (same axis as `pixel`).
                    `lamp=None` ⇒ no reference (only normalized).
    coeffs        : polynomial `nm = P(pixel)`.
    paired        : already prepared candidates (`library.to_grid`).

    Raises `LowCoverage` when less than `min_coverage` of the band is
    usable; the caller decides how to translate that to its language.
    """
    sample = np.asarray(sample, dtype=float)
    pixel = np.asarray(pixel, dtype=float)

    if lamp is not None:
        lamp = np.asarray(lamp, dtype=float)
        n = min(sample.size, lamp.size, pixel.size)
        y = pseudo_reflectance(sample[:n], lamp[:n], smooth=smooth,
                               gamma=gamma, full_scale=full_scale)
        px = pixel[:n]
        has_lamp = True
    else:
        y = np.clip(linearize(sample, gamma, full_scale), 0.0, None)
        m = float(np.max(y))
        y = y / m if m > 0 else y
        px = pixel
        has_lamp = False

    nm = apply_cal(coeffs, px)
    y_grid = resample(nm, y, grid)          # keeps the mask's NaN
    ok = np.isfinite(y_grid)
    if int(ok.sum()) < int(min_coverage * grid.size):
        raise LowCoverage(int(ok.sum()), int(grid.size), min_coverage)

    # filled version ONLY for display/serialization; the engine uses `y_grid`
    y_show = fill_nan(y_grid)
    results = match(grid, y_grid, paired, grid, top=top)
    color = lab_to_hex(lab_of_spectrum(grid, y_grid))
    return Analysis(results=results, y=y_grid, y_show=y_show, nm=nm,
                    coverage=float(ok.mean()), has_lamp=has_lamp,
                    color=color)
