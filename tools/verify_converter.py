#!/usr/bin/env python3
"""
verify_converter.py - prove that an alternative converter produces exactly the
same BMPs as Waveshare's official `convert` binary.

It runs both converters on the same images, in two separate sandbox folders, and
compares the results byte by byte (SHA-256). When two outputs differ it does not
just say "different": it opens both BMPs and reports what kind of difference it
is, which is what tells you where the bug lives.

    same size?            no  -> the geometry/resize stage differs
    colours outside the   yes -> the candidate is not using the 7-colour palette
      7-colour palette?
    few pixels, delta 1?      -> the JPEG decoding differs (rounding)
    many pixels, all of       -> the dithering differs (the usual case for a
      them palette colours       from-scratch Floyd-Steinberg rewrite)

Two ways to use it:

A) you already have the two sets of BMPs, in two folders:

    tools/verify_converter.py --ref-dir bmporiginali --cand-dir bmp

   Files are paired by name, ignoring the converter's suffixes
   (`foto_pp_scale_output.bmp` pairs with `foto_pp.bmp`, `foto_pp_mio.bmp`,
   ...). Anything that cannot be paired is listed, never silently skipped.

B) you have the source images and want the harness to run both converters
   itself, each in its own sandbox:

    tools/verify_converter.py -i FOLDER --cand-cmd './mio_script.sh {in}'
    tools/verify_converter.py -i FOLDER --cand-cmd 'python3 mio.py {in} -o {out}'
    tools/verify_converter.py -i FOLDER -c ./convert --ref-args '--mode cut' \\
                              --cand-cmd '...' -n 50 -j 8 --csv report.csv

  {in}  is replaced by the input image (inside the candidate sandbox)
  {out} is optional: if present, the candidate must write exactly that file;
        if absent, whatever new file the command creates is taken as the output.

Only the Python standard library is required. numpy is used if present (faster
diff statistics), Pillow only for the optional --diff-maps.
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

PALETTE = {
    (0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
    (255, 0, 0), (255, 255, 0), (255, 128, 0),
}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


# --------------------------------------------------------------------- BMP I/O
def read_bmp(path):
    """Minimal reader for the 24-bit uncompressed BMPs both converters write.

    Returns (width, height, rows) with rows top-down, each row b'RGBRGB...'.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    hdr = struct.unpack_from("<I", data, 14)[0]
    if hdr < 40:
        raise ValueError(f"unsupported BMP header ({hdr} bytes)")
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if bpp != 24 or compression != 0:
        raise ValueError(f"unsupported BMP: {bpp} bpp, compression {compression}")
    bottom_up = height > 0
    height = abs(height)
    stride = (width * 3 + 3) & ~3
    rows = []
    for y in range(height):
        src = y if not bottom_up else height - 1 - y
        start = offset + src * stride
        line = data[start:start + width * 3]
        if len(line) < width * 3:
            raise ValueError("truncated BMP")
        # BMP stores BGR; flip to RGB
        rows.append(b"".join(line[x * 3 + 2:x * 3 + 3] +
                             line[x * 3 + 1:x * 3 + 2] +
                             line[x * 3:x * 3 + 1] for x in range(width)))
    return width, height, rows


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ comparison
def compare_bmp(ref_path, cand_path):
    """Return a dict describing how the two BMPs differ."""
    try:
        rw, rh, rrows = read_bmp(ref_path)
        cw, ch, crows = read_bmp(cand_path)
    except ValueError as exc:
        return {"kind": "unreadable", "detail": str(exc)}

    if (rw, rh) != (cw, ch):
        return {"kind": "size", "detail": f"reference {rw}x{rh} vs candidate {cw}x{ch}"}

    ndiff = 0
    maxdelta = 0
    first = None
    off_palette = 0
    for y, (a, b) in enumerate(zip(rrows, crows)):
        if a == b:
            continue
        for x in range(rw):
            pa = a[x * 3:x * 3 + 3]
            pb = b[x * 3:x * 3 + 3]
            if pa != pb:
                ndiff += 1
                maxdelta = max(maxdelta, max(abs(i - j) for i, j in zip(pa, pb)))
                if first is None:
                    first = (x, y, tuple(pa), tuple(pb))
            if tuple(pb) not in PALETTE:
                off_palette += 1

    total = rw * rh
    pct = 100.0 * ndiff / total
    if off_palette:
        kind = "palette"
        detail = (f"{off_palette} candidate pixels are not one of the 7 device colours: "
                  f"the palette or the final P->RGB conversion differs")
    elif pct < 0.5:
        kind = "sparse"
        detail = (f"{ndiff} pixels ({pct:.3f}%), all device colours: an upstream rounding "
                  f"difference (decoding/resize) that the dithering then spreads a little")
    else:
        kind = "dither"
        detail = (f"{ndiff} pixels ({pct:.2f}%), all device colours: the dithering or the "
                  f"nearest-colour search differs (or the decoded input already differed)")
    return {"kind": kind, "detail": detail, "ndiff": ndiff, "total": total,
            "maxdelta": maxdelta, "first": first}


