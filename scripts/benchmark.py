#!/usr/bin/env python
"""Identification benchmark: synthetic leave-one-out validation.

What it measures
---------------
For every spectrum in the library it **simulates a real measurement** of
that same material (white LED × reflectance, noise, 8-bit quantization,
optional gamma and optional calibration error) and checks whether that
material comes back in the top-1 / top-3 of the crossing against the
library.

It is a **closed, synthetic** benchmark: it validates the pipeline (lamp
mask, linearization, resolution, scoring) and how far apart minerals
actually are in 400-700 nm. It is NOT a validation with real samples nor
does it measure real-world accuracy: that would need a colour chart /
known paints with the instrument in hand.

It also includes **negative controls** (flat, noise, ramp, LED without
dividing) where nothing should be identified: it measures the false
positive rate of the "unknown" class.

Usage
---
    python scripts/benchmark.py                    # 200 samples
    python scripts/benchmark.py --n 500 --noise 0.02
    python scripts/benchmark.py --gamma 2.2 --cal-err 5 --json out.json
    python scripts/benchmark.py --fwhm 0            # do not degrade refs
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from espectro import library as lib                      # noqa: E402
from espectro import match as mtc                        # noqa: E402
from espectro.calib import led_model                     # noqa: E402
from espectro.library import degrade_resolution          # noqa: E402
from espectro.reflectance import (pseudo_reflectance,   # noqa: E402
                                  resample)

GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)
W = 1280


PEAK = 220.0


def measure(sig: np.ndarray, rng: np.random.Generator, noise: float,
            gamma: float, gain: float, full_scale: float = 255.0
            ) -> np.ndarray:
    """Optical signal · `gain` → 8-bit DN with noise and (optional) gamma.

    `gain` is COMMON to sample and bulb (same exposure, as the README
    requires): that way a dark material really has worse SNR instead of
    getting the same one as a bright one.  Noise combines readout
    (`noise`·220 DN) and photon (∝ √DN).
    """
    sig = np.asarray(sig, dtype=float)
    dn = np.clip(sig * gain, 0.0, full_scale)
    read = float(noise) * PEAK
    sigma = read * np.sqrt(1.0 + np.clip(dn, 0.0, None) / PEAK)
    if read > 0:
        dn = dn + rng.normal(0.0, 1.0, dn.size) * sigma
    dn = np.clip(dn, 0.0, full_scale)
    if gamma and abs(float(gamma) - 1.0) > 1e-9:
        dn = full_scale * (dn / full_scale) ** float(gamma)
    return np.clip(np.rint(dn), 0.0, full_scale)


def capture(s_sig: np.ndarray, l_sig: np.ndarray,
            rng: np.random.Generator, noise: float, gamma: float,
            stray: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Expose for the bulb (never saturates) and capture sample + bulb.

    `stray` is stray light / residual dark current in DN: it is ADDED to
    both captures, so it does NOT cancel out in the division and
    compresses the spectrum towards white — exactly like in a real
    instrument.
    """
    gain = PEAK / max(float(np.max(l_sig)), 1e-9)
    off = float(stray)
    return (measure(np.asarray(s_sig) * gain + off, rng, noise, gamma, 1.0),
            measure(np.asarray(l_sig) * gain + off, rng, noise, gamma, 1.0))


def cal_distortion(p: np.ndarray, cal_err: float,
                   rng: np.random.Generator) -> np.ndarray:
    """Smooth calibration error with absolute maximum `cal_err` nm."""
    if cal_err <= 0:
        return np.zeros_like(p)
    u = (p - p.mean()) / max(p.max() - p.min(), 1.0)
    a, b, c = rng.normal(size=3)
    d = a + b * u + c * (u * u - float(np.mean(u * u)))
    d = d - float(d.mean())
    m = float(np.max(np.abs(d)))
    return d * (float(cal_err) / m) if m > 0 else d


