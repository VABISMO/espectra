"""Comparación de un espectro desconocido con una biblioteca.

Métricas combinadas (todas invariantes a la escala de intensidad):

* SAM   : ángulo espectral clásico entre las curvas sin centrar
          (Kruse/Aldrich) — invariante a la escala, no al offset.
* r     : correlación de Pearson de las curvas centradas (se reporta
          aparte y se usa como filtro `min_r`).
* CR    : eliminación de continuo (hull convexo superior) + correlación
          de las bandas de absorción, que es donde está la información
          química real y donde se ignora el color global.

score = w_sam·cos(SAM) + w_cr·(r_cr+1)/2 + w_color·(1-ΔE/60) →1.0 perfecto.
Los tres términos son magnitudes DISTINTAS: con la media centrada, el
primero coincidía exactamente con el segundo (medido: cos(SAM) ≡ r).

Los puntos donde la muestra es NaN (zonas sin lámpara, fuera de la
calibración) se descartan ANTES de comparar: se evalúa solo el subconjunto
válido, igual para la muestra y para cada candidato.

Y como casi todos los minerales son suaves en 400-700 nm, el resultado
lleva un **veredicto** (`MatchOutcome.verdict`) con un umbral de score y
de margen: si nada llega a ellos se responde *unknown* en lugar de
entregar el primer candidato como si fuera una identificación.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .color import delta_e, lab_of_spectrum, lab_to_hex
from .library import Spectrum


def family_key(name: str) -> str:
    """Clave de familia de un nombre: primera palabra sin puntuación.

    Sirve para no confundir "dos muestras del mismo mineral" con
    "dos materiales distintos empatados": si el mejor y el segundo
    mejor candidato son la misma familia, el margen real es contra el
    primer candidato de otra familia.
    """
    tok = (name or "").strip().split()
    return re.sub(r"[^a-z0-9]", "", tok[0].lower()) if tok else ""


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


def spectral_angle(a: np.ndarray, b: np.ndarray) -> float:
    """Ángulo espectral clásico (Kruse/Aldrich) en radianes: el ángulo
    entre las dos curvas **sin centrar**, por eso es invariante a la
    escala pero conserva el offset (el albedo es información).

    0 = misma forma y proporción.  Si se centrara la media esto se
    convertiría en el coeficiente de Pearson y el score repetiría dos
    veces la misma magnitud (medido: cos(SAM) ≡ r en la versión anterior).
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
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


# --------------------------------------------------------------- veredicto
# Pesos del score: elegidos con `scripts/benchmark.py --sweep-weights` sobre
# 4 escenarios (2 semillas, ruido 2 % + calibración 5 nm, luz parásita 6 DN):
#   0.35 / 0.40 / 0.25 -> 25.4 % top-1 por familia (media)   [antes]
#   0.20 / 0.10 / 0.70 -> 55.8 %   <- elegido  (87 % de la ganancia)
#   0.00 / 0.10 / 0.90 -> 60.5 %   (el óptimo puro deja el score en un
#                                    colorímetro: se descarta)
# El continuo bajó porque es el término más ruidoso (Pearson sobre curvas
# ya sin continuo amplifica el ruido); las bandas de absorción siguen
# pesando por la penalización multiplicativa `1 - 0.5·depth_diff`, que NO
# depende de los pesos.
W_SAM = 0.20
W_CONT = 0.10
W_COLOR = 0.70

# Umbrales por defecto: fijados con `scripts/benchmark.py --sweep`, no a
# ojo.  Con 200-300 muestras sintéticas ruidosas y 40-60 controles negativos:
#   (0.70, 0.06) → 0 % de falsos positivos, precisión por familia 86-100 %
#                  y ~7-13 % de las muestras declaradas "identified".
#   Bajar el margen a 0.02 sube el recall pero dispara los falsos
#   positivos al 5-8 %.
# No son probabilidades: son cortes sobre el score combinado.
VERDICT_MIN_SCORE = 0.70
VERDICT_MIN_MARGIN = 0.06

VERDICT_LABELS = ("identified", "uncertain", "unknown")


@dataclass(frozen=True)
class Verdict:
    """¿Hay material suficiente en la biblioteca para afirmar algo?

    identified : el mejor candidato supera `min_score` y se separa del
                 primer candidato de otra familia por `min_margin`.
    uncertain  : hay candidatos buenos pero no se distingue entre familias
                 (p. ej. calcita vs cuarzo en 400-700 nm).
    unknown    : ningún candidato llega al mínimo (muestra plana, ruido,
                 material ausente de la biblioteca) o no hay candidatos.
    """
    label: str
    score: float              # score del mejor candidato
    margin: float             # s1 - primer candidato de otra familia
    runner_up: float          # s1 - s2 (aunque sea de la misma familia)
    n: int                    # candidatos evaluados
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
    """Lista de MatchResult con el veredicto adjunto (`outcome.verdict`).

    Subclase de list para que el código existente (`results[0]`,
    `len(results)`, `for r in results`) siga funcionando igual.
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
    """Clasifica el ranking completo (sin truncar) en identified/uncertain/unknown."""
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
    """Compara el muestreo (en la rejilla `grid`) con la biblioteca ya
    re-muestreada (lista de (Spectrum, y_en_grid)).

    score = w_sam·cos(SAM) + w_cont·(r_continuo+1)/2 + w_color·(1-ΔE/60)
            todo ello con penalización por desajuste de profundidad de banda.

    Los NaN de `sample_y` (lámpara débil, calibración incompleta) se
    excluyen de todas las métricas en lugar de tratarse como cero.

    Devuelve un `MatchOutcome` (lista de `MatchResult`, truncada a `top`)
    con `.verdict`: `min_score` y `min_margin` fijan el corte entre
    identified / uncertain / unknown **sin filtrar** candidatos
    (`min_r` sí los filtra).
    """
    s = np.asarray(sample_y, dtype=float)
    if s.shape != grid.shape:
        raise ValueError("el espectro de muestra debe estar en la rejilla")
    valid = np.isfinite(s)
    if int(valid.sum()) < 8:
        raise ValueError(
            f"solo {int(valid.sum())}/{grid.size} puntos válidos: la lámpara "
            "no cubre la banda o falta calibración")
    g = grid[valid]
    sv = s[valid]
    s_cr = continuum_removal(g, sv)
    s_lab = lab_of_spectrum(g, sv)

    results: list[MatchResult] = []
    for spec, y in library:
        yv = np.asarray(y, dtype=float)[valid]
        if yv.size != sv.size:
            continue
        r_raw = pearson(sv, yv)
        if not np.isfinite(r_raw) or r_raw < min_r:
            continue
        sam = spectral_angle(sv, yv)
        y_cr = continuum_removal(g, yv)
        r_cr = pearson(s_cr, y_cr)
        depth_diff = float(np.max(np.abs(band_depths(s_cr) - band_depths(y_cr))))

        y_lab = lab_of_spectrum(g, yv)
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
    verdict = assess(results, min_score=min_score, min_margin=min_margin)
    return MatchOutcome(results[:top], verdict)
