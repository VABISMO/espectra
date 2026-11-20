"""End-to-end tests of the spectrometer (no pytest: `python tests/run_tests.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []
CHECKS = 0        # mandatory checks (always executed)
CHECKS_OPT = 0    # environment dependent (USB camera, opencv)


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    _report(name, cond, detail)


def check_opt(name: str, cond: bool, detail: str = "") -> None:
    """Like `check`, but for checks that do not exist in every
    environment (no USB camera / no opencv). They do not count towards
    the count the README publishes, which is why it stays stable in CI.
    """
    global CHECKS_OPT
    CHECKS_OPT += 1
    _report(name, cond, detail)


def _report(name: str, cond: bool, detail: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 1. demo
def test_demo() -> None:
    print("\n1) demo end-to-end (LED + library + colour)")
    from espectro.cli import main
    rc = main(["demo"])
    check("demo PASS", rc == 0, f"rc={rc}")


# ------------------------------------------------------- 2. LED calibration
def test_led_calibration() -> None:
    print("\n2) automatic calibration from LED peaks")
    import numpy as np

    from espectro import calib as cal
    W = 1280
    p = np.arange(W, dtype=float)
    true_nm = 400.0 + p / (W - 1) * 300.0
    lamp = cal.led_model(true_nm)
    peaks = cal.detect_led_peaks(p, lamp)
    check("detects two peaks", peaks is not None)
    if peaks:
        coeffs = cal.poly_from_anchors([(peaks[0], 450.0), (peaks[1], 560.0)])
        coeffs, r = cal.refine_led(coeffs, p, lamp)
        err = float(np.max(np.abs(np.polyval(coeffs, p) - true_nm)))
        check("maximum error < 5 nm", err < 5.0, f"err={err:.1f} nm")
        check("correlation > 0.999", r > 0.999, f"r={r:.4f}")


# ------------------------------------- 3. lamp mask + gamma
def test_reflectance_mask() -> None:
    print("\n3) pseudo-reflectance: lamp mask and gamma")
    import numpy as np

    from espectro import match as mtc
    from espectro.color import lab_of_spectrum
    from espectro.library import Spectrum
    from espectro.reflectance import fill_nan, linearize, pseudo_reflectance

    # --- where the bulb does not reach, NaN (never zero)
    n = 240
    lamp = np.ones(n)
    lamp[:30] = 0.0                 # cola azul
    lamp[200:] = 0.0                # >680 nm: el LED no emite
    r = pseudo_reflectance(0.4 * lamp, lamp, smooth=1, floor_frac=0.03)
    check("lamp-less zone = NaN",
          bool(np.isnan(r[:30]).all() and np.isnan(r[200:]).all()))
    check("valid zone = true reflectance",
          abs(float(np.nanmedian(r[60:150])) - 0.4) < 1e-9, str(r[100]))
    check("no artificial zeros in the red", bool(not (r[200:] == 0).any()))

    # --- gamma linearization
    x = np.array([0.0, 63.75, 127.5, 255.0])
    check("gamma=1 is the identity", bool(np.allclose(linearize(x, 1.0), x)))
    lin = linearize(x, 2.2, 255.0)
    check("linearizes gamma 2.2",
          bool(lin[1] > x[1] and abs(lin[3] - 255.0) < 1e-9), str(lin[1]))

    # dividing after linearizing recovers the true reflectance
    true_r = 0.4
    s_enc = np.array([(true_r ** 2.2) * 255.0])     # what the camera encodes
    l_enc = np.array([255.0])
    got = pseudo_reflectance(s_enc, l_enc, smooth=1, gamma=2.2,
                             full_scale=255.0)
    check("divides after linearizing", abs(float(got[0]) - true_r) < 1e-9,
          f"{float(got[0]):.4f}")
    got_off = pseudo_reflectance(s_enc, l_enc, smooth=1, gamma=1.0,
                                 full_scale=255.0)
    check("without linearizing the band is compressed",
          float(got_off[0]) < true_r - 0.1, f"{float(got_off[0]):.4f}")

    # --- match() drops NaN instead of treating them as zero
    grid = np.arange(400.0, 700.0 + 1e-9, 5.0)
    y1 = 0.2 + 0.6 * np.exp(-0.5 * ((grid - 500.0) / 40.0) ** 2)
    y2 = 0.2 + 0.6 * np.exp(-0.5 * ((grid - 620.0) / 40.0) ** 2)
    pairs = [(Spectrum("banda500", grid, y1), y1),
             (Spectrum("banda620", grid, y2), y2)]
    s = y1.copy()
    s[grid >= 660.0] = np.nan      # lamp-less zone
    res = mtc.match(grid, s, pairs, grid, top=2)
    check("match with NaN returns candidates", len(res) == 2, str(len(res)))
    check("finite scores",
          bool(res and all(np.isfinite(m.score) for m in res)))
    check("the lamp-less zone does not decide the winner",
          bool(res and res[0].spectrum.name == "banda500"),
          res[0].spectrum.name if res else "sin resultados")
    check("fill_nan leaves no gaps", bool(np.isfinite(fill_nan(s)).all()))
    check("colour with no NaN",
          bool(np.isfinite(lab_of_spectrum(grid, s)).all()))


# ------------------------------------------------------- 4. verdict
def test_verdict() -> None:
    print("\n4) verdict: identified / uncertain / unknown")
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

    # 1. a real library member -> identified (even if the 2nd is
    #    de la MISMA familia: el margen se mide contra otra familia)
    y = g(451.5)
    out = mtc.match(grid, y, lib, grid, top=4)
    check("real member -> identified",
          out.verdict.label == "identified", str(out.verdict))
    check("the margin skips the tied family",
          out.verdict.margin > mtc.VERDICT_MIN_MARGIN, str(out.verdict.margin))

    # 2. flat sample -> no candidates (Pearson undefined) -> unknown
    out2 = mtc.match(grid, np.full(grid.size, 0.5), lib, grid, top=4)
    check("plano -> unknown", out2.verdict.label == "unknown", str(len(out2)))

    # 3. mix of two distant families: never confidently identified
    out3 = mtc.match(grid, 0.5 * (g(450) + g(620)), lib, grid, top=4)
    check("ambiguous mix is not identified",
          out3.verdict.label in ("uncertain", "unknown"), str(out3.verdict))

    # 3b. two DIFFERENT families with practically the same spectrum
    #     (the real calcite vs quartz case) -> uncertain, not identified
    ramp = 0.15 + 0.6 * (grid - 400.0) / 300.0
    twin = np.clip(ramp + 0.002, 0.0, 1.0)
    lib2 = [(Spectrum("Beta s1", grid, ramp), ramp),
            (Spectrum("Gamma s1", grid, twin), twin),
            (Spectrum("Alpha s1", grid, g(450)), g(450))]
    out4 = mtc.match(grid, ramp, lib2, grid, top=3)
    check("twin spectra from another family -> uncertain",
          out4.verdict.label == "uncertain", str(out4.verdict))

    # 4. reglas de corte, en aislamiento
    def mk(s, name="X y"):
        return mtc.MatchResult(Spectrum(name, grid, y), score=s,
                               sam_deg=0.0, r_raw=s, r_continuum=s,
                               max_depth_diff=0.0)
    check("no candidates -> unknown",
          mtc.assess([]).label == "unknown")
    check("low score -> unknown",
          mtc.assess([mk(0.55)]).label == "unknown")
    check("narrow margin -> uncertain",
          mtc.assess([mk(0.80), mk(0.79, "Z w")]).label == "uncertain")
    check("enough score and margin -> identified",
          mtc.assess([mk(0.85), mk(0.70, "Z w")]).label == "identified")
    d = mtc.assess([mk(0.85), mk(0.70)]).as_dict()
    check("verdict serializable to JSON",
          all(isinstance(v, (int, float, str)) for v in d.values()))
    check("MatchOutcome is still a list",
          isinstance(out, list) and len(out) == 4)


# ------------------------------------------------------------ 5. API REST
def test_api() -> None:
    print("\n5) API (FastAPI TestClient): status, calibration, analysis")
    from fastapi.testclient import TestClient

    import server

    ex = ROOT / "data" / "examples"
    cal_path = ROOT / "data" / "calibration" / "cal.json"
    if not (ex / "demo_sample.png").exists():
        check("fotos de ejemplo", False, "ejecuta demo antes")
        return

    client = TestClient(server.app)

    # --- estado
    r = client.get("/api/status")
    check("GET /api/status", r.status_code == 200)
    st = r.json()
    check("library loaded", st["library"]["total"] > 500,
          str(st["library"]["total"]))
    if st["cameras"]:
        check_opt("camera detected", len(st["cameras"]) >= 1)
    else:
        print("  (no cameras in this environment — skipped; CI has no V4L2)")

    # --- frontend servido
    r = client.get("/")
    check("GET / (React)", r.status_code == 200 and "ESPECTRA" in r.text)

    # --- inject the demo photos as if the camera had captured them
    for slot, name in (("sample", "demo_sample.png"),
                       ("lamp", "demo_lamp.png"),
                       ("cal", "demo_lamp.png")):
        server.STATE["slots"][slot] = (ex / name).read_bytes()

    # --- calibration through the API (LED mode)
    if cal_path.exists():
        cal_path.unlink()
    # the automatic mode (default) must reject a white LED
    # and fall back to LED mode instead of inventing mercury lines
    r0 = client.post("/api/calibrate", json={"method": "auto"})
    check("POST /api/calibrate (auto)", r0.status_code == 200, r0.text[:200])
    if r0.status_code == 200:
        check("auto does not mistake an LED for Hg lines",
              r0.json()["meta"]["mode"] == "led",
              str(r0.json()["meta"].get("mode")))
    r = client.post("/api/calibrate", json={"method": "led"})
    check("POST /api/calibrate", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        data = r.json()
        check("LED mode calibration", data["meta"]["mode"] == "led")
        check("peaks detected", bool(data["meta"].get("led_peaks_pixel")))
        check("fit r > 0.99",
              float(data["meta"].get("led_corr") or 0) > 0.99,
              str(data["meta"].get("led_corr")))
        check("saved to cal.json", cal_path.exists())
        check("preview_b64", bool(data.get("preview_b64")))

    r = client.get("/api/calibration/preview")
    check("GET calibration preview", r.status_code == 200
          and r.headers["content-type"] == "image/png")

    # --- analysis
    r = client.post("/api/analyze", json={
        "slot": "sample", "axis": "auto", "channel": "sum",
        "smooth": 5, "frac": 0.25, "rows": None, "top": 10})
    check("POST /api/analyze", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        a = r.json()
        res = a["results"]
        check("returns results", len(res) >= 3, str(len(res)))
        check("best candidate azurite",
              "azurite" in res[0]["name"].lower(), res[0]["name"])
        check("with LED reference", a["has_lamp"] is True)
        check("sample colour", a["sample_color"].startswith("#"))
        check("candidate curves", len(res[0]["curve"]) == len(a["curve"]["nm"]))
        check("candidate continuum", len(res[0]["continuum"]) == 61)
        check("band extracted", a["rows"][1] > a["rows"][0])
        import math
        nums = ([res[0][k] for k in
                 ("score", "delta_e", "sam_deg", "r_raw", "r_continuum")]
                + a["curve"]["reflectance"] + a["continuum"])
        check("JSON without NaN", all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in nums))
        check("band coverage",
              0.0 < float(a.get("coverage", 0)) <= 1.0,
              str(a.get("coverage")))
        check("gamma in the response", a.get("gamma") == 1.0,
              str(a.get("gamma")))
        check("verdict identified",
              a.get("verdict", {}).get("label") == "identified",
              str(a.get("verdict")))
        check("reference fwhm in the response", a.get("fwhm") == 7.0,
              str(a.get("fwhm")))

        # gamma linearization does not break the pipeline
        r2 = client.post("/api/analyze", json={
            "slot": "sample", "axis": "auto", "channel": "sum",
            "smooth": 5, "frac": 0.25, "rows": None, "top": 5,
            "gamma": 2.2})
        check("analyze with gamma 2.2", r2.status_code == 200, r2.text[:200])

    # --- photo upload (camera alternative)
    png = (ex / "demo_sample.png").read_bytes()
    r = client.post("/api/frame/sample",
                    files={"file": ("sample.png", png, "image/png")})
    check("POST /api/frame (upload)", r.status_code == 200, r.text[:120])
    check("photo uploaded into the slot",
          client.get("/api/status").json()["slots"]["sample"] is True)

    # --- ranuras / borrado
    r = client.delete("/api/frame/sample")
    check("DELETE frame", r.status_code == 200)
    check("slot cleared",
          client.get("/api/status").json()["slots"]["sample"] is False)


# --------------------------------------------------------------- 6. camera
def test_camera() -> None:
    print("\n6) USB camera (V4L2/OpenCV)")
    from espectro import camara as cam
    try:
        cams = cam.probe_cameras()
    except RuntimeError as e:
        check_opt("opencv disponible", False, str(e))
        return
    if not cams:
        print("  (no cameras — skipped)")
        return
    check_opt("detects cameras", len(cams) >= 1, "0 cameras")
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
    check_opt("captures an RGB frame", ok_any, "all busy or unreadable")


# ------------------------------------------------- 7. mercury lines
def test_hg_lines() -> None:
    print("\n7) calibration from mercury/CFL lines")
    import numpy as np

    from espectro import calib as cal

    W = 1280
    p = np.arange(W, dtype=float)
    true_nm = 400.0 + p / (W - 1) * 300.0

    # mercury lamp: lines as seen through the instrument (7 nm FWHM)
    sig = cal.hg_model(true_nm, sigma=7.0 / 2.35482)
    sig = sig / sig.max()

    peaks = cal.detect_narrow_peaks(p, sig)
    check("detects the narrow peaks", len(peaks) >= 3, str(len(peaks)))
    fit = cal.fit_hg_lines(p, sig, min_lines=3)
    check("finds the line fit", fit is not None)
    if fit:
        coeffs, meta = fit
        err = float(np.max(np.abs(np.polyval(coeffs, p) - true_nm)))
        check("error < 2 nm with Hg lines", err < 2.0, f"err={err:.2f} nm")
        check("≥3 lines explained", meta["lines_explained"] >= 3,
              str(meta["lines_explained"]))
        # resolution is NOT assumed: it is measured from the line width
        check("measures the FWHM (7 nm true)",
              6.0 <= float(meta.get("fwhm_nm") or 0) <= 8.0,
              str(meta.get("fwhm_nm")))

    # with another resolution it must return a DIFFERENT number (otherwise
    # it would be a fixed value disguised as a measurement)
    sig10 = cal.hg_model(true_nm, sigma=10.0 / 2.35482)
    sig10 = sig10 / sig10.max()
    fit10 = cal.fit_hg_lines(p, sig10, min_lines=3)
    check("resolution is measured, not fixed",
          fit10 is not None
          and 9.0 <= float(fit10[1].get("fwhm_nm") or 0) <= 11.0,
          str(fit10[1].get("fwhm_nm")) if fit10 else "no fit")

    # only two visible lines (435.8 / 546.1): the automatic mode rejects
    # them (it cannot validate them), but `--lines` accepts them
    two = np.zeros_like(true_nm)
    for c in (435.83, 546.07):
        two = two + np.exp(-0.5 * ((true_nm - c) / (7.0 / 2.35482)) ** 2)
    two = two / two.max()
    check("two peaks: the automatic mode does not risk it",
          cal.fit_hg_lines(p, two, min_lines=3) is None)
    fit2 = cal.fit_hg_lines(p, two, min_lines=2, allow_pair=True)
    check("two peaks: `--lines` assumes the classic pair", fit2 is not None)
    if fit2:
        c2, m2 = fit2
        e2 = float(np.max(np.abs(np.polyval(c2, p) - true_nm)))
        pk2 = cal.detect_narrow_peaks(p, two)
        used_nm = sorted(d["nm"] for d in m2["lines"])
        check("classic pair with error < 2 nm", e2 < 2.0,
              f"err={e2:.2f} nm · picos={[round(q, 1) for q, _ in pk2]} · "
              f"usadas={[(d['pixel'], d['nm']) for d in m2['lines']]} · "
              f"c2={np.array2string(c2, precision=4)}")
        check("picks the 435.8/546.1 pair and no other",
              used_nm == [435.83, 546.07], str(used_nm))
        check("warns that it assumes the pair", m2["lines_classic_pair"])

    # a white LED (broad peak + hump) cannot pass for lines
    led = cal.led_model(true_nm)
    led = led / led.max()
    check("the white LED is not taken for lines",
          cal.fit_hg_lines(p, led, min_lines=3) is None)
    check("the LED has no narrow peaks",
          len(cal.detect_narrow_peaks(p, led)) < 2)


# ------------------------------------------- 8. shared pipeline
def test_pipeline() -> None:
    print("\n8) shared pipeline (REST API + Streamlit)")
    import numpy as np

    from espectro import library as lib
    from espectro import pipeline as pl
    from espectro.calib import led_model

    grid = pl.GRID
    W = 1280
    p = np.arange(W, dtype=float)
    true_nm = 400.0 + p / (W - 1) * 300.0
    coeffs = np.polyfit(p, true_nm, 1)

    R = 0.2 + 0.6 * np.exp(-0.5 * ((true_nm - 500.0) / 40.0) ** 2)
    spec = lib.Spectrum("Synthetic sample", grid,
                        0.2 + 0.6 * np.exp(-0.5 * ((grid - 500.0) / 40.0) ** 2))
    paired = lib.to_grid([spec], grid, fwhm=None)

    lamp = led_model(true_nm)
    # real sensor roll-off in the red: without it the idealised LED never
    # drops below3 % and the mask is never exercised
    lamp = lamp * np.clip((690.0 - true_nm) / 20.0, 0.0, 1.0)
    lamp = lamp / lamp.max() * 220.0          # in DN, like a photo
    sample = lamp * R

    an = pl.analyze(sample, lamp, p, coeffs, paired, smooth=5, top=3)
    check("analyzes with lamp", an.has_lamp is True)
    check("partial coverage (LED tail masked)",
          0.5 < an.coverage < 1.0, f"{an.coverage:.2f}")
    check("the sample keeps the mask's NaN",
          bool(np.isnan(an.y).any()))
    check("y_show without NaN (for charts/JSON)",
          bool(np.isfinite(an.y_show).all()))
    check("returns results with a verdict",
          len(an.results) > 0 and an.results.verdict.label in
          ("identified", "uncertain", "unknown"), str(an.results.verdict))
    check("colour in hex", an.color.startswith("#"), an.color)
    check("candidate curves",
          len(pl.curves_of(an.results, paired)) == len(an.results))

    an2 = pl.analyze(sample, None, p, coeffs, paired, smooth=5, top=3)
    check("analyzes without lamp", an2.has_lamp is False)

    try:
        pl.analyze(sample, lamp, p, coeffs, paired, min_coverage=1.5)
        check("raises LowCoverage", False, "did not raise")
    except pl.LowCoverage as e:
        check("raises LowCoverage when coverage is insufficient",
              e.covered < int(e.min_coverage * e.total) and e.total > 0,
              f"{e.covered}/{e.total} minimum {e.min_coverage}")


def verify_readme() -> None:
    """The count published in the README must match the **mandatory**
    checks.

    Camera checks do not count: they do not exist in CI nor on a machine
    without a webcam. Without this check the README number was manual
    work that had to be touched three times in a single session.
    """
    import re

    pats = (r"\*\*(\d+) end-to-end tests\*\*",
            r"# (\d+) checks:",
            r"The (\d+) checks")
    nums: set[int] = set()
    path = ROOT / "README.md"
    if path.exists():
        txt = path.read_text(encoding="utf-8")
        for pat in pats:
            nums.update(int(m) for m in re.findall(pat, txt))
    ok = bool(nums) and nums == {CHECKS}
    print(("  PASS  " if ok else "  FAIL  ") +
          f"README: published count {sorted(nums)} == {CHECKS} mandatory"
          + (f" (+{CHECKS_OPT} camera-only)" if CHECKS_OPT else ""))
    if not ok:
        FAILS.append("README test count")


if __name__ == "__main__":
    test_demo()
    test_led_calibration()
    test_reflectance_mask()
    test_verdict()
    test_api()
    test_camera()
    test_hg_lines()
    test_pipeline()
    verify_readme()
    print("\n" + ("TODO OK ✅" if not FAILS else f"FALLOS: {FAILS}"))
    sys.exit(1 if FAILS else 0)
