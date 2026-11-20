"""Comparison of an unknown spectrum against a library.

Combined metrics (all invariant to intensity scale):

* SAM   : classic spectral angle between the curves, NOT mean-centred
          (Kruse/Aldrich) — invariant to scale, sensitive to offset.
* r     : Pearson correlation of the centred curves (reported separately
          and used as the `min_r` filter).
* CR    : continuum removal (upper convex hull) + correlation of the
          absorption bands, which is where the real chemical information
          lives and where the global colour is ignored.

score = w_sam·cos(SAM) + w_cr·(r_cr+1)/2 + w_color·(1-ΔE/60) → 1.0 perfect.
The three terms are DIFFERENT quantities: with the mean centred, the
first one was exactly the second (measured: cos(SAM) ≡ r).

Points where the sample is NaN (lamp-less zones, outside the
calibration) are dropped BEFORE comparing: only the valid subset is
evaluated, the same for the sample and for every candidate.

And because almost every mineral is smooth in 400-700 nm, the result
carries a **verdict** (`MatchOutcome.verdict`) with a score and a margin
threshold: if nothing reaches them the answer is *unknown* instead of
handing back the top candidate as if it were an identification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .color import delta_e, lab_of_spectrum, lab_to_hex
from .library import Prepared, Spectrum
from .reflectance import band_depths, continuum_removal


def family_key(name: str) -> str:
    """Family key of a name: first word without punctuation.

    Keeps "two samples of the same mineral" apart from "two different
    materials tied": if the best and the second best candidate are the
    same family, the real margin is against the first candidate of a
    different family.
    """
    tok = (name or "").strip().split()
    return re.sub(r"[^a-z0-9]", "", tok[0].lower()) if tok else ""


def spectral_angle(a: np.ndarray, b: np.ndarray) -> float:
    """Classic spectral angle (Kruse/Aldrich) in radians: the angle
    between the two curves **without centring**, so it is invariant to
    scale but keeps the offset (albedo is information).

    0 = same shape and proportion.  If the mean were centred this would
    become Pearson's coefficient and the score would repeat the same
    quantity twice (measured: cos(SAM) ≡ r in the previous version).
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return float("nan")
    cos = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
    return float(np.arccos(cos))


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson's r with direct arithmetic.

    `np.corrcoef` builds a whole covariance matrix and `a.std()` was
    called before for the guard: on 61-point vectors that cost ~35 % of
    the total time of `match()` (measured with cProfile). Here it is five
    small operations; the result is the same up to ~1e-15.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size != b.size:
        return float("nan")
    ac = a - a.mean()
    bc = b - b.mean()
    na = float(np.dot(ac, ac))
    nb = float(np.dot(bc, bc))
    if na <= 0.0 or nb <= 0.0:
        return float("nan")
    return float(np.dot(ac, bc) / np.sqrt(na * nb))


@dataclass
class MatchResult:
    spectrum: Spectrum
    score: float
    sam_deg: float
    r_raw: float
    r_continuum: float
    max_depth_diff: float     # maximum band depth difference
    delta_e: float = float("nan")   # CIELAB colour distance (D65)
    color_hex: str = "#808080"      # material colour (for display)

    def as_row(self) -> dict:
        return {
            "material": self.spectrum.name,
            "category": self.spectrum.category,
            "source": self.spectrum.source,
            "score": round(self.score, 4),
            "color": self.color_hex,
            "delta_e": round(self.delta_e, 2),
            "sam_deg": round(self.sam_deg, 2),
            "pearson": round(self.r_raw, 4),
            "continuum_r": round(self.r_continuum, 4),
            "band_delta": round(self.max_depth_diff, 4),
        }


