#!/usr/bin/env python3
"""Spectrometer API (FastAPI) + React frontend server.

    .venv/bin/python server.py            # http://localhost:8000
    .venv/bin/uvicorn server:app --reload # development

Reuses the `espectro/` engine (extraction, LED calibration, libraries,
CIELAB colour matching) and exposes it as JSON for the UI.
"""
from __future__ import annotations

import base64
import io
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from espectro import calib as cal
from espectro import camara as cam
from espectro import image as im
from espectro import library as lib
from espectro import match as mtc
from espectro import pipeline
from espectro import plotting as pl
from espectro.calib import load_cal, save_cal
from espectro.color import lab_of_spectrum, lab_to_hex

ROOT = Path(__file__).resolve().parent
CAL_PATH = ROOT / "data" / "calibration" / "cal.json"
LIB_DIR = ROOT / "data" / "libraries"
DIST = ROOT / "frontend" / "dist"
GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)
SLOTS = ("sample", "lamp", "cal", "dark")


def _m(lang: str, es: str, en: str) -> str:
    """Bilingual message from the request's `lang` parameter."""
    return es if lang == "es" else en

app = FastAPI(title="Spectrometer API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ estado
STATE: dict = {"slots": {k: None for k in SLOTS}}      # PNG bytes por slot
LIB_CACHE: dict = {"libs": None, "version": 0}
FETCH_STATUS: dict = {"running": False, "log": "", "done": False}


class _Cam:
    """Shared USB camera (a single V4L2 handle)."""

    def __init__(self) -> None:
        self.cap = None
        self.index: int | None = None
        self.expo: int = -6
        self.auto: bool = True
        self.lock = threading.Lock()

    def open(self, index: int | None = None, lang: str = "en") -> None:
        with self.lock:
            if self.cap is not None and (index is None or index == self.index):
                return
            self.close_locked()
            idx = index if index is not None else _default_cam_index(lang)
            self.cap = cam.open_camera(idx, warmup=6)
            self.index = idx
            if not self.auto:
                cam.set_exposure(self.cap, self.expo, auto_off=True)

    def close(self) -> None:
        with self.lock:
            self.close_locked()

    def close_locked(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:                            # noqa: BLE001
                pass
            self.cap = None
            self.index = None

    def read(self) -> np.ndarray | None:
        with self.lock:
            if self.cap is None:
                return None
            return cam.read_rgb(self.cap)


CAM = _Cam()


def _default_cam_index(lang: str = "en") -> int:
    cams = cam.probe_cameras()
    idx = cam.default_index(cams)
    if idx is None:
        raise HTTPException(400, _m(lang, "No hay ninguna cámara USB conectada",
                                    "No USB camera connected"))
    return idx


def _png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _img_from_slot(slot: str, lang: str = "en") -> np.ndarray:
    data = STATE["slots"].get(slot)
    if not data:
        raise HTTPException(400, _m(lang, f"No hay imagen en la ranura '{slot}'",
                                    f"No image in slot '{slot}'"))
    return np.asarray(Image.open(io.BytesIO(data)))


# --------------------------------------------------------------- library
def get_libs(fwhm: float | None = lib.FWHM_DEFAULT):
    """(spectra, paired) cached; invalidated when files are added or the
    requested resolution (`fwhm`) changes."""
    marker = sum(1 for _ in LIB_DIR.rglob("*.csv")) * 1_000_000 + \
        sum(p.stat().st_mtime for p in LIB_DIR.rglob("*") if p.is_file())
    key = (marker, None if fwhm is None else round(float(fwhm), 3))
    if LIB_CACHE["libs"] is None or LIB_CACHE.get("marker") != key:
        spectra = lib.load_library(LIB_DIR)
        LIB_CACHE["libs"] = (spectra, lib.to_grid(spectra, GRID, fwhm=fwhm))
        LIB_CACHE["marker"] = key
    return LIB_CACHE["libs"]


# ---------------------------------------------------------------- endpoints
@app.get("/api/status")
def status(refresh_cameras: bool = False) -> dict:
    cal_info = None
    if CAL_PATH.exists():
        coeffs, meta = load_cal(CAL_PATH)
        cal_info = {"mode": meta.get("mode"),
                    "coeffs": [float(round(c, 6)) for c in coeffs],
                    "led_corr": meta.get("led_corr"),
                    "led_peaks": meta.get("led_peaks_pixel"),
                    "fwhm_nm": meta.get("fwhm_nm"),
                    "file": str(CAL_PATH.relative_to(ROOT))}
    try:
        cameras = cam.probe_cameras(refresh=refresh_cameras)
    except RuntimeError:
        cameras = []
    try:
        spectra, _ = get_libs()
        from collections import Counter
        cats = Counter(s.category or "uncategorized" for s in spectra)
        srcs = Counter(s.source or "?" for s in spectra)
        lib_info = {"total": len(spectra), "categories": dict(cats.most_common()),
                    "sources": dict(srcs.most_common())}
    except Exception:                                     # noqa: BLE001
        lib_info = {"total": 0, "categories": {}, "sources": {}}
    return {
        "calibration": cal_info,
        "cameras": cameras,
        "slots": {k: STATE["slots"].get(k) is not None for k in SLOTS},
        "library": lib_info,
        "fetch": FETCH_STATUS,
    }


@app.get("/api/stream")
def stream() -> StreamingResponse:
    """Live preview (MJPEG)."""

    def gen():
        try:
            CAM.open()
        except Exception as e:                           # noqa: BLE001
            yield (b"--frame\r\nContent-Type: text/plain\r\n\r\n"
                   + str(e).encode() + b"\r\n")
            return
        while True:
            rgb = CAM.read()
            if rgb is None:
                time.sleep(0.2)
                continue
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, format="JPEG", quality=85)
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + buf.getvalue() + b"\r\n")
            time.sleep(0.02)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; "
                             "boundary=frame")


