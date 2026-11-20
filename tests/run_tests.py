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


# ------------------------------------- 3. máscara de lámpara + gamma
def test_reflectance_mask() -> None:
    print("\n3) pseudo-reflectancia: máscara de lámpara y gamma")
    import numpy as np

    from espectro import match as mtc
    from espectro.color import lab_of_spectrum
    from espectro.library import Spectrum
    from espectro.reflectance import fill_nan, linearize, pseudo_reflectance

    # --- donde la bombilla no llega, NaN (nunca cero)
    n = 240
    lamp = np.ones(n)
    lamp[:30] = 0.0                 # cola azul
    lamp[200:] = 0.0                # >680 nm: el LED no emite
    r = pseudo_reflectance(0.4 * lamp, lamp, smooth=1, floor_frac=0.03)
    check("zona sin lámpara = NaN",
          bool(np.isnan(r[:30]).all() and np.isnan(r[200:]).all()))
    check("zona válida = reflectancia real",
          abs(float(np.nanmedian(r[60:150])) - 0.4) < 1e-9, str(r[100]))
    check("sin ceros artificiales en el rojo", bool(not (r[200:] == 0).any()))

    # --- linealización gamma
    x = np.array([0.0, 63.75, 127.5, 255.0])
    check("gamma=1 es identidad", bool(np.allclose(linearize(x, 1.0), x)))
    lin = linearize(x, 2.2, 255.0)
    check("linealiza gamma 2.2",
          bool(lin[1] > x[1] and abs(lin[3] - 255.0) < 1e-9), str(lin[1]))

    # dividir con gamma aplicada recupera la reflectancia verdadera
    true_r = 0.4
    s_enc = np.array([(true_r ** 2.2) * 255.0])     # lo que codifica la cámara
    l_enc = np.array([255.0])
    got = pseudo_reflectance(s_enc, l_enc, smooth=1, gamma=2.2,
                             full_scale=255.0)
    check("divide tras linealizar", abs(float(got[0]) - true_r) < 1e-9,
          f"{float(got[0]):.4f}")
    got_off = pseudo_reflectance(s_enc, l_enc, smooth=1, gamma=1.0,
                                 full_scale=255.0)
    check("sin linealizar comprime la banda",
          float(got_off[0]) < true_r - 0.1, f"{float(got_off[0]):.4f}")

    # --- match() descarta los NaN en lugar de tratarlos como cero
    grid = np.arange(400.0, 700.0 + 1e-9, 5.0)
    y1 = 0.2 + 0.6 * np.exp(-0.5 * ((grid - 500.0) / 40.0) ** 2)
    y2 = 0.2 + 0.6 * np.exp(-0.5 * ((grid - 620.0) / 40.0) ** 2)
    pairs = [(Spectrum("banda500", grid, y1), y1),
             (Spectrum("banda620", grid, y2), y2)]
    s = y1.copy()
    s[grid >= 660.0] = np.nan      # zona sin lámpara
    res = mtc.match(grid, s, pairs, grid, top=2)
    check("match con NaN devuelve candidatos", len(res) == 2, str(len(res)))
    check("scores finitos",
          bool(res and all(np.isfinite(m.score) for m in res)))
    check("la zona sin lámpara no decide el ganador",
          bool(res and res[0].spectrum.name == "banda500"),
          res[0].spectrum.name if res else "sin resultados")
    check("fill_nan sin huecos", bool(np.isfinite(fill_nan(s)).all()))
    check("color sin NaN",
          bool(np.isfinite(lab_of_spectrum(grid, s)).all()))


