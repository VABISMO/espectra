#!/usr/bin/env python3
"""Interfaz gráfica del espectrómetro (Streamlit).

    .venv/bin/streamlit run app.py

Diseño pensado en 4 pantallas:

  🎯 Analizar   — subir fotos → ajustes con vista previa → resultados con
                  color, ΔE, tabla y gráficas superpuestas + descargas.
  🔧 Calibración— subir la foto de la bombilla, elegir método (LED
                  automático por defecto) y guardar la calibración activa.
  📚 Bibliotecas— ver, ampliar y subir bases de datos de materiales.
  💡 Guía       — cómo capturar buenas fotos y qué esperar del análisis.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import streamlit as st
from PIL import Image

from espectro import camara as cam
from espectro import image as im
from espectro import library as lib
from espectro import match as mtc
from espectro import plotting as pl
from espectro.calib import apply_cal, load_cal
from espectro.cli import CAL_DIR, LIB_DIR, cmd_calibrate
from espectro.color import lab_of_spectrum, lab_to_hex
from espectro.reflectance import (fill_nan, linearize, pseudo_reflectance,
                                  resample)

ROOT = Path(__file__).resolve().parent
GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)

st.set_page_config(page_title="Espectrómetro", page_icon="🔬",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container {padding-top: 1.2rem; max-width: 1180px;}
  h1, h2, h3 {font-family: 'Segoe UI', system-ui, sans-serif;}
  div[data-testid="stSidebar"] {background: linear-gradient(180deg,#0d1b2a 0%,#1b263b 100%);}
  div[data-testid="stSidebar"] * {color: #e0e6ed;}
  .brand {font-size:1.35rem; font-weight:700; color:#ffffff !important;
          padding:.2rem 0 .0rem 0; letter-spacing:.5px;}
  .tag {color:#9fb3c8 !important; font-size:.8rem; margin-bottom:.6rem;}
  .card {background:#f7f9fc; border:1px solid #e3e9f0; border-radius:14px;
         padding:1rem 1.2rem; margin:.4rem 0 1rem 0;}
  .step {background:#eef4fb; border-left:4px solid #2b6cb0; padding:.55rem .9rem;
         border-radius:0 10px 10px 0; margin:.3rem 0 .8rem 0; font-size:.95rem;}
  .swatch {display:inline-block; width:64px; height:44px; border-radius:8px;
           border:1px solid rgba(0,0,0,.15); margin:2px 6px 2px 0; vertical-align:middle;}
  .swlabel {font-size:.72rem; color:#445; text-align:center; display:block;
            width:64px; margin-right:6px;}
  .winner {background:linear-gradient(90deg,#e8f5e9,#f7f9fc); border:1px solid #b9dfc0;
           border-radius:14px; padding:.9rem 1.2rem; margin-bottom:.6rem;}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------- utilidades
def _to_gray(data: bytes, channel: str = "sum") -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        return im.load_gray(tmp, channel=channel)
    finally:
        tmp.unlink(missing_ok=True)


def _run_cli(fn, ns: argparse.Namespace) -> str:
    """Ejecuta un subcomando del CLI capturando su salida para la UI."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            fn(ns)
        except SystemExit as e:
            print(f"ERROR: {e}")
    return buf.getvalue()


@st.cache_resource(show_spinner="Cargando bibliotecas de materiales…")
def load_libs(fwhm: float | None = lib.FWHM_DEFAULT
              ) -> tuple[list[lib.Spectrum], list[tuple[lib.Spectrum, np.ndarray]]]:
    spectra = lib.load_library(LIB_DIR)
    return spectra, lib.to_grid(spectra, GRID, fwhm=fwhm)


def active_cal() -> tuple[np.ndarray | None, dict]:
    """Calibración vigente: sesión > data/calibration/cal.json."""
    if "cal_coeffs" in st.session_state:
        return st.session_state["cal_coeffs"], st.session_state.get("cal_meta", {})
    default = CAL_DIR / "cal.json"
    if default.exists():
        c, m = load_cal(default)
        return c, m
    return None, {}