@app.post("/api/camera/settings")
def camera_settings(payload: dict) -> dict:
    idx = payload.get("index")
    if "expo" in payload:
        CAM.expo = int(payload["expo"])
    if "auto" in payload:
        CAM.auto = bool(payload["auto"])
    if idx is not None and idx != CAM.index:
        CAM.close()
        if idx >= 0:
            try:
                CAM.open(int(idx), lang=payload.get("lang", "en"))
            except HTTPException:
                raise
            except Exception as e:                        # noqa: BLE001
                raise HTTPException(400, str(e))
    elif CAM.cap is not None:
        cam.set_exposure(CAM.cap, CAM.expo, auto_off=not CAM.auto)
    return {"index": CAM.index, "expo": CAM.expo, "auto": CAM.auto}


@app.post("/api/camera/refresh")
def camera_refresh() -> dict:
    CAM.close()
    cam.probe_cameras(refresh=True)
    return {"cameras": cam.probe_cameras()}


@app.post("/api/camera/stop")
def camera_stop() -> dict:
    CAM.close()
    return {"ok": True}


@app.post("/api/capture/{slot}")
def capture(slot: str, lang: str = "en") -> dict:
    if slot not in SLOTS:
        raise HTTPException(404, _m(lang, "ranura desconocida",
                                    "unknown slot"))
    if CAM.cap is None:
        try:
            CAM.open(lang=lang)
        except HTTPException:
            raise
        except Exception as e:                           # noqa: BLE001
            raise HTTPException(400, str(e))
        opened = True
    else:
        opened = False
    rgb = None
    for _ in range(6):               # discard auto-exposure frames
        rgb = CAM.read()
        if rgb is not None:
            break
        time.sleep(0.05)
    if rgb is None:
        if opened:
            CAM.close()
        raise HTTPException(500, _m(lang, "No se pudo leer de la cámara",
                                    "Could not read from the camera"))
    STATE["slots"][slot] = _png_bytes(rgb)
    if opened:
        CAM.close()
    return {"ok": True, "slot": slot}


@app.get("/api/frame/{slot}")
def frame(slot: str, lang: str = "en") -> Response:
    data = STATE["slots"].get(slot)
    if not data:
        raise HTTPException(404, _m(lang, "sin imagen", "no image"))
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/frame/{slot}")
async def frame_upload(slot: str, file: UploadFile = File(...),
                       lang: str = "en") -> dict:
    """Upload an existing photo into a slot (camera alternative)."""
    if slot not in SLOTS:
        raise HTTPException(404, _m(lang, "ranura desconocida", "unknown slot"))
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(400, _m(lang, "archivo de imagen no válido",
                                    "not a valid image file")) from exc
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    STATE["slots"][slot] = buf.getvalue()
    return {"ok": True, "slot": slot}


