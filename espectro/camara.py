"""Captura directa desde cámaras USB (UVC) con OpenCV/V4L2.

    from espectro.camara import probe_cameras, open_camera, read_rgb

Las cámaras UVC suelen exponer dos nodos por dispositivo (imagen +
metadatos); solo los de imagen abren.  El sondeo se cachea con TTL y si
un dispositivo está ocupado (p. ej. por la propia vista en vivo) se lista
igualmente desde sysfs como `openable: False`.
"""
from __future__ import annotations

import time as _time

import numpy as np

_cam_cache: dict = {"ts": -1e9, "cams": []}
_TTL = 20.0        # segundos de validez del sondeo


def cv2_module():
    try:
        import cv2
        return cv2
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError(
            "OpenCV no está instalado → .venv/bin/pip install "
            "opencv-python-headless") from exc


def probe_cameras(max_index: int = 6, refresh: bool = False) -> list[dict]:
    """Lista de cámaras utilizables:
    [{'index','w','h','fourcc','fps','name','openable'}...].

    `name` viene de sysfs y permite distinguir la webcam integrada del
    portátil de una USB real.  Si un dispositivo no abre (ocupado) pero
    existe en sysfs, se lista igualmente con openable=False.
    """
    global _cam_cache
    now = _time.monotonic()
    if not refresh and _cam_cache["cams"] and now - _cam_cache["ts"] < _TTL:
        return _cam_cache["cams"]
    cv2 = cv2_module()
    from pathlib import Path

    def sysfs_name(i: int) -> str:
        try:
            return Path(f"/sys/class/video4linux/video{i}/name").read_text(
                encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    found: list[dict] = []
    seen: set[str] = set()
    for i in range(max_index):
        sname = sysfs_name(i)
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            # ocupada: la listamos solo si no es un nodo duplicado
            # (los nodos de metadatos comparten el nombre con su cámara)
            if sname and sname not in seen:
                seen.add(sname)
                found.append({"index": i, "w": 0, "h": 0, "fourcc": "",
                              "fps": 0.0, "name": sname, "openable": False})
            continue
        if sname and sname in seen:
            cap.release()
            continue                      # nodo duplicado (metadatos)
        if sname:
            seen.add(sname)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        found.append({
            "index": i,
            "w": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "h": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fourcc": "".join(chr((fourcc >> 8 * k) & 0xFF)
                              for k in range(4)).strip(),
            "fps": float(cap.get(cv2.CAP_PROP_FPS)),
            "name": sname,
            "openable": True,
        })
        cap.release()
    _cam_cache = {"ts": now, "cams": found}
    return found


def is_builtin(cam_info: dict) -> bool:
    """¿Es la webcam integrada del portátil?"""
    n = (cam_info.get("name") or "").lower()
    return any(k in n for k in ("integrated", "internal", "laptop",
                                "interno", "built-in"))


def default_index(cams: list[dict]) -> int | None:
    """Índice por defecto: SIEMPRE una cámara USB externa si existe
    (nunca la integrada del portátil)."""
    extern = [c for c in cams if not is_builtin(c)]
    pool = extern or cams
    return pool[0]["index"] if pool else None


def open_camera(index: int, warmup: int = 6):
    """Abre /dev/video<index> y descarta los primeros frames."""
    cv2 = cv2_module()
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"No se pudo abrir /dev/video{index}")
    for _ in range(max(0, warmup)):
        cap.read()
    return cap


def read_rgb(cap) -> np.ndarray | None:
    """Siguiente frame en RGB (uint8, HxWx3) o None si falla."""
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    if frame.ndim == 2:                     # sensores monochrome (GREY)
        frame = np.stack([frame] * 3, axis=-1)
    elif frame.shape[-1] == 4:
        frame = frame[..., :3]
    return np.ascontiguousarray(frame[:, :, ::-1])   # BGR → RGB


def set_exposure(cap, value: float | None = None,
                 auto_off: bool = True) -> bool:
    """Control de exposición V4L2.  `auto_off=True` → manual (usa `value`);
    `auto_off=False` → restaura la auto-exposición de la cámara.
    Devuelve si la cámara admitió algo."""
    cv2 = cv2_module()
    changed = False
    if auto_off:
        for v in (0.25, 0):                       # modo manual (varía por driver)
            try:
                if cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v):
                    changed = True
                    break
            except Exception:                     # noqa: BLE001
                pass
        if value is not None:
            try:
                if cap.set(cv2.CAP_PROP_EXPOSURE, float(value)):
                    changed = True
            except Exception:                     # noqa: BLE001
                pass
    else:
        for v in (1.0, 0.75):                     # modo automático
            try:
                if cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v):
                    changed = True
                    break
            except Exception:                     # noqa: BLE001
                pass
    return changed
