#!/usr/bin/env python3
"""Graphical interface of the spectrometer (Streamlit).

    .venv/bin/streamlit run app.py

Designed around4 screens:

  🎯 Analyze   — upload photos → settings with preview → results with
                  colour, ΔE, table and overlaid charts + downloads.
  🔧 Calibration — upload the bulb photo, choose the method (auto
                  LED by default) and save the active calibration.
  📚 Libraries— browse, extend and upload material databases.
  💡 Guide — how to take good photos and what to expect from the analysis.
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
from espectro import pipeline
from espectro import plotting as pl
from espectro.calib import load_cal
from espectro.cli import CAL_DIR, LIB_DIR, cmd_calibrate

ROOT = Path(__file__).resolve().parent
GRID = pipeline.GRID          # misma rejilla que la API REST

st.set_page_config(page_title="Spectrometer", page_icon="🔬",
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
    """Runs a CLI subcommand capturing its output for the UI."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            fn(ns)
        except SystemExit as e:
            print(f"ERROR: {e}")
    return buf.getvalue()


@st.cache_resource(show_spinner="Loading material libraries…")
def load_libs(fwhm: float | None = lib.FWHM_DEFAULT
              ) -> tuple[list[lib.Spectrum], list[tuple[lib.Spectrum, np.ndarray]]]:
    spectra = lib.load_library(LIB_DIR)
    return spectra, lib.to_grid(spectra, GRID, fwhm=fwhm)


def active_cal() -> tuple[np.ndarray | None, dict]:
    """Active calibration: session > data/calibration/cal.json."""
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
        st.markdown('<div class="brand">🔬 Spectrometer</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="tag">USB camera + dispersion tube</div>',
                    unsafe_allow_html=True)
        page = st.radio("Navigation",
                        ["🎯 Analyze sample", "🔧 Calibration",
                         "📚 Libraries", "💡 Guide"],
                        label_visibility="collapsed")
        st.divider()
        st.markdown("**Estado**")
        if coeffs is not None:
            modo = {"led": "🟦 White LED", "lines": "⚡ Mercury/CFL lines",
                    "anchors": "📍 Manual anchors",
                    "range": "📏 Assumed range",
                    "range_fallback": "📏 Rango (fallback)",
                    "blackbody": "🔴 Blackbody"}.get(
                        meta.get("mode", "?"), meta.get("mode", "?"))
            st.success(f"Calibration: {modo}")
            if meta.get("lines"):
                st.caption("lines used: " + ", ".join(
                    f"{d['nm']:g} nm" for d in meta["lines"]))
            if meta.get("led_corr") is not None and np.isfinite(meta.get("led_corr", np.nan)):
                st.caption(f"LED template fit: r = {meta['led_corr']:.3f}")
        else:
            st.warning("No calibration → go to **🔧 Calibration**")
        st.info(f"📚 {n_lib} materials in the database")
    return page


def swatches(items: list[tuple[str, str]], title: str) -> None:
    """Renders a row of colour swatches (label, hex)."""
    html = f'<div style="margin:.2rem 0 .4rem 0"><b>{title}</b><br>'
    for label, hx in items:
        html += (f'<span class="swatch" style="background:{hx}"></span>'
                 f'<span class="swlabel">{label}</span>')
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------- USB camera
def _release_cam() -> None:
    cap = st.session_state.pop("cam_cap", None)
    if cap is not None:
        try:
            cap.release()
        except Exception:                            # noqa: BLE001
            pass
    st.session_state["cam_live"] = False


def _capture_cb(slot: str) -> None:
    """Saves the last frame as PNG into `photo_<slot>` and freezes the view."""
    frame = st.session_state.get("cam_frame")
    if frame is None:
        return
    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, format="PNG")
    st.session_state[f"photo_{slot}"] = buf.getvalue()
    st.session_state["cam_live"] = False


