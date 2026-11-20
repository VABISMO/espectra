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
            rng: np.random.Generator, noise: float, gamma: float
            ) -> tuple[np.ndarray, np.ndarray]:
    """Expone para la bombilla (nunca satura) y captura muestra + bombilla."""
    gain = PEAK / max(float(np.max(l_sig)), 1e-9)
    return (measure(s_sig, rng, noise, gamma, gain),
            measure(l_sig, rng, noise, gamma, gain))


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
             gamma: float, cal_err: float, fwhm: float | None
             ) -> np.ndarray:
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
    s_dn, l_dn = capture(s_sig, l_sig, rng, noise, gamma)
    y = pseudo_reflectance(s_dn, l_dn, smooth=5, gamma=gamma,
                           full_scale=255.0)
    nm_cal = nm_true + cal_distortion(p, cal_err, rng)
    return resample(nm_cal, y, GRID)


NEGATIVES = ("flat", "noise", "ramp", "led")


def negative(kind: str, rng: np.random.Generator, noise: float,
             gamma: float, fwhm: float | None) -> np.ndarray:
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
                         rng, noise, gamma)
    if kind == "led":
        y = s_dn / max(float(s_dn.max()), 1e-9)
    else:
        y = pseudo_reflectance(s_dn, l_dn, smooth=5, gamma=gamma,
                               full_scale=255.0)
    return resample(nm_true, y, GRID)


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
    ap.add_argument("--fwhm", type=float, default=lib.FWHM_DEFAULT,
                    help="resolución del instrumento en nm (0 = sin degradar)")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sweep", action="store_true",
                    help="imprimir un barrido de los umbrales del veredicto")
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

    exact1 = exact3 = fam1 = fam3 = 0
    verdicts: Counter[str] = Counter()
    right_when_identified = identified = 0
    misses: list[str] = []
    records: list[dict] = []
    negatives_rec: list[dict] = []

    for i in picks:
        spec = pool[int(i)]
        y = simulate(spec, rng, args.noise, args.gamma, args.cal_err, fwhm)
        out = mtc.match(GRID, y, paired, GRID, top=max(args.top, 10))
        verdicts[out.verdict.label] += 1
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
        y = negative(kind, rng, args.noise, args.gamma, fwhm)
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
                   "cal_err_nm": args.cal_err, "fwhm": fwhm,
                   "seed": args.seed, "library": args.library},
    }

    print(f"Benchmark LOO sintético · {n} muestras · biblioteca "
          f"{len(paired)} candidatos")
    print(f"  config: ruido={args.noise:.3f} gamma={args.gamma:g} "
          f"error_cal={args.cal_err:g} nm fwhm={fwhm} semilla={args.seed}")
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
