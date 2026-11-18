"""Calibración píxel -> longitud de onda (nm).

Cuatro modos, por orden de fiabilidad:

1. LED blanco (`--led`, POR DEFECTO): tu lámpara es un LED blanco, cuyo
   espectro tiene dos marcas muy estables: el **pico de la bomba azul
   InGaN (~450 nm)** y el **hump del fósforo (~560 nm)**.  Se detectan los
   dos picos en la foto, se usan como anclas y se refina con una plantilla
   de LED (correlación con Planck-free gaussiano + respuesta del sensor).

2. Anclas (`--anchor px nm ...`): marcas que identificas tú. Con 2 anclas
   recta, con 3 parábola.  Es lo más preciso si tienes lámpara de
   mercurio/neón (picos a 435.8, 546.1, 577/579 nm).

3. Rango asumido (`--range 400 700`): lineal entre el primer y el último
   píxel encendido.  Rápido pero solo orientativo.

4. Cuerpo negro (`--blackbody [T]`): SOLO para bombillas de filamento
   (tungsteno 2700-3000 K).  No sirve para LED: un LED no emite como un
   cuerpo negro (por eso ahora el modo por defecto es `led`).

El resultado se guarda en JSON: coeficientes de un polinomio
nm(p) = c[0]*p^k + ... + c[-1].
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


# Respuesta cuántica aproximada de un sensor CMOS con filtro IR-cut:
# gaussiana ancha alrededor de 550 nm.
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
    """Plantilla de un LED blanco: bomba azul InGaN + fósforo + cola roja.

    Picos nominales: 450 nm (bomb) y 560 nm (fósforo).  Los LED reales
    varían ±10-15 nm; la refinación por correlación absorbe esa variación.
    """
    nm = np.asarray(nm, dtype=float)
    blue = _gauss(nm, 450.0, 17.0)
    phosphor = 0.85 * _gauss(nm, 560.0, 75.0)
    red_tail = 0.30 * _gauss(nm, 645.0, 95.0)
    return blue + phosphor + red_tail


def led_model(nm: np.ndarray) -> np.ndarray:
    """LED observado por el sensor: plantilla · respuesta cuántica."""
    return led_template(nm) * qe_model(nm)


def poly_from_anchors(anchors: list[tuple[float, float]]) -> np.ndarray:
    """anchors = [(pixel, nm), ...] -> coeficientes np.polyfit."""
    if len(anchors) < 2:
        raise ValueError("hacen falta al menos 2 anclas (pixel, nm)")
    px = np.array([a[0] for a in anchors], dtype=float)
    nm = np.array([a[1] for a in anchors], dtype=float)
    deg = 1 if len(anchors) == 2 else 2
    return np.polyfit(px, nm, deg)


def linear_from_range(p0: float, p1: float, nm0: float, nm1: float) -> np.ndarray:
    if p1 <= p0:
        raise ValueError("rango de píxeles inválido")
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
                  da0_span=45.0, s_span=0.20, c2_span=1.5e-5,
                  iters=2, n_da0=13, n_s=13, n_c2=9):
    """Maximiza corr(y, model_fn(nm)) ajustando offset, escala y curvatura
    del mapeo nm(p) = a0 + a1·p + c2·u².  Devuelve (poly, correlación)."""
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
        # malla geométrica: cada iteración afina alrededor del mejor
        da0_span = max(1.0, da0_span * 0.35)
        s_span = max(0.004, s_span * 0.35)
    r, a0f, a1f, c2f = best
    if r <= 0:
        return np.asarray(coeffs, dtype=float), float("-inf")
    d = c2f / span**2
    return np.array([d, a1f - 2.0 * d * mid, a0f + d * mid**2]), float(r)


def detect_led_peaks(pixel: np.ndarray, intensity: np.ndarray
                     ) -> tuple[float, float] | None:
    """Localiza (pico azul, pico de fósforo) en píxeles.

    Estrategia: pico global → máscara → segundo pico → validar que entre
    ambos hay valle (si no, son lóbulos del mismo hump y se descarta).
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
        return None                      # sin valle: es un solo hump
    return float(pixel[p1]), float(pixel[p2])


def refine_led(coeffs: np.ndarray, pixel: np.ndarray,
               intensity: np.ndarray) -> tuple[np.ndarray, float]:
    """Refina la calibración ajustando la plantilla de LED blanco."""
    p, y = _prep(pixel, intensity)
    if p is None:
        return coeffs, float("nan")
    return _refine_shape(coeffs, p, y, led_model, iters=4)


def refine_blackbody(
    coeffs: np.ndarray,
    pixel: np.ndarray,
    intensity: np.ndarray,
    T_range: tuple[float, float] = (2300.0, 3500.0),
    c2_span: float = 1.5e-5,
    steps: int = 61,
) -> tuple[np.ndarray, float, float]:
    """Refina con la curva Planck·QE: prueba temperaturas y para cada una
    ajusta offset/escala/curvatura.  SOLO para bombillas de filamento.

    Devuelve (coeffs_nuevos, T, correlación).
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