def camera_panel(slots: list[tuple[str, str]]) -> None:
    """Live stream of the USB camera + capture buttons.

    `slots` = [(key, label), ...]; every button stores a PNG in
    `st.session_state['photo_<clave>']`.
    """
    try:
        cams = cam.probe_cameras()
    except RuntimeError as e:
        st.error(str(e))
        return
    if not cams:
        c1, c2 = st.columns([4, 1])
        c1.warning("No USB camera detected. Is it plugged in? "
                   "Also check the `video` group permissions.")
        if c2.button("🔄 Reintentar", key="cam_retry"):
            cam.probe_cameras(refresh=True)
            st.rerun()
        return

    def _label(i: int) -> str:
        c = next(c for c in cams if c["index"] == i)
        mono = " · monochrome" if c["fourcc"] == "GREY" else ""
        return (f"/dev/video{i} — {c['w']}×{c['h']} {c['fourcc']} "
                f"{c['fps']:.0f}fps{mono}")

    idx = st.selectbox("Camera", [c["index"] for c in cams],
                       format_func=_label, key="cam_index",
                       help="The spectrometer one is usually the one showing "
                            "como GREY (monochrome) o la de menor brillo")
    live = st.checkbox("🔴 Live stream", key="cam_live",
                       help="Keeps the camera open; it closes when "
                            "uncheck it or when you change screens")

    with st.expander("⚙️ Camera exposure (avoid saturating)"):
        e1, e2 = st.columns(2)
        expo = e1.slider("Exposure (V4L2)", -30, 0, -6, key="cam_expo",
                         help="More negative = less light. If your camera does not "
                              "admite, usa v4l2-ctl -d /dev/videoX "
                              "--set-ctrl=exposure_absolute=...")
        auto_off = e2.checkbox("Turn off auto-exposure", True,
                               key="cam_autoexp")

    # capture buttons always visible (they also work while streaming)
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
                st.error(f"Could not open the camera: {e}")
                st.session_state["cam_live"] = False
                return
        ph = st.empty()
        for _ in range(45):                           # ~2,7 s por pasada
            rgb = cam.read_rgb(cap)
            if rgb is None:
                st.error("The camera stopped responding.")
                st.session_state["cam_live"] = False
                break
            st.session_state["cam_frame"] = rgb
            ph.image(rgb, caption=_label(idx), use_container_width=True)
            time.sleep(0.06)
        st.rerun()                                    # re-read the widgets
    else:
        _release_cam()
        frame = st.session_state.get("cam_frame")
        if frame is not None:
            st.image(frame, caption="last frame — press a button to "
                                    "capture it to use it",
                     use_container_width=True)


def _thumbs() -> None:
    """Thumbnails of the captured/uploaded photos with their delete button."""
    labels = {"sample": "Muestra", "lamp": "LED (referencia)",
              "cal": "LED (calibration)"}
    items = [(k, st.session_state.get(f"photo_{k}"))
             for k in labels if st.session_state.get(f"photo_{k}")]
    if not items:
        return
    cols = st.columns(len(items))
    for col, (k, png) in zip(cols, items):
        with col:
            st.markdown(f"**{labels[k]}**")
            st.image(png, use_container_width=True)
            if st.button("✖ Borrar", key=f"clr_{k}"):
                st.session_state.pop(f"photo_{k}", None)
                st.rerun()


