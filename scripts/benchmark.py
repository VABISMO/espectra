#!/usr/bin/env python
"""Benchmark de identificación: validación sintética leave-one-out.

Lo que mide
-----------
Para cada espectro de la biblioteca se **simula una medición real** de ese
mismo material (LED blanco × reflectancia, ruido, cuantización de 8 bits,
gamma opcional y error de calibración opcional) y se comprueba si el
material aparece en el top-1 / top-3 del cruce contra la biblioteca.

Es un benchmark **cerrado y sintético**: valida el pipeline (máscara de
lámpara, linealización, resolución, scoring) y cuánto se separan los
minerales entre sí en 400-700 nm.  NO es una validación con muestras
reales ni mide la exactitud en el mundo tangible: para eso haría falta
una carta de color / pinturas conocidas con el instrumento en la mano.

Además incluye **controles negativos** (plano, ruido, rampa, LED sin
dividir) para el que no debería identificarse nada: mide la tasa de
falsos positivos de la clase "desconocido".

Uso
---
    python scripts/benchmark.py                    # 200 muestras
    python scripts/benchmark.py --n 500 --noise 0.02
    python scripts/benchmark.py --gamma 2.2 --cal-err 5 --json out.json
    python scripts/benchmark.py --fwhm 0            # sin degradar referencias
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
    """Señal óptica · `gain` → DN de 8 bits con ruido y (opcional) gamma.

    `gain` es COMÚN para muestra y bombilla (misma exposición, como exige
    el README): así un material oscuro tiene realmente peor SNR en vez de
    recibir la misma que uno brillante.  El ruido combina lectura
    (`noise`·220 DN) y fotónico (∝ √DN).
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
    """Expone para la bombilla (nunca satura) y captura muestra + bombilla.

    `stray` es luz parásita / corriente oscura residual en DN: se AÑADE a
    ambas capturas, así que NO se cancela en la división y comprime el
    espectro hacia el blanco — exactamente como en el instrumento real.
    """
    gain = PEAK / max(float(np.max(l_sig)), 1e-9)
    off = float(stray)
    return (measure(np.asarray(s_sig) * gain + off, rng, noise, gamma, 1.0),
            measure(np.asarray(l_sig) * gain + off, rng, noise, gamma, 1.0))


def cal_distortion(p: np.ndarray, cal_err: float,
                   rng: np.random.Generator) -> np.ndarray:
    """Error suave de calibración con máximo absoluto `cal_err` nm."""
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
    """Muestra sintética de `spec` medida con el LED, ya en la rejilla.

    La muestra pasa por la función de línea del instrumento (gaussiana de
    FWHM `fwhm`), igual que las referencias: es lo que ve el sensor de
    verdad.  Sin esto, la muestra sale "más nítida" que la biblioteca y
    el cruce queda penalizado de forma injusta.
    """
    p = np.arange(W, dtype=float)
    nm_true = 400.0 + p / (W - 1) * 300.0
    R = np.interp(nm_true, spec.nm, spec.reflectance,
                  left=float(spec.reflectance[0]),
                  right=float(spec.reflectance[-1]))
    lamp = led_model(nm_true)
    lamp = lamp / lamp.max() if lamp.max() > 0 else lamp
    # el detector integra R·L a través de la ILS
    s_sig = degrade_resolution(nm_true, lamp * R, fwhm)
    l_sig = degrade_resolution(nm_true, lamp, fwhm)
    s_dn, l_dn = capture(s_sig, l_sig, rng, noise, gamma, stray)
    y = pseudo_reflectance(s_dn, l_dn, smooth=5, gamma=gamma,
                           full_scale=255.0)
    nm_cal = nm_true + cal_distortion(p, cal_err, rng)
    return resample(nm_cal, y, GRID)


NEGATIVES = ("flat", "noise", "ramp", "led")

# Rejilla de pesos del score: w_color = 1 - w_sam - w_cont (siempre ≥0.10).
# Incluye el par por defecto (0.20 / 0.10 / 0.70) como referencia.
WEIGHT_COMBOS = [(ws, wc, round(1.0 - ws - wc, 2))
                 for ws in (0.00, 0.10, 0.20, 0.30, 0.35, 0.40, 0.50, 0.60)
                 for wc in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60)
                 if 1.0 - ws - wc >= 0.10]


def score_of(comp: tuple, w_sam: float, w_cont: float,
             w_color: float) -> float:
    """Recompone el score de un candidato a partir de sus componentes.

    comp = (cos_SAM, r_continuo, color_term, penalización_de_banda, nombre)
    — exactamente la fórmula de `match.match()`, así que el barrido de
    pesos no vuelve a recorrer la biblioteca.
    """
    cos, r_cr, color, pen, _ = comp
    v = (w_sam * cos
         + w_cont * 0.5 * (r_cr + 1.0)
         + w_color * color) * pen
    return float(np.clip(v, 0.0, 1.0))