def set_active_cal(coeffs: np.ndarray, meta: dict, path: Path | None = None) -> None:
    st.session_state["cal_coeffs"] = coeffs
    st.session_state["cal_meta"] = meta
    if path:
        st.session_state["cal_file"] = str(path)


def status_sidebar(cal_info: tuple[np.ndarray | None, dict], n_lib: int) -> str:
    coeffs, meta = cal_info
    with st.sidebar:
        st.markdown('<div class="brand">🔬 Espectrómetro</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="tag">cámara USB + tubo dispersor</div>',
                    unsafe_allow_html=True)
        page = st.radio("Navegación",
                        ["🎯 Analizar muestra", "🔧 Calibración",
                         "📚 Bibliotecas", "💡 Guía"],
                        label_visibility="collapsed")
        st.divider()
        st.markdown("**Estado**")
        if coeffs is not None:
            modo = {"led": "🟦 LED blanco", "lines": "⚡ Líneas de mercurio/CFL",
                    "anchors": "📍 Anclas manuales",
                    "range": "📏 Rango asumido",
                    "range_fallback": "📏 Rango (fallback)",
                    "blackbody": "🔴 Cuerpo negro"}.get(
                        meta.get("mode", "?"), meta.get("mode", "?"))
            st.success(f"Calibración: {modo}")
            if meta.get("lines"):
                st.caption("líneas usadas: " + ", ".join(
                    f"{d['nm']:g} nm" for d in meta["lines"]))
            if meta.get("led_corr") is not None and np.isfinite(meta.get("led_corr", np.nan)):
                st.caption(f"ajuste plantilla LED: r = {meta['led_corr']:.3f}")
        else:
            st.warning("Sin calibración → ve a **🔧 Calibración**")
        st.info(f"📚 {n_lib} materiales en la base")
    return page


def swatches(items: list[tuple[str, str]], title: str) -> None:
    """Muestra una fila de muestras de color (label, hex)."""
    html = f'<div style="margin:.2rem 0 .4rem 0"><b>{title}</b><br>'
    for label, hx in items:
        html += (f'<span class="swatch" style="background:{hx}"></span>'
                 f'<span class="swlabel">{label}</span>')
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ------------------------------------------------------------ cámara USB
def _release_cam() -> None:
    cap = st.session_state.pop("cam_cap", None)
    if cap is not None:
        try:
            cap.release()
        except Exception:                            # noqa: BLE001
            pass
    st.session_state["cam_live"] = False


def _capture_cb(slot: str) -> None:
    """Guarda el último frame como PNG en `foto_<slot>` y congela la vista."""
    frame = st.session_state.get("cam_frame")
    if frame is None:
        return
    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, format="PNG")
    st.session_state[f"foto_{slot}"] = buf.getvalue()
    st.session_state["cam_live"] = False


