#!/usr/bin/env python3
"""
reverse_convert.py - work out how a photo was converted, by trying every way of
converting it until the BMP matches the official one pixel by pixel.

Give it the photos and the BMPs the official Waveshare `convert` produced from
them. For one photo it builds the image in every combination of

    preprocessing  x  framing  x  resampling filter  x  padding colour
                   x  dithering  x  palette  x  API

- around 960 combinations - and compares each result with the official BMP
value by value, on all three channels of all 384000 pixels. The combinations
that reproduce it exactly are kept as hypotheses; the next photo narrows them
down. Then the surviving recipe is verified on the whole corpus, and any photo
that disagrees reopens the search.

Nothing is ever "close enough": a recipe survives only if every single pixel is
equal, and the BMP file itself hashes the same.

    tools/reverse_convert.py -i foto -b bmporiginali
    tools/reverse_convert.py -i foto -b bmporiginali -j 8 --csv esito.csv

  -i  folder with the photos that were converted
  -b  folder with the BMPs the official tool produced
  -n  verify on the first N photos only (default: all)
  -k  photos used to narrow the hypotheses down (default 3)
  -j  parallel workers (default: number of cores)

Self-contained: only Pillow is needed (numpy is used if present, for speed).
"""

import argparse
import hashlib
import io
import os
import platform
import struct
import sys
from collections import namedtuple
from concurrent.futures import ProcessPoolExecutor

try:
    from PIL import Image, ImageOps
    import PIL
except ImportError:
    sys.exit("Pillow is required:  pip install pillow")
try:
    import numpy as np
except ImportError:
    np = None

DEVICE_COLOURS = (0, 0, 0, 255, 255, 255, 0, 255, 0, 0, 0, 255,
                  255, 0, 0, 255, 255, 0, 255, 128, 0)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
STRIP = ("_scale_output", "_cut_output", "_output", "_pp")

# ---------------------------------------------------------------- the axes
PREPS = ("as opened", "convert RGB", "exif rotated", "ICC to sRGB")
GEOMS = ("scale+pad", "stretch", "fit", "cut")
FILTERS = (None, "NEAREST", "BILINEAR", "BICUBIC", "LANCZOS", "BOX", "HAMMING")
PADS = ("white", "black")
DITHERS = ("FLOYDSTEINBERG", "NONE")
PALETTES = ("256 entries", "7 entries")
APIS = ("quantize()", "convert('P') low level")

Recipe = namedtuple("Recipe", "prep geom filt pad dither palette api")


def recipe_name(r):
    filt = r.filt or "default"
    return (f"{r.prep} | {r.geom} filter={filt} pad={r.pad} | "
            f"dither={r.dither} palette={r.palette} via {r.api}")


OFFICIAL = Recipe("as opened", "scale+pad", None, "white",
                  "FLOYDSTEINBERG", "256 entries", "quantize()")


