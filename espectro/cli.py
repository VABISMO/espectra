"""Interfaz de línea de comandos del espectrómetro.

    python -m espectro extract   FOTO   ...   -> curva intensidad vs píxel
    python -m espectro calibrate BOMBILLA ...  -> calibración píxel -> nm
    python -m espectro match     MUESTRA ...   -> comparar con la biblioteca
    python -m espectro info                    -> estado de las bibliotecas
    python -m espectro demo                    -> prueba completa sintética
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
from .reflectance import pseudo_reflectance, resample

ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "data" / "libraries"
CAL_DIR = ROOT / "data" / "calibration"
OUT_DIR = ROOT / "resultados"
DEFAULT_GRID = np.arange(400.0, 700.0 + 1e-9, 5.0)


# ----------------------------------------------------------------- extract
def cmd_extract(args: argparse.Namespace) -> int:
    img = im.load_gray(args.image, channel=args.channel)
    dark = im.load_gray(args.dark, channel=args.channel) if args.dark else None
    rows = tuple(args.rows) if args.rows else None
    ext = im.extract_profile(img, rows=rows, axis=args.axis, dark=dark,
                             smooth=args.smooth, frac=args.frac)
    print(f"  eje de dispersión: {ext.axis}   banda de filas: {ext.rows}   "
          f"{ext.pixel.size} píxeles")

    pixel, inten = ext.pixel, ext.intensity
    domain = "pixel"
    if args.cal:
        coeffs, meta = cal.load_cal(args.cal)
        nm = cal.apply_cal(coeffs, pixel)
        keep = (nm >= 180) & (nm <= 2600)
        pixel, inten = nm[keep], inten[keep]
        domain = "nm"
        print(f"  calibración aplicada desde {args.cal}")

    im.save_spectrum(args.output, pixel, inten,
                     extra={"domain": domain,
                            "source_image": str(args.image),
                            "rows": f"{ext.rows[0]}-{ext.rows[1]}"})
    print(f"  espectro guardado en {args.output}  (dominio: {domain})")

    if args.preview:
        pl.plot_extraction(img, ext.rows, ext.pixel, ext.intensity,
                           args.preview, axis=ext.axis)
        print(f"  vista previa en {args.preview}")
    return 0


# --------------------------------------------------------------- calibrate
def cmd_calibrate(args: argparse.Namespace) -> int:
    lang = getattr(args, "lang", "es")
    tr = lambda es, en: es if lang == "es" else en
    img = im.load_gray(args.image, channel=args.channel)
    dark = im.load_gray(args.dark, channel=args.channel) if args.dark else None
    ext = im.extract_profile(img, axis=args.axis, dark=dark,
                             smooth=args.smooth)
    pixel, inten = ext.pixel, ext.intensity

    meta: dict = {"source_image": str(args.image), "rows": f"{ext.rows[0]}-{ext.rows[1]}"}
    nm_range = tuple(args.range) if args.range else (400.0, 700.0)
    T = corr = None
    led_peaks = None

    if args.anchor:
        coeffs = cal.poly_from_anchors([(p, n) for p, n in args.anchor])
        meta["mode"] = "anchors"
        meta["anchors"] = [{"pixel": p, "nm": n} for p, n in args.anchor]
        print(tr(f"  {len(args.anchor)} anclas manuales aplicadas",
                  f"  {len(args.anchor)} manual anchors applied"))

    elif args.blackbody is not None:          # solo bombillas de filamento
        coeffs, T, corr = _range_init(pixel, inten, nm_range, meta, lang)
        T_ask = args.blackbody if args.blackbody > 0 else 3000.0
        coeffs, T, corr = cal.refine_blackbody(
            coeffs, pixel, inten,
            T_range=(max(1800.0, T_ask - 600), T_ask + 600))
        meta["mode"] = "blackbody"
        meta["blackbody_T"] = T
        meta["blackbody_corr"] = corr
        print(tr(f"  cuerpo negro: T={T:.0f} K, r={corr:.3f}  "
                  "(válido solo para filamento, NO para LED)",
                  f"  blackbody: T={T:.0f} K, r={corr:.3f}  "
                  "(filament bulbs only, NOT for LED)"))

    elif args.range is not None:              # rango asumido explícito
        coeffs = _range_init(pixel, inten, nm_range, meta, lang)[0]
        meta["mode"] = "range"

    else:                                     # LED blanco (por defecto)
        led_peaks = cal.detect_led_peaks(pixel, inten)
        if led_peaks is not None:
            p_blue, p_phos = led_peaks
            coeffs = cal.poly_from_anchors([(p_blue, 450.0),
                                            (p_phos, 560.0)])
            meta["mode"] = "led"
            meta["led_peaks_pixel"] = [p_blue, p_phos]
            meta["led_peaks_nm"] = [450.0, 560.0]
            print(tr(f"  picos LED detectados: azul p={p_blue:.0f} (~450 nm), "
                      f"fósforo p={p_phos:.0f} (~560 nm)",
                      f"  LED peaks found: blue p={p_blue:.0f} (~450 nm), "
                      f"phosphor p={p_phos:.0f} (~560 nm)"))
            coeffs, corr = cal.refine_led(coeffs, pixel, inten)
            meta["led_corr"] = corr
            if np.isfinite(corr):
                print(tr(f"  refinado con plantilla de LED: r={corr:.3f}",
                          f"  refined with LED template: r={corr:.3f}"))
        else:
            print(tr("  aviso: no se detectaron los dos picos del LED; "
                      "se usa el rango asumido (menos preciso)",
                      "  warning: LED peaks not found; using the assumed "
                      "range (less accurate)"))
            coeffs = _range_init(pixel, inten, nm_range, meta, lang)[0]
            meta["mode"] = "range_fallback"

    cal.save_cal(args.output, coeffs, meta)
    print(tr(f"  calibración guardada en {args.output}",
              f"  calibration saved to {args.output}"))
    print(f"    nm(p) = {np.array2string(np.asarray(coeffs), precision=4)}")

    if args.preview:
        model_fn = cal.led_model if meta.get("mode") == "led" else None
        pl.plot_calibration(pixel, inten, coeffs, T, corr, args.preview,
                            model_fn=model_fn)
        print(tr(f"  vista previa en {args.preview}",
                  f"  preview written to {args.preview}"))
    return 0


def _range_init(pixel, inten, nm_range, meta, lang="es"):
    """Mapa lineal del rango activo de la banda a `nm_range`."""
    on = inten > inten.max() * 0.03
    idx = np.flatnonzero(on)
    if idx.size < 8:
        raise SystemExit("ERROR: " + (
            "la banda de la bombilla es demasiado corta" if lang == "es"
            else "the bulb band is too short"))
    p0, p1 = float(idx[0]), float(idx[-1])
    nm0, nm1 = nm_range
    coeffs = cal.linear_from_range(p0, p1, nm0, nm1)
    meta["pixel_range"] = [p0, p1]
    meta["assumed_nm_range"] = [nm0, nm1]
    print("  " + (f"rango activo: píxeles {p0:.0f}..{p1:.0f} → {nm0:.0f}..{nm1:.0f} nm"
                   if lang == "es" else
                   f"active range: pixels {p0:.0f}..{p1:.0f} → {nm0:.0f}..{nm1:.0f} nm"))
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

    # ---- 1. pseudo-reflectancia (en el dominio en que estén ambos)
    if lamp_px is not None:
        if lamp_px.size != s_px.size:
            print("  ERROR: muestra y bombilla tienen distinto número de "
                  "puntos; extráelas igual (--rows/--smooth)", file=sys.stderr)
            return 2
        y = pseudo_reflectance(s_iy, lamp_iy, smooth=args.smooth)
    else:
        print("  aviso: sin bombilla de referencia se usa la intensidad "
              "normalizada (sin dividir lámpara/sensor)")
        y = np.clip(s_iy, 0, None)
        y = y / y.max() if y.max() > 0 else y

    # ---- 2. píxel -> nm
    if domain != "nm":
        if not args.cal:
            print("  ERROR: los datos están en píxeles y falta --cal "
                  "(ejecuta primero `calibrate`)", file=sys.stderr)
            return 2
        coeffs, _ = cal.load_cal(args.cal)
        nm = cal.apply_cal(coeffs, s_px)
    else:
        nm = s_px

    grid = np.arange(args.grid[0], args.grid[1] + 1e-9, args.grid[2])
    y_grid = resample(nm, y, grid)
    ok = np.isfinite(y_grid)
    if ok.sum() < 0.5 * grid.size:
        print(f"  ERROR: solo {ok.sum()}/{grid.size} puntos caen dentro del "
              "rango de la calibración", file=sys.stderr)
        return 2
    y_grid = np.nan_to_num(y_grid, nan=float(np.nanmean(y_grid[ok])))

    # ---- 3. biblioteca
    cats = set(args.categories.split(",")) if args.categories else None
    spectra = lib.load_library(args.library, categories=cats)
    if not spectra:
        print(f"  ERROR: sin espectros en {args.library}", file=sys.stderr)
        return 2
    paired = lib.to_grid(spectra, grid)
    print(f"  muestra: {args.sample}")
    print(f"  biblioteca: {len(spectra)} espectros cargados, "
          f"{len(paired)} comparables en {grid[0]:.0f}-{grid[-1]:.0f} nm")

    # ---- 4. comparar
    results = mtc.match(grid, y_grid, paired, grid, top=args.top)
    if not results:
        print("  sin coincidencias", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [r.as_row() for r in results]
    csv_path = out / "coincidencias.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lib_curves = {r.name: y for r, y in paired}
    png = out / "comparacion.png"
    pl.plot_matches(grid, y_grid, results,
                    {k: v for k, v in lib_curves.items()
                     for r in results if r.spectrum.name == k},
                    png, title=Path(args.sample).name)
    pl.plot_top_matches_table(results, out / "top_coincidencias.png")

    from .color import lab_of_spectrum, lab_to_hex
    s_hex = lab_to_hex(lab_of_spectrum(grid, y_grid))
    print(f"\n  color de la muestra (D65): {s_hex}")
    print(f"  TOP {len(results)} coincidencias:")
    for i, r in enumerate(results, 1):
        print(f"   {i:>2}. {r.score:.3f}  ΔE={r.delta_e:5.1f}  {r.color_hex}  "
              f"{r.spectrum.name[:58]}")
    print(f"\n  resultados en {out}/ (coincidencias.csv, comparacion.png)")
    return 0


# -------------------------------------------------------------------- info
def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.library)
    if not root.exists():
        print(f"  no existe {root}  →  ejecuta: python scripts/fetch_usgs.py")
        return 1
    spectra = lib.load_library(root)
    from collections import Counter
    cats = Counter((s.category or "sin categoría") for s in spectra)
    vis = [s for s in spectra if s.visible]
    print(f"  biblioteca: {root}")
    print(f"    espectros totales : {len(spectra)}")
    print(f"    con visible (400-700 nm): {len(vis)}")
    for c, n in cats.most_common():
        print(f"      {c:<24} {n}")
    return 0


# -------------------------------------------------------------------- demo
def _nm_to_rgb(nm: float) -> tuple[float, float, float]:
    """Aproximación de color visible (Wyman et al. 2013, lóbulos gaussianos)."""
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
    TRUE = lambda p: 400.0 + (p / (W - 1)) * 300.0     # calibración real (demo)
    p = np.arange(W, dtype=float)
    nm = TRUE(p)

    from .calib import led_model
    lamp_sig = led_model(nm)
    lamp_sig /= lamp_sig.max()

    # elegimos una muestra real de la biblioteca
    spectra = lib.load_library(LIB_DIR)
    if not spectra:
        print("  ERROR: biblioteca vacía → python scripts/fetch_usgs.py",
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
    print(f"  muestra sintética: {target.name}")

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
            # el canal suma R+G+B debe devolver exactamente `cube`
            rgb[:, i, 0] = np.clip(cube[:, i] * cr / tot, 0, 255).astype(np.uint8)
            rgb[:, i, 1] = np.clip(cube[:, i] * cg / tot, 0, 255).astype(np.uint8)
            rgb[:, i, 2] = np.clip(cube[:, i] * cb / tot, 0, 255).astype(np.uint8)
        return rgb

    img_lamp = render(lamp_sig, 200.0, 0.010)
    img_sample = render(lamp_sig * r_sig, 200.0, 0.010)
    Image.fromarray(img_lamp).save(out / "demo_bombilla.png")
    Image.fromarray(img_sample).save(out / "demo_muestra.png")
    print(f"  imágenes sintéticas en {out}/")

    # --- calibración automática por picos del LED blanco
    cal_args = argparse.Namespace(
        image=str(out / "demo_bombilla.png"), dark=None, axis=None,
        channel="sum", smooth=5, anchor=[], led=True, range=None,
        blackbody=None, output=str(CAL_DIR / "demo_cal.json"),
        preview=str(out / "demo_calibracion.png"))
    if cmd_calibrate(cal_args) != 0:
        return 1

    # --- extracción de la muestra
    ex_args = argparse.Namespace(
        image=str(out / "demo_muestra.png"), dark=None, rows=None, axis=None,
        channel="sum", smooth=5, frac=0.25, cal=cal_args.output,
        output=str(out / "demo_muestra.csv"),
        preview=str(out / "demo_extraccion.png"))
    if cmd_extract(ex_args) != 0:
        return 1
    ex_lamp = argparse.Namespace(
        **{**vars(ex_args), "image": cal_args.image,
           "output": str(out / "demo_bombilla.csv"), "preview": None})
    if cmd_extract(ex_lamp) != 0:
        return 1

    # --- comparación
    mt_args = argparse.Namespace(
        sample=ex_args.output, lamp=ex_lamp.output, cal=cal_args.output,
        library=str(LIB_DIR), grid=(400.0, 700.0, 5.0), top=5,
        categories="", smooth=5, domain="pixel",
        out=str(out / "demo_resultado"))
    rc = cmd_match(mt_args)
    if rc != 0:
        return rc

    # --- veredicto: ¿aparece la muestra en el top 3?
    import csv as _csv
    with (out / "demo_resultado" / "coincidencias.csv").open(
            encoding="utf-8") as fh:
        names = [r["material"].lower() for r in _csv.DictReader(fh)]
    key = target.name.split(" [")[0].split()[0].lower()
    hit = any(key in n for n in names[:3])
    print(f"\n  PRUEBA: buscado '{key}' en top 3 → "
          + ("PASS" if hit else "REVISAR (muestra ruido/calibración)"))
    return 0 if hit else 1


# -------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="espectro",
        description="Espectrómetro con cámara USB: foto → reflectancia → "
                    "comparación con bibliotecas de materiales.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="foto → curva intensidad vs píxel")
    e.add_argument("image")
    e.add_argument("--dark", help="foto oscura (misma exposición) para restar")
    e.add_argument("--rows", nargs=2, type=int, metavar=("R0", "R1"),
                   help="filas de la banda a promediar (auto si no se da)")
    e.add_argument("--axis", choices=["x", "y"],
                   help="eje de dispersión (auto si no se da)")
    e.add_argument("--channel", choices=list(im.CHANNELS), default="sum")
    e.add_argument("--smooth", type=int, default=5)
    e.add_argument("--frac", type=float, default=0.25,
                   help="umbral de detección de la banda (0-1)")
    e.add_argument("--cal", help="calibración JSON → guarda en nm")
    e.add_argument("-o", "--output", required=True)
    e.add_argument("--preview", help="PNG con banda y perfil")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("calibrate", help="bombilla → calibración píxel→nm")
    c.add_argument("image", help="foto del espectro de la bombilla")
    c.add_argument("--dark")
    c.add_argument("--anchor", nargs=2, type=float, action="append",
                   metavar=("PIXEL", "NM"), default=[],
                   help="ancla conocida (repetible)")
    c.add_argument("--led", action="store_true",
                   help="calibración por picos del LED blanco "
                        "(POR DEFECTO: azul 450 nm + fósforo 560 nm)")
    c.add_argument("--range", nargs=2, type=float, default=None,
                   metavar=("NM0", "NM1"),
                   help="asumir la banda activa entre estos nm "
                        "(p.ej. 400 700); anula el modo LED")
    c.add_argument("--blackbody", nargs="?", type=float, default=None,
                   const=3000.0, metavar="T_K",
                   help="refinar con Planck a T Kelvin — SOLO bombillas de "
                        "filamento, no LED")
    c.add_argument("--axis", choices=["x", "y"])
    c.add_argument("--channel", choices=list(im.CHANNELS), default="sum")
    c.add_argument("--smooth", type=int, default=5)
    c.add_argument("-o", "--output", default=str(CAL_DIR / "cal.json"))
    c.add_argument("--preview")
    c.set_defaults(func=cmd_calibrate)

    m = sub.add_parser("match", help="espectro → comparar con bibliotecas")
    m.add_argument("sample", help="CSV de la muestra (extract)")
    m.add_argument("--lamp", help="CSV de la bombilla (misma extracción)")
    m.add_argument("--cal", help="calibración JSON")
    m.add_argument("--library", default=str(LIB_DIR))
    m.add_argument("--grid", nargs=3, type=float, default=[400.0, 700.0, 5.0],
                   metavar=("MIN", "MAX", "PASO"))
    m.add_argument("--top", type=int, default=5)
    m.add_argument("--categories", default="",
                   help="filtrar por subcadena, p.ej. mineral,vegeta")
    m.add_argument("--smooth", type=int, default=5)
    m.add_argument("--domain", choices=["pixel", "nm"], default="pixel")
    m.add_argument("--out", default=str(OUT_DIR / "ultima"))
    m.set_defaults(func=cmd_match)

    i = sub.add_parser("info", help="estado de las bibliotecas")
    i.add_argument("--library", default=str(LIB_DIR))
    i.set_defaults(func=cmd_info)

    d = sub.add_parser("demo", help="prueba completa con imágenes sintéticas")
    d.add_argument("--out", default=str(ROOT / "data" / "examples"))
    d.set_defaults(func=cmd_demo)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)