# ------------------------------------------------------- 4. veredicto
def test_verdict() -> None:
    print("\n4) veredicto: identified / uncertain / unknown")
    import numpy as np

    from espectro import match as mtc
    from espectro.library import Spectrum

    grid = np.arange(400.0, 700.0 + 1e-9, 5.0)
    g = lambda c, w=35.0: 0.15 + 0.7 * np.exp(-0.5 * ((grid - c) / w) ** 2)
    ramp = 0.15 + 0.6 * (grid - 400.0) / 300.0
    lib = [(Spectrum("Alpha s1", grid, g(450)), g(450)),
           (Spectrum("Alpha s2", grid, g(453)), g(453)),
           (Spectrum("Beta s1", grid, g(620)), g(620)),
           (Spectrum("Gamma s1", grid, ramp), ramp)]

    # 1. un miembro real de la biblioteca -> identified (aunque el 2º sea
    #    de la MISMA familia: el margen se mide contra otra familia)
    y = g(451.5)
    out = mtc.match(grid, y, lib, grid, top=4)
    check("miembro real -> identified",
          out.verdict.label == "identified", str(out.verdict))
    check("el margen salta la familia empatada",
          out.verdict.margin > mtc.VERDICT_MIN_MARGIN, str(out.verdict.margin))

    # 2. muestra plana -> sin candidatos (Pearson indefinido) -> unknown
    out2 = mtc.match(grid, np.full(grid.size, 0.5), lib, grid, top=4)
    check("plano -> unknown", out2.verdict.label == "unknown", str(len(out2)))

    # 3. mezcla de dos familias lejanas: nunca se identifica con confianza
    out3 = mtc.match(grid, 0.5 * (g(450) + g(620)), lib, grid, top=4)
    check("mezcla ambigua no se identifica",
          out3.verdict.label in ("uncertain", "unknown"), str(out3.verdict))

    # 3b. dos familias DISTINTAS con prácticamente el mismo espectro
    #     (el caso real de calcita vs cuarzo) -> uncertain, no identified
    ramp = 0.15 + 0.6 * (grid - 400.0) / 300.0
    twin = np.clip(ramp + 0.002, 0.0, 1.0)
    lib2 = [(Spectrum("Beta s1", grid, ramp), ramp),
            (Spectrum("Gamma s1", grid, twin), twin),
            (Spectrum("Alpha s1", grid, g(450)), g(450))]
    out4 = mtc.match(grid, ramp, lib2, grid, top=3)
    check("espectros gemelos de otra familia -> uncertain",
          out4.verdict.label == "uncertain", str(out4.verdict))

    # 4. reglas de corte, en aislamiento
    def mk(s, name="X y"):
        return mtc.MatchResult(Spectrum(name, grid, y), score=s,
                               sam_deg=0.0, r_raw=s, r_continuum=s,
                               max_depth_diff=0.0)
    check("sin candidatos -> unknown",
          mtc.assess([]).label == "unknown")
    check("score bajo -> unknown",
          mtc.assess([mk(0.55)]).label == "unknown")
    check("margen estrecho -> uncertain",
          mtc.assess([mk(0.80), mk(0.79, "Z w")]).label == "uncertain")
    check("score y margen suficientes -> identified",
          mtc.assess([mk(0.85), mk(0.70, "Z w")]).label == "identified")
    d = mtc.assess([mk(0.85), mk(0.70)]).as_dict()
    check("veredicto serializable a JSON",
          all(isinstance(v, (int, float, str)) for v in d.values()))
    check("MatchOutcome sigue siendo una lista",
          isinstance(out, list) and len(out) == 4)


# ------------------------------------------------------------ 5. API REST
def test_api() -> None:
    print("\n5) API (FastAPI TestClient): estado, calibración, análisis")
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
    if st["cameras"]:
        check("cámara detectada", len(st["cameras"]) >= 1)
    else:
        print("  (sin cámaras en este entorno — se omite; CI no tiene V4L2)")

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
    # el modo automático (por defecto) tiene que rechazar un LED blanco
    # y caer en el modo LED, no inventarse líneas de mercurio
    r0 = client.post("/api/calibrate", json={"method": "auto"})
    check("POST /api/calibrate (auto)", r0.status_code == 200, r0.text[:200])
    if r0.status_code == 200:
        check("auto no confunde un LED con líneas Hg",
              r0.json()["meta"]["mode"] == "led",
              str(r0.json()["meta"].get("mode")))
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
        check("cobertura de banda",
              0.0 < float(a.get("coverage", 0)) <= 1.0,
              str(a.get("coverage")))
        check("gamma en la respuesta", a.get("gamma") == 1.0,
              str(a.get("gamma")))
        check("veredicto identified",
              a.get("verdict", {}).get("label") == "identified",
              str(a.get("verdict")))
        check("fwhm de referencias en la respuesta", a.get("fwhm") == 7.0,
              str(a.get("fwhm")))

        # la linealización gamma no rompe el pipeline
        r2 = client.post("/api/analyze", json={
            "slot": "sample", "axis": "auto", "channel": "sum",
            "smooth": 5, "frac": 0.25, "rows": None, "top": 5,
            "gamma": 2.2})
        check("analyze con gamma 2.2", r2.status_code == 200, r2.text[:200])

    # --- subida de foto (alternativa cuando no hay cámara)
    png = (ex / "demo_muestra.png").read_bytes()
    r = client.post("/api/frame/sample",
                    files={"file": ("muestra.png", png, "image/png")})
    check("POST /api/frame (subida)", r.status_code == 200, r.text[:120])
    check("foto subida en la ranura",
          client.get("/api/status").json()["slots"]["sample"] is True)

    # --- ranuras / borrado
    r = client.delete("/api/frame/sample")
    check("DELETE frame", r.status_code == 200)
    check("ranura borrada",
          client.get("/api/status").json()["slots"]["sample"] is False)


