"""Extracción del perfil espectral a partir de la fotografía de la cámara.

La foto es una banda luminosa cuyo eje largo corresponde a la dispersión
(longitud de onda) y cuyo eje corto es la anchura de la rendija/banda.

Funciones principales
---------------------
load_gray        -> imagen en punto flotante
extract_profile  -> intensidad vs. píxel (y filas usadas)
save_spectrum    -> CSV  pixel,intensity
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

CHANNELS = ("sum", "max", "r", "g", "b", "luma")


def array_gray(arr: np.ndarray, channel: str = "sum") -> np.ndarray:
    """Aplica el canal a un array de imagen (H×W o H×W×3/4) → float64.

    sum  = R+G+B  (recomendado: respuesta plana ante igual DN en cualquier
           canal, ideal para una banda espectral multicolor)
    max  = max(R,G,B)
    r/g/b= un solo canal
    luma = 0.2126R+0.7152G+0.0722B (pesos de luminancia: deforma el azul)
    """
    if channel not in CHANNELS:
        raise ValueError(f"channel debe ser uno de {CHANNELS}")
    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim == 2:
        return arr
    if arr.shape[-1] >= 3:
        if channel == "luma":
            return (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1]
                    + 0.0722 * arr[..., 2])
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        if channel == "sum":
            return r + g + b
        if channel == "max":
            return np.maximum(np.maximum(r, g), b)
        if channel == "r":
            return r
        if channel == "g":
            return g
        return b
    return arr[..., 0]


def load_gray(path: str | Path, channel: str = "sum") -> np.ndarray:
    """Carga una imagen como float64 aplicando `channel` (ver array_gray)."""
    img = Image.open(path)
    if img.mode in ("P", "PA", "1"):
        img = img.convert("RGB")
    if channel == "luma" and img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    return array_gray(np.asarray(img), channel)


@dataclass
class Extraction:
    pixel: np.ndarray          # índice de píxel a lo largo del eje de dispersión
    intensity: np.ndarray      # intensidad media (oscura restada)
    rows: tuple[int, int]      # filas usadas (incluido el extremo superior)
    axis: str                  # 'x' (dispersión en columnas) o 'y'
    preview: np.ndarray | None = None   # imagen recortada a la banda


def _detect_axis(img: np.ndarray) -> str:
    """El eje de dispersión es el que está 'encendido' durante más tramo.

    La banda es una franja fina: en el eje transversal solo una fracción
    pequeña de filas/columnas tiene señal; en el eje de dispersión la
    banda suele recorrer casi toda la imagen.  Desempata por estructura
    (número de giros de pendiente tras suavizar): el espectro varía más
    que el perfil gaussial transversal de la banda.
    """

    def score(p: np.ndarray) -> tuple[float, float]:
        if p.size < 8:
            return 0.0, 0.0
        on = float((p >= p.max() * 0.05).mean())
        sm = moving_average(p, 9)
        d = np.diff(sm)
        turns = float(np.count_nonzero(np.diff(np.sign(d)) != 0)) / p.size
        return on, turns

    on_x, t_x = score(img.mean(axis=0))
    on_y, t_y = score(img.mean(axis=1))
    sx, sy = on_x + 4.0 * t_x, on_y + 4.0 * t_y
    return "x" if sx >= sy else "y"


def _find_band(profile: np.ndarray, frac: float = 0.25) -> tuple[int, int]:
    """Filas cuya intensidad supera `frac` del máximo (tronco continuo)."""
    thr = profile.max() * frac
    above = profile >= thr
    if not above.any():
        return 0, profile.size
    idx = np.flatnonzero(above)
    # tramo más largo de filas encendidas
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [idx.size - 1]))
    best = int(np.argmax(ends - starts))
    return int(idx[starts[best]]), int(idx[ends[best]])


def moving_average(y: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return y
    window = min(int(window), max(3, y.size // 2))
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window) / window
    pad = window // 2
    padded = np.pad(y, pad, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def extract_profile(
    img: np.ndarray,
    rows: tuple[int, int] | None = None,
    axis: str | None = None,
    dark: np.ndarray | float | None = None,
    smooth: int = 5,
    frac: float = 0.25,
) -> Extraction:
    """Extrae intensidad vs. píxel promediando sobre la banda luminosa.

    img     : imagen cargada con load_gray
    rows    : (fila_inicio, fila_fin) fijas a mano; si es None se detecta
    axis    : 'x' (dispersión en columnas, típico) o 'y'; None = autodetección
    dark    : imagen oscura (mismas dimensiones) o valor escalar a restar
    smooth  : ventana de media móvil (1 = sin suavizar)
    frac    : fracción del máximo para considerar una fila "encendida"
    """
    if axis is None:
        axis = _detect_axis(img)
    if axis == "y":
        img = img.T

    if isinstance(dark, np.ndarray):
        d = dark.T if axis == "y" else dark
        if d.shape != img.shape:
            raise ValueError(f"imagen oscura {d.shape} != imagen {img.shape}")
        work = img - d
    elif dark is not None:
        work = img - float(dark)
    else:
        work = img.copy()

    row_profile = work.mean(axis=1)
    if rows is None:
        r0, r1 = _find_band(row_profile, frac)
    else:
        r0, r1 = int(rows[0]), int(rows[1])
    r0 = max(0, min(r0, work.shape[0] - 1))
    r1 = max(r0 + 1, min(r1, work.shape[0]))

    band = work[r0:r1, :]
    intensity = band.mean(axis=0)
    intensity = moving_average(np.clip(intensity, 0, None), smooth)

    return Extraction(
        pixel=np.arange(intensity.size, dtype=float),
        intensity=intensity,
        rows=(r0, r1),
        axis=axis,
        preview=img[r0:r1, :],
    )


def save_spectrum(path: str | Path, pixel: np.ndarray, intensity: np.ndarray,
                  extra: dict | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# pixel,intensity\n")
        if extra:
            for k, v in extra.items():
                fh.write(f"# {k}: {v}\n")
        for p, i in zip(pixel, intensity):
            fh.write(f"{p:.0f},{i:.6f}\n")


def load_spectrum(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Lee un CSV con 2 columnas numéricas (ignora cabeceras '#')."""
    xs, ys = [], []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p for p in line.replace(";", ",").split(",") if p.strip()]
            if len(parts) < 2:
                parts = line.split()
            if len(parts) < 2:
                continue
            try:
                xs.append(float(parts[0]))
                ys.append(float(parts[1]))
            except ValueError:
                continue  # fila de cabecera sin comentarios
    if len(xs) < 8:
        raise ValueError(f"{path}: demasiadas pocas filas numéricas ({len(xs)})")
    return np.asarray(xs), np.asarray(ys)
