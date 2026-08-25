#!/usr/bin/env python3
"""
find_exact_recipe.py - find the exact recipe that reproduces Waveshare's
`convert`, by brute force against real converted files.

Give it the images that were converted and the BMPs the official tool produced
from them; it builds each image with every plausible combination of

    geometry step  (how the photo becomes an 800x480 RGB frame)
      x
    quantisation step  (how those pixels become the 7 device colours)

and compares the result with the official BMP, pixel by pixel and byte by byte.
The recipe that comes out at 0.0000% is the one to implement.

    tools/find_exact_recipe.py -i foto -b bmporiginali
    tools/find_exact_recipe.py -i foto -b bmporiginali -n 30

  -i  folder with the images that were fed to `convert`
  -b  folder with the BMPs `convert` produced from them
  -n  how many pairs to test (default 12; a perfect recipe is then confirmed
      on all of them)

Needs Pillow (the same one your converter uses). numpy is used if present.
Paste the whole output back: it contains everything needed to write the
converter - which recipe wins, by how much the others lose, and the exact
versions involved.
"""

import argparse
import hashlib
import io
import os
import platform
import sys

try:
    from PIL import Image, ImageOps, features
    import PIL
except ImportError:
    sys.exit("Pillow is required: pip install 'pillow==10.0.0'")

try:
    import numpy as np
except ImportError:
    np = None

DEVICE_PALETTE = (0, 0, 0, 255, 255, 255, 0, 255, 0, 0, 0, 255,
                  255, 0, 0, 255, 255, 0, 255, 128, 0)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
STRIP = ("_scale_output", "_cut_output", "_output", "_pp")