def camera_panel(slots: list[tuple[str, str]]) -> None:
    """Transmisión en vivo de la cámara USB + botones de captura.

    `slots` = [(clave, etiqueta), ...]; cada botón guarda un PNG en
    `st.session_state['foto_<clave>']`.
    """
    try:
        cams = cam.probe_cameras()
    except RuntimeError as e:
        st.error(str(e))
        return
    if not cams:
        c1, c2 = st.columns([4, 1])
        c1.warning("No se detectó ninguna cámara USB. ¿Está enchufada? "
                   "Comprueba también los permisos del grupo `video`.")
        if c2.button("🔄 Reintentar", key="cam_retry"):
            cam.probe_cameras(refresh=True)
            st.rerun()
        return

    def _label(i: int) -> str:
        c = next(c for c in cams if c["index"] == i)
        mono = " · monochrome" if c["fourcc"] == "GREY" else ""
        return (f"/dev/video{i} — {c['w']}×{c['h']} {c['fourcc']} "
                f"{c['fps']:.0f}fps{mono}")

    idx = st.selectbox("Cámara", [c["index"] for c in cams],
                       format_func=_label, key="cam_index",
                       help="La del espectrómetro suele ser la que aparece "
                            "como GREY (monochrome) o la de menor brillo")
    live = st.checkbox("🔴 Transmisión en vivo", key="cam_live",
                       help="Deja la cámara abierta; se cierra al "
                            "desmarcar o al cambiar de pantalla")

    with st.expander("⚙️ Exposición de la cámara (evita saturar)"):
        e1, e2 = st.columns(2)
        expo = e1.slider("Exposición (V4L2)", -30, 0, -6, key="cam_expo",
                         help="Más negativo = menos luz. Si tu cámara no lo "
                              "admite, usa v4l2-ctl -d /dev/videoX "
                              "--set-ctrl=exposure_absolute=...")
        auto_off = e2.checkbox("Apagar auto-exposición", True,
                               key="cam_autoexp")

    # botones de captura siempre visibles (funcionan también en vivo)
    cols = st.columns(len(slots))
    for (key, label), col in zip(slots, cols):
        with col:
            st.button(label, key=f"btn_{key}", use_container_width=True,
                      type="primary" if key == "sample" else "secondary",
                      disabled="cam_frame" not in st.session_state,
                      on_click=_capture_cb, args=(key,))

    if live:
        cap = st.session_state.get("cam_cap")
        if cap is None:
            try:
                cap = cam.open_camera(idx)
                cam.set_exposure(cap, expo, auto_off)
                st.session_state["cam_cap"] = cap
            except Exception as e:                    # noqa: BLE001
                st.error(f"No se pudo abrir la cámara: {e}")
                st.session_state["cam_live"] = False
                return
        ph = st.empty()
        for _ in range(45):                           # ~2,7 s por pasada
            rgb = cam.read_rgb(cap)
            if rgb is None:
                st.error("La cámara dejó de responder.")
                st.session_state["cam_live"] = False
                break
            st.session_state["cam_frame"] = rgb
            ph.image(rgb, caption=_label(idx), use_container_width=True)
            time.sleep(0.06)
        st.rerun()                                    # releer los widgets
    else:
        _release_cam()
        frame = st.session_state.get("cam_frame")
        if frame is not None:
            st.image(frame, caption="último frame — pulsa un botón de "
                                    "captura para usarlo",
                     use_container_width=True)


def _thumbs() -> None:
    """Miniaturas de las fotos capturadas/subidas con su botón de borrar."""
    labels = {"sample": "Muestra", "lamp": "LED (referencia)",
              "cal": "LED (calibración)"}
    items = [(k, st.session_state.get(f"foto_{k}"))
             for k in labels if st.session_state.get(f"foto_{k}")]
    if not items:
        return
    cols = st.columns(len(items))
    for col, (k, png) in zip(cols, items):
        with col:
            st.markdown(f"**{labels[k]}**")
            st.image(png, use_container_width=True)
            if st.button("✖ Borrar", key=f"clr_{k}"):
                st.session_state.pop(f"foto_{k}", None)
                st.rerun()