def simulate(spec: lib.Spectrum, rng: np.random.Generator, noise: float,
             gamma: float, cal_err: float, fwhm: float | None,
             stray: float = 0.0) -> np.ndarray:
    """Synthetic measurement of `spec` taken with the LED, already on the grid.

    The sample goes through the instrument line shape (Gaussian of FWHM
    `fwhm`), just like the references: that is what the sensor really
    sees.  Without it the sample comes out "sharper" than the library and
    the crossing is unfairly penalised.
    """
    p = np.arange(W, dtype=float)
    nm_true = 400.0 + p / (W - 1) * 300.0
    R = np.interp(nm_true, spec.nm, spec.reflectance,
                  left=float(spec.reflectance[0]),
                  right=float(spec.reflectance[-1]))
    lamp = led_model(nm_true)
    lamp = lamp / lamp.max() if lamp.max() > 0 else lamp
    # the detector integrates R·L through the ILS
    s_sig = degrade_resolution(nm_true, lamp * R, fwhm)
    l_sig = degrade_resolution(nm_true, lamp, fwhm)
    s_dn, l_dn = capture(s_sig, l_sig, rng, noise, gamma, stray)
    y = pseudo_reflectance(s_dn, l_dn, smooth=5, gamma=gamma,
                           full_scale=255.0)
    nm_cal = nm_true + cal_distortion(p, cal_err, rng)
    return resample(nm_cal, y, GRID)


NEGATIVES = ("flat", "noise", "ramp", "led")

# Score weight grid: w_color = 1 - w_sam - w_cont (always ≥0.10).
# Includes the default triple (0.20 / 0.10 / 0.70) as a reference.
WEIGHT_COMBOS = [(ws, wc, round(1.0 - ws - wc, 2))
                 for ws in (0.00, 0.10, 0.20, 0.30, 0.35, 0.40, 0.50, 0.60)
                 for wc in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60)
                 if 1.0 - ws - wc >= 0.10]


def score_of(comp: tuple, w_sam: float, w_cont: float,
             w_color: float) -> float:
    """Rebuild a candidate's score from its components.

    comp = (cos_SAM, r_continuum, color_term, band_penalty, name)
    — exactly the formula of `match.match()`, so the weight sweep does not
    walk the library again.
    """
    cos, r_cr, color, pen, _ = comp
    v = (w_sam * cos
         + w_cont * 0.5 * (r_cr + 1.0)
         + w_color * color) * pen
    return float(np.clip(v, 0.0, 1.0))


def negative(kind: str, rng: np.random.Generator, noise: float,
             gamma: float, fwhm: float | None, stray: float = 0.0
             ) -> np.ndarray:
    """Negative control: a curve that is NOT a material of the library."""
    p = np.arange(W, dtype=float)
    nm_true = 400.0 + p / (W - 1) * 300.0
    if kind == "flat":
        mat = np.full(W, 0.5)
    elif kind == "noise":
        mat = np.clip(0.5 + rng.normal(0.0, 0.12, W), 0.0, None)
    elif kind == "ramp":
        mat = 0.2 + 0.6 * (nm_true - 400.0) / 300.0
    else:                                   # "led": olvidarse de dividir
        mat = np.ones(W)
    lamp = led_model(nm_true)
    lamp = lamp / lamp.max() if lamp.max() > 0 else lamp
    s_dn, l_dn = capture(degrade_resolution(nm_true, lamp * mat, fwhm),
                         degrade_resolution(nm_true, lamp, fwhm),
                         rng, noise, gamma, stray)
    if kind == "led":
        y = s_dn / max(float(s_dn.max()), 1e-9)
    else:
        y = pseudo_reflectance(s_dn, l_dn, smooth=5, gamma=gamma,
                               full_scale=255.0)
    return resample(nm_true, y, GRID)