# ------------------------------------------------------- analyze page
def page_analyze(cal_info) -> None:
    st.title("🎯 Analyze a sample")
    source = st.radio("Photo source",
                      ["📷 USB camera", "📁 Upload files"],
                      horizontal=True, key="photo_source",
                      help="The camera reads your USB spectrometer directly")

    if source.startswith("📷"):
        camera_panel([("sample", "📸 CAPTURE SAMPLE"),
                      ("lamp", "📸 CAPTURE LED (reference)")])
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 1 · Sample photo")
            sf = st.file_uploader("Drag the photo of the sample spectrum",
                                  type=["png", "jpg", "jpeg",
                                        "bmp", "tif", "tiff"],
                                  key="up_sample")
            if sf is not None:
                st.session_state["photo_sample"] = sf.getvalue()
            st.caption("Same exposure as the LED, unsaturated, in PNG.")
        with c2:
            st.markdown("#### 2 · LED photo (reference)")
            lf = st.file_uploader("Drag the photo of the LED without the "
                                  "sample (cancels lamp and sensor)",
                                  type=["png", "jpg", "jpeg", "bmp"],
                                  key="up_lamp")
            if lf is not None:
                st.session_state["photo_lamp"] = lf.getvalue()
            st.caption("Recommended: without it only the raw shape is compared.")

    _thumbs()
    sample_png = st.session_state.get("photo_sample")
    lamp_png = st.session_state.get("photo_lamp")

    with st.expander("⚙️ Extraction and dark-frame settings (optional)"):
        dark_f = st.file_uploader("Dark photo (same exposure) to "
                                  "subtract noise", type=["png", "jpg"],
                                  key="up_dark")
        a1, a2, a3 = st.columns(3)
        axis = a1.selectbox("Dispersion axis", ["auto", "x", "y"])
        channel = a2.selectbox("Canal", ["sum", "max", "r", "g", "b", "luma"],
                               index=0,
                               help="sum = R+G+B, the right one for multicolour "
                                    "bands")
        smooth = a3.slider("Smoothing (pixels)", 1, 41, 5, step=2)
        f1, f2 = st.columns(2)
        frac = f1.slider("Band threshold", 0.05, 0.6, 0.25, 0.05)
        rows_auto = f2.checkbox("Automatic row detection", True)
        rows = None
        if not rows_auto:
            rr = st.slider("Band rows (start, end)", 0, 5000,
                           (0, 500))
            rows = tuple(rr)

    if sample_png is None:
        st.markdown('<div class="step">📷 Turn on the <b>live '
                    'stream</b>, place the sample and press <b>📸 CAPTURE '
                    'SAMPLE</b> (and earlier <b>📸 CAPTURE LED</b> with the '
                    'sample out of the beam). Or switch to '
                    '<i>📁 Upload files</i>.</div>',
                    unsafe_allow_html=True)
        return

    img = _to_gray(sample_png, channel)
    dark = _to_gray(dark_f.getvalue(), channel) if dark_f else None
    ext = im.extract_profile(img, rows=rows,
                             axis=None if axis == "auto" else axis,
                             dark=dark, smooth=smooth, frac=frac)

    st.markdown('<div class="step">👁️ Check that the cyan strip covers the '
                'band and that the profile below is smooth with signal across '
                'the whole range.</div>', unsafe_allow_html=True)
    with tempfile.TemporaryDirectory() as td:
        pv = Path(td) / "prev.png"
        pl.plot_extraction(img, ext.rows, ext.pixel, ext.intensity, pv,
                           axis=ext.axis)
        st.image(str(pv), use_container_width=True)

    coeffs, meta = cal_info
    if coeffs is None:
        st.warning("Pixel→nm calibration missing. Go to **🔧 Calibration** "
                   "(just the LED photo,10 seconds).")
        return
    if lamp_png is None:
        st.info("No LED photo: raw intensity will be compared instead. "
                "Capture it with **📸 CAPTURE LED** and the result improves "
                "a lot.")

    if st.button("🔍 Analyze and compare", type="primary",
                 use_container_width=True):
        with st.spinner("Extracting, calibrating and comparing…"):
            try:
                results, grid, y_grid, s_hex = _analyze(
                    ext, lamp_png, coeffs, channel, smooth, frac, axis)
            except pipeline.LowCoverage as e:
                st.error(f"⚠ {e}")
                return
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