# ---------------------------------------------------------- página: analizar
def page_analyze(cal_info) -> None:
    st.title("🎯 Analizar una muestra")
    source = st.radio("Fuente de las fotos",
                      ["📷 Cámara USB", "📁 Subir archivos"],
                      horizontal=True, key="fuente",
                      help="La cámara lee directamente tu espectrómetro USB")

    if source.startswith("📷"):
        camera_panel([("sample", "📸 Capturar MUESTRA"),
                      ("lamp", "📸 Capturar LED (referencia)")])
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 1 · Foto de la muestra")
            sf = st.file_uploader("Arrastra la foto del espectro de la "
                                  "muestra", type=["png", "jpg", "jpeg",
                                                   "bmp", "tif", "tiff"],
                                  key="up_sample")
            if sf is not None:
                st.session_state["foto_sample"] = sf.getvalue()
            st.caption("Misma exposición que el LED, sin saturar, en PNG.")
        with c2:
            st.markdown("#### 2 · Foto del LED (referencia)")
            lf = st.file_uploader("Arrastra la foto del LED sin muestra "
                                  "(cancela lámpara y sensor)",
                                  type=["png", "jpg", "jpeg", "bmp"],
                                  key="up_lamp")
            if lf is not None:
                st.session_state["foto_lamp"] = lf.getvalue()
            st.caption("Recomendado: sin esto solo se compara la forma bruta.")

    _thumbs()
    sample_png = st.session_state.get("foto_sample")
    lamp_png = st.session_state.get("foto_lamp")

    with st.expander("⚙️ Ajustes de extracción y foto oscura (opcional)"):
        dark_f = st.file_uploader("Foto oscura (misma exposición) para "
                                  "restar ruido", type=["png", "jpg"],
                                  key="up_dark")
        a1, a2, a3 = st.columns(3)
        axis = a1.selectbox("Eje de dispersión", ["auto", "x", "y"])
        channel = a2.selectbox("Canal", ["sum", "max", "r", "g", "b", "luma"],
                               index=0,
                               help="sum = R+G+B, el correcto para bandas "
                                    "multicolor")
        smooth = a3.slider("Suavizado (píxeles)", 1, 41, 5, step=2)
        f1, f2 = st.columns(2)
        frac = f1.slider("Umbral de banda", 0.05, 0.6, 0.25, 0.05)
        rows_auto = f2.checkbox("Detección automática de filas", True)
        rows = None
        if not rows_auto:
            rr = st.slider("Filas de la banda (inicio, fin)", 0, 5000,
                           (0, 500))
            rows = tuple(rr)

    if sample_png is None:
        st.markdown('<div class="step">📷 Activa la <b>transmisión en '
                    'vivo</b>, coloca la muestra y pulsa <b>📸 Capturar '
                    'MUESTRA</b> (y antes <b>📸 Capturar LED</b> con la '
                    'muestra fuera del haz). O cambia a '
                    '<i>📁 Subir archivos</i>.</div>',
                    unsafe_allow_html=True)
        return

    img = _to_gray(sample_png, channel)
    dark = _to_gray(dark_f.getvalue(), channel) if dark_f else None
    ext = im.extract_profile(img, rows=rows,
                             axis=None if axis == "auto" else axis,
                             dark=dark, smooth=smooth, frac=frac)

    st.markdown('<div class="step">👁️ Revisa que la franja cian cubra la '
                'banda y que el perfil de abajo sea suave y con señal en '
                'todo el rango.</div>', unsafe_allow_html=True)
    with tempfile.TemporaryDirectory() as td:
        pv = Path(td) / "prev.png"
        pl.plot_extraction(img, ext.rows, ext.pixel, ext.intensity, pv,
                           axis=ext.axis)
        st.image(str(pv), use_container_width=True)

    coeffs, meta = cal_info
    if coeffs is None:
        st.warning("Falta la calibración píxel→nm. Ve a **🔧 Calibración** "
                   "(solo la foto del LED, 10 segundos).")
        return
    if lamp_png is None:
        st.info("Sin foto del LED: se comparará la intensidad bruta. "
                "Captúrala con **📸 Capturar LED** y el resultado mejora "
                "mucho.")

    if st.button("🔍 Analizar y comparar con la base", type="primary",
                 use_container_width=True):
        with st.spinner("Extrayendo, calibrando y comparando…"):
            try:
                results, grid, y_grid, s_hex = _analyze(
                    img, ext, lamp_png, coeffs, channel, smooth, frac, rows,
                    axis, dark)
            except Exception as e:                       # noqa: BLE001
                st.error(f"No se pudo analizar: {e}")
                return
        st.session_state["results"] = results
        st.session_state["grid"] = grid
        st.session_state["y_grid"] = y_grid
        st.session_state["s_hex"] = s_hex

    if "results" in st.session_state:
        _show_results(st.session_state["results"],
                      st.session_state["grid"],
                      st.session_state["y_grid"],
                      st.session_state["s_hex"])