# --------------------------------------------------------------- verdict
# Score weights: chosen with `scripts/benchmark.py --sweep-weights` over
# 4 scenarios (2 seeds, 2 % noise + 5 nm calibration error, 6 DN stray light):
#   0.35 / 0.40 / 0.25 -> 25.4 % top-1 per family (mean)   [before]
#   0.20 / 0.10 / 0.70 -> 55.8 %   <- chosen  (87 % of the gain)
#   0.00 / 0.10 / 0.90 -> 60.5 %   (the raw optimum turns the score into
#                                    a colourimeter: rejected)
# The continuum weight dropped because it is the noisiest term (Pearson
# on already continuum-removed curves amplifies noise); absorption bands
# still weigh through the multiplicative `1 - 0.5·depth_diff` penalty,
# which does NOT depend on the weights.
W_SAM = 0.20
W_CONT = 0.10
W_COLOR = 0.70

# Default thresholds: set with `scripts/benchmark.py --sweep`, not by eye.
# With 200-300 noisy synthetic samples and 40-60 negative controls:
#   (0.70, 0.06) → 0 % false positives, 86-100 % family precision
#                  and ~7-13 % of samples declared "identified".
#   Lowering the margin to 0.02 raises recall but spikes false positives
#   to 5-8 %.
# No son probabilidades: son cortes sobre el score combinado.
VERDICT_MIN_SCORE = 0.70
VERDICT_MIN_MARGIN = 0.06

VERDICT_LABELS = ("identified", "uncertain", "unknown")


@dataclass(frozen=True)
class Verdict:
    """Is there enough material in the library to claim something?

    identified : the best candidate beats `min_score` and is separated
                 from the first candidate of another family by
                 `min_margin`.
    uncertain  : there are good candidates but families cannot be told
                 apart (e.g. calcite vs quartz in 400-700 nm).
    unknown    : no candidate reaches the minimum (flat sample, noise,
                 material missing from the library) or there are none.
    """
    label: str
    score: float              # score of the best candidate
    margin: float             # s1 - first candidate of another family
    runner_up: float          # s1 - s2 (even if the same family)
    n: int                    # candidates evaluated
    min_score: float
    min_margin: float

    @property
    def key(self) -> str:
        return {"identified": "an.verdict.identified",
                "uncertain": "an.verdict.uncertain"}.get(
                    self.label, "an.verdict.unknown")

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "score": round(self.score, 4),
            "margin": round(self.margin, 4),
            "runner_up": round(self.runner_up, 4),
            "n": self.n,
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }


class MatchOutcome(list):
    """List of MatchResult with the verdict attached (`outcome.verdict`).

    A list subclass so existing code (`results[0]`, `len(results)`,
    `for r in results`) keeps working unchanged.
    """

    def __init__(self, results: list[MatchResult] | None = None,
                 verdict: Verdict | None = None):
        super().__init__(results or [])
        self.verdict = verdict or Verdict(
            "unknown", 0.0, 0.0, 0.0, 0,
            VERDICT_MIN_SCORE, VERDICT_MIN_MARGIN)


def assess(results: list[MatchResult],
           min_score: float = VERDICT_MIN_SCORE,
           min_margin: float = VERDICT_MIN_MARGIN) -> Verdict:
    """Classify the full ranking (untruncated) as identified/uncertain/unknown."""
    if not results:
        return Verdict("unknown", 0.0, 0.0, 0.0, 0, min_score, min_margin)
    s1 = float(results[0].score)
    runner = float(results[1].score) if len(results) > 1 else s1
    f1 = family_key(results[0].spectrum.name)
    margin = 0.0
    for r in results[1:]:
        if family_key(r.spectrum.name) != f1:
            margin = s1 - float(r.score)
            break

    if not np.isfinite(s1) or s1 < min_score:
        label = "unknown"
    elif margin < min_margin:
        label = "uncertain"
    else:
        label = "identified"
    return Verdict(label, s1, margin, s1 - runner, len(results),
                   min_score, min_margin)


