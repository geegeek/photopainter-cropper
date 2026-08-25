#!/usr/bin/env python3
"""
tune_recipe.py - converge on the recipe that reproduces the official converter
on EVERY photo, refining it as it goes.

It works the way you would by hand, but over the whole corpus:

  round 1   convert every photo with the current recipe and compare the result
            with the official BMP (SHA-256, so "match" means byte-identical)
  round 2   for each photo that did NOT match, search the whole space of
            recipes for the ones that reproduce that photo exactly, and keep
            only the recipes that also worked on every photo seen so far
  round 3   if the surviving recipe changed, start again on all photos

It stops when one recipe reproduces every photo, or when it can prove no single
recipe does - and then it tells you exactly which photos disagree and by how
much. Photos are never averaged away: a recipe is only kept if it is exact.

    tools/tune_recipe.py -i foto -b bmporiginali
    tools/tune_recipe.py -i foto -b bmporiginali -n 200 -j 8

  -i  folder with the images that were fed to `convert`
  -b  folder with the BMPs `convert` produced from them
  -n  test only the first N photos (default: all of them)
  -j  parallel workers (default: number of cores)

Needs Pillow and find_exact_recipe.py in the same folder.
"""

import argparse
import hashlib
import os
import struct
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from find_exact_recipe import (geometry_variants, quantise_variants, read_bmp,
                                   index_folder, diff_stats, bmp_bytes, recipe_code,
                                   pixel_report, IMAGE_EXT)
    import PIL
except ImportError as exc:
    sys.exit(f"find_exact_recipe.py must sit next to this script ({exc})")

OFFICIAL = ("scale (official)", "quantize FS, 256-entry palette (official)")


def build_one(src, tw, th, gname, qname):
    """Build this photo with one recipe, whatever version of the sibling file is
    installed (`only=` is an optimisation added later; fall back without it)."""
    try:
        frame = geometry_variants(src, tw, th, only=gname).get(gname)
    except TypeError:
        frame = geometry_variants(src, tw, th).get(gname)
    if frame is None:
        return None
    try:
        return quantise_variants(frame, only=qname)[qname]
    except TypeError:
        return quantise_variants(frame)[qname]


def bmp_target_size(path):
    """Width/height of a BMP without decoding its pixels."""
    with open(path, "rb") as fh:
        head = fh.read(30)
    w, h = struct.unpack_from("<ii", head, 18)
    return w, abs(h)


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------- round 1 test
def check_one(job):
    """Convert this photo and compare EVERY pixel with the official BMP.

    The file hash is checked too, so a difference in the BMP header cannot hide
    behind identical pixels (or the other way round).
    """
    key, src, ref, gname, qname = job
    try:
        tw, th, ref_rgb = read_bmp(ref)
        result = build_one(src, tw, th, gname, qname)
        if result is None:
            return key, False, 0, 0, 0, None, False, "recipe not applicable to this image"
        got = result.tobytes()
        if len(got) != len(ref_rgb):
            return (key, False, tw * th, tw * th, 255, (0, 0), False,
                    f"different size: {result.size} vs {(tw, th)}")
        nd, total, maxd, first = pixel_report(got, ref_rgb, tw)
        file_ok = hashlib.sha256(bmp_bytes(result)).hexdigest() == sha_file(ref)
        return key, (nd == 0 and file_ok), nd, total, maxd, first, file_ok, ""
    except Exception as exc:
        return key, False, 0, 0, 0, None, False, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------- round 2 full search
