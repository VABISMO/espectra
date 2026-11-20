"""Command line interface of the spectrometer.

    python -m espectro extract   PHOTO  ...   -> intensity vs pixel curve
    python -m espectro calibrate BULB  ...   -> pixel -> nm calibration
    python -m espectro match     SAMPLE ...  -> compare against the library
    python -m espectro info                   -> library status
    python -m espectro demo                   -> full synthetic test
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

from . import calib as cal
from . import image as im
from . import library as lib
from . import match as mtc
from . import plotting as pl
from .reflectance import fill_nan, linearize, pseudo_reflectance, resample

ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "data" / "libraries"
CAL_DIR = ROOT / "data" / "calibration"
OUT_DIR = ROOT / "results"
DEFAULT_GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)


# ----------------------------------------------------------------- extract
def cmd_extract(args: argparse.Namespace) -> int:
    img = im.load_gray(args.image, channel=args.channel)
    dark = im.load_gray(args.dark, channel=args.channel) if args.dark else None
    rows = tuple(args.rows) if args.rows else None
    ext = im.extract_profile(img, rows=rows, axis=args.axis, dark=dark,
                             smooth=args.smooth, frac=args.frac)
    print(f"  dispersion axis: {ext.axis}   band rows: {ext.rows}   "
          f"{ext.pixel.size} pixels")

    pixel, inten = ext.pixel, ext.intensity
    domain = "pixel"
    if args.cal:
        coeffs, meta = cal.load_cal(args.cal)
        nm = cal.apply_cal(coeffs, pixel)
        keep = (nm >= 180) & (nm <= 2600)
        pixel, inten = nm[keep], inten[keep]
        domain = "nm"
        print(f"  calibration applied from {args.cal}")

    im.save_spectrum(args.output, pixel, inten,
                     extra={"domain": domain,
                            "source_image": str(args.image),
                            "rows": f"{ext.rows[0]}-{ext.rows[1]}"})
    print(f"  spectrum saved to {args.output}  (domain: {domain})")

    if args.preview:
        pl.plot_extraction(img, ext.rows, ext.pixel, ext.intensity,
                           args.preview, axis=ext.axis)
        print(f"  preview written to {args.preview}")
    return 0


# --------------------------------------------------------------- calibrate
def cmd_calibrate(args: argparse.Namespace) -> int:
    lines_mode = bool(getattr(args, "lines", False))
    led_mode = bool(getattr(args, "led", False))
    img = im.load_gray(args.image, channel=args.channel)
    dark = im.load_gray(args.dark, channel=args.channel) if args.dark else None
    ext = im.extract_profile(img, axis=args.axis, dark=dark,
                             smooth=args.smooth)
    pixel, inten = ext.pixel, ext.intensity

    meta: dict = {"source_image": str(args.image),
                  "rows": f"{ext.rows[0]}-{ext.rows[1]}"}
    nm_range = tuple(args.range) if args.range else (400.0, 700.0)
    T = corr = None
    led_peaks = None

    if args.anchor:
        coeffs = cal.poly_from_anchors([(p, n) for p, n in args.anchor])
        meta["mode"] = "anchors"
        meta["anchors"] = [{"pixel": p, "nm": n} for p, n in args.anchor]
        print(f"  {len(args.anchor)} manual anchors applied")

    elif args.blackbody is not None:          # filament bulbs only
        coeffs, T, corr = _range_init(pixel, inten, nm_range, meta)
        T_ask = args.blackbody if args.blackbody > 0 else 3000.0
        coeffs, T, corr = cal.refine_blackbody(
            coeffs, pixel, inten,
            T_range=(max(1800.0, T_ask - 600), T_ask + 600))
        meta["mode"] = "blackbody"
        meta["blackbody_T"] = T
        meta["blackbody_corr"] = corr
        print(f"  blackbody: T={T:.0f} K, r={corr:.3f}  "
              "(filament bulbs only, NOT for LED)")

    elif args.range is not None:              # explicit assumed range
        coeffs = _range_init(pixel, inten, nm_range, meta)[0]
        meta["mode"] = "range"

    else:
        # Mercury/CFL lines first (they are narrow: they really fix the
        # curvature) and the white LED only if there are none.
        if lines_mode:
            line_fit = cal.fit_hg_lines(pixel, inten, min_lines=2,
                                        allow_pair=True)
        elif led_mode:
            line_fit = None
        else:
            line_fit = cal.fit_hg_lines(pixel, inten, min_lines=3,
                                        allow_pair=False)

        if line_fit is not None:
            coeffs, lmeta = line_fit
            meta.update(lmeta)
            meta["mode"] = "lines"
            lines_txt = ", ".join(
                f"{d['nm']:g} nm@p{d['pixel']:.0f}" for d in lmeta["lines"])
            print(f"  {len(lmeta['lines'])} mercury/CFL lines: {lines_txt}")
            if lmeta["lines_classic_pair"]:
                print("  (only two peaks: assuming the classic "
                      "435.8/546.1 pair — make sure the lamp really is "
                      "mercury/CFL)")
            if lmeta.get("fwhm_nm"):
                print(f"  measured instrument resolution: FWHM "
                      f"{lmeta['fwhm_nm']:g} nm (used when matching against "
                      "the library)")

        else:
            if lines_mode:
                print("  warning: no mercury lines found; falling back to "
                      "the white LED")
            led_peaks = cal.detect_led_peaks(pixel, inten)
            if led_peaks is not None:
                p_blue, p_phos = led_peaks
                coeffs = cal.poly_from_anchors([(p_blue, 450.0),
                                                (p_phos, 560.0)])
                meta["mode"] = "led"
                meta["led_peaks_pixel"] = [p_blue, p_phos]
                meta["led_peaks_nm"] = [450.0, 560.0]
                print(f"  LED peaks found: blue p={p_blue:.0f} (~450 nm), "
                      f"phosphor p={p_phos:.0f} (~560 nm)")
                coeffs, corr = cal.refine_led(coeffs, pixel, inten)
                meta["led_corr"] = corr
                if np.isfinite(corr):
                    print(f"  refined with LED template: r={corr:.3f}")
            else:
                print("  warning: LED peaks not found; using the assumed "
                      "range (less accurate)")
                coeffs = _range_init(pixel, inten, nm_range, meta)[0]
                meta["mode"] = "range_fallback"

    cal.save_cal(args.output, coeffs, meta)
    print(f"  calibration saved to {args.output}")
    print(f"    nm(p) = {np.array2string(np.asarray(coeffs), precision=4)}")

    if args.preview:
        mode = meta.get("mode")
        model_fn = {"led": cal.led_model, "lines": cal.hg_model}.get(mode)
        model_label = {"led": "LED template",
                       "lines": "Hg/CFL lines"}.get(mode)
        pl.plot_calibration(pixel, inten, coeffs, T, corr, args.preview,
                            model_fn=model_fn, model_label=model_label)
        print(f"  preview written to {args.preview}")
    return 0


def _range_init(pixel, inten, nm_range, meta):
    """Linear map of the active band range onto `nm_range`."""
    on = inten > inten.max() * 0.03
    idx = np.flatnonzero(on)
    if idx.size < 8:
        raise SystemExit("ERROR: the bulb band is too short")
    p0, p1 = float(idx[0]), float(idx[-1])
    nm0, nm1 = nm_range
    coeffs = cal.linear_from_range(p0, p1, nm0, nm1)
    meta["pixel_range"] = [p0, p1]
    meta["assumed_nm_range"] = [nm0, nm1]
    print(f"  active range: pixels {p0:.0f}..{p1:.0f} → "
          f"{nm0:.0f}..{nm1:.0f} nm")
    return coeffs, None, None


# ------------------------------------------------------------------- match
def _read_domain(path: Path) -> str:
    with path.open(encoding="utf-8") as fh:
        for _ in range(10):
            line = fh.readline()
            if not line:
                break
            if line.startswith("#") and "domain" in line:
                return line.partition(":")[2].strip()
    return ""


def cmd_match(args: argparse.Namespace) -> int:
    s_px, s_iy = im.load_spectrum(args.sample)
    domain = _read_domain(Path(args.sample)) or args.domain

    lamp_px = lamp_iy = None
    if args.lamp:
        lamp_px, lamp_iy = im.load_spectrum(args.lamp)

    # ---- 1. pseudo-reflectance (in the domain both are in)
    if lamp_px is not None:
        if lamp_px.size != s_px.size:
            print("  ERROR: sample and lamp have a different number of "
                  "points; extract them with the same options "
                  "(--rows/--smooth)", file=sys.stderr)
            return 2
        y = pseudo_reflectance(s_iy, lamp_iy, smooth=args.smooth,
                               gamma=args.gamma)
    else:
        print("  warning: no reference bulb, using normalized intensity "
              "(lamp/sensor not divided)")
        y = np.clip(linearize(s_iy, args.gamma), 0, None)
        y = y / y.max() if y.max() > 0 else y

    # ---- 2. pixel -> nm
    cal_meta: dict = {}
    if domain != "nm":
        if not args.cal:
            print("  ERROR: data is in pixels and --cal is missing "
                  "(run `calibrate` first)", file=sys.stderr)
            return 2
        coeffs, cal_meta = cal.load_cal(args.cal)
        nm = cal.apply_cal(coeffs, s_px)
    else:
        nm = s_px

    grid = np.arange(args.grid[0], args.grid[1] + 1e-9, args.grid[2])
    y_grid = resample(nm, y, grid)          # keeps the mask's NaN
    ok = np.isfinite(y_grid)
    if ok.sum() < 0.5 * grid.size:
        print(f"  ERROR: only {ok.sum()}/{grid.size} points fall inside the "
              "calibration range or have enough lamp", file=sys.stderr)
        return 2
    y_show = fill_nan(y_grid)               # filled version for plots/colour

    # ---- 3. library
    cats = set(args.categories.split(",")) if args.categories else None
    spectra = lib.load_library(args.library, categories=cats)
    if not spectra:
        print(f"  ERROR: no spectra in {args.library}", file=sys.stderr)
        return 2
    if args.fwhm is not None:                  # explicit --fwhm
        fwhm = None if args.fwhm <= 0 else float(args.fwhm)
    else:                                      # auto: what the cal measured
        fwhm = float(cal_meta.get("fwhm_nm") or lib.FWHM_DEFAULT)
    paired = lib.to_grid(spectra, grid, fwhm=fwhm)
    print(f"  sample: {args.sample}")
    print(f"  library: {len(spectra)} spectra loaded, "
          f"{len(paired)} comparable in {grid[0]:.0f}-{grid[-1]:.0f} nm")
    print("  references degraded to "
          + (f"FWHM {fwhm:g} nm" if fwhm else "their native resolution"))

    # ---- 4. compare
    results = mtc.match(grid, y_grid, paired, grid, top=args.top)
    if not results:
        print("  no matches", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [r.as_row() for r in results]
    csv_path = out / "matches.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lib_curves = {r.name: y for r, y in paired}
    png = out / "comparison.png"
    pl.plot_matches(grid, y_show, results,
                    {k: v for k, v in lib_curves.items()
                     for r in results if r.spectrum.name == k},
                    png, title=Path(args.sample).name)
    pl.plot_top_matches_table(results, out / "top_matches.png")

    from .color import lab_of_spectrum, lab_to_hex
    s_hex = lab_to_hex(lab_of_spectrum(grid, y_show))
    print(f"\n  valid points: {int(ok.sum())}/{grid.size} "
          f"({100.0 * ok.mean():.0f}% of the band)")
    print(f"  sample colour (D65): {s_hex}")
    v = results.verdict
    names = {"identified": "IDENTIFIED",
             "uncertain": "UNCERTAIN",
             "unknown": "UNKNOWN"}
    print(f"  verdict: {names.get(v.label, v.label.upper())}  "
          f"(score {v.score:.3f} · margin between families {v.margin:.3f} · "
          f"thresholds {v.min_score:.2f}/{v.min_margin:.2f} · "
          f"{v.n} candidates)")
    if v.label == "unknown":
        print("  → no candidate reaches the threshold: the sample is "
              "featureless, noise-dominated, or the material is not in "
              "the library")
    elif v.label == "uncertain":
        print("  → good candidates exist but families cannot be told "
              "apart in 400-700 nm")
    print(f"  TOP {len(results)} matches:")
    for i, r in enumerate(results, 1):
        print(f"   {i:>2}. {r.score:.3f}  ΔE={r.delta_e:5.1f}  {r.color_hex}  "
              f"{r.spectrum.name[:58]}")
    print(f"\n  results in {out}/ (matches.csv, comparison.png)")
    return 0


# -------------------------------------------------------------------- info
def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.library)
    if not root.exists():
        print(f"  {root} does not exist → run: python scripts/fetch_usgs.py")
        return 1
    spectra = lib.load_library(root)
    from collections import Counter
    cats = Counter((s.category or "uncategorized") for s in spectra)
    vis = [s for s in spectra if s.visible]
    print(f"  library: {root}")
    print(f"    total spectra : {len(spectra)}")
    print(f"    with visible (400-700 nm): {len(vis)}")
    for c, n in cats.most_common():
        print(f"      {c:<24} {n}")
    return 0


# -------------------------------------------------------------------- demo
def _nm_to_rgb(nm: float) -> tuple[float, float, float]:
    """Visible colour approximation (Wyman et al.2013, Gaussian lobes)."""
    if nm < 380 or nm > 780:
        return 0.0, 0.0, 0.0
    def lobe(c: float, w: float, a: float) -> float:
        return a * np.exp(-0.5 * ((nm - c) / w) ** 2)
    r = lobe(640.0, 38.0, 1.06) + lobe(600.0, 40.0, 0.49)
    g = lobe(535.0, 42.0, 1.00) + lobe(575.0, 40.0, 0.30)
    b = lobe(465.0, 40.0, 1.05) + lobe(500.0, 45.0, 0.35)
    return float(np.clip(r, 0, 1)), float(np.clip(g, 0, 1)), float(np.clip(b, 0, 1))


def cmd_demo(args: argparse.Namespace) -> int:
    from PIL import Image

    rng = np.random.default_rng(7)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    W, H = 1280, 260
    TRUE = lambda p: 400.0 + (p / (W - 1)) * 300.0     # real calibration (demo)
    p = np.arange(W, dtype=float)
    nm = TRUE(p)

    from .calib import led_model
    lamp_sig = led_model(nm)
    lamp_sig /= lamp_sig.max()

    # pick a real sample from the library
    spectra = lib.load_library(LIB_DIR)
    if not spectra:
        print("  ERROR: empty library → python scripts/fetch_usgs.py",
              file=sys.stderr)
        return 2
    prefer = ("hematite", "goethite", "malachite", "azurite", "sulphur", "sulfur")
    avoid = ("thin", "film", "mixture", "+", "coating", "paint", "glass")
    target = next(
        (s for s in spectra
         if any(k in s.name.lower() for k in prefer)
         and not any(k in s.name.lower() for k in avoid)),
        next((s for s in spectra if any(k in s.name.lower() for k in prefer)),
             spectra[0]))
    r_sig = np.interp(nm, target.nm, target.reflectance,
                      left=float(target.reflectance[0]),
                      right=float(target.reflectance[-1]))
    print(f"  synthetic sample: {target.name}")

    def render(sig: np.ndarray, gain: float, noise: float) -> np.ndarray:
        prof = np.clip(sig * gain, 0, 250)
        rows = np.exp(-0.5 * ((np.arange(H) - H / 2) / (H * 0.16)) ** 2)
        cube = prof[None, :] * rows[:, None]
        cube += rng.normal(0.0, noise * gain, cube.shape)
        cube = np.clip(cube, 0, 250)
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        for i, wl in enumerate(nm):
            cr, cg, cb = _nm_to_rgb(wl)
            tot = max(cr + cg + cb, 1e-6)
            # the R+G+B sum channel must return exactly `cube`
            rgb[:, i, 0] = np.clip(cube[:, i] * cr / tot, 0, 255).astype(np.uint8)
            rgb[:, i, 1] = np.clip(cube[:, i] * cg / tot, 0, 255).astype(np.uint8)
            rgb[:, i, 2] = np.clip(cube[:, i] * cb / tot, 0, 255).astype(np.uint8)
        return rgb

    img_lamp = render(lamp_sig, 200.0, 0.010)
    img_sample = render(lamp_sig * r_sig, 200.0, 0.010)
    Image.fromarray(img_lamp).save(out / "demo_lamp.png")
    Image.fromarray(img_sample).save(out / "demo_sample.png")
    print(f"  synthetic images in {out}/")

    # --- automatic calibration from the white LED peaks
    cal_args = argparse.Namespace(
        image=str(out / "demo_lamp.png"), dark=None, axis=None,
        channel="sum", smooth=5, anchor=[], led=False, lines=False,
        range=None, blackbody=None, output=str(CAL_DIR / "demo_cal.json"),
        preview=str(out / "demo_calibration.png"))
    if cmd_calibrate(cal_args) != 0:
        return 1

    # --- extract the sample
    ex_args = argparse.Namespace(
        image=str(out / "demo_sample.png"), dark=None, rows=None, axis=None,
        channel="sum", smooth=5, frac=0.25, cal=cal_args.output,
        output=str(out / "demo_sample.csv"),
        preview=str(out / "demo_extraction.png"))
    if cmd_extract(ex_args) != 0:
        return 1
    ex_lamp = argparse.Namespace(
        **{**vars(ex_args), "image": cal_args.image,
           "output": str(out / "demo_lamp.csv"), "preview": None})
    if cmd_extract(ex_lamp) != 0:
        return 1

    # --- compare
    mt_args = argparse.Namespace(
        sample=ex_args.output, lamp=ex_lamp.output, cal=cal_args.output,
        library=str(LIB_DIR), grid=(400.0, 700.0, 5.0), top=5,
        categories="", smooth=5, domain="pixel", gamma=1.0,
        fwhm=lib.FWHM_DEFAULT,
        out=str(out / "demo_result"))
    rc = cmd_match(mt_args)
    if rc != 0:
        return rc

    # --- verdict: does the sample show up in the top3?
    import csv as _csv
    with (out / "demo_result" / "matches.csv").open(
            encoding="utf-8") as fh:
        names = [r["material"].lower() for r in _csv.DictReader(fh)]
    key = target.name.split(" [")[0].split()[0].lower()
    hit = any(key in n for n in names[:3])
    print(f"\n  TEST: searched '{key}' in top3 → "
          + ("PASS" if hit else "REVIEW (sample noise/calibration)"))
    return 0 if hit else 1


# -------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="espectro",
        description="USB camera spectrometer: photo → reflectance → "
                    "comparison against material libraries.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="photo → intensity vs pixel curve")
    e.add_argument("image")
    e.add_argument("--dark", help="dark photo (same exposure) to subtract")
    e.add_argument("--rows", nargs=2, type=int, metavar=("R0", "R1"),
                   help="band rows to average (auto when not given)")
    e.add_argument("--axis", choices=["x", "y"],
                   help="dispersion axis (auto when not given)")
    e.add_argument("--channel", choices=list(im.CHANNELS), default="sum")
    e.add_argument("--smooth", type=int, default=5)
    e.add_argument("--frac", type=float, default=0.25,
                   help="band detection threshold (0-1)")
    e.add_argument("--cal", help="calibration JSON → saves in nm")
    e.add_argument("-o", "--output", required=True)
    e.add_argument("--preview", help="PNG with band and profile")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("calibrate", help="bulb → pixel→nm calibration")
    c.add_argument("image", help="photo of the bulb spectrum")
    c.add_argument("--dark")
    c.add_argument("--anchor", nargs=2, type=float, action="append",
                   metavar=("PIXEL", "NM"), default=[],
                   help="known anchor (repeatable)")
    c.add_argument("--lines", action="store_true",
                   help="mercury/CFL line calibration "
                        "(404.7/435.8/546.1/577-579 nm) — the accurate path; "
                        "with only two peaks the 435.8/546.1 pair is assumed")
    c.add_argument("--led", action="store_true",
                   help="force LED mode (by default mercury/CFL lines are "
                        "tried first and the LED is used otherwise: blue "
                        "450 nm + phosphor 560 nm)")
    c.add_argument("--range", nargs=2, type=float, default=None,
                   metavar=("NM0", "NM1"),
                   help="assume the active band spans these nm "
                        "(e.g.400700); overrides LED mode")
    c.add_argument("--blackbody", nargs="?", type=float, default=None,
                   const=3000.0, metavar="T_K",
                   help="refine with Planck at T kelvin — filament bulbs "
                        "ONLY, not LED")
    c.add_argument("--axis", choices=["x", "y"])
    c.add_argument("--channel", choices=list(im.CHANNELS), default="sum")
    c.add_argument("--smooth", type=int, default=5)
    c.add_argument("-o", "--output", default=str(CAL_DIR / "cal.json"))
    c.add_argument("--preview")
    c.set_defaults(func=cmd_calibrate)

    m = sub.add_parser("match", help="spectrum → compare against libraries")
    m.add_argument("sample", help="sample CSV (from extract)")
    m.add_argument("--lamp", help="bulb CSV (same extraction)")
    m.add_argument("--cal", help="calibration JSON")
    m.add_argument("--library", default=str(LIB_DIR))
    m.add_argument("--grid", nargs=3, type=float, default=[400.0, 700.0, 5.0],
                   metavar=("MIN", "MAX", "STEP"))
    m.add_argument("--top", type=int, default=5)
    m.add_argument("--categories", default="",
                   help="filter by substring, e.g. mineral,vegeta")
    m.add_argument("--smooth", type=int, default=5)
    m.add_argument("--fwhm", type=float, default=None,
                   metavar="NM",
                   help="instrument resolution (FWHM nm) the references are "
                        "degraded to before comparing; defaults to the one "
                        f"the mercury-line calibration measured, else "
                        f"{lib.FWHM_DEFAULT:g} nm; 0 = do not degrade")
    m.add_argument("--gamma", type=float, default=1.0,
                   help="camera gamma before dividing (2.2 = typical "
                        "8-bit UVC;1.0 = do not linearize)")
    m.add_argument("--domain", choices=["pixel", "nm"], default="pixel")
    m.add_argument("--out", default=str(OUT_DIR / "last"))
    m.set_defaults(func=cmd_match)

    i = sub.add_parser("info", help="library status")
    i.add_argument("--library", default=str(LIB_DIR))
    i.set_defaults(func=cmd_info)

    d = sub.add_parser("demo", help="full test with synthetic images")
    d.add_argument("--out", default=str(ROOT / "data" / "examples"))
    d.set_defaults(func=cmd_demo)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