def components(r) -> tuple:
    """Weight-independent components of a MatchResult."""
    cos = float(np.cos(np.radians(r.sam_deg))) if np.isfinite(r.sam_deg) else 0.0
    r_cr = float(r.r_continuum) if np.isfinite(r.r_continuum) else 0.0
    color = (float(np.clip(1.0 - r.delta_e / 60.0, 0.0, 1.0))
             if np.isfinite(r.delta_e) else 0.0)
    pen = max(0.0, 1.0 - 0.5 * float(r.max_depth_diff))
    return (cos, r_cr, color, pen, r.spectrum.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="benchmark",
        description="Synthetic LOO validation of the identification pipeline")
    ap.add_argument("--n", type=int, default=200,
                    help="samples to evaluate (default 200)")
    ap.add_argument("--negatives", type=int, default=40,
                    help="negative controls (0 to disable)")
    ap.add_argument("--open-set", type=int, default=0,
                    help="K families removed from the library: checks that, "
                         "with the true material absent, the verdict does "
                         "NOT claim to identify it (0 = off)")
    ap.add_argument("--noise", type=float, default=0.01,
                    help="gaussian noise as a fraction of the peak (0.01 = 1%%)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="simulated camera gamma (2.2 = typical UVC)")
    ap.add_argument("--cal-err", type=float, default=0.0,
                    help="maximum calibration error in nm (0 = exact)")
    ap.add_argument("--stray", type=float, default=0.0,
                    help="residual stray light in DN (does not cancel in the "
                         "division; e.g. 5 = ~2%% of the peak)")
    ap.add_argument("--fwhm", type=float, default=lib.FWHM_DEFAULT,
                    help="instrument resolution in nm (0 = do not degrade)")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sweep", action="store_true",
                    help="print a sweep of the verdict thresholds")
    ap.add_argument("--sweep-weights", action="store_true",
                    help="print a sweep of the score weights "
                         "(w_sam / w_continuum / w_color)")
    ap.add_argument("--library", default=str(ROOT / "data" / "libraries"))
    ap.add_argument("--json", default=None, help="dump the result as JSON")
    args = ap.parse_args(argv)

    spectra = lib.load_library(args.library)
    if not spectra:
        print(f"ERROR: empty library at {args.library} "
              "→ python scripts/fetch_usgs.py", file=sys.stderr)
        return 2
    fwhm = None if args.fwhm <= 0 else args.fwhm
    paired = lib.to_grid(spectra, GRID, fwhm=fwhm)
    pool = [s for s, _ in paired]
    rng = np.random.default_rng(args.seed)

    n = min(max(1, args.n), len(pool))
    picks = rng.choice(len(pool), size=n, replace=False)
    # the weight sweep needs EVERY candidate, not just the top
    top_k = 10 ** 6 if args.sweep_weights else max(args.top, 10)

    exact1 = exact3 = fam1 = fam3 = 0
    verdicts: Counter[str] = Counter()
    right_when_identified = identified = 0
    misses: list[str] = []
    records: list[dict] = []
    negatives_rec: list[dict] = []
    comps_all: list[tuple[str, list[tuple]]] = []

    for i in picks:
        spec = pool[int(i)]
        y = simulate(spec, rng, args.noise, args.gamma, args.cal_err, fwhm,
                     args.stray)
        out = mtc.match(GRID, y, paired, GRID, top=top_k)
        verdicts[out.verdict.label] += 1
        if args.sweep_weights:
            comps_all.append((spec.name, [components(r) for r in out]))
        if not out:
            misses.append(f"{spec.name} [sin candidatos]")
            continue
        names = [r.spectrum.name for r in out]
        fam = mtc.family_key
        exact = names[0] == spec.name
        family = fam(names[0]) == fam(spec.name)
        records.append({"s1": out.verdict.score, "margin": out.verdict.margin,
                        "exact": exact, "family": family})
        exact1 += exact
        exact3 += spec.name in names[:3]
        fam1 += family
        fam3 += any(fam(x) == fam(spec.name) for x in names[:3])
        if out.verdict.label == "identified":
            identified += 1
            right_when_identified += exact
        if not exact:
            misses.append(f"{spec.name} → {names[0]}")

    # --- negative controls: nothing should be identified
    neg_total = neg_identified = neg_uncertain = 0
    kinds = Counter()
    for k in range(max(0, args.negatives)):
        kind = NEGATIVES[k % len(NEGATIVES)]
        y = negative(kind, rng, args.noise, args.gamma, fwhm, args.stray)
        out = mtc.match(GRID, y, paired, GRID, top=5)
        neg_total += 1
        negatives_rec.append({"s1": out.verdict.score,
                              "margin": out.verdict.margin,
                              "kind": kind})
        if out.verdict.label == "identified":
            neg_identified += 1
            kinds[f"{kind}→false positive"] += 1
        elif out.verdict.label == "uncertain":
            neg_uncertain += 1

    # --- open-set: the true family is NOT in the library
    os_total = os_ident = os_unc = os_unk = 0
    if args.open_set > 0:
        by_fam: dict[str, list] = {}
        for p in paired:
            by_fam.setdefault(mtc.family_key(p.spectrum.name), []).append(p)
        fams = sorted(by_fam)
        pick = rng.choice(len(fams),
                          size=min(args.open_set, len(fams)), replace=False)
        for k in pick:
            fam = fams[int(k)]
            rest = [p for p in paired
                    if mtc.family_key(p.spectrum.name) != fam]
            target = by_fam[fam][0]
            y = simulate(target.spectrum, rng, args.noise, args.gamma,
                         args.cal_err, fwhm, args.stray)
            out = mtc.match(GRID, y, rest, GRID, top=5)
            lab = out.verdict.label
            os_total += 1
            os_ident += lab == "identified"
            os_unc += lab == "uncertain"
            os_unk += lab == "unknown"

    pct = lambda a, b: 100.0 * a / b if b else 0.0
    res = {
        "n": int(n),
        "top1_exact": round(pct(exact1, n), 2),
        "top3_exact": round(pct(exact3, n), 2),
        "top1_family": round(pct(fam1, n), 2),
        "top3_family": round(pct(fam3, n), 2),
        "verdicts": dict(verdicts),
        "identified": identified,
        "precision_when_identified": round(pct(right_when_identified,
                                               identified), 2),
        "negatives": {"n": neg_total, "identified": neg_identified,
                      "uncertain": neg_uncertain,
                      "rate_identified": round(pct(neg_identified,
                                                   neg_total), 2)},
        "open_set": {"n": os_total, "identified": os_ident,
                     "uncertain": os_unc, "unknown": os_unk,
                     "rate_identified": round(pct(os_ident, os_total), 2)},
        "config": {"noise": args.noise, "gamma": args.gamma,
                   "cal_err_nm": args.cal_err, "stray_dn": args.stray,
                   "fwhm": fwhm, "seed": args.seed, "library": args.library},
    }

    print(f"Synthetic LOO benchmark · {n} samples · library "
          f"{len(paired)} candidates")
    print(f"  config: noise={args.noise:.3f} gamma={args.gamma:g} "
          f"cal_err={args.cal_err:g} nm stray={args.stray:g} DN "
          f"fwhm={fwhm} seed={args.seed}")
    print(f"  top-1 exact name  : {res['top1_exact']:5.1f} %")
    print(f"  top-3 exact name  : {res['top3_exact']:5.1f} %")
    print(f"  top-1 family      : {res['top1_family']:5.1f} %")
    print(f"  top-3 family      : {res['top3_family']:5.1f} %")
    print("  verdicts          : "
          + " · ".join(f"{k} {v} ({pct(v, n):.0f}%)"
                       for k, v in verdicts.most_common()))
    print(f"  correct when it says 'identified': "
          f"{res['precision_when_identified']:.1f} % "
          f"({right_when_identified}/{identified})")
    if neg_total:
        print(f"  negative controls: {neg_total} · false positives "
              f"(identified) = {neg_identified} "
              f"({res['negatives']['rate_identified']:.1f}%) · "
              f"uncertain = {neg_uncertain}")
        for k, v in kinds.most_common():
            print(f"    · {k}: {v}")
    if os_total:
        print(f"  open-set ({os_total} families removed from the library): "
              f"confidently identified something = {os_ident} "
              f"({pct(os_ident, os_total):.1f} %) · uncertain = {os_unc} · "
              f"unknown = {os_unk} "
              f"({pct(os_unk + os_unc, os_total):.1f} % abstained)")

    sweep = []
    if args.sweep and records:
        print("\n  verdict threshold sweep "
              "(min_score × min_margin)")
        print("   score  margin | ident%  family acc  exact acc  "
              "FP negatives")
        for ms in (0.55, 0.65, 0.70, 0.75, 0.80, 0.85):
            for mm in (0.02, 0.04, 0.05, 0.06, 0.08, 0.10):
                sel = [r for r in records
                       if r["s1"] >= ms and r["margin"] >= mm]
                neg = [r for r in negatives_rec
                       if r["s1"] >= ms and r["margin"] >= mm]
                prec_f = pct(sum(r["family"] for r in sel), len(sel))
                prec_e = pct(sum(r["exact"] for r in sel), len(sel))
                fp = pct(len(neg), len(negatives_rec)) if negatives_rec else 0.0
                row = {"min_score": ms, "min_margin": mm,
                       "identified_pct": round(pct(len(sel), n), 1),
                       "precision_family": round(prec_f, 1),
                       "precision_exact": round(prec_e, 1),
                       "false_positive_pct": round(fp, 1)}
                sweep.append(row)
                print(f"   {ms:4.2f}   {mm:4.2f}   | "
                      f"{row['identified_pct']:5.1f}  {prec_f:13.1f}  "
                      f"{prec_e:13.1f}  {fp:12.1f}")
        res["sweep"] = sweep
    if args.sweep_weights and comps_all:
        fam = mtc.family_key
        rows = []
        for ws, wc, wcol in WEIGHT_COMBOS:
            e1 = f1 = f3 = 0
            for target, comps in comps_all:
                ranked = sorted(comps, key=lambda c: -score_of(c, ws, wc, wcol))
                names = [c[4] for c in ranked]
                e1 += names[0] == target
                f1 += fam(names[0]) == fam(target)
                f3 += any(fam(x) == fam(target) for x in names[:3])
            rows.append((pct(f1, n), pct(e1, n), pct(f3, n), ws, wc, wcol))
        rows.sort(reverse=True)
        print("\n  SCORE WEIGHT sweep (w_sam · w_continuum · w_color)")
        print("  w_sam w_cont  w_col | top-1 exact  top-1 family  "
              "top-3 family")
        for f1, e1, f3, ws, wc, wcol in rows:
            mark = "  <- default" if (ws, wc, wcol) == (0.20, 0.10, 0.70) \
                else ("  <- before" if (ws, wc, wcol) == (0.35, 0.40, 0.25)
                      else "")
            print(f"  {ws:.2f}  {wc:.2f}  {wcol:.2f} | {e1:11.1f}  "
                  f"{f1:13.1f}  {f3:13.1f}{mark}")
        res["sweep_weights"] = [
            {"w_sam": ws, "w_cont": wc, "w_color": wcol,
             "top1_exact": round(e1, 1), "top1_family": round(f1, 1),
             "top3_family": round(f3, 1)}
            for f1, e1, f3, ws, wc, wcol in rows]
        b = rows[0]
        print(f"  best: w = {b[3]:.2f} / {b[4]:.2f} / {b[5]:.2f}  "
              f"(top-1 family {b[0]:.1f} %, exact {b[1]:.1f} %)")

    if misses:
        print(f"  top-1 misses ({len(misses)}):")
        for line in misses[:10]:
            print(f"    · {line}")
        if len(misses) > 10:
            print(f"    … and {len(misses) - 10} more")

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        res["misses"] = misses[:200]
        out_path.write_text(json.dumps(res, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"  JSON en {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
