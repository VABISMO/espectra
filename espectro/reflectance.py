"""Conversión a pseudo-reflectancia y re-muestreo.

Con una bombilla de referencia se divide el espectro de la muestra entre el
de la bombilla: así se cancelan la lámpara, la transmisión óptica del tubo
y la respuesta del sensor.  El resultado NO es reflectancia absoluta
(falta el blanco de referencia), pero conserva la forma espectral, que es
lo que usamos para comparar con las bibliotecas.

Dos matices importantes:

* **Gamma**: si la cámara de 8 bits entrega valores con gamma aplicada,
  hay que linealizarlos ANTES de dividir (`gamma=2.2`); en caso contrario
  la división devuelve (s/l)^γ y las bandas de absorción quedan
  comprimidas.  Desactivado por defecto (`gamma=1.0`).

* **Zonas sin lámpara**: donde la bombilla es débil la razón no tiene
  sentido y se devuelve NaN, nunca 0.  Un cero artificial crearía una
  banda de absorción falsa en el rojo y desplazaría el color.  El NaN
  viaja hasta `match()`, que solo evalúa los puntos válidos.
"""
from __future__ import annotations

import numpy as np


def linearize(y: np.ndarray, gamma: float = 1.0,
              full_scale: float | None = None) -> np.ndarray:
    """Deshace la codificación gamma de una cámara de 8 bits.

    gamma      : exponente aplicado por la cámara (1.0 = sin efecto).
    full_scale : DN que corresponde al blanco saturado (255 en un canal,
                 765 en R+G+B).  Si no se da, se usa el máximo de `y`.
    """
    if gamma is None or abs(float(gamma) - 1.0) < 1e-9:
        return np.asarray(y, dtype=float)
    g = float(gamma)
    if g <= 0:
        raise ValueError("gamma debe ser > 0")
    y = np.asarray(y, dtype=float)
    fs = float(full_scale) if full_scale else max(float(np.nanmax(y)), 1e-12)
    return np.power(np.clip(y, 0.0, fs) / fs, 1.0 / g) * fs


def _smooth_nan(y: np.ndarray, window: int | None) -> np.ndarray:
    """Media móvil que respeta los NaN (no arrastra ceros a zonas válidas)."""
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
    """R(p) = muestra(p) / bombilla(p), con NaN donde la bombilla es débil.

    `floor_frac` es la fracción del máximo de la bombilla por debajo de la
    cual la razón es ruido puro (colas del LED por encima de ~680 nm, valle
    azul-verde ~470-490 nm): esos puntos se marcan NaN en lugar de
    dividirse o rellenarse con cero.  La máscara se decide con la señal
    CRUDA de la bombilla (el S/N no depende de la gamma).
    """
    raw_l = np.asarray(lamp, dtype=float)
    thr = max(float(np.nanmax(raw_l)) * floor_frac, 1e-12)
    mask = raw_l > thr

    s = linearize(sample, gamma, full_scale)
    l = linearize(lamp, gamma, full_scale)
    if s.shape != l.shape:
        raise ValueError(f"tamaños distintos: {s.shape} vs {l.shape}")
    ratio = np.divide(s, l, out=np.full_like(s, np.nan),
                      where=mask & (l > 0.0))
    ratio = np.clip(ratio, 0.0, 3.0)        # NaN se propaga a través de clip
    return _smooth_nan(ratio, smooth)


def fill_nan(y: np.ndarray) -> np.ndarray:
    """Rellena NaN interpolando linealmente entre los puntos válidos.

    Solo para visualización / JSON: el motor (`match`) trabaja con la
    versión que conserva los NaN y descarta esos puntos.
    """
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    if ok.all():
        return y
    if not ok.any():
        return np.zeros_like(y)
    x = np.arange(y.size, dtype=float)
    return np.interp(x, x[ok], y[ok])


def normalize(y: np.ndarray, mode: str = "max") -> np.ndarray:
    y = np.asarray(y, dtype=float)
    if mode == "none":
        return y
    if mode == "max":
        m = np.nanmax(y)
        return y / m if m > 0 else y
    if mode == "unit":                       # media 0, norma 1 (para SAM)
        m = np.nanmean(y)
        y = y - m
        n = np.linalg.norm(y)
        return y / n if n > 0 else y
    raise ValueError("mode debe ser 'none', 'max' o 'unit'")


def resample(nm: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Re-muestrea a `grid` promediando dentro de ±medio paso.

    Promediar (y no muestrear un solo píxel) es lo que hace el detector
    sobre el bin de la rejilla: sin eso el ruido de la cámara pasa entero
    a la curva y el cruce con la biblioteca se resiente.  Los NaN (lámpara
    débil) se ignoran dentro del bin; si no queda ningún punto válido el
    resultado es NaN.
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
        # fuera del rango de la calibración
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
        # ventana local de ±1 paso de la rejilla
        lo = np.searchsorted(nm, g - 1.0)
        hi = np.searchsorted(nm, g + 1.0, side="right")
        if hi <= lo:
            out[i] = y[j] if abs(nm[j] - g) < 50 else np.nan
        else:
            out[i] = float(np.mean(y[lo:hi]))
    return out