def _analyze(img, ext, lamp_png, coeffs, channel, smooth, frac, rows, axis,
             dark, gamma=1.0):
    maxv = 765.0 if channel == "sum" else 255.0
    if lamp_png:
        lamp_img = _to_gray(lamp_png, channel)
        ext_l = im.extract_profile(lamp_img, rows=None,
                                   axis=None if axis == "auto" else axis,
                                   smooth=smooth, frac=frac)
        if ext_l.intensity.size != ext.intensity.size:
            # recorta al común (fotos de distinto tamaño)
            n = min(ext_l.intensity.size, ext.intensity.size)
            y = pseudo_reflectance(ext.intensity[:n], ext_l.intensity[:n],
                                   smooth=smooth, gamma=gamma,
                                   full_scale=maxv)
            px = ext.pixel[:n]
        else:
            y = pseudo_reflectance(ext.intensity, ext_l.intensity,
                                   smooth=smooth, gamma=gamma,
                                   full_scale=maxv)
            px = ext.pixel
    else:
        y = np.clip(linearize(ext.intensity, gamma, maxv), 0, None)
        y = y / y.max() if y.max() > 0 else y
        px = ext.pixel

    nm = apply_cal(coeffs, px)
    y_grid = resample(nm, y, GRID)          # conserva los NaN de la máscara
    ok = np.isfinite(y_grid)
    if ok.sum() < 0.4 * GRID.size:
        raise ValueError("la calibración no cubre la banda de la foto "
                         f"(solo {ok.sum()}/{GRID.size} puntos); recalibra")
    spectra, paired = load_libs()
    results = mtc.match(GRID, y_grid, paired, GRID, top=10)
    s_hex = lab_to_hex(lab_of_spectrum(GRID, y_grid))
    # para mostrar/graficar se rellenan los huecos; el cruce usa los NaN
    return results, GRID, fill_nan(y_grid), s_hex