def _analyze(ext, lamp_png, coeffs, channel, smooth, frac, axis, gamma=1.0):
    """Profile → pseudo-reflectance → crossing (see `espectro.pipeline`).

    The logic lives in `espectro/pipeline.py` and is the SAME one the
    REST API uses: here only the bulb profile is extracted and presented.
    """
    maxv = 765.0 if channel == "sum" else 255.0
    lamp_intensity = None
    if lamp_png:
        lamp_img = _to_gray(lamp_png, channel)
        ext_l = im.extract_profile(lamp_img, rows=None,
                                   axis=None if axis == "auto" else axis,
                                   smooth=smooth, frac=frac)
        lamp_intensity = ext_l.intensity
    _, paired = load_libs()
    an = pipeline.analyze(ext.intensity, lamp_intensity, ext.pixel, coeffs,
                          paired, gamma=gamma, full_scale=maxv,
                          smooth=smooth, top=10)
    # gaps are filled for display; the crossing uses the NaN
    return an.results, GRID, an.y_show, an.color


def _show_results(results, grid, y_grid, s_hex) -> None:
    if not results:
        st.error("No matches. Try uploading the reference bulb "
                 "reference or review the calibration.")
        return
    top = results[0]
    # candidate curves already resampled onto the grid
    lib_curves = {r.name: y for r, y in lib.to_grid(
        [r.spectrum for r in results], GRID, fwhm=lib.FWHM_DEFAULT)}

    st.markdown('<div class="winner">', unsafe_allow_html=True)
    w1, w2 = st.columns([3, 1])
    with w1:
        st.markdown(f"### 🏆 {top.spectrum.name}")
        st.caption(f"Category: {top.spectrum.category or '—'} · "
                   f"source: {top.spectrum.source or '—'}")
    with w2:
        st.metric("Confidence (score)", f"{top.score:.2f}",
                  f"ΔE color = {top.delta_e:.1f}")
    v = getattr(results, "verdict", None)
    if v is not None and v.label != "identified":
        st.warning({
            "unknown": ("⚠ UNKNOWN: no candidate reaches the threshold "
                        f"(score ≥ {v.min_score:.2f}). The sample may be "
                        "featureless, noise-dominated, or absent from the "
                        "library."),
            "uncertain": ("⚠ UNCERTAIN: there are good candidates but they "
                          "cannot be told apart (margin "
                          f"{v.margin:.3f} < {v.min_margin:.2f})."),
        }.get(v.label, ""))
    swatches([("sample", s_hex), (top.spectrum.name.split(' [')[0][:14],
                                   top.color_hex)],
             "Colour (D65): sample vs best candidate")
    st.markdown("</div>", unsafe_allow_html=True)

    tabs = st.tabs(["📈 Overlaid curves", "🪞 Bands (continuum)",
                    "🧮 Results table", "⬇️ Downloads"])
    with tabs[0]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cmp.png"
            pl.plot_matches(grid, y_grid, results, lib_curves, p)
            st.image(str(p), use_container_width=True)
        st.caption("Black = your sample (pseudo-reflectance). The "
                   "coloured lines are the candidate materials.")

    with tabs[1]:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from espectro.match import continuum_removal
        fig, ax = plt.subplots(figsize=(10, 4.2))
        ax.plot(grid, continuum_removal(grid, y_grid), "k-", lw=2.2,
                label="sample")
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
        st.caption("After removing the 'continuum' only the absorption "
                   "bands remain: the real chemical signature of the "
                   "material.")

    with tabs[2]:
        import pandas as pd
        df = pd.DataFrame([r.as_row() for r in results])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("score = 0.20·spectral angle + 0.10·bands + "
                   "0.70·colour (ΔE), with a penalty for badly "
                   "matched bands (that penalty does not depend on the weights). "
                   "ΔE: <5 nearly identical, >20 very different.")

    with tabs[3]:
        import pandas as pd
        csv = pd.DataFrame([r.as_row() for r in results]).to_csv(
            index=False).encode()
        st.download_button("⬇️ Download results (CSV)", csv,
                           "matches.csv", "text/csv")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cmp.png"
            pl.plot_matches(grid, y_grid, results, lib_curves, p)
            st.download_button("⬇️ Download chart (PNG)", Path(p).read_bytes(),
                               "comparison.png", "image/png")