def search_one(job):
    """Every recipe that reproduces this photo exactly, plus the best near miss."""
    key, src, ref = job
    try:
        tw, th, ref_rgb = read_bmp(ref)
        ref_sha = sha_file(ref)
        exact, best = set(), (1.0, None)
        for gname, frame in geometry_variants(src, tw, th).items():
            for qname, result in quantise_variants(frame).items():
                nd, tot = diff_stats(result.tobytes(), ref_rgb)
                if nd == 0 and hashlib.sha256(bmp_bytes(result)).hexdigest() == ref_sha:
                    exact.add((gname, qname))
                ratio = nd / max(1, tot)
                if ratio < best[0]:
                    best = (ratio, (gname, qname))
        return key, exact, best, ""
    except Exception as exc:
        return key, set(), (1.0, None), f"{type(exc).__name__}: {exc}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--images", required=True)
    ap.add_argument("-b", "--bmp", required=True)
    ap.add_argument("-n", "--limit", type=int, default=0, help="test only the first N photos")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--csv", help="write the per-photo outcome here")
    args = ap.parse_args()

    src_idx = index_folder(args.images, IMAGE_EXT)
    ref_idx = index_folder(args.bmp, (".bmp",))
    keys = sorted(set(src_idx) & set(ref_idx))
    if args.limit:
        keys = keys[:args.limit]
    if not keys:
        sys.exit("no photo could be paired with an official BMP")

    print("=" * 78)
    print("tune_recipe - converging on the recipe that matches every photo")
    print("=" * 78)
    print(f"Pillow {PIL.__version__} | photos paired: {len(keys)} | workers: {args.jobs}")
    print(f"Starting recipe: {OFFICIAL[0]}  +  {OFFICIAL[1]}\n")

    probe = check_one((keys[0], src_idx[keys[0]], ref_idx[keys[0]], *OFFICIAL))
    _, p_ok, p_nd, p_total, p_maxd, p_first, p_file, p_err = probe
    print(f"Pre-flight on {os.path.basename(src_idx[keys[0]])}: ", end="")
    if p_err:
        print(f"FAILED -> {p_err}\n\nNothing was compared. Fix this before reading any "
              f"result below.")
        return 2
    print(f"{p_total - p_nd:,}/{p_total:,} pixels identical, "
          f"file {'identical' if p_file else 'DIFFERENT'}\n")

    recipe = OFFICIAL
    # Recipes still consistent with every photo checked so far. None = not yet
    # constrained (no photo has needed a search).
    alive = None
    outliers = {}
    results = {}
    history = []   # (recipe, matches, per-photo outcome) for every recipe tried

    for rnd in range(1, args.max_rounds + 1):
        jobs = [(k, src_idx[k], ref_idx[k], recipe[0], recipe[1]) for k in keys]
        failures, errors = [], {}
        done = px_seen = px_bad = 0
        detail = {}
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for key, ok, nd, total, maxd, first, file_ok, err in pool.map(
                    check_one, jobs, chunksize=8):
                done += 1
                px_seen += total
                px_bad += nd
                results[key] = "identical" if ok else "different"
                detail[key] = (nd, total, maxd, first, file_ok)
                if not ok:
                    failures.append(key)
                    if err:
                        errors[key] = err
                    if nd == 0 and not file_ok:
                        print(f"  {key}: every pixel matches but the BMP file differs "
                              f"(header/encoder), not the image data", flush=True)
                    elif nd and len(failures) <= 5:
                        x, y = first
                        print(f"  {key}: {nd:,}/{total:,} pixels differ "
                              f"(max channel delta {maxd}), first at ({x},{y})", flush=True)
                if done % 200 == 0 or done == len(jobs):
                    print(f"  round {rnd}: {done}/{len(jobs)} photos, "
                          f"{px_seen:,} pixels compared, {px_bad:,} different, "
                          f"{len(failures)} photo(s) not matching", flush=True)
        pixels_checked = (px_seen, px_bad)

        history.append((recipe, len(keys) - len(failures), dict(results), pixels_checked,
                        dict(detail)))

        if errors:
            common = {}
            for msg in errors.values():
                common[msg] = common.get(msg, 0) + 1
            top, count = max(common.items(), key=lambda kv: kv[1])
            print(f"\n  {len(errors)} photo(s) could not even be converted. Most common:")
            print(f"    {top}   ({count} photo(s))")
            if count == len(keys):
                print("\nThis is a systematic failure, not a pixel difference: every photo\n"
                      "raised the same error, so nothing was actually compared.\n"
                      "Most likely find_exact_recipe.py next to this script is an older\n"
                      "copy - use the two files from the same version.")
                return 2

        if not failures:
            print(f"\nRound {rnd}: every one of the {len(keys)} photos is byte-identical.")
            break

        print(f"\nRound {rnd}: {len(failures)} photo(s) do not match. Searching the whole "
              f"recipe space on {min(len(failures), 25)} of them...\n")
        search_jobs = [(k, src_idx[k], ref_idx[k]) for k in failures[:25]]
        changed = False
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for key, exact, best, err in pool.map(search_one, search_jobs):
                if err:
                    print(f"  {key}: {err}")
                    continue
                if not exact:
                    ratio, who = best
                    outliers[key] = (ratio, who)
                    print(f"  {key}: NO recipe reproduces it "
                          f"(best {100 * ratio:.4f}% different, {who[0]} + {who[1]})")
                    continue
                # keep only the recipes that also worked on everything so far
                alive = exact if alive is None else (alive & exact)
                if not alive:
                    print(f"  {key}: matched by {len(exact)} recipe(s), but none of them "
                          f"works on the photos already checked -> no single recipe fits all")
                    alive = set()
                    break
                print(f"  {key}: matched by {len(exact)} recipe(s); "
                      f"{len(alive)} still consistent with all photos")

        if alive:
            pick = OFFICIAL if OFFICIAL in alive else sorted(alive)[0]
            if pick != recipe:
                recipe = pick
                changed = True
                print(f"\n  -> refined recipe: {recipe[0]}  +  {recipe[1]}\n")
        if not changed:
            print("\nThe recipe cannot be refined further: the photos that fail are not "
                  "explained by any recipe in the search space.")
            break

    # ------------------------------------------------------------------ report
    # Report the best recipe tried, not merely the last one: a refinement driven
    # by one odd photo can score worse than where we started.
    if history:
        recipe, ok_n, results, pixels_checked, detail = max(history, key=lambda h: h[1])
        others = [(r, n) for r, n, _, _, _ in history if r != recipe and n]
    else:
        ok_n, others, pixels_checked, detail = 0, [], (0, 0), {}
    print("\n" + "=" * 78)
    px_seen, px_bad = pixels_checked
    print(f"RESULT: {ok_n}/{len(keys)} photos byte-identical "
          f"({100.0 * ok_n / len(keys):.4f}%)")
    if px_seen:
        print(f"Pixels: {px_seen - px_bad:,}/{px_seen:,} identical to the official BMP "
              f"({100.0 * (px_seen - px_bad) / px_seen:.6f}%), {px_bad:,} different")
    print(f"Recipe : {recipe[0]}  +  {recipe[1]}")
    if alive and ok_n == 0:
        print(f"\nINCONSISTENT: the per-photo search says {len(alive)} recipe(s) reproduce "
              f"these photos exactly,\nyet the check above matched none. The two stages "
              f"disagree, so this run proves nothing:\nre-run with matching versions of "
              f"tune_recipe.py and find_exact_recipe.py.")
    elif alive:
        print(f"({len(alive)} recipe(s) are consistent with every photo; this is the one the "
              f"official tool uses)")
    if others:
        print("\nThe corpus is not consistent: other recipes reproduce photos this one "
              "cannot.")
        for r, n in sorted(others, key=lambda x: -x[1]):
            print(f"  {n:5d} photo(s) match: {r[0]}  +  {r[1]}")
        print("  => the official BMPs were not all produced the same way "
              "(different run, different options, or edited files).")
    if outliers:
        print(f"\n{len(outliers)} photo(s) no recipe can reproduce:")
        for k, (ratio, who) in list(outliers.items())[:10]:
            print(f"  {k}: best {100 * ratio:.4f}% different ({who[0]} + {who[1]})")
        print("\nThese are worth opening by hand: usually the official BMP was made from a\n"
              "different version of the photo (re-cropped, re-exported), not from the file\n"
              "now sitting in the sources folder.")
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
        print(f"per-photo outcome written to {args.csv}")

    if ok_n == len(keys):
        print("\nThe converter to write is exactly this:\n")
        print(recipe_code(recipe[0], recipe[1]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