def _show_results(results, grid, y_grid, s_hex) -> None:
    if not results:
        st.error("Sin coincidencias. Prueba a subir la bombilla de "
                 "referencia o revisa la calibración.")
        return
    top = results[0]
    # curvas de los candidatos ya re-muestreadas a la rejilla
    lib_curves = {r.name: y for r, y in lib.to_grid(
        [r.spectrum for r in results], GRID, fwhm=lib.FWHM_DEFAULT)}

    st.markdown('<div class="winner">', unsafe_allow_html=True)
    w1, w2 = st.columns([3, 1])
    with w1:
        st.markdown(f"### 🏆 {top.spectrum.name}")
        st.caption(f"Categoría: {top.spectrum.category or '—'} · "
                   f"fuente: {top.spectrum.source or '—'}")
    with w2:
        st.metric("Confianza (score)", f"{top.score:.2f}",
                  f"ΔE color = {top.delta_e:.1f}")
    v = getattr(results, "verdict", None)
    if v is not None and v.label != "identified":
        st.warning({
            "unknown": ("⚠ DESCONOCIDO: ningún candidato llega al umbral "
                        f"(score ≥ {v.min_score:.2f}). La muestra puede ser "
                        "plana, estar dominada por ruido o no estar en la "
                        "biblioteca."),
            "uncertain": ("⚠ DUDOSO: hay candidatos buenos pero no se "
                          "distingue entre familias (margen "
                          f"{v.margin:.3f} < {v.min_margin:.2f})."),
        }.get(v.label, ""))
    swatches([("muestra", s_hex), (top.spectrum.name.split(' [')[0][:14],
                                   top.color_hex)],
             "Color (D65): muestra vs mejor candidato")
    st.markdown("</div>", unsafe_allow_html=True)

    tabs = st.tabs(["📈 Curvas superpuestas", "🪞 Bandas (continuo)",
                    "🧮 Tabla de resultados", "⬇️ Descargas"])
    with tabs[0]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cmp.png"
            pl.plot_matches(grid, y_grid, results, lib_curves, p)
            st.image(str(p), use_container_width=True)
        st.caption("Negro = tu muestra (pseudo-reflectancia). Las líneas de "
                   "color son los materiales candidatos.")

    with tabs[1]:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from espectro.match import continuum_removal
        fig, ax = plt.subplots(figsize=(10, 4.2))
        ax.plot(grid, continuum_removal(grid, y_grid), "k-", lw=2.2,
                label="muestra")
        for r in results[:5]:
            spec_curve = lib_curves.get(r.spectrum.name)
            if spec_curve is not None:
                ax.plot(grid, continuum_removal(grid, spec_curve),
                        lw=1.2, alpha=.85,
                        label=r.spectrum.name.split(" [")[0])
        ax.set_xlabel("longitud de onda (nm)")
        ax.set_ylabel("R / continuo")
        ax.grid(alpha=.3)
        ax.legend(fontsize=8)
        st.pyplot(fig)
        plt.close(fig)
        st.caption("Tras quitar el 'continuo' solo quedan las bandas de "
                   "absorción: la firma química real del material.")

    with tabs[2]:
        import pandas as pd
        df = pd.DataFrame([r.as_row() for r in results])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("score = 0.20·ángulo espectral + 0.10·bandas + "
                   "0.70·color (ΔE), con penalización por bandas mal "
                   "ajustadas (esa penalización no depende de los pesos). "
                   "ΔE: <5 casi idéntico, >20 muy distinto.")

    with tabs[3]:
        import pandas as pd
        csv = pd.DataFrame([r.as_row() for r in results]).to_csv(
            index=False).encode()
        st.download_button("⬇️ Descargar resultados (CSV)", csv,
                           "coincidencias.csv", "text/csv")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cmp.png"
            pl.plot_matches(grid, y_grid, results, lib_curves, p)
            st.download_button("⬇️ Descargar gráfica (PNG)", Path(p).read_bytes(),
                               "comparacion.png", "image/png")


