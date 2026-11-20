"""Direct capture from USB cameras (UVC) with OpenCV/V4L2.

    from espectro.camara import probe_cameras, open_camera, read_rgb

UVC cameras usually expose two nodes per device (image + metadata); only
the image ones can be opened.  Probing is cached with a TTL, and a busy
device (e.g. blocked by the live view itself) is still listed from sysfs
as `openable: False`.
"""
from __future__ import annotations

import time as _time

import numpy as np

_cam_cache: dict = {"ts": -1e9, "cams": []}
_TTL = 20.0        # seconds the probe stays valid


def cv2_module():
    try:
        import cv2
        return cv2
    except ImportError as exc:                       # pragma: no cover
        raise RuntimeError(
            "OpenCV is not installed → .venv/bin/pip install "
            "opencv-python-headless") from exc


def probe_cameras(max_index: int = 6, refresh: bool = False) -> list[dict]:
    """List of usable cameras:
    [{'index','w','h','fourcc','fps','name','openable'}...].

    `name` comes from sysfs and tells the laptop's built-in webcam apart
    from a real USB one.  A device that cannot be opened (busy) but does
    exist in sysfs is still listed with openable=False.
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
            # busy: list it only if it is not a duplicate node
            # (metadata nodes share the name with their camera)
            if sname and sname not in seen:
                seen.add(sname)
                found.append({"index": i, "w": 0, "h": 0, "fourcc": "",
                              "fps": 0.0, "name": sname, "openable": False})
            continue
        if sname and sname in seen:
            cap.release()
            continue                      # duplicate node (metadata)
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
    """Is it the laptop's built-in webcam?"""
    n = (cam_info.get("name") or "").lower()
    return any(k in n for k in ("integrated", "internal", "laptop",
                                "interno", "built-in"))


def default_index(cams: list[dict]) -> int | None:
    """Default index: ALWAYS an external USB camera when one exists
    (never the built-in laptop one)."""
    extern = [c for c in cams if not is_builtin(c)]
    pool = extern or cams
    return pool[0]["index"] if pool else None


def open_camera(index: int, warmup: int = 6):
    """Open /dev/video<index> and discard the first frames."""
    cv2 = cv2_module()
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"could not open /dev/video{index}")
    for _ in range(max(0, warmup)):
        cap.read()
    return cap


def read_rgb(cap) -> np.ndarray | None:
    """Next frame in RGB (uint8, HxWx3) or None on failure."""
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    if frame.ndim == 2:                     # monochrome sensors (GREY)
        frame = np.stack([frame] * 3, axis=-1)
    elif frame.shape[-1] == 4:
        frame = frame[..., :3]
    return np.ascontiguousarray(frame[:, :, ::-1])   # BGR → RGB


def set_exposure(cap, value: float | None = None,
                 auto_off: bool = True) -> bool:
    """V4L2 exposure control.  `auto_off=True` → manual (uses `value`);
    `auto_off=False` → restores the camera auto-exposure.
    Returns whether the camera accepted anything."""
    cv2 = cv2_module()
    changed = False
    if auto_off:
        for v in (0.25, 0):                       # manual mode (driver dependent)
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
        for v in (1.0, 0.75):                     # automatic mode
            try:
                if cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, v):
                    changed = True
                    break
            except Exception:                     # noqa: BLE001
                pass
    return changed