# ---------------------------------------------------- calibration page
def page_calibrate(cal_info) -> None:
    st.title("🔧 Calibration pixel → nanometers")
    st.markdown('<div class="step">If you have a <b>mercury or '
                'CFL lamp</b>, its narrow lines (404.7 / 435.8 / 546.1 / '
                '577-579 nm) are the most accurate: they fix position and '
                'curvature (±1-2 nm). With a <b>white LED</b> there are only '
                'two marks — blue peak ~450 nm and phosphor hump ~560 nm '
                '— and the result is affine (±5-10 nm). Do it <b>once</b> '
                'and whenever you change the setup.</div>', unsafe_allow_html=True)

    src = st.radio("LED source", ["📷 USB camera", "📁 Upload file"],
                   horizontal=True, key="photo_source_cal")
    if src.startswith("📷"):
        camera_panel([("cal", "📸 CAPTURE LED to calibrate")])
        _thumbs()
        data = st.session_state.get("photo_cal")
    else:
        f = st.file_uploader("📷 Photo of the LED spectrum (no sample)",
                             type=["png", "jpg", "jpeg", "bmp"],
                             key="up_cal")
        data = f.getvalue() if f is not None else None

    method = st.radio("Method", ["🎯 Automatic (Hg lines → LED)",
                                 "⚡ Mercury/CFL lines",
                                 "🟦 White LED",
                                 "📍 Manual anchors",
                                 "📏 Assumed range",
                                 "🔴 Blackbody (filament only)"],
                      horizontal=True)

    anchors = None
    if method.startswith("📍"):
        st.caption("Enter pairs (pixel, nm). With a mercury lamp: "
                   "435.8, 546.1, 577 and 579 nm.")
        if "anchor_rows" not in st.session_state:
            st.session_state.anchor_rows = [{"px": 0, "nm": 450.0},
                                            {"px": 1000, "nm": 700.0}]
        edited = st.data_editor(st.session_state.anchor_rows, num_rows="dynamic",
                                column_config={
                                    "px": st.column_config.NumberColumn(
                                        "pixel", min_value=0),
                                    "nm": st.column_config.NumberColumn(
                                        "longitud de onda (nm)", min_value=100.0,
                                        max_value=2500.0)})
        anchors = [(r["px"], r["nm"]) for r in edited
                   if r.get("px") is not None]
    rng = None
    if method.startswith("📏"):
        a, b = st.number_input("nm at band start", 100.0, 2500.0,
                               400.0), st.number_input(
            "nm at band end", 100.0, 2500.0, 700.0)
        rng = [a, b]
    bb = None
    if method.startswith("🔴"):
        bb = st.slider("Bulb colour temperature (K)", 2000, 4000,
                       3000, 50)

    if st.button("⚙️ Calibrar", type="primary", disabled=data is None):
        if data is None:
            st.warning("Capture or upload the LED photo first.")
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
    st.markdown("#### 💾 Saved calibrations")
    saved = sorted(CAL_DIR.glob("*.json")) if CAL_DIR.exists() else []
    if not saved:
        st.caption("No saved calibrations yet.")
    else:
        cols = st.columns(min(4, len(saved)))
        for i, p in enumerate(saved):
            with cols[i % len(cols)]:
                if st.button(f"Activar {p.stem}", key=f"act_{p.name}"):
                    c, m = load_cal(p)
                    set_active_cal(c, m, p)
                    st.rerun()
                st.caption(f"{p.name} · {p.stat().st_size} B")