# ----------------------------------------------------------------- BMP reader
def read_bmp(path):
    """Return (width, height, RGB bytes) for a 24-bit BMP, row 0 first."""
    import struct
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"BM":
        raise ValueError("not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    comp = struct.unpack_from("<I", data, 30)[0]
    if bpp != 24 or comp != 0:
        raise ValueError(f"unsupported BMP ({bpp}bpp, compression {comp})")
    bottom_up = height > 0
    height = abs(height)
    stride = (width * 3 + 3) & ~3
    need = offset + stride * height
    if len(data) < need:
        raise ValueError("truncated BMP")

    if np is not None:
        rows = np.frombuffer(data[offset:need], dtype=np.uint8).reshape(height, stride)
        rows = rows[:, :width * 3].reshape(height, width, 3)[:, :, ::-1]  # BGR -> RGB
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


def pixel_report(got, ref, width):
    """Compare two RGB byte strings pixel by pixel.

    Returns (differing pixels, total pixels, largest channel delta,
    (x, y) of the first difference).
    """
    total = len(ref) // 3
    if got == ref:
        return 0, total, 0, None
    if np is not None:
        a = np.frombuffer(got, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
        b = np.frombuffer(ref, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
        bad = (a != b).any(axis=1)
        idx = int(np.argmax(bad))
        return (int(bad.sum()), total, int(np.abs(a - b).max()),
                (idx % width, idx // width))
    nd = maxd = 0
    first = None
    for i in range(total):
        pa, pb = got[i * 3:i * 3 + 3], ref[i * 3:i * 3 + 3]
        if pa != pb:
            nd += 1
            maxd = max(maxd, max(abs(x - y) for x, y in zip(pa, pb)))
            if first is None:
                first = (i % width, i // width)
    return nd, total, maxd, first


# ------------------------------------------------------------ geometry stages
class _Variants(dict):
    """A dict that ignores anything but the variant asked for, if one is."""

    def __init__(self, only=None):
        super().__init__()
        self.only = only

    def __setitem__(self, key, value):
        if self.only is None or key == self.only:
            super().__setitem__(key, value)

def _paste_scaled(im, tw, th, resample):
    """What the official tool does in --mode scale."""
    w, h = im.size
    ratio = max(tw / w, th / h)
    rw, rh = int(w * ratio), int(h * ratio)
    scaled = im.resize((rw, rh)) if resample is None else im.resize((rw, rh), resample)
    frame = Image.new("RGB", (tw, th), (255, 255, 255))
    frame.paste(scaled, ((tw - rw) // 2, (th - rh) // 2))
    return frame


def _cut(im, tw, th):
    """What the official tool does in --mode cut."""
    w, h = im.size
    return ImageOps.pad(im.crop((0, 0, w, h)), (tw, th),
                        color=(255, 255, 255), centering=(0.5, 0.5)).convert("RGB")


def geometry_variants(path, tw, th, only=None):
    """name -> 800x480 RGB image, for every plausible way of framing the photo.

    `only` builds just that one variant (used to confirm a winner quickly).
    """
    out = _Variants(only)
    base = Image.open(path)
    base.load()


    out["scale (official)"] = _paste_scaled(base, tw, th, None)
    for fname in ("NEAREST", "BILINEAR", "BICUBIC", "LANCZOS", "BOX", "HAMMING"):
        filt = getattr(Image.Resampling, fname, None)
        if filt is not None:
            out[f"scale resample={fname}"] = _paste_scaled(base, tw, th, filt)
    out["cut"] = _cut(base, tw, th)
    out["stretch to 800x480"] = base.convert("RGB").resize((tw, th))
    out["ImageOps.fit"] = ImageOps.fit(base.convert("RGB"), (tw, th))
    out["RGB first, then scale"] = _paste_scaled(base.convert("RGB"), tw, th, None)

    try:
        rotated = ImageOps.exif_transpose(base)
        if rotated.size != base.size or rotated.tobytes() != base.tobytes():
            out["exif rotated, then scale"] = _paste_scaled(rotated, tw, th, None)
    except Exception:
        pass

    if base.info.get("icc_profile"):
        try:
            from PIL import ImageCms
            src = ImageCms.ImageCmsProfile(io.BytesIO(base.info["icc_profile"]))
            srgb = ImageCms.createProfile("sRGB")
            conv = ImageCms.profileToProfile(base.convert("RGB"), src, srgb, outputMode="RGB")
            out["ICC to sRGB, then scale"] = _paste_scaled(conv, tw, th, None)
        except Exception:
            pass
    return out


# -------------------------------------------------------- quantisation stages
def _pal_image(entries):
    pal = Image.new("P", (1, 1))
    pal.putpalette(entries)
    return pal


PAL256 = _pal_image(DEVICE_PALETTE + (0, 0, 0) * 249)   # what the official tool builds
PAL7 = _pal_image(DEVICE_PALETTE)                       # the "obvious" 7-entry palette


def quantise_variants(frame, only=None):
    """name -> final RGB image, for every plausible way of reaching 7 colours."""
    out = _Variants(only)
    FS = Image.Dither.FLOYDSTEINBERG
    NONE = Image.Dither.NONE
    out["quantize FS, 256-entry palette (official)"] = frame.quantize(dither=FS, palette=PAL256).convert("RGB")
    out["quantize FS, 7-entry palette"] = frame.quantize(dither=FS, palette=PAL7).convert("RGB")
    out["quantize NONE, 256-entry palette"] = frame.quantize(dither=NONE, palette=PAL256).convert("RGB")
    out["quantize NONE, 7-entry palette"] = frame.quantize(dither=NONE, palette=PAL7).convert("RGB")
    try:  # the low-level call quantize() itself makes - proves they are the same path
        low = frame._new(frame.im.convert("P", FS, PAL256.im)).convert("RGB")
        out["im.convert('P', FS, palette) low level"] = low
    except Exception:
        pass
    try:
        out["convert('P', ADAPTIVE, colors=7)"] = frame.convert(
            "P", dither=FS, palette=Image.Palette.ADAPTIVE, colors=7).convert("RGB")
    except Exception:
        pass
    return out


# --------------------------------------------------------------------- pairing
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


# ------------------------------------------------------------------ comparison
def diff_stats(a, b):
    """(differing pixels, total pixels) between two RGB byte strings."""
    if a == b:
        return 0, len(a) // 3
    if np is not None:
        x = np.frombuffer(a, dtype=np.uint8).reshape(-1, 3)
        y = np.frombuffer(b, dtype=np.uint8).reshape(-1, 3)
        return int((x != y).any(axis=1).sum()), len(x)
    n = len(a) // 3
    return sum(1 for i in range(n) if a[i * 3:i * 3 + 3] != b[i * 3:i * 3 + 3]), n


def bmp_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--images", required=True, help="folder with the source images")
    ap.add_argument("-b", "--bmp", required=True, help="folder with the official BMPs")
    ap.add_argument("-n", "--limit", type=int, default=12, help="pairs to test (default 12)")
    ap.add_argument("--confirm", type=int, default=40,
                    help="pairs used to confirm a perfect recipe (default 40)")
    args = ap.parse_args()

    print("=" * 78)
    print("find_exact_recipe - which recipe reproduces the official converter?")
    print("=" * 78)
    print(f"Python  : {sys.version.split()[0]} on {platform.system()} {platform.machine()}")
    print(f"Pillow  : {PIL.__version__}")
    try:
        print(f"libjpeg : {features.version('jpg')}   (turbo {features.version('jpg_turbo')})")
    except Exception:
        pass
    print(f"numpy   : {np.__version__ if np is not None else 'not installed (slower)'}")

    src_idx = index_folder(args.images, IMAGE_EXT)
    ref_idx = index_folder(args.bmp, (".bmp",))
    keys = sorted(set(src_idx) & set(ref_idx))
    print(f"\nSources : {len(src_idx)} in {args.images}")
    print(f"Official: {len(ref_idx)} in {args.bmp}")
    print(f"Paired  : {len(keys)}")
    if not keys:
        sys.exit("\nNo pair found: the file names of the two folders do not match.")

    test_keys = keys[:max(1, args.limit)]
    print(f"Testing the recipes on {len(test_keys)} pair(s)\n")

    scores = {}          # recipe -> [differing pixels, total pixels, files identical]
    geometry_note = {}
    for n, k in enumerate(test_keys, 1):
        src, ref = src_idx[k], ref_idx[k]
        try:
            tw, th, ref_rgb = read_bmp(ref)
        except ValueError as exc:
            print(f"[{n}] {os.path.basename(ref)}: {exc}")
            continue
        with Image.open(src) as probe:
            ssize, smode = probe.size, probe.mode
        print(f"[{n}/{len(test_keys)}] {os.path.basename(src)}  {ssize[0]}x{ssize[1]} {smode}"
              f"  ->  {tw}x{th}")
        geometry_note[k] = (ssize, (tw, th))

        try:
            geoms = geometry_variants(src, tw, th)
        except Exception as exc:
            print(f"      cannot open: {exc}")
            continue

        # Several framings collapse to the same pixels (e.g. when the source is
        # already 800x480); quantise each distinct frame only once.
        seen = {}
        for gname, frame in geoms.items():
            h = hashlib.sha256(frame.tobytes()).hexdigest()
            if h in seen:
                seen[h][1].append(gname)
            else:
                seen[h] = (frame, [gname])

        for frame, gnames in seen.values():
            for qname, result in quantise_variants(frame).items():
                got = result.tobytes()
                nd, tot = diff_stats(got, ref_rgb)
                same_file = (nd == 0 and
                             hashlib.sha256(bmp_bytes(result)).hexdigest() ==
                             hashlib.sha256(open(ref, "rb").read()).hexdigest())
                for gname in gnames:
                    s = scores.setdefault(f"{gname}  +  {qname}", [0, 0, 0])
                    s[0] += nd
                    s[1] += tot
                    s[2] += 1 if same_file else 0

    if not scores:
        sys.exit("nothing could be compared")

    print("\n" + "=" * 78)
    print("RESULTS - percentage of pixels that differ from the official BMP")
    print("=" * 78)
    OFFICIAL = "scale (official)  +  quantize FS, 256-entry palette (official)"
    # ties go to the recipe the official tool actually uses
    ranked = sorted(scores.items(),
                    key=lambda kv: (kv[1][0] / max(1, kv[1][1]),
                                    0 if kv[0] == OFFICIAL else 1, kv[0]))
    tested = len(test_keys)
    perfect_rows = [(n, v) for n, v in ranked if v[0] == 0]
    other_rows = [(n, v) for n, v in ranked if v[0] != 0]

    if perfect_rows:
        print(f"IDENTICAL: {len(perfect_rows)} recipe(s) reproduce the official BMP exactly")
        for name, (_, _, files) in perfect_rows[:8]:
            print(f"   0.0000%  {name}   [{files}/{tested} files byte-identical]")
        if len(perfect_rows) > 8:
            print(f"   ... and {len(perfect_rows) - 8} more, all identical")
        same_size = all(s == t for s, t in geometry_note.values())
        if len(perfect_rows) > 1 and same_size:
            print("\n   Several recipes tie because the sources are already the target size:\n"
                  "   the framing step does nothing, so every framing gives the same pixels.\n"
                  "   What is being proved here is the quantisation step.")
        print()
    else:
        print("No recipe is identical. Best attempts:")
    for name, (nd, tot, _) in other_rows[:10]:
        print(f"{100.0 * nd / max(1, tot):9.4f}%  {name}")
    if len(other_rows) > 10:
        print(f"... {len(other_rows) - 10} more recipes, worse than these")

    perfect = [n for n, (nd, _, _) in ranked if nd == 0]
    print("\n" + "=" * 78)
    if perfect:
        best = perfect[0]
        print(f"WINNER: {best}")
        confirm_keys = keys[:max(len(test_keys), args.confirm)]
        print(f"Confirming on {len(confirm_keys)} pair(s)...")
        gname, qname = best.split("  +  ")
        bad = 0
        for k in confirm_keys:
            tw, th, ref_rgb = read_bmp(ref_idx[k])
            frame = geometry_variants(src_idx[k], tw, th, only=gname).get(gname)
            if frame is None:
                bad += 1
                continue
            result = quantise_variants(frame, only=qname)[qname]
            if hashlib.sha256(bmp_bytes(result)).hexdigest() != \
               hashlib.sha256(open(ref_idx[k], "rb").read()).hexdigest():
                bad += 1
                print(f"  MISMATCH on {os.path.basename(src_idx[k])}")
        if bad == 0:
            print(f"CONFIRMED: {len(confirm_keys)}/{len(confirm_keys)} files byte-identical.")
            print("\nThe converter to write is exactly this:\n")
            print(recipe_code(gname, qname))
        else:
            print(f"{bad}/{len(confirm_keys)} files do NOT match: the recipe works on some "
                  f"images only - paste this output back.")
    else:
        nd, tot, _ = ranked[0][1]
        print(f"No recipe reproduces the official BMPs. Closest: {ranked[0][0]} "
              f"({100.0 * nd / max(1, tot):.4f}% of pixels)")
        odd = [k for k, (s, t) in geometry_note.items() if s != t and s != (t[0], t[1])]
        if odd:
            print("\nNote: the sources are not 800x480, so the official tool did its own\n"
                  "scaling. If these photos went through the cropper first, the folder\n"
                  "must contain the *_pp.jpg files it exported, not the originals:\n"
                  "the crop you chose by hand cannot be guessed from the original photo.")
        print("\nPaste this whole output back to get the next set of hypotheses.")
    print("=" * 78)
    return 0


def recipe_code(gname, qname):
    scale = '''    w, h = im.size
    ratio = max(TW / w, TH / h)
    rw, rh = int(w * ratio), int(h * ratio)
    frame = Image.new("RGB", (TW, TH), (255, 255, 255))
    frame.paste(im.resize((rw, rh){filt}), ((TW - rw) // 2, (TH - rh) // 2))'''
    geom = {
        "scale (official)": scale.format(filt=""),
        "cut": '''    frame = ImageOps.pad(im.crop((0, 0) + im.size), (TW, TH),
                         color=(255, 255, 255), centering=(0.5, 0.5)).convert("RGB")''',
        "stretch to 800x480": '    frame = im.convert("RGB").resize((TW, TH))',
        "ImageOps.fit": '    frame = ImageOps.fit(im.convert("RGB"), (TW, TH))',
        "RGB first, then scale": '    im = im.convert("RGB")\n' + scale.format(filt=""),
        "exif rotated, then scale": '    im = ImageOps.exif_transpose(im)\n' + scale.format(filt=""),
    }
    for f in ("NEAREST", "BILINEAR", "BICUBIC", "LANCZOS", "BOX", "HAMMING"):
        geom[f"scale resample={f}"] = scale.format(filt=f", Image.Resampling.{f}")

    pal = '''    pal = Image.new("P", (1, 1))
    pal.putpalette((0,0,0, 255,255,255, 0,255,0, 0,0,255,
                    255,0,0, 255,255,0, 255,128,0) + (0,0,0) * 249)'''
    pal7 = '''    pal = Image.new("P", (1, 1))
    pal.putpalette((0,0,0, 255,255,255, 0,255,0, 0,0,255,
                    255,0,0, 255,255,0, 255,128,0))'''
    quant = {
        "quantize FS, 256-entry palette (official)":
            pal + '\n    out = frame.quantize(dither=Image.Dither.FLOYDSTEINBERG, palette=pal).convert("RGB")',
        "quantize FS, 7-entry palette":
            pal7 + '\n    out = frame.quantize(dither=Image.Dither.FLOYDSTEINBERG, palette=pal).convert("RGB")',
        "quantize NONE, 256-entry palette":
            pal + '\n    out = frame.quantize(dither=Image.Dither.NONE, palette=pal).convert("RGB")',
        "quantize NONE, 7-entry palette":
            pal7 + '\n    out = frame.quantize(dither=Image.Dither.NONE, palette=pal).convert("RGB")',
        "im.convert('P', FS, palette) low level":
            pal + '\n    out = frame._new(frame.im.convert("P", Image.Dither.FLOYDSTEINBERG, pal.im)).convert("RGB")',
        "convert('P', ADAPTIVE, colors=7)":
            '    out = frame.convert("P", dither=Image.Dither.FLOYDSTEINBERG,\n'
            '                       palette=Image.Palette.ADAPTIVE, colors=7).convert("RGB")',
    }
    g = geom.get(gname)
    q = quant.get(qname)
    if g is None or q is None:
        return f"# winning recipe: {gname}  +  {qname}\n# (paste this output back for the code)"
    return f'''from PIL import Image, ImageOps   # pillow=={PIL.__version__}

def convert_one(path, out_path):
    im = Image.open(path)
    TW, TH = (800, 480) if im.size[0] > im.size[1] else (480, 800)
{g}
{q}
    out.save(out_path)'''


if __name__ == "__main__":
    sys.exit(main())