@app.delete("/api/frame/{slot}")
def frame_delete(slot: str) -> dict:
    STATE["slots"].pop(slot, None)
    return {"ok": True}


@app.post("/api/analyze")
def analyze(payload: dict) -> dict:
    lang = payload.get("lang", "en")
    slot = payload.get("slot", "sample")
    channel = payload.get("channel", "sum")
    gamma = float(payload.get("gamma", 1.0))
    img = im.array_gray(_img_from_slot(slot, lang), channel)
    # relative brightness (to warn about dark photos in the UI)
    maxv = 765.0 if channel == "sum" else 255.0
    brightness = float(np.clip(img.mean() / maxv, 0.0, 1.0))
    dark = (im.array_gray(_img_from_slot("dark", lang), channel)
            if STATE["slots"].get("dark") else None)
    rows = tuple(payload["rows"]) if payload.get("rows") else None
    ext = im.extract_profile(
        img, rows=rows,
        axis=None if payload.get("axis", "auto") == "auto" else payload["axis"],
        dark=dark, smooth=int(payload.get("smooth", 5)),
        frac=float(payload.get("frac", 0.25)))

    # --- reference bulb (if present)
    lamp_png = STATE["slots"].get("lamp")
    lamp_intensity = None
    if lamp_png:
        lamp_img = im.array_gray(_img_from_slot("lamp"), channel)
        ext_l = im.extract_profile(lamp_img, axis=ext.axis, smooth=5,
                                   frac=float(payload.get("frac", 0.25)))
        lamp_intensity = ext_l.intensity

    # --- calibration pixel → nm
    if not CAL_PATH.exists():
        raise HTTPException(400, _m(
            lang, "Falta la calibración: captura el LED en la pestaña Calibración",
            "Calibration missing: capture the LED in the Calibration tab"))
    coeffs, cal_meta = load_cal(CAL_PATH)
    # instrument resolution: asked by the client; otherwise the one the
    # calibration measured with the mercury lines; otherwise the default.
    # `0` / `"off"` disables it.
    raw_fwhm = payload.get("fwhm")
    if raw_fwhm in (0, "off"):
        fwhm = None
    elif raw_fwhm in (None, "", "auto"):
        fwhm = float(cal_meta.get("fwhm_nm") or lib.FWHM_DEFAULT)
    else:
        fwhm = float(raw_fwhm)
    _, paired = get_libs(fwhm)

    # --- pseudo-reflectance + crossing (logic shared with app.py)
    try:
        an = pipeline.analyze(
            ext.intensity, lamp_intensity, ext.pixel, coeffs, paired,
            gamma=gamma, full_scale=maxv, smooth=5,
            top=int(payload.get("top", 10)))
    except pipeline.LowCoverage as e:
        raise HTTPException(400, _m(
            lang,
            f"La calibración solo cubre {e.covered}/{e.total} puntos de la "
            "banda — recalibra",
            f"The calibration only covers {e.covered}/{e.total} points of "
            "the band — recalibrate")) from e

    results = an.results
    y_show = an.y_show
    curves = pipeline.curves_of(results, paired)

    r3 = lambda a: [round(float(v), 3) for v in a]
    r1 = lambda a: [round(float(v), 1) for v in a]
    return {
        "axis": ext.axis,
        "brightness": round(brightness, 4),
        "rows": [int(ext.rows[0]), int(ext.rows[1])],
        "image_shape": [int(img.shape[0]), int(img.shape[1])],
        "has_lamp": an.has_lamp,
        "domain": "nm" if an.has_lamp else "pixel",
        "gamma": gamma,
        "fwhm": fwhm,
        "verdict": results.verdict.as_dict(),
        "coverage": round(an.coverage, 4),
        "profile": {"pixel": [int(p) for p in ext.pixel],
                    "intensity": r3(ext.intensity)},
        "curve": {"nm": r1(GRID), "reflectance": r3(y_show)},
        "continuum": r3(mtc.continuum_removal(GRID, y_show)),
        "sample_color": an.color,
        "results": [
            {
                "name": r.spectrum.name,
                "category": r.spectrum.category,
                "source": r.spectrum.source,
                "score": round(float(r.score), 4)
                if np.isfinite(r.score) else 0.0,
                "color": r.color_hex,
                "delta_e": round(float(r.delta_e), 2)
                if np.isfinite(r.delta_e) else 0.0,
                "sam_deg": round(float(r.sam_deg), 2)
                if np.isfinite(r.sam_deg) else 0.0,
                "r_raw": round(float(r.r_raw), 4)
                if np.isfinite(r.r_raw) else 0.0,
                "r_continuum": round(float(r.r_continuum), 4)
                if np.isfinite(r.r_continuum) else 0.0,
                "delta_banda": round(float(r.max_depth_diff), 4),
                "curve": r3(curves.get(r.spectrum.name, [])),
                "continuum": r3(mtc.continuum_removal(
                    GRID, curves.get(r.spectrum.name, np.zeros_like(GRID))))
            }
            for r in results
        ],
    }


