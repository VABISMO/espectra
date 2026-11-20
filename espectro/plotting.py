"""Workflow plots (matplotlib, no display needed)."""
from __future__ import annotations

from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .match import MatchResult


def _save(fig, out: str | Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_extraction(img: np.ndarray, rows: tuple[int, int],
                    pixel: np.ndarray, intensity: np.ndarray,
                    out: str | Path, axis: str = "x") -> Path:
    """Preview: image with the band highlighted + the extracted profile."""
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(10, 6),
                                   gridspec_kw={"height_ratios": [2, 1]})
    show = img if axis == "x" else img.T
    ax0.imshow(show, aspect="auto", cmap="gray")
    ax0.axhline(rows[0], color="cyan", lw=1)
    ax0.axhline(rows[1], color="cyan", lw=1)
    ax0.set_title("Band used for extraction (cyan)")
    ax0.set_ylabel("fila")

    ax1.plot(pixel, intensity, lw=1.2, color="#1f77b4")
    ax1.set_xlabel("pixel")
    ax1.set_ylabel("intensity")
    ax1.set_title("Extracted spectral profile")
    ax1.grid(alpha=0.3)
    return _save(fig, out)


def plot_calibration(pixel: np.ndarray, intensity: np.ndarray,
                     coeffs: np.ndarray, fit_T: float | None,
                     fit_corr: float | None, out: str | Path,
                     model_fn=None, model_label: str | None = None) -> Path:
    """Big calibration plot; `model_fn(nm)` draws the fitting template
    (LED, Hg lines or Planck·QE)."""
    nm = np.polyval(coeffs, pixel)
    fig, ax = plt.subplots(figsize=(14, 6.8))
    order = np.argsort(nm)
    ax.plot(nm[order], intensity[order], lw=1.4, color="#1f77b4",
            label="espectro medido")
    if fit_T and np.isfinite(fit_T):
        from .calib import planck, qe_model
        model = planck(nm, fit_T) * qe_model(nm)
        m = np.nanmax(intensity)
        ax.plot(nm[order], model[order] * m, lw=1.2, ls="--", color="darkorange",
                label=f"Planck {fit_T:.0f} K · sensor (r={fit_corr:.3f})")
    elif model_fn is not None:
        model = model_fn(nm)
        m = np.nanmax(intensity)
        if m > 0 and np.nanmax(model) > 0:
            lbl = model_label or "LED template"
            if fit_corr and np.isfinite(fit_corr):
                lbl = f"{lbl} (r={fit_corr:.3f})"
            ax.plot(nm[order], model[order] / np.nanmax(model) * m,
                    lw=1.2, ls="--", color="darkorange", label=lbl)
    ax.set_xlabel("wavelength (nm)")
    ax.set_ylabel("intensidad (u.a.)")
    ax.set_title("Calibration pixel → nm")
    ax.legend()
    ax.grid(alpha=0.3)
    return _save(fig, out)


def plot_matches(sample_nm: np.ndarray, sample_y: np.ndarray,
                 results: list[MatchResult],
                 lib_curves: dict[str, np.ndarray],
                 out: str | Path, title: str = "Comparison against the library") -> Path:
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11, 8),
                                   gridspec_kw={"height_ratios": [1, 1]})
    ax0.plot(sample_nm, sample_y, lw=2.2, color="black",
             label="sample (pseudo-reflectance)")
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(results), 1)))
    for i, r in enumerate(results):
        y = lib_curves.get(r.spectrum.name)
        if y is None:
            continue
        ax0.plot(sample_nm, y, lw=1.1, color=colors[i], alpha=0.85,
                 label=f"{r.spectrum.name.split(' [')[0]}  ({r.score:.2f})")
    ax0.set_xlabel("wavelength (nm)")
    ax0.set_ylabel("relative reflectance")
    ax0.set_title(title)
    ax0.legend(fontsize=8, loc="best")
    ax0.grid(alpha=0.3)

    from .match import continuum_removal
    s_cr = continuum_removal(sample_nm, sample_y)
    ax1.plot(sample_nm, s_cr, lw=2.2, color="black", label="sample")
    for i, r in enumerate(results):
        y = lib_curves.get(r.spectrum.name)
        if y is None:
            continue
        ax1.plot(sample_nm, continuum_removal(sample_nm, y), lw=1.1,
                 color=colors[i], alpha=0.85,
                 label=f"{r.spectrum.name.split(' [')[0]}")
    ax1.set_xlabel("wavelength (nm)")
    ax1.set_ylabel("R / continuo")
    ax1.set_title("Continuum removal (absorption bands)")
    ax1.legend(fontsize=8, loc="best")
    ax1.grid(alpha=0.3)
    return _save(fig, out)


def plot_top_matches_table(results: list[MatchResult], out: str | Path) -> Path:
    """Summary table saved as PNG (to read without opening the CSV)."""
    rows = [r.as_row() for r in results]
    if not rows:
        Path(out).write_text("no matches\n", encoding="utf-8")
        return Path(out)
    cols = ["material", "category", "score", "color", "delta_e", "sam_deg",
            "pearson", "continuum_r", "band_delta"]
    fig, ax = plt.subplots(figsize=(11, 0.6 * len(rows) + 1.6))
    ax.axis("off")
    table = ax.table(
        cellText=[[str(r[c]) for c in cols] for r in rows],
        colLabels=cols, loc="center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.35)
    for (r, _c), cell in table.get_celld().items():
        if r == 0:
            cell.set_facecolor("#d9e6f2")
            cell.set_text_props(weight="bold")
    ax.set_title("Top matches")
    return _save(fig, out)
