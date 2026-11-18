"""Comparación de un espectro desconocido con una biblioteca.

Métricas combinadas (todas invariantes a la escala de intensidad):

* SAM   : ángulo espectral entre las curvas normalizadas (0 = idénticas).
* r     : correlación de Pearson de las curvas normalizadas.
* CR    : eliminación de continuo (hull convexo superior) + correlación
          de las bandas de absorción, que es donde está la información
          química real y donde se ignora el color global.

score = 1 - (w_sam·cos + w_cr·r_cr)/(... )   → 1.0 = coincidencia perfecta.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .color import delta_e, lab_of_spectrum, lab_to_hex
from .library import Spectrum


def upper_hull(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Envoltura convexa superior (monotone chain) interpolada sobre x."""
    pts: list[tuple[float, float]] = []
    for xi, yi in zip(x, y):
        pts.append((float(xi), float(yi)))
        while len(pts) >= 3:
            (x0, y0), (x1, y1), (x2, y2) = pts[-3:]
            # cruce > 0 -> punto medio bajo la línea = quitarlo
            cross = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
            if cross <= 0:
                break
            pts.pop(-2)
    hx = np.array([p[0] for p in pts])
    hy = np.array([p[1] for p in pts])
    return np.interp(x, hx, hy)


def continuum_removal(nm: np.ndarray, y: np.ndarray) -> np.ndarray:
    """R/continuo en el rango visible (hull convexo superior)."""
    hull = upper_hull(nm, y)
    hull = np.where(hull <= 1e-9, np.nan, hull)
    cr = y / hull
    if np.isnan(cr).all():
        return np.ones_like(y)
    return np.nan_to_num(cr, nan=float(np.nanmean(cr)))


def _unit(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    y = y - y.mean()
    n = np.linalg.norm(y)
    return y / n if n > 0 else y


def spectral_angle(a: np.ndarray, b: np.ndarray) -> float:
    """Ángulo en radianes entre dos curvas (0 = idénticas)."""
    a, b = _unit(a), _unit(b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return float("nan")
    cos = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
    return float(np.arccos(cos))


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


@dataclass
class MatchResult:
    spectrum: Spectrum
    score: float
    sam_deg: float
    r_raw: float
    r_continuum: float
    max_depth_diff: float     # diferencia máxima de profundidad de banda
    delta_e: float = float("nan")   # distancia de color CIELAB (D65)
    color_hex: str = "#808080"      # color del material (para mostrar)

    def as_row(self) -> dict:
        return {
            "material": self.spectrum.name,
            "categoria": self.spectrum.category,
            "fuente": self.spectrum.source,
            "score": round(self.score, 4),
            "color": self.color_hex,
            "delta_e": round(self.delta_e, 2),
            "sam_deg": round(self.sam_deg, 2),
            "pearson": round(self.r_raw, 4),
            "continuo_r": round(self.r_continuum, 4),
            "delta_banda": round(self.max_depth_diff, 4),
        }


def band_depths(cr: np.ndarray) -> np.ndarray:
    """Profundidades de banda (1 - CR) sobre una rejilla fina."""
    return 1.0 - np.asarray(cr, dtype=float)


def match(
    sample_nm: np.ndarray,
    sample_y: np.ndarray,
    library: list[tuple[Spectrum, np.ndarray]],
    grid: np.ndarray,
    top: int = 5,
    w_sam: float = 0.35,
    w_cont: float = 0.40,
    w_color: float = 0.25,
    min_r: float = -1.0,
) -> list[MatchResult]:
    """Compara el muestreo (en la rejilla `grid`) con la biblioteca ya
    re-muestreada (lista de (Spectrum, y_en_grid)).

    score = w_sam·cos(SAM) + w_cont·(r_continuo+1)/2 + w_color·(1-ΔE/60)
            todo ello con penalización por desajuste de profundidad de banda.
    """
    s = np.asarray(sample_y, dtype=float)
    if s.shape != grid.shape:
        raise ValueError("el espectro de muestra debe estar en la rejilla")
    s_cr = continuum_removal(grid, s)
    s_lab = lab_of_spectrum(grid, s)

    results: list[MatchResult] = []
    for spec, y in library:
        r_raw = pearson(s, y)
        if not np.isfinite(r_raw) or r_raw < min_r:
            continue
        sam = spectral_angle(s, y)
        y_cr = continuum_removal(grid, y)
        r_cr = pearson(s_cr, y_cr)
        depth_diff = float(np.max(np.abs(band_depths(s_cr) - band_depths(y_cr))))

        y_lab = lab_of_spectrum(grid, y)
        d_e = delta_e(s_lab, y_lab)
        color_term = float(np.clip(1.0 - d_e / 60.0, 0.0, 1.0))

        # combinación: ángulo espectral, bandas de absorción y color
        cos = float(np.cos(sam)) if np.isfinite(sam) else 0.0
        r_c = 0.0 if not np.isfinite(r_cr) else r_cr
        combined = (w_sam * cos
                    + w_cont * (0.5 * (r_c + 1.0))
                    + w_color * color_term)
        # penalización suave por bandas mal ajustadas
        combined *= max(0.0, 1.0 - 0.5 * depth_diff)
        score = float(np.clip(combined, 0.0, 1.0))

        results.append(MatchResult(
            spectrum=spec, score=score,
            sam_deg=float(np.degrees(sam)) if np.isfinite(sam) else float("nan"),
            r_raw=r_raw, r_continuum=r_cr, max_depth_diff=depth_diff,
            delta_e=d_e, color_hex=lab_to_hex(y_lab),
        ))

    # el color de la muestra lo calcula quien llama con lab_of_spectrum()
    results.sort(key=lambda m: m.score, reverse=True)
    return results[:top]