# ------------------------------------------------------ library page
def page_library() -> None:
    st.title("📚 Material libraries")
    spectra, _ = load_libs()
    from collections import Counter
    cats = Counter((s.category or "uncategorized") for s in spectra)
    srcs = Counter((s.source or "?") for s in spectra)

    m = st.columns(3)
    m[0].metric("Espectros", len(spectra))
    m[1].metric("Categories", len(cats))
    m[2].metric("Colecciones", len(srcs))
    st.bar_chart({"Materials by category": dict(cats.most_common())},
                 horizontal=True)

    st.markdown("#### Add collections")
    st.caption("· USGS splib05a (740 ya incluidos) · pigmentos Zenodo "
               "(116 already included) · ECOSTRESS >3400 (manual download at "
               "speclib.jpl.nasa.gov, se leen sus .spectrum.txt) · USGS v7 "
               "· any own CSV wavelength_nm,reflectance")
    sc1, sc2 = st.columns(2)
    if sc1.button("⬇️ Download USGS splib05a"):
        _run_script("scripts/fetch_usgs.py")
    if sc2.button("⬇️ Download pigments (Zenodo)"):
        _run_script("scripts/fetch_pigments.py")

    st.markdown("#### Upload your own spectrum")
    up = st.file_uploader("CSV con columnas wavelength_nm,reflectance",
                          type=["csv", "txt", "asc"])
    if up is not None and st.button("Save into the library"):
        dest = LIB_DIR / "mis_espectros"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / up.name).write_bytes(up.getvalue())
        load_libs.clear()
        st.success(f"Saved {up.name}. It is now available for analysis.")
        st.rerun()


def _run_script(rel: str) -> None:
    with st.spinner(f"Running {rel}…"):
        r = subprocess.run([sys.executable, str(ROOT / rel)],
                           capture_output=True, text=True, timeout=1800)
    (st.code if r.returncode == 0 else st.error)(r.stdout[-3000:])
    if r.returncode != 0:
        st.error(r.stderr[-2000:])
    else:
        load_libs.clear()
        st.success("Done — library updated.")


# ----------------------------------------------------------- guide page
def page_guide() -> None:
    st.title("💡 Quick guide")
    st.markdown("""
**Full flow (3 steps, calibration only once):**

1. **🔧 Calibration** — photograph your white LED with no sample → *auto
   LED* → save. Repeat only if you change the setup or the light source.
2. **🎯 Analyze** — upload the *sample photo* and the *LED photo*
   (same exposure) → *Analyze*.
3. Read the result: best candidate, score, colour ΔE and the overlaid
   curves. Download CSV/PNG if you want.

**Good capture**

| Do | Avoid |
|---|---|
| Same exposure on LED and sample | Different auto-exposure |
| Raw PNG/BMP | JPEG (introduces fake bands) |
| No pixels at 255 (saturated) | Clipped red/blue band |
| Dark photo to subtract noise | Background noise not subtracted |
| Sharp, centred band | Diffuse or cut-off band |

**What it measures and what it does not**

- The *sample ÷ LED* division cancels lamp, optics and sensor → keeps
  the spectral shape (pseudo-reflectance). It is not absolute
  reflectance.
- Only **400-700 nm** is visible: many materials look alike there.
  The score combines three signals: spectral shape (SAM),
  absorption bands (continuum) and **CIELAB colour (ΔE)** — which is why
  the sample swatch is shown next to the candidate.
- For maximum accuracy: **mercury lamp** (lines 435.8 / 546.1 /
  577 nm) and calibrate with *manual anchors* (±1-2 nm instead of ±10).

**Reading the score**

| score | meaning |
|---|---|
| > 0.85 + ΔE < 8 | very likely |
| 0.65 – 0.85 | reasonable candidate, look at the bands |
| < 0.65 | material not in the library, or noise/bad calibration |
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
        _release_cam()          # the camera only lives on the capture screens
        if page.startswith("📚"):
            page_library()
        else:
            page_guide()


if __name__ == "__main__":
    main()
