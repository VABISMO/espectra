"""Pruebas end-to-end del espectrómetro (sin pytest: `python tests/run_tests.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 1. demo
def test_demo() -> None:
    print("\n1) demo end-to-end (LED + biblioteca + color)")
    from espectro.cli import main
    rc = main(["demo"])
    check("demo PASS", rc == 0, f"rc={rc}")


# ------------------------------------------------------- 2. calibración LED
def test_led_calibration() -> None:
    print("\n2) calibración automática por picos de LED")
    import numpy as np

    from espectro import calib as cal
    W = 1280
    p = np.arange(W, dtype=float)
    true_nm = 400.0 + p / (W - 1) * 300.0
    lamp = cal.led_model(true_nm)
    peaks = cal.detect_led_peaks(p, lamp)
    check("detecta dos picos", peaks is not None)
    if peaks:
        coeffs = cal.poly_from_anchors([(peaks[0], 450.0), (peaks[1], 560.0)])
        coeffs, r = cal.refine_led(coeffs, p, lamp)
        err = float(np.max(np.abs(np.polyval(coeffs, p) - true_nm)))
        check("error máximo < 5 nm", err < 5.0, f"err={err:.1f} nm")
        check("correlación > 0.999", r > 0.999, f"r={r:.4f}")


# ------------------------------------------------------------ 3. API REST
def test_api() -> None:
    print("\n3) API (FastAPI TestClient): estado, calibración, análisis")
    from fastapi.testclient import TestClient

    import server

    ex = ROOT / "data" / "examples"
    cal_path = ROOT / "data" / "calibration" / "cal.json"
    if not (ex / "demo_muestra.png").exists():
        check("fotos de ejemplo", False, "ejecuta demo antes")
        return

    client = TestClient(server.app)

    # --- estado
    r = client.get("/api/status")
    check("GET /api/status", r.status_code == 200)
    st = r.json()
    check("biblioteca cargada", st["library"]["total"] > 500,
          str(st["library"]["total"]))
    check("cámara detectada", len(st["cameras"]) >= 1)

    # --- frontend servido
    r = client.get("/")
    check("GET / (React)", r.status_code == 200 and "ESPECTRA" in r.text)

    # --- inyecta las fotos del demo como si las hubiera capturado la cámara
    for slot, name in (("sample", "demo_muestra.png"),
                       ("lamp", "demo_bombilla.png"),
                       ("cal", "demo_bombilla.png")):
        server.STATE["slots"][slot] = (ex / name).read_bytes()

    # --- calibración por la API (modo LED)
    if cal_path.exists():
        cal_path.unlink()
    r = client.post("/api/calibrate", json={"method": "led"})
    check("POST /api/calibrate", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        data = r.json()
        check("calibración modo led", data["meta"]["mode"] == "led")
        check("picos detectados", bool(data["meta"].get("led_peaks_pixel")))
        check("ajuste r > 0.99",
              float(data["meta"].get("led_corr") or 0) > 0.99,
              str(data["meta"].get("led_corr")))
        check("guardado en cal.json", cal_path.exists())
        check("preview_b64", bool(data.get("preview_b64")))

    r = client.get("/api/calibration/preview")
    check("GET preview calibración", r.status_code == 200
          and r.headers["content-type"] == "image/png")

    # --- análisis
    r = client.post("/api/analyze", json={
        "slot": "sample", "axis": "auto", "channel": "sum",
        "smooth": 5, "frac": 0.25, "rows": None, "top": 10})
    check("POST /api/analyze", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        a = r.json()
        res = a["results"]
        check("devuelve resultados", len(res) >= 3, str(len(res)))
        check("mejor candidato azurita",
              "azurite" in res[0]["name"].lower(), res[0]["name"])
        check("con referencia LED", a["has_lamp"] is True)
        check("color de muestra", a["sample_color"].startswith("#"))
        check("curvas candidato", len(res[0]["curve"]) == len(a["curve"]["nm"]))
        check("continuo candidato", len(res[0]["continuum"]) == 61)
        check("banda extraída", a["rows"][1] > a["rows"][0])
        import math
        nums = ([res[0][k] for k in
                 ("score", "delta_e", "sam_deg", "r_raw", "r_continuum")]
                + a["curve"]["reflectance"] + a["continuum"])
        check("JSON sin NaN", all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in nums))

    # --- ranuras / borrado
    r = client.delete("/api/frame/sample")
    check("DELETE frame", r.status_code == 200)
    check("ranura borrada",
          client.get("/api/status").json()["slots"]["sample"] is False)


# --------------------------------------------------------------- 4. cámara
def test_camera() -> None:
    print("\n4) cámara USB (V4L2/OpenCV)")
    from espectro import camara as cam
    try:
        cams = cam.probe_cameras()
    except RuntimeError as e:
        check("opencv disponible", False, str(e))
        return
    if not cams:
        print("  (sin cámaras — se omite)")
        return
    check("detecta cámaras", len(cams) >= 1, "0 cámaras")
    cap = cam.open_camera(cams[0]["index"], warmup=3)
    rgb = cam.read_rgb(cap)
    cap.release()
    check("captura un frame RGB",
          rgb is not None and rgb.ndim == 3 and rgb.shape[-1] == 3)


if __name__ == "__main__":
    test_led_calibration()
    test_demo()
    test_api()
    test_camera()
    print("\n" + ("TODO OK ✅" if not FAILS else f"FALLOS: {FAILS}"))
    sys.exit(1 if FAILS else 0)