# ------------------------------------------------------------------ BMP I/O
def read_bmp(path):
    """(width, height, RGB bytes) of a 24-bit BMP, first row first."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"BM":
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    comp = struct.unpack_from("<I", data, 30)[0]
    if bpp != 24 or comp != 0:
        raise ValueError(f"unsupported BMP ({bpp} bpp, compression {comp})")
    bottom_up = height > 0
    height = abs(height)
    stride = (width * 3 + 3) & ~3
    end = offset + stride * height
    if len(data) < end:
        raise ValueError("truncated BMP")
    if np is not None:
        rows = np.frombuffer(data[offset:end], dtype=np.uint8).reshape(height, stride)
        rows = rows[:, :width * 3].reshape(height, width, 3)[:, :, ::-1]
        if bottom_up:
            rows = rows[::-1]
        return width, height, rows.tobytes()
    out = bytearray()
    for y in range(height):
        src = height - 1 - y if bottom_up else y
        line = data[offset + src * stride: offset + src * stride + width * 3]
        for x in range(width):
            out += line[x * 3 + 2:x * 3 + 3] + line[x * 3 + 1:x * 3 + 2] + line[x * 3:x * 3 + 1]
    return width, height, bytes(out)


def compare_pixels(got, ref, width):
    """Every channel of every pixel: (differing pixels, total, max delta, first (x,y))."""
    total = len(ref) // 3
    if got == ref:
        return 0, total, 0, None
    if np is not None:
        a = np.frombuffer(got, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
        b = np.frombuffer(ref, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
        bad = (a != b).any(axis=1)
        i = int(np.argmax(bad))
        return int(bad.sum()), total, int(np.abs(a - b).max()), (i % width, i // width)
    nd = maxd = 0
    first = None
    for i in range(total):
        pa, pb = got[i * 3:i * 3 + 3], ref[i * 3:i * 3 + 3]
        if pa != pb:
            nd += 1
            maxd = max(maxd, max(abs(u - v) for u, v in zip(pa, pb)))
            if first is None:
                first = (i % width, i // width)
    return nd, total, maxd, first


def bmp_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------ building blocks
def apply_prep(im, prep):
    if prep == "as opened":
        return im
    if prep == "convert RGB":
        return im.convert("RGB")
    if prep == "exif rotated":
        return ImageOps.exif_transpose(im)
    if prep == "ICC to sRGB":
        profile = im.info.get("icc_profile")
        if not profile:
            return None
        from PIL import ImageCms
        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        return ImageCms.profileToProfile(im.convert("RGB"), src,
                                         ImageCms.createProfile("sRGB"), outputMode="RGB")
    return None


def build_frame(im, tw, th, geom, filt, pad):
    """The 800x480 RGB image handed to the dithering."""
    colour = (255, 255, 255) if pad == "white" else (0, 0, 0)
    resample = getattr(Image.Resampling, filt) if filt else None
    w, h = im.size
    if geom == "scale+pad":
        ratio = max(tw / w, th / h)
        rw, rh = int(w * ratio), int(h * ratio)
        scaled = im.resize((rw, rh)) if resample is None else im.resize((rw, rh), resample)
        frame = Image.new("RGB", (tw, th), colour)
        frame.paste(scaled, ((tw - rw) // 2, (th - rh) // 2))
        return frame
    if geom == "stretch":
        rgb = im.convert("RGB")
        return rgb.resize((tw, th)) if resample is None else rgb.resize((tw, th), resample)
    if geom == "fit":
        rgb = im.convert("RGB")
        return (ImageOps.fit(rgb, (tw, th)) if resample is None
                else ImageOps.fit(rgb, (tw, th), method=resample))
    if geom == "cut":
        return ImageOps.pad(im.crop((0, 0, w, h)), (tw, th),
                            color=colour, centering=(0.5, 0.5)).convert("RGB")
    return None


def _palette(kind):
    pal = Image.new("P", (1, 1))
    pal.putpalette(DEVICE_COLOURS + ((0, 0, 0) * 249 if kind == "256 entries" else ()))
    return pal


PAL_CACHE = {k: _palette(k) for k in PALETTES}


def quantise(frame, dither, palette, api):
    d = Image.Dither.FLOYDSTEINBERG if dither == "FLOYDSTEINBERG" else Image.Dither.NONE
    pal = PAL_CACHE[palette]
    if api == "quantize()":
        return frame.quantize(dither=d, palette=pal).convert("RGB")
    return frame._new(frame.im.convert("P", d, pal.im)).convert("RGB")


def all_recipes():
    out = []
    for prep in PREPS:
        for geom in GEOMS:
            filters = (None,) if geom == "cut" else FILTERS
            pads = PADS if geom in ("scale+pad", "cut") else ("white",)
            for filt in filters:
                for pad in pads:
                    for dither in DITHERS:
                        for palette in PALETTES:
                            for api in APIS:
                                out.append(Recipe(prep, geom, filt, pad,
                                                  dither, palette, api))
    return out


# --------------------------------------------------------------- convert once
def convert_with(src, tw, th, r):
    im = Image.open(src)
    im.load()
    prepped = apply_prep(im, r.prep)
    if prepped is None:
        return None
    frame = build_frame(prepped, tw, th, r.geom, r.filt, r.pad)
    if frame is None:
        return None
    return quantise(frame, r.dither, r.palette, r.api)


# ----------------------------------------------------- search one photo fully
def search_photo(src, ref, verbose=True):
    """Try every recipe on this photo; return the ones that match it exactly."""
    tw, th, ref_rgb = read_bmp(ref)
    ref_sha = sha_file(ref)
    im = Image.open(src)
    im.load()

    # Many recipes produce the very same frame (a photo already 800x480 makes
    # every framing a no-op); build each distinct frame once.
    frames = {}
    skipped = 0
    for prep in PREPS:
        prepped = apply_prep(im, prep)
        if prepped is None:
            skipped += 1
            continue
        for geom in GEOMS:
            filters = (None,) if geom == "cut" else FILTERS
            pads = PADS if geom in ("scale+pad", "cut") else ("white",)
            for filt in filters:
                for pad in pads:
                    try:
                        frame = build_frame(prepped, tw, th, geom, filt, pad)
                    except Exception:
                        continue
                    if frame is None:
                        continue
                    frames.setdefault(hashlib.sha256(frame.tobytes()).hexdigest(),
                                      (frame, []))[1].append((prep, geom, filt, pad))

    exact, best, tried = [], (1.0, None, None), 0
    for frame, combos in frames.values():
        for dither in DITHERS:
            for palette in PALETTES:
                for api in APIS:
                    result = quantise(frame, dither, palette, api)
                    got = result.tobytes()
                    tried += 1
                    if len(got) != len(ref_rgb):
                        continue
                    nd, total, maxd, first = compare_pixels(got, ref_rgb, tw)
                    if nd == 0 and hashlib.sha256(bmp_bytes(result)).hexdigest() == ref_sha:
                        for prep, geom, filt, pad in combos:
                            exact.append(Recipe(prep, geom, filt, pad, dither, palette, api))
                    ratio = nd / max(1, total)
                    if ratio < best[0]:
                        best = (ratio, Recipe(combos[0][0], combos[0][1], combos[0][2],
                                              combos[0][3], dither, palette, api),
                                (nd, total, maxd, first))
    if verbose:
        print(f"    {len(frames)} distinct framings x {len(DITHERS) * len(PALETTES) * len(APIS)} "
              f"quantisations = {tried} conversions compared pixel by pixel")
    return set(exact), best


# ------------------------------------------------- verify a recipe everywhere
def verify_one(job):
    key, src, ref, r = job
    try:
        tw, th, ref_rgb = read_bmp(ref)
        result = convert_with(src, tw, th, r)
        if result is None:
            return key, False, 0, 0, 0, None, False, "recipe not applicable"
        got = result.tobytes()
        if len(got) != len(ref_rgb):
            return key, False, tw * th, tw * th, 255, (0, 0), False, "different size"
        nd, total, maxd, first = compare_pixels(got, ref_rgb, tw)
        file_ok = hashlib.sha256(bmp_bytes(result)).hexdigest() == sha_file(ref)
        return key, (nd == 0 and file_ok), nd, total, maxd, first, file_ok, ""
    except Exception as exc:
        return key, False, 0, 0, 0, None, False, f"{type(exc).__name__}: {exc}"


# -------------------------------------------------------------------- pairing
def key_of(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    changed = True
    while changed:
        changed = False
        for suf in STRIP:
            if stem.lower().endswith(suf):
                stem, changed = stem[:-len(suf)], True
    return stem.lower()


def index_folder(folder, exts):
    idx = {}
    for n in sorted(os.listdir(folder)):
        if n.lower().endswith(exts) and not n.startswith("._"):
            idx.setdefault(key_of(n), os.path.join(folder, n))
    return idx


# ---------------------------------------------------------------- code output
def recipe_code(r):
    lines = ["from PIL import Image, ImageOps", "import os", "",
             f"# reproduces the official converter - pillow=={PIL.__version__}",
             "", "def convert_one(path, out_path):", "    im = Image.open(path)"]
    if r.prep == "convert RGB":
        lines.append('    im = im.convert("RGB")')
    elif r.prep == "exif rotated":
        lines.append("    im = ImageOps.exif_transpose(im)")
    elif r.prep == "ICC to sRGB":
        lines.append("    # ICC profile converted to sRGB first (see ImageCms)")
    lines.append("    w, h = im.size")
    lines.append("    TW, TH = (800, 480) if w > h else (480, 800)")
    colour = "(255, 255, 255)" if r.pad == "white" else "(0, 0, 0)"
    filt = f", Image.Resampling.{r.filt}" if r.filt else ""
    if r.geom == "scale+pad":
        lines += ["    ratio = max(TW / w, TH / h)",
                  "    rw, rh = int(w * ratio), int(h * ratio)",
                  f'    frame = Image.new("RGB", (TW, TH), {colour})',
                  f"    frame.paste(im.resize((rw, rh){filt}), "
                  "((TW - rw) // 2, (TH - rh) // 2))"]
    elif r.geom == "stretch":
        lines.append(f'    frame = im.convert("RGB").resize((TW, TH){filt})')
    elif r.geom == "fit":
        m = f", method=Image.Resampling.{r.filt}" if r.filt else ""
        lines.append(f'    frame = ImageOps.fit(im.convert("RGB"), (TW, TH){m})')
    else:
        lines.append(f"    frame = ImageOps.pad(im.crop((0, 0) + im.size), (TW, TH),")
        lines.append(f"                         color={colour}, centering=(0.5, 0.5)"
                     f').convert("RGB")')
    tail = " + (0,0,0) * 249" if r.palette == "256 entries" else ""
    lines += ['    pal = Image.new("P", (1, 1))',
              "    pal.putpalette((0,0,0, 255,255,255, 0,255,0, 0,0,255,",
              f"                    255,0,0, 255,255,0, 255,128,0){tail})"]
    if r.api == "quantize()":
        lines.append(f"    out = frame.quantize(dither=Image.Dither.{r.dither}, "
                     'palette=pal).convert("RGB")')
    else:
        lines.append(f'    out = frame._new(frame.im.convert("P", Image.Dither.{r.dither}, '
                     'pal.im)).convert("RGB")')
    lines += ["    out.save(out_path)", "", "",
              "# output name used by the official tool:",
              '#   os.path.splitext(path)[0] + "_scale_output.bmp"']
    return "\n".join(lines)


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--images", required=True)
    ap.add_argument("-b", "--bmp", required=True)
    ap.add_argument("-n", "--limit", type=int, default=0)
    ap.add_argument("-k", "--learn", type=int, default=3,
                    help="photos used to narrow the hypotheses (default 3)")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--max-rounds", type=int, default=4)
    ap.add_argument("--csv")
    args = ap.parse_args()

    print("=" * 78)
    print("reverse_convert - how was this photo converted?")
    print("=" * 78)
    print(f"Python {sys.version.split()[0]} on {platform.system()} {platform.machine()} | "
          f"Pillow {PIL.__version__} | numpy {np.__version__ if np is not None else 'no'}")

    src_idx = index_folder(args.images, IMAGE_EXT)
    ref_idx = index_folder(args.bmp, (".bmp",))
    keys = sorted(set(src_idx) & set(ref_idx))
    if args.limit:
        keys = keys[:args.limit]
    if not keys:
        sys.exit("no photo could be paired with an official BMP - check the file names")
    print(f"Photos paired: {len(keys)}   |   recipes in the search space: {len(all_recipes())}")

    # ---------------------------------------------------------------- learning
    print("\n--- LEARNING: trying every recipe on the first photos "
          "-------------------")
    alive = None
    for k in keys[:max(1, args.learn)]:
        print(f"  {os.path.basename(src_idx[k])}")
        exact, best = search_photo(src_idx[k], ref_idx[k])
        if not exact:
            ratio, r, stats = best
            nd, total, maxd, first = stats
            print(f"    NO recipe reproduces this photo. Closest: {recipe_name(r)}")
            print(f"    {nd:,}/{total:,} pixels differ, max channel delta {maxd}, "
                  f"first at {first}")
            print("\n    The photo in the sources folder is probably not the file the\n"
                  "    official BMP was made from (re-cropped or re-exported since).")
            continue
        alive = exact if alive is None else (alive & exact)
        print(f"    {len(exact)} recipe(s) reproduce it exactly, every pixel"
              f" -> {len(alive) if alive else 0} still consistent with the previous photos")
        if alive is not None and len(alive) == 1:
            break

    if not alive:
        print("\nNo recipe reproduces the photos. Nothing to verify.")
        return 1

    recipe = OFFICIAL if OFFICIAL in alive else sorted(alive, key=recipe_name)[0]
    print(f"\n  Hypotheses left: {len(alive)}")
    print(f"  Recipe to verify: {recipe_name(recipe)}")
    if len(alive) > 1:
        print("  (the others differ only where this corpus cannot tell them apart,\n"
              "   e.g. equivalent filters, or padding that is never visible)")

    # ------------------------------------------------------------ verification
    results, detail = {}, {}
    for rnd in range(1, args.max_rounds + 1):
        print(f"\n--- VERIFYING on all {len(keys)} photos, pixel by pixel "
              f"(round {rnd}) ------")
        jobs = [(k, src_idx[k], ref_idx[k], recipe) for k in keys]
        failures, errors = [], {}
        done = px_seen = px_bad = 0
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for key, ok, nd, total, maxd, first, file_ok, err in pool.map(
                    verify_one, jobs, chunksize=8):
                done += 1
                px_seen += total
                px_bad += nd
                results[key] = "identical" if ok else "different"
                detail[key] = (nd, total, maxd, first, file_ok)
                if not ok:
                    failures.append(key)
                    if err:
                        errors[key] = err
                    elif len(failures) <= 5:
                        where = f"first at {first}" if first else "pixels equal, file differs"
                        print(f"  {key}: {nd:,}/{total:,} pixels differ, {where}")
                if done % 200 == 0 or done == len(jobs):
                    print(f"  {done}/{len(jobs)} photos | {px_seen:,} pixels compared | "
                          f"{px_bad:,} different | {len(failures)} photo(s) not matching",
                          flush=True)
        if errors:
            msg = max(set(errors.values()), key=list(errors.values()).count)
            print(f"\n  {len(errors)} photo(s) raised an error, e.g. {msg}")
            if len(errors) == len(keys):
                print("  Systematic failure: nothing was compared.")
                return 2
        if not failures:
            print(f"\nEvery pixel of every photo matches: {px_seen:,} pixels compared, "
                  f"0 different.")
            break
        print(f"\n  {len(failures)} photo(s) disagree -> reopening the search on "
              f"{min(3, len(failures))} of them")
        changed = False
        for k in failures[:3]:
            print(f"  {os.path.basename(src_idx[k])}")
            exact, best = search_photo(src_idx[k], ref_idx[k])
            if not exact:
                ratio, r, stats = best
                print(f"    no recipe reproduces it (best {100 * ratio:.4f}% of pixels differ)")
                continue
            merged = alive & exact
            if merged:
                alive = merged
                new = OFFICIAL if OFFICIAL in alive else sorted(alive, key=recipe_name)[0]
                if new != recipe:
                    recipe, changed = new, True
                    print(f"    -> refined: {recipe_name(recipe)}")
            else:
                print(f"    matched by {len(exact)} recipe(s), none compatible with the "
                      f"photos already verified: the corpus is not homogeneous")
        if not changed:
            print("\n  The search cannot explain the photos that fail; stopping here.")
            break

    # ------------------------------------------------------------------ report
    ok_n = sum(1 for v in results.values() if v == "identical")
    px_seen = sum(d[1] for d in detail.values())
    px_bad = sum(d[0] for d in detail.values())
    print("\n" + "=" * 78)
    print(f"PHOTOS: {ok_n}/{len(keys)} byte-identical ({100.0 * ok_n / len(keys):.4f}%)")
    if px_seen:
        print(f"PIXELS: {px_seen - px_bad:,}/{px_seen:,} identical "
              f"({100.0 * (px_seen - px_bad) / px_seen:.6f}%), {px_bad:,} different")
    print(f"RECIPE: {recipe_name(recipe)}")
    print("=" * 78)

    if args.csv:
        import csv
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["photo", "outcome", "pixels_total", "pixels_different",
                        "max_channel_delta", "first_difference", "file_identical"])
            for k in keys:
                nd, total, maxd, first, file_ok = detail.get(k, (0, 0, 0, None, False))
                w.writerow([os.path.basename(src_idx[k]), results.get(k, "?"), total, nd,
                            maxd, f"{first[0]},{first[1]}" if first else "", file_ok])
        print(f"per-photo report written to {args.csv}")

    if ok_n == len(keys):
        print("\nThe converter to write is exactly this:\n")
        print(recipe_code(recipe))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