def negative(kind: str, rng: np.random.Generator, noise: float,
             gamma: float, fwhm: float | None, stray: float = 0.0
             ) -> np.ndarray:
    """Control negativo: una curva que NO es un material de la biblioteca."""
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
    """Componentes independientes de los pesos de un MatchResult."""
    cos = float(np.cos(np.radians(r.sam_deg))) if np.isfinite(r.sam_deg) else 0.0
    r_cr = float(r.r_continuum) if np.isfinite(r.r_continuum) else 0.0
    color = (float(np.clip(1.0 - r.delta_e / 60.0, 0.0, 1.0))
             if np.isfinite(r.delta_e) else 0.0)
    pen = max(0.0, 1.0 - 0.5 * float(r.max_depth_diff))
    return (cos, r_cr, color, pen, r.spectrum.name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="benchmark",
        description="Validación sintética LOO del pipeline de identificación")
    ap.add_argument("--n", type=int, default=200,
                    help="muestras a evaluar (por defecto 200)")
    ap.add_argument("--negatives", type=int, default=40,
                    help="controles negativos (0 para desactivar)")
    ap.add_argument("--noise", type=float, default=0.01,
                    help="ruido gaussiano como fracción del pico (0.01 = 1%%)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="gamma de la cámara simulada (2.2 = típica UVC)")
    ap.add_argument("--cal-err", type=float, default=0.0,
                    help="error máximo de calibración en nm (0 = exacto)")
    ap.add_argument("--stray", type=float, default=0.0,
                    help="luz parásita residual en DN (no se cancela en la "
                         "división; p.ej. 5 = ~2%% del pico)")
    ap.add_argument("--fwhm", type=float, default=lib.FWHM_DEFAULT,
                    help="resolución del instrumento en nm (0 = sin degradar)")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sweep", action="store_true",
                    help="imprimir un barrido de los umbrales del veredicto")
    ap.add_argument("--sweep-weights", action="store_true",
                    help="imprimir un barrido de los pesos del score "
                         "(w_sam / w_continuo / w_color)")
    ap.add_argument("--library", default=str(ROOT / "data" / "libraries"))
    ap.add_argument("--json", default=None, help="volcar resultado en JSON")
    args = ap.parse_args(argv)

    spectra = lib.load_library(args.library)
    if not spectra:
        print(f"ERROR: biblioteca vacía en {args.library} "
              "→ python scripts/fetch_usgs.py", file=sys.stderr)
        return 2
    fwhm = None if args.fwhm <= 0 else args.fwhm
    paired = lib.to_grid(spectra, GRID, fwhm=fwhm)
    pool = [s for s, _ in paired]
    rng = np.random.default_rng(args.seed)

    n = min(max(1, args.n), len(pool))
    picks = rng.choice(len(pool), size=n, replace=False)
    # con barrido de pesos necesitamos TODOS los candidatos, no solo el top
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

    # --- controles negativos: no deberían identificarse
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
            kinds[f"{kind}→falso positivo"] += 1
        elif out.verdict.label == "uncertain":
            neg_uncertain += 1

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
        "config": {"noise": args.noise, "gamma": args.gamma,
                   "cal_err_nm": args.cal_err, "stray_dn": args.stray,
                   "fwhm": fwhm, "seed": args.seed, "library": args.library},
    }

    print(f"Benchmark LOO sintético · {n} muestras · biblioteca "
          f"{len(paired)} candidatos")
    print(f"  config: ruido={args.noise:.3f} gamma={args.gamma:g} "
          f"error_cal={args.cal_err:g} nm luz_parásita={args.stray:g} DN "
          f"fwhm={fwhm} semilla={args.seed}")
    print(f"  top-1 exacto      : {res['top1_exact']:5.1f} %")
    print(f"  top-3 exacto      : {res['top3_exact']:5.1f} %")
    print(f"  top-1 misma familia: {res['top1_family']:5.1f} %")
    print(f"  top-3 misma familia: {res['top3_family']:5.1f} %")
    print("  veredictos        : "
          + " · ".join(f"{k} {v} ({pct(v, n):.0f}%)"
                       for k, v in verdicts.most_common()))
    print(f"  acierto cuando dice 'identified': "
          f"{res['precision_when_identified']:.1f} % "
          f"({right_when_identified}/{identified})")
    if neg_total:
        print(f"  controles negativos: {neg_total} · falsos positivos "
              f"(identified) = {neg_identified} "
              f"({res['negatives']['rate_identified']:.1f}%) · "
              f"uncertain = {neg_uncertain}")
        for k, v in kinds.most_common():
            print(f"    · {k}: {v}")

    sweep = []
    if args.sweep and records:
        print("\n  barrido de umbrales del veredicto "
              "(min_score × min_margin)")
        print("   score  margen | ident%  acierto familia  acierto exacto  "
              "FP negativos")
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
        print("\n  barrido de PESOS del score (w_sam · w_continuo · w_color)")
        print("  w_sam w_cont  w_col | top-1 exacto  top-1 familia  "
              "top-3 familia")
        for f1, e1, f3, ws, wc, wcol in rows:
            mark = "  <- por defecto" if (ws, wc, wcol) == (0.20, 0.10, 0.70) \
                else ("  <- antes" if (ws, wc, wcol) == (0.35, 0.40, 0.25)
                      else "")
            print(f"  {ws:.2f}  {wc:.2f}  {wcol:.2f} | {e1:11.1f}  "
                  f"{f1:13.1f}  {f3:13.1f}{mark}")
        res["sweep_weights"] = [
            {"w_sam": ws, "w_cont": wc, "w_color": wcol,
             "top1_exact": round(e1, 1), "top1_family": round(f1, 1),
             "top3_family": round(f3, 1)}
            for f1, e1, f3, ws, wc, wcol in rows]
        b = rows[0]
        print(f"  mejor: w = {b[3]:.2f} / {b[4]:.2f} / {b[5]:.2f}  "
              f"(top-1 familia {b[0]:.1f} %, exacto {b[1]:.1f} %)")

    if misses:
        print(f"  fallos top-1 ({len(misses)}):")
        for line in misses[:10]:
            print(f"    · {line}")
        if len(misses) > 10:
            print(f"    … y {len(misses) - 10} más")

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