# ------------------------------------------------------------------- pairing
STRIP_SUFFIXES = ("_scale_output", "_cut_output", "_output")


def pair_key(filename, extra_strip=()):
    """Name used to pair a reference BMP with a candidate BMP.

    Both converters usually decorate the name of the source image, each in its
    own way, so the common part is what is left after removing the suffixes.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    changed = True
    while changed:
        changed = False
        for suf in tuple(extra_strip) + STRIP_SUFFIXES:
            if suf and stem.lower().endswith(suf.lower()):
                stem = stem[:-len(suf)]
                changed = True
    return stem.lower()


def index_bmps(folder, extra_strip, recursive):
    """Map pair_key -> path for every BMP in a folder. Returns (index, dupes)."""
    index, dupes = {}, []
    for root, dirs, names in os.walk(folder):
        for n in sorted(names):
            if not n.lower().endswith(".bmp") or n.startswith("._"):
                continue
            key = pair_key(n, extra_strip)
            path = os.path.join(root, n)
            if key in index:
                dupes.append((key, index[key], path))
            else:
                index[key] = path
        if not recursive:
            break
    return index, dupes


def autodetect_strip(cand_keys, ref_keys):
    """Guess the suffix your converter appends, when nothing pairs as-is.

    Returns (suffix, how_many_files_it_pairs) or ("", 0).
    """
    seeds = list(cand_keys)[:50]
    tried = set()
    for k in seeds:
        for length in range(1, min(40, len(k))):
            tried.add(k[-length:])
    best, best_n = "", 0
    for suf in tried:
        n = sum(1 for k in cand_keys if k.endswith(suf) and k[:-len(suf)] in ref_keys)
        if n > best_n or (n == best_n and n and len(suf) < len(best)):
            best, best_n = suf, n
    return best, best_n


def compare_existing(job):
    """Compare one already-converted pair of BMPs."""
    key, ref_path, cand_path, keep_dir = job
    result = {"file": os.path.basename(cand_path), "status": "", "detail": "",
              "ref_sha": sha256(ref_path), "cand_sha": sha256(cand_path)}
    if result["ref_sha"] == result["cand_sha"]:
        result["status"] = "identical"
        return result
    info = compare_bmp(ref_path, cand_path)
    result["status"] = "DIFFERENT/" + info["kind"]
    result["detail"] = info["detail"]
    if info.get("first"):
        x, y, pa, pb = info["first"]
        result["detail"] += f"; first at ({x},{y}) ref={pa} cand={pb}"
    if keep_dir:
        dest = os.path.join(keep_dir, key)
        os.makedirs(dest, exist_ok=True)
        shutil.copy2(ref_path, os.path.join(dest, "reference.bmp"))
        shutil.copy2(cand_path, os.path.join(dest, "candidate.bmp"))
        result["detail"] += f"; saved in {dest}"
    return result


# -------------------------------------------------------------------- runners
def _new_output(workdir, before, source_name):
    after = set(os.listdir(workdir))
    new = [f for f in after - before if f != source_name]
    if not new:
        return None, "the converter produced no new file"
    bmps = [f for f in new if f.lower().endswith(".bmp")]
    pick = bmps or new
    if len(pick) > 1:
        return None, f"ambiguous output, {len(pick)} new files: {sorted(pick)}"
    return os.path.join(workdir, pick[0]), None


def _run(cmd, workdir, shell):
    proc = subprocess.run(cmd, cwd=workdir, shell=shell,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def run_pair(job):
    (src, ref_bin, ref_args, cand_tpl, keep_dir) = job
    name = os.path.basename(src)
    result = {"file": name, "status": "", "detail": "", "ref_sha": "", "cand_sha": ""}

    tmp = tempfile.mkdtemp(prefix="ppverify.")
    try:
        # --- reference -------------------------------------------------------
        refdir = os.path.join(tmp, "ref")
        os.mkdir(refdir)
        shutil.copy2(src, os.path.join(refdir, name))
        before = {name}
        rc, log = _run([os.path.abspath(ref_bin), name] + ref_args, refdir, False)
        ref_out, err = _new_output(refdir, before, name)
        if ref_out is None:
            result.update(status="ref-error", detail=f"{err} (exit {rc}) {log}")
            return result

        # --- candidate -------------------------------------------------------
        canddir = os.path.join(tmp, "cand")
        os.mkdir(canddir)
        shutil.copy2(src, os.path.join(canddir, name))
        explicit = "{out}" in cand_tpl
        out_name = os.path.splitext(name)[0] + "_candidate.bmp"
        cmd = cand_tpl.replace("{in}", _q(name)).replace("{out}", _q(out_name))
        rc, log = _run(cmd, canddir, True)
        if explicit:
            cand_out = os.path.join(canddir, out_name)
            if not os.path.isfile(cand_out):
                result.update(status="cand-error",
                              detail=f"{out_name} not created (exit {rc}) {log}")
                return result
        else:
            cand_out, err = _new_output(canddir, {name}, name)
            if cand_out is None:
                result.update(status="cand-error", detail=f"{err} (exit {rc}) {log}")
                return result

        # --- compare ---------------------------------------------------------
        result["ref_sha"] = sha256(ref_out)
        result["cand_sha"] = sha256(cand_out)
        if result["ref_sha"] == result["cand_sha"]:
            result["status"] = "identical"
            return result

        info = compare_bmp(ref_out, cand_out)
        result["status"] = "DIFFERENT/" + info["kind"]
        result["detail"] = info["detail"]
        if info.get("first"):
            x, y, pa, pb = info["first"]
            result["detail"] += f"; first at ({x},{y}) ref={pa} cand={pb}"
        if keep_dir:
            dest = os.path.join(keep_dir, os.path.splitext(name)[0])
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(ref_out, os.path.join(dest, "reference.bmp"))
            shutil.copy2(cand_out, os.path.join(dest, "candidate.bmp"))
            result["detail"] += f"; saved in {dest}"
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


def check_determinism(src, ref_bin, ref_args):
    """Run the reference twice on the same image: the reference must be stable."""
    shas = []
    for _ in range(2):
        tmp = tempfile.mkdtemp(prefix="ppdet.")
        try:
            name = os.path.basename(src)
            shutil.copy2(src, os.path.join(tmp, name))
            _run([os.path.abspath(ref_bin), name] + ref_args, tmp, False)
            out, err = _new_output(tmp, {name}, name)
            if out is None:
                return None, err
            shas.append(sha256(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return shas[0] == shas[1], shas[0]


# ------------------------------------------------------- mode A: two folders
def compare_dirs(args):
    for d in (args.ref_dir, args.cand_dir):
        if not os.path.isdir(d):
            sys.exit(f"folder not found: {d}")

    ref_index, ref_dupes = index_bmps(args.ref_dir, args.strip, args.recursive)
    cand_index, cand_dupes = index_bmps(args.cand_dir, args.strip, args.recursive)

    for label, dupes in (("reference", ref_dupes), ("candidate", cand_dupes)):
        for key, first, second in dupes:
            print(f"WARNING: two {label} files pair to the same name '{key}':\n"
                  f"         {first}\n         {second}\n"
                  f"         only the first is compared; use --strip to disambiguate")

    if not set(ref_index) & set(cand_index):
        # The two converters decorate the names differently: work out how.
        suf, n = autodetect_strip(set(cand_index), set(ref_index))
        if n:
            print(f"Names do not match as-is: detected the candidate suffix '{suf}' "
                  f"({n} files pair with it). Pass --strip {suf} to make it explicit.\n")
            cand_index = {(k[:-len(suf)] if k.endswith(suf) and k[:-len(suf)] in ref_index
                           else k): v for k, v in cand_index.items()}
        else:
            suf, n = autodetect_strip(set(ref_index), set(cand_index))
            if n:
                print(f"Names do not match as-is: detected the reference suffix '{suf}' "
                      f"({n} files pair with it).\n")
                ref_index = {(k[:-len(suf)] if k.endswith(suf) and k[:-len(suf)] in cand_index
                              else k): v for k, v in ref_index.items()}

    keys = sorted(set(ref_index) & set(cand_index))
    only_ref = sorted(set(ref_index) - set(cand_index))
    only_cand = sorted(set(cand_index) - set(ref_index))
    if args.limit:
        keys = keys[:args.limit]

    print(f"Reference : {args.ref_dir}  ({len(ref_index)} BMP)")
    print(f"Candidate : {args.cand_dir}  ({len(cand_index)} BMP)")
    print(f"Pairs     : {len(keys)}  (jobs: {args.jobs})\n")
    if not keys:
        sys.exit("no file could be paired between the two folders: check the names, "
                 "or add --strip SUFFIX for your converter's own suffix")

    keep = os.path.abspath(args.keep) if args.keep else None
    if keep:
        os.makedirs(keep, exist_ok=True)

    jobs = [(k, ref_index[k], cand_index[k], keep) for k in keys]
    results, counters = [], {}
    width = len(str(len(keys)))
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, res in enumerate(pool.map(compare_existing, jobs), 1):
            results.append(res)
            counters[res["status"]] = counters.get(res["status"], 0) + 1
            flag = "ok  " if res["status"] == "identical" else "DIFF"
            line = f"[{i:{width}d}/{len(keys)}] {flag} {res['file']}"
            if res["status"] != "identical":
                line += f"\n           -> {res['status']}: {res['detail']}"
            print(line, flush=True)

    print("\n" + "-" * 70)
    for status, n in sorted(counters.items()):
        print(f"{status:24s} {n}")
    if only_ref:
        print(f"{'only in reference':24s} {len(only_ref)}")
    if only_cand:
        print(f"{'only in candidate':24s} {len(only_cand)}")
    print("-" * 70)

    if only_ref or only_cand:
        print("\nNot compared, present on one side only "
              f"({len(only_ref) + len(only_cand)} file(s)):")
        for label, names, index in (("reference", only_ref, ref_index),
                                    ("candidate", only_cand, cand_index)):
            for k in names[:10]:
                print(f"  only in {label}: {os.path.basename(index[k])}")
            if len(names) > 10:
                print(f"  ... and {len(names) - 10} more only in {label}"
                      f"{' (full list in the CSV)' if args.csv else ''}")
        for label, names, index in (("only-in-reference", only_ref, ref_index),
                                    ("only-in-candidate", only_cand, cand_index)):
            for k in names:
                results.append({"file": os.path.basename(index[k]), "status": label,
                                "detail": "not compared: no matching file in the other folder",
                                "ref_sha": "", "cand_sha": ""})

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=["file", "status", "detail", "ref_sha", "cand_sha"])
            w.writeheader()
            w.writerows(results)
        print(f"report written to {args.csv}")

    identical = counters.get("identical", 0)
    unpaired = len(only_ref) + len(only_cand)
    if identical == len(keys) and not unpaired:
        print(f"\nPASS - {identical}/{len(keys)} BMPs are byte-identical to the official "
              f"converter's ones.\nEvery file you would copy to the SD card is the same "
              f"file, bit for bit.")
        return 0
    if identical == len(keys):
        print(f"\nPASS with warnings - all {identical} paired BMPs are byte-identical to the "
              f"official converter's ones.\n{unpaired} file(s) exist on one side only and "
              f"were not compared (exit code 2).")
        return 2
    print(f"\nFAIL - {len(keys) - identical}/{len(keys)} BMPs differ from the official "
          f"converter's ones.")
    return 1


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description="Compare an alternative converter against Waveshare's official one.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--ref-dir", help="folder with the BMPs produced by the official converter")
    ap.add_argument("--cand-dir", help="folder with the BMPs produced by your own converter")
    ap.add_argument("--strip", action="append", default=[], metavar="SUFFIX",
                    help="extra name suffix to ignore when pairing files (repeatable), "
                         "e.g. --strip _mio")
    ap.add_argument("-i", "--images", help="folder with the source images (run-both mode)")
    ap.add_argument("-c", "--convert", default="./convert", help="official convert binary")
    ap.add_argument("--ref-args", default="", help="extra args for the reference, e.g. '--mode cut'")
    ap.add_argument("--cand-cmd",
                    help="candidate command; {in} = input file, {out} = optional output path")
    ap.add_argument("-n", "--limit", type=int, default=0, help="test only the first N files")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--csv", help="write a per-file report to this CSV")
    ap.add_argument("--keep", metavar="DIR", help="save the BMP pairs that differ into DIR")
    ap.add_argument("-r", "--recursive", action="store_true")
    ap.add_argument("--lossless-probe", action="store_true",
                    help="first re-encode every source image to PNG (lossless, decoded "
                         "identically by any decoder) and compare on those: if the outputs "
                         "match here but not on the original JPEGs, the JPEG decoder is the "
                         "culprit, not the conversion algorithm. Needs Pillow.")
    args = ap.parse_args()

    if args.ref_dir or args.cand_dir:
        if not (args.ref_dir and args.cand_dir):
            sys.exit("--ref-dir and --cand-dir must be used together")
        return compare_dirs(args)
    if not args.images or not args.cand_cmd:
        sys.exit("either --ref-dir/--cand-dir, or -i IMAGES with --cand-cmd. See -h")

    if not os.path.isfile(args.convert):
        sys.exit(f"reference converter not found: {args.convert}")
    if not os.access(args.convert, os.X_OK):
        sys.exit(f"reference converter is not executable: {args.convert}")

    files = []
    for root, dirs, names in os.walk(args.images):
        for n in sorted(names):
            low = n.lower()
            if low.endswith(IMAGE_EXT) and not low.endswith(("_scale_output.bmp", "_cut_output.bmp")) \
                    and not n.startswith("._"):
                files.append(os.path.join(root, n))
        if not args.recursive:
            break
    files.sort()
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no image found in {args.images}")

    probe_dir = None
    if args.lossless_probe:
        try:
            from PIL import Image
        except ImportError:
            sys.exit("--lossless-probe needs Pillow (pip install pillow) to re-encode the images")
        probe_dir = tempfile.mkdtemp(prefix="ppprobe.")
        converted = []
        for f in files:
            dst = os.path.join(probe_dir, os.path.splitext(os.path.basename(f))[0] + ".png")
            with Image.open(f) as im:
                im.convert("RGB").save(dst)
            converted.append(dst)
        files = converted
        print(f"Lossless probe: {len(files)} images re-encoded as PNG in {probe_dir}")

    ref_args = args.ref_args.split()
    keep = os.path.abspath(args.keep) if args.keep else None
    if keep:
        os.makedirs(keep, exist_ok=True)

    print(f"Reference : {args.convert} {' '.join(ref_args)}")
    print(f"Candidate : {args.cand_cmd}")
    print(f"Images    : {len(files)}  (jobs: {args.jobs})\n")

    stable, sha = check_determinism(files[0], args.convert, ref_args)
    if stable is None:
        sys.exit(f"the reference converter failed on {files[0]}: {sha}")
    if not stable:
        sys.exit("the reference converter is NOT deterministic: it gives different "
                 "results for the same input, so bit-exact comparison is meaningless.")
    print(f"Determinism check: the reference gives the same bytes twice (sha {sha[:16]}...)\n")

    jobs = [(f, args.convert, ref_args, args.cand_cmd, keep) for f in files]
    results = []
    counters = {}
    width = len(str(len(files)))
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, res in enumerate(pool.map(run_pair, jobs), 1):
            results.append(res)
            counters[res["status"]] = counters.get(res["status"], 0) + 1
            flag = "ok  " if res["status"] == "identical" else "DIFF"
            line = f"[{i:{width}d}/{len(files)}] {flag} {res['file']}"
            if res["status"] != "identical":
                line += f"\n           -> {res['status']}: {res['detail']}"
            print(line, flush=True)

    print("\n" + "-" * 70)
    identical = counters.get("identical", 0)
    for status, n in sorted(counters.items()):
        print(f"{status:24s} {n}")
    print("-" * 70)

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=["file", "status", "detail", "ref_sha", "cand_sha"])
            w.writeheader()
            w.writerows(results)
        print(f"report written to {args.csv}")

    if identical == len(files):
        print(f"\nPASS - {identical}/{len(files)} outputs are byte-identical to the official "
              f"converter.\nEvery BMP you would put on the SD card is the same file, bit for bit.")
        return 0
    print(f"\nFAIL - {len(files) - identical}/{len(files)} outputs differ from the official converter.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