# ------------------------------------------------------ página: calibración
def page_calibrate(cal_info) -> None:
    st.title("🔧 Calibración píxel → nanómetros")
    st.markdown('<div class="step">Si tienes una <b>lámpara de mercurio o '
                'CFL</b>, sus líneas estrechas (404,7 / 435,8 / 546,1 / '
                '577-579 nm) son lo más preciso: fijan posición y '
                'curvatura (±1-2 nm). Con un <b>LED blanco</b> solo hay '
                'dos marcas — pico azul ~450 nm y hump de fósforo ~560 nm '
                '— y el resultado es afín (±5-10 nm). Hazlo <b>una vez</b> '
                'y al cambiar el montaje.</div>', unsafe_allow_html=True)

    src = st.radio("Fuente del LED", ["📷 Cámara USB", "📁 Subir archivo"],
                   horizontal=True, key="fuente_cal")
    if src.startswith("📷"):
        camera_panel([("cal", "📸 Capturar LED para calibrar")])
        _thumbs()
        data = st.session_state.get("foto_cal")
    else:
        f = st.file_uploader("📷 Foto del espectro del LED (sin muestra)",
                             type=["png", "jpg", "jpeg", "bmp"],
                             key="up_cal")
        data = f.getvalue() if f is not None else None

    method = st.radio("Método", ["🎯 Automático (líneas Hg → LED)",
                                 "⚡ Líneas de mercurio/CFL",
                                 "🟦 LED blanco",
                                 "📍 Anclas manuales",
                                 "📏 Rango asumido",
                                 "🔴 Cuerpo negro (solo filamento)"],
                      horizontal=True)

    anchors = None
    if method.startswith("📍"):
        st.caption("Indica pares (píxel, nm). Con lámpara de mercurio: "
                   "435.8, 546.1, 577 y 579 nm.")
        if "anchor_rows" not in st.session_state:
            st.session_state.anchor_rows = [{"px": 0, "nm": 450.0},
                                            {"px": 1000, "nm": 700.0}]
        edited = st.data_editor(st.session_state.anchor_rows, num_rows="dynamic",
                                column_config={
                                    "px": st.column_config.NumberColumn(
                                        "píxel", min_value=0),
                                    "nm": st.column_config.NumberColumn(
                                        "longitud de onda (nm)", min_value=100.0,
                                        max_value=2500.0)})
        anchors = [(r["px"], r["nm"]) for r in edited
                   if r.get("px") is not None]
    rng = None
    if method.startswith("📏"):
        a, b = st.number_input("nm del inicio de la banda", 100.0, 2500.0,
                               400.0), st.number_input(
            "nm del final de la banda", 100.0, 2500.0, 700.0)
        rng = [a, b]
    bb = None
    if method.startswith("🔴"):
        bb = st.slider("Temperatura de color de la bombilla (K)", 2000, 4000,
                       3000, 50)

    if st.button("⚙️ Calibrar", type="primary", disabled=data is None):
        if data is None:
            st.warning("Captura o sube primero la foto del LED.")
        else:
            with tempfile.TemporaryDirectory() as td:
                img_path = Path(td) / "led.png"
                img_path.write_bytes(data)
                out_path = Path(td) / "cal.json"
                pv = Path(td) / "cal_prev.png"
                ns = argparse.Namespace(
                    image=str(img_path), dark=None, axis=None, channel="sum",
                    smooth=5, anchor=anchors or [],
                    led=method.startswith("🟦"),
                    lines=method.startswith("⚡"),
                    range=rng,
                    blackbody=bb, output=str(out_path), preview=str(pv))
                out = _run_cli(cmd_calibrate, ns)
                st.code(out, language=None)
                coeffs, meta = load_cal(out_path)
                set_active_cal(coeffs, meta, out_path)
                st.session_state["cal_preview"] = pv.read_bytes()
                st.session_state["cal_raw"] = data

    if "cal_preview" in st.session_state:
        st.image(st.session_state["cal_preview"], use_container_width=True)
        coeffs, meta = active_cal()
        if coeffs is not None:
            m = {k: v for k, v in meta.items() if k != "source_image"}
            st.json(m)

    st.divider()
    st.markdown("#### 💾 Calibraciones guardadas")
    saved = sorted(CAL_DIR.glob("*.json")) if CAL_DIR.exists() else []
    if not saved:
        st.caption("Aún no hay calibraciones guardadas.")
    else:
        cols = st.columns(min(4, len(saved)))
        for i, p in enumerate(saved):
            with cols[i % len(cols)]:
                if st.button(f"Activar {p.stem}", key=f"act_{p.name}"):
                    c, m = load_cal(p)
                    set_active_cal(c, m, p)
                    st.rerun()
                st.caption(f"{p.name} · {p.stat().st_size} B")