def match(
    sample_nm: np.ndarray,
    sample_y: np.ndarray,
    library: list[tuple[Spectrum, np.ndarray]],
    grid: np.ndarray,
    top: int = 5,
    w_sam: float = W_SAM,
    w_cont: float = W_CONT,
    w_color: float = W_COLOR,
    min_r: float = -1.0,
    min_score: float = VERDICT_MIN_SCORE,
    min_margin: float = VERDICT_MIN_MARGIN,
) -> MatchOutcome:
    """Compare the sample (on `grid`) against the already prepared
    library: a list of `Prepared` (what `library.to_grid` returns, with
    colour and continuum precomputed) or loose `(Spectrum, y_on_grid)`
    pairs.

    score = w_sam·cos(SAM) + w_cont·(r_continuum+1)/2 + w_color·(1-ΔE/60)
            all of it with a penalty for badly matched band depth.

    The NaN of `sample_y` (weak lamp, incomplete calibration) are
    excluded from every metric instead of being treated as zero.

    Returns a `MatchOutcome` (list of `MatchResult`, truncated to `top`)
    with `.verdict`: `min_score` and `min_margin` set the cut between
    identified / uncertain / unknown **without filtering** candidates
    (`min_r` does filter them).
    """
    s = np.asarray(sample_y, dtype=float)
    if s.shape != grid.shape:
        raise ValueError("sample spectrum must be on the grid")
    valid = np.isfinite(s)
    if int(valid.sum()) < 8:
        raise ValueError(
            f"only {int(valid.sum())}/{grid.size} valid points: the lamp "
            "does not cover the band or calibration is missing")
    g = grid[valid]
    sv = s[valid]
    s_cr = continuum_removal(g, sv)
    s_lab = lab_of_spectrum(g, sv)

    results: list[MatchResult] = []
    for item in library:
        if isinstance(item, Prepared):
            spec = item.spectrum
            y = item.y
            lab, cr_full, depth_full = item.lab, item.cr, item.depth
        else:                                   # par (Spectrum, y) suelto
            spec, y = item
            lab = cr_full = depth_full = None
        yv = np.asarray(y, dtype=float)[valid]
        if yv.size != sv.size:
            continue
        r_raw = pearson(sv, yv)
        if not np.isfinite(r_raw) or r_raw < min_r:
            continue
        sam = spectral_angle(sv, yv)
        if cr_full is None:
            y_cr = continuum_removal(g, yv)
            depth = band_depths(y_cr)
        else:
            # precalculado en to_grid sobre la rejilla completa: el color y
            # the candidate's bands are its own properties: they do not
            # depend on which points the sample managed to measure
            y_cr = np.asarray(cr_full, dtype=float)[valid]
            depth = np.asarray(depth_full, dtype=float)[valid]
        r_cr = pearson(s_cr, y_cr)
        depth_diff = float(np.max(np.abs(band_depths(s_cr) - depth)))

        y_lab = lab if lab is not None else lab_of_spectrum(g, yv)
        d_e = delta_e(s_lab, y_lab)
        color_term = float(np.clip(1.0 - d_e / 60.0, 0.0, 1.0))

        # combination: spectral angle, absorption bands and colour
        cos = float(np.cos(sam)) if np.isfinite(sam) else 0.0
        r_c = 0.0 if not np.isfinite(r_cr) else r_cr
        combined = (w_sam * cos
                    + w_cont * (0.5 * (r_c + 1.0))
                    + w_color * color_term)
        # soft penalty for badly matched bands
        combined *= max(0.0, 1.0 - 0.5 * depth_diff)
        score = float(np.clip(combined, 0.0, 1.0))

        results.append(MatchResult(
            spectrum=spec, score=score,
            sam_deg=float(np.degrees(sam)) if np.isfinite(sam) else float("nan"),
            r_raw=r_raw, r_continuum=r_cr, max_depth_diff=depth_diff,
            delta_e=d_e, color_hex=lab_to_hex(y_lab),
        ))

    # the sample colour is computed by the caller with lab_of_spectrum()
    results.sort(key=lambda m: m.score, reverse=True)
    verdict = assess(results, min_score=min_score, min_margin=min_margin)
    return MatchOutcome(results[:top], verdict)
