"""Colorimetría: reflectancia → color CIE Lab (bajo D65) y distancia ΔE.

Permite comparar el color que "se ve" en el espectro con el de cada
material de la biblioteca: en el visible (400-700 nm) el color es una
señal muy discriminante y complementa a las bandas de absorción.

Datos incluidos en `espectro/data/`:
  * cmf_cie1931_2deg.csv  — funciones de igualación CIE 1931 2º
  * illuminant_d65.csv    — SPD del iluminante D65
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=4)
def _load_tables() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cmf = np.loadtxt(DATA / "cmf_cie1931_2deg.csv", delimiter=",",
                     comments="#", skiprows=0)
    d65 = np.loadtxt(DATA / "illuminant_d65.csv", delimiter=",",
                     comments="#", skiprows=0)
    # los comentarios '#' los maneja skiprows de np.loadtxt con comments=
    return (cmf[:, 0], cmf[:, 1:4], d65[:, 0], d65[:, 1])


def _integrate(wl_cmf: np.ndarray, values: np.ndarray, wl: np.ndarray,
               f: np.ndarray) -> np.ndarray:
    """∫ f(λ)·values(λ) dλ alineando rejillas (f se interpola a wl_cmf)."""
    fi = np.interp(wl_cmf, wl, f, left=0.0, right=0.0)
    return np.trapezoid(values * fi[:, None] if values.ndim > 1 else values * fi,
                        wl_cmf, axis=0) if values.ndim > 1 else float(
        np.trapezoid(values * fi, wl_cmf))


def lab_of_spectrum(nm: np.ndarray, reflectance: np.ndarray) -> tuple[float, float, float]:
    """Color CIE L*a*b* (D65) de una reflectancia relativa."""
    cmf_wl, cmf, d65_wl, spd = _load_tables()
    # rejilla común 380-780 nm
    grid = cmf_wl[(cmf_wl >= 380) & (cmf_wl <= 780)]
    xbar, ybar, zbar = (np.interp(grid, cmf_wl, cmf[:, i])
                        for i in range(3))
    s = np.interp(grid, d65_wl, spd, left=0.0, right=0.0)
    r = np.interp(grid, nm, reflectance, left=np.nan, right=np.nan)
    r = np.nan_to_num(r, nan=0.0)
    r = np.clip(r, 0.0, 1.0)

    def tri(v: np.ndarray) -> np.ndarray:
        return np.trapezoid(s * r * v, grid)

    X, Y, Z = tri(xbar), tri(ybar), tri(zbar)
    Xn, Yn, Zn = (float(np.trapezoid(s * v, grid))
                  for v in (xbar, ybar, zbar))

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(X / Xn), f(Y / Yn), f(Z / Zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(lab1: tuple[float, float, float],
            lab2: tuple[float, float, float]) -> float:
    """Distancia ΔE*ab (CIELAB) — 0 = mismo color, >10 = muy distinto."""
    return float(np.linalg.norm(np.array(lab1) - np.array(lab2)))


def lab_to_hex(lab: tuple[float, float, float]) -> str:
    """Convierte Lab → color sRGB '#rrggbb' (aproximado, para mostrar)."""
    L, a, b = lab

    def finv(t: float) -> float:
        return t ** 3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)

    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    # punto blanco D65
    Xn, Yn, Zn = 0.95047, 1.0, 1.08883
    X, Y, Z = finv(fx) * Xn, finv(fy) * Yn, finv(fz) * Zn

    def gam(u: float) -> float:
        u = np.clip(u, 0.0, 1.0)
        return 12.92 * u if u <= 0.0031308 else 1.055 * u ** (1 / 2.4) - 0.055

    r = gam(3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z)
    g = gam(-0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z)
    bb = gam(0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z)
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(bb * 255)):02x}"