# ----------------------------------------------------- página: bibliotecas
def page_library() -> None:
    st.title("📚 Bibliotecas de materiales")
    spectra, _ = load_libs()
    from collections import Counter
    cats = Counter((s.category or "sin categoría") for s in spectra)
    srcs = Counter((s.source or "?") for s in spectra)

    m = st.columns(3)
    m[0].metric("Espectros", len(spectra))
    m[1].metric("Categorías", len(cats))
    m[2].metric("Colecciones", len(srcs))
    st.bar_chart({"Materiales por categoría": dict(cats.most_common())},
                 horizontal=True)

    st.markdown("#### Añadir colecciones")
    st.caption("· USGS splib05a (740 ya incluidos) · pigmentos Zenodo "
               "(116 ya incluidos) · ECOSTRESS >3400 (descarga manual en "
               "speclib.jpl.nasa.gov, se leen sus .spectrum.txt) · USGS v7 "
               "· cualquier CSV propio wavelength_nm,reflectance")
    sc1, sc2 = st.columns(2)
    if sc1.button("⬇️ Descargar USGS splib05a"):
        _run_script("scripts/fetch_usgs.py")
    if sc2.button("⬇️ Descargar pigmentos (Zenodo)"):
        _run_script("scripts/fetch_pigments.py")

    st.markdown("#### Subir un espectro propio")
    up = st.file_uploader("CSV con columnas wavelength_nm,reflectance",
                          type=["csv", "txt", "asc"])
    if up is not None and st.button("Guardar en la biblioteca"):
        dest = LIB_DIR / "mis_espectros"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / up.name).write_bytes(up.getvalue())
        load_libs.clear()
        st.success(f"Guardado {up.name}. Ya está en el análisis.")
        st.rerun()


def _run_script(rel: str) -> None:
    with st.spinner(f"Ejecutando {rel}…"):
        r = subprocess.run([sys.executable, str(ROOT / rel)],
                           capture_output=True, text=True, timeout=1800)
    (st.code if r.returncode == 0 else st.error)(r.stdout[-3000:])
    if r.returncode != 0:
        st.error(r.stderr[-2000:])
    else:
        load_libs.clear()
        st.success("Listo — biblioteca actualizada.")


# --------------------------------------------------------------- página: guía
def page_guide() -> None:
    st.title("💡 Guía rápida")
    st.markdown("""
**Flujo completo (3 pasos, una sola vez la calibración):**

1. **🔧 Calibración** — fotografía tu LED blanco sin muestra → *LED
   automático* → guardar. Repetir solo si cambias el montaje o el foco.
2. **🎯 Analizar** — sube la *foto de la muestra* y la *foto del LED*
   (misma exposición) → *Analizar*.
3. Lee el resultado: mejor candidato, score, ΔE de color y las curvas
   superpuestas. Descarga CSV/PNG si quieres.

**Captura correcta**

| Hacer | Evitar |
|---|---|
| Misma exposición en LED y muestra | Auto-exposición distinta |
| PNG/BMP sin procesar | JPEG (introduce bandas falsas) |
| Sin píxeles a 255 (saturado) | Banda roja/azul recortada |
| Foto oscura para restar ruido | Ruido de fondo sin restar |
| Banda nítida y centrada | Banda difusa o cortada |

**Qué mide y qué no**

- La división *muestra ÷ LED* cancela lámpara, óptica y sensor → conserva
  la forma espectral (pseudo-reflectancia). No es reflectancia absoluta.
- Solo se ve **400-700 nm**: muchos materiales se parecen en el visible.
  El score combina tres señales: forma espectral (SAM), bandas de
  absorción (continuo) y **color CIELAB (ΔE)** — por eso el color de la
  muestra se muestra junto al candidato.
- Para máxima precisión: **lámpara de mercurio** (picos 435.8 / 546.1 /
  577 nm) y calibra con *Anclas manuales* (±1-2 nm en vez de ±10).

**Interpretación del score**

| score | significado |
|---|---|
| > 0.85 + ΔE < 8 | muy probable |
| 0.65 – 0.85 | candidato razonable, mira las bandas |
| < 0.65 | material no está en la biblioteca o hay ruido/calibración mal |
""")


# --------------------------------------------------------------------- main
def main() -> None:
    cal_info = active_cal()
    spectra, _ = load_libs()
    page = status_sidebar(cal_info, len(spectra))
    if page.startswith("🎯"):
        page_analyze(cal_info)
    elif page.startswith("🔧"):
        page_calibrate(cal_info)
    else:
        _release_cam()          # la cámara solo vive en las pantallas de toma
        if page.startswith("📚"):
            page_library()
        else:
            page_guide()


if __name__ == "__main__":
    main()