# --------------------------------------------------------------- 6. cámara
def test_camera() -> None:
    print("\n6) cámara USB (V4L2/OpenCV)")
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
    ok_any = False
    for c in cams:                       # algunas pueden estar ocupadas
        try:
            cap = cam.open_camera(c["index"], warmup=2)
            rgb = cam.read_rgb(cap)
            cap.release()
            if rgb is not None:
                ok_any = True
                break
        except Exception:                 # noqa: BLE001
            continue
    check("captura un frame RGB", ok_any, "todas ocupadas o ilegibles")


# ------------------------------------------------- 7. líneas de mercurio
def test_hg_lines() -> None:
    print("\n7) calibración por líneas de mercurio/CFL")
    import numpy as np

    from espectro import calib as cal

    W = 1280
    p = np.arange(W, dtype=float)
    true_nm = 400.0 + p / (W - 1) * 300.0

    # lámpara de mercurio: líneas vistas con el instrumento (7 nm FWHM)
    sig = cal.hg_model(true_nm, sigma=7.0 / 2.35482)
    sig = sig / sig.max()

    peaks = cal.detect_narrow_peaks(p, sig)
    check("detecta los picos estrechos", len(peaks) >= 3, str(len(peaks)))
    fit = cal.fit_hg_lines(p, sig, min_lines=3)
    check("encuentra el ajuste por líneas", fit is not None)
    if fit:
        coeffs, meta = fit
        err = float(np.max(np.abs(np.polyval(coeffs, p) - true_nm)))
        check("error < 2 nm con líneas Hg", err < 2.0, f"err={err:.2f} nm")
        check("≥3 líneas explicadas", meta["lines_explained"] >= 3,
              str(meta["lines_explained"]))

    # solo dos líneas visibles (435,8 / 546,1): el modo automático las
    # rechaza (no puede validarse), pero `--lines` las acepta
    two = np.zeros_like(true_nm)
    for c in (435.83, 546.07):
        two = two + np.exp(-0.5 * ((true_nm - c) / (7.0 / 2.35482)) ** 2)
    two = two / two.max()
    check("dos picos: el automático no se arriesga",
          cal.fit_hg_lines(p, two, min_lines=3) is None)
    fit2 = cal.fit_hg_lines(p, two, min_lines=2, allow_pair=True)
    check("dos picos: `--lines` asume el par clásico", fit2 is not None)
    if fit2:
        c2, m2 = fit2
        e2 = float(np.max(np.abs(np.polyval(c2, p) - true_nm)))
        pk2 = cal.detect_narrow_peaks(p, two)
        used_nm = sorted(d["nm"] for d in m2["lines"])
        check("par clásico con error < 2 nm", e2 < 2.0,
              f"err={e2:.2f} nm · picos={[round(q, 1) for q, _ in pk2]} · "
              f"usadas={[(d['pixel'], d['nm']) for d in m2['lines']]} · "
              f"c2={np.array2string(c2, precision=4)}")
        check("elige el par 435,8/546,1 y no otro",
              used_nm == [435.83, 546.07], str(used_nm))
        check("avisa de que asume el par", m2["lines_classic_pair"])

    # un LED blanco (pico ancho + hump) NO puede colarse como líneas
    led = cal.led_model(true_nm)
    led = led / led.max()
    check("el LED blanco no se toma por líneas",
          cal.fit_hg_lines(p, led, min_lines=3) is None)
    check("el LED no tiene picos estrechos",
          len(cal.detect_narrow_peaks(p, led)) < 2)


if __name__ == "__main__":
    test_demo()
    test_led_calibration()
    test_reflectance_mask()
    test_verdict()
    test_api()
    test_camera()
    test_hg_lines()
    print("\n" + ("TODO OK ✅" if not FAILS else f"FALLOS: {FAILS}"))
    sys.exit(1 if FAILS else 0)