@app.post("/api/calibrate")
def calibrate(payload: dict) -> dict:
    lang = payload.get("lang", "en")
    img = _img_from_slot("cal", lang)
    with tempfile_dir() as td:
        img_path = Path(td) / "led.png"
        Image.fromarray(img).save(img_path)
        out_path = Path(td) / "cal.json"
        pv = Path(td) / "preview.png"
        method = payload.get("method", "auto")
        anchors = payload.get("anchors") or []
        ns = _ns(image=str(img_path), dark=None, axis=None, channel="sum",
                 smooth=5, anchor=[(float(a), float(b)) for a, b in anchors],
                 led=method == "led", lines=method == "lines",
                 range=[float(v) for v in payload["range"]]
                 if payload.get("range") else None,
                 blackbody=float(payload["blackbody"])
                 if payload.get("blackbody") is not None else None,
                 output=str(out_path), preview=str(pv), lang=lang)
        log = _run_cmd_calibrate(ns)
        if not out_path.exists():
            raise HTTPException(400, log.strip() or _m(
                lang, "calibración fallida", "calibration failed"))
        coeffs, meta = load_cal(out_path)
        save_cal(CAL_PATH, coeffs, meta)
        pv_b64 = base64.b64encode(pv.read_bytes()).decode() if pv.exists() else None
    return {"ok": True, "log": log, "meta": meta,
            "coeffs": [float(round(c, 6)) for c in coeffs],
            "preview_b64": pv_b64}


