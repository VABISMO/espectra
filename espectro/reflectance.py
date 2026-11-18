"""Conversión a pseudo-reflectancia y re-muestreo.

Con una bombilla de referencia se divide el espectro de la muestra entre el
de la bombilla: así se cancelan la lámpara, la transmisión óptica del tubo
y la respuesta del sensor.  El resultado NO es reflectancia absoluta
(falta el blanco de referencia), pero conserva la forma espectral, que es
lo que usamos para comparar con las bibliotecas.
"""
from __future__ import annotations

import numpy as np

from .image import moving_average


def pseudo_reflectance(
    sample: np.ndarray,
    lamp: np.ndarray,
    smooth: int = 5,
    floor_frac: float = 0.03,
) -> np.ndarray:
    """R(p) = muestra(p) / bombilla(p) con máscara donde la bombilla es débil."""
    s = np.asarray(sample, dtype=float)
    l = np.asarray(lamp, dtype=float)
    if s.shape != l.shape:
        raise ValueError(f"tamaños distintos: {s.shape} vs {l.shape}")
    thr = max(l.max() * floor_frac, 1e-12)
    ratio = np.divide(s, l, out=np.full_like(s, np.nan), where=l > thr)
    ratio = np.clip(np.nan_to_num(ratio, nan=0.0), 0.0, 3.0)
    return moving_average(ratio, smooth)


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
    """Interpola linealmente a una rejilla común (NaN fuera del rango)."""
    nm = np.asarray(nm, dtype=float)
    y = np.asarray(y, dtype=float)
    order = np.argsort(nm)
    nm, y = nm[order], y[order]
    out = np.interp(grid, nm, y, left=np.nan, right=np.nan)
    # puntos duplicados en nm -> media
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