class tempfile_dir:
    """Context manager minimalista (tempfile.TemporaryDirectory)."""

    def __enter__(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        return self._td.name

    def __exit__(self, *exc):
        self._td.cleanup()
        return False


def _ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


def _run_cmd_calibrate(ns) -> str:
    import contextlib
    buf = io.StringIO()
    from espectro.cli import cmd_calibrate
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            cmd_calibrate(ns)
        except SystemExit as e:
            buf.write(f"ERROR: {e}\n")
    return buf.getvalue()


@app.get("/api/calibration/preview")
def calibration_preview(lang: str = "en") -> Response:
    # se regenera desde la foto 'cal' + cal vigente
    if not STATE["slots"].get("cal") or not CAL_PATH.exists():
        raise HTTPException(404, _m(lang, "sin vista previa", "no preview"))
    img = _img_from_slot("cal", lang)
    coeffs, meta = load_cal(CAL_PATH)
    ext = im.extract_profile(im.array_gray(img, "sum"), smooth=5)
    with tempfile_dir() as td:
        pv = Path(td) / "p.png"
        model = cal.led_model if meta.get("mode") == "led" else None
        pl.plot_calibration(ext.pixel, ext.intensity, coeffs,
                            meta.get("blackbody_T"), meta.get("blackbody_corr"),
                            pv, model_fn=model)
        data = pv.read_bytes()
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/library")
def library_stats() -> dict:
    spectra, _ = get_libs()
    from collections import Counter
    return {"total": len(spectra),
            "categories": dict(Counter(s.category or "uncategorized"
                                       for s in spectra).most_common()),
            "sources": dict(Counter(s.source or "?" for s in spectra).most_common())}


@app.get("/api/library/search")
def library_search(q: str = "", limit: int = 24, lang: str = "en") -> dict:
    """Autocomplete: search by name (priority) and by category."""
    spectra, _ = get_libs()
    ql = q.strip().lower()
    if not ql:
        return {"results": [], "total": 0}
    by_name, by_cat = [], []
    for s in spectra:
        if ql in s.name.lower():
            by_name.append(s)
        elif ql in (s.category or "").lower():
            by_cat.append(s)
    hits = (by_name + by_cat)[:limit]
    return {
        "total": len(by_name) + len(by_cat),
        "results": [{"name": s.name, "category": s.category,
                     "source": s.source} for s in hits],
    }


@app.get("/api/library/spectrum")
def library_spectrum(name: str, lang: str = "en") -> dict:
    """Material detail card: data + color + visible window of the spectrum."""
    spectra, _ = get_libs()
    spec = next((s for s in spectra if s.name == name), None)
    if spec is None:
        raise HTTPException(404, _m(lang, "Material no encontrado",
                                    "Material not found"))
    mask = (spec.nm >= 380) & (spec.nm <= 800)
    if int(mask.sum()) < 8:
        mask = np.ones(spec.nm.shape, dtype=bool)
    nm, y = spec.nm[mask], spec.reflectance[mask]
    if nm.size > 260:                       # downsample for the JSON
        idx = np.linspace(0, nm.size - 1, 260).astype(int)
        nm, y = nm[idx], y[idx]
    lab = lab_of_spectrum(spec.nm, spec.reflectance)
    return {
        "name": spec.name,
        "category": spec.category,
        "source": spec.source,
        "path": str(spec.path),
        "color": lab_to_hex(lab),
        "nm": [round(float(v), 1) for v in nm],
        "reflectance": [round(float(v), 4) for v in y],
        "stats": {
            "min": round(float(np.min(y)), 4),
            "max": round(float(np.max(y)), 4),
            "mean": round(float(np.mean(y)), 4),
            "range_nm": [round(float(spec.nm.min()), 1),
                         round(float(spec.nm.max()), 1)],
            "points": int(spec.nm.size),
        },
    }


@app.post("/api/library/fetch/{name}")
def library_fetch(name: str, lang: str = "en") -> dict:
    script = {"usgs": "fetch_usgs.py", "pigments": "fetch_pigments.py",
              "revigo": "fetch_revigo.py"}.get(name)
    if not script:
        raise HTTPException(404, _m(lang, "colección desconocida",
                                    "unknown collection"))
    if FETCH_STATUS["running"]:
        raise HTTPException(409, _m(lang, "ya hay una descarga en curso",
                                    "a download is already running"))
    path = ROOT / "scripts" / script
    if not path.exists():
        raise HTTPException(404, _m(lang, f"no existe {script}",
                                    f"{script} does not exist"))

    def run():
        FETCH_STATUS.update(running=True, done=False, log="")
        try:
            p = subprocess.run([sys.executable, str(path)],
                               capture_output=True, text=True, timeout=3600)
            FETCH_STATUS["log"] = (p.stdout + p.stderr)[-6000:]
        except Exception as e:                           # noqa: BLE001
            FETCH_STATUS["log"] = str(e)
        FETCH_STATUS.update(running=False, done=True)

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


@app.post("/api/library/upload")
async def library_upload(file: UploadFile = File(...)) -> dict:
    dest = LIB_DIR / "mis_espectros"
    dest.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename or "espectro.csv").name
    (dest / safe).write_bytes(await file.read())
    LIB_CACHE["libs"] = None
    return {"ok": True, "saved": safe}


# ------------------------------------------------------------------ static
@app.get("/")
def index() -> FileResponse:
    if not (DIST / "index.html").exists():
        raise HTTPException(503, "Frontend no construido: "
                                 "cd frontend && npm install && npm run build")
    return FileResponse(DIST / "index.html")


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> FileResponse:
    """SPA routing: any path serves index.html."""
    if full_path.startswith("api/"):
        raise HTTPException(404, "endpoint desconocido")
    candidate = DIST / full_path
    if full_path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
