#!/usr/bin/env bash
#
# converterTo7color_all.sh - batch conversion for the Waveshare PhotoPainter
# 7-color panel, running several `convert` processes at the same time.
#
# The original script converted one image at a time. `convert` is a PyInstaller
# bundle: every invocation unpacks the archive and boots a Python interpreter
# before touching a single pixel, and the conversion itself is single threaded.
# With 2000 photos that leaves most of the CPU idle, so here N workers are kept
# busy at all times: as soon as one image is done the next one starts.
#
# Usage:  ./converterTo7color_all.sh [options]
# Help:   ./converterTo7color_all.sh -h
#
set -u

VERSION="2.0"

# ---------------------------------------------------------------- worker mode
# Re-entrant call: xargs runs this script once per image, one process per job
# slot. Everything it needs arrives through PP_* environment variables.
if [ "${1:-}" = "--worker" ]; then
    file=$2

    base=${file%.*}
    out="${base}_${PP_MODE}_output.bmp"

    # A short critical section (mkdir is atomic on every filesystem) keeps the
    # counters exact and the progress lines in order, even with 32 workers.
    bump() { # bump <counter-file> -> echoes the new global progress index
        i=0
        until mkdir "$PP_STATE/lock" 2>/dev/null; do
            i=$(( i + 1 ))
            [ "$i" -gt 400 ] && break          # never deadlock on a stale lock
            sleep 0.05 2>/dev/null || sleep 1
        done
        printf 'x' >> "$1"
        printf 'x' >> "$PP_STATE/progress"
        wc -c < "$PP_STATE/progress"
        rmdir "$PP_STATE/lock" 2>/dev/null
    }

    if [ "$PP_FORCE" -eq 0 ] && [ -f "$out" ] && [ "$out" -nt "$file" ]; then
        n=$(( $(bump "$PP_STATE/skipped") ))
        [ "$PP_QUIET" -eq 1 ] || printf '[%*d/%d] skip  %s (already converted)\n' \
            "${#PP_TOTAL}" "$n" "$PP_TOTAL" "$file"
        exit 0
    fi

    set -- "$file" --mode "$PP_MODE"
    [ -n "$PP_DIR" ] && set -- "$@" --dir "$PP_DIR"
    [ -n "$PP_DITHER" ] && set -- "$@" --dither "$PP_DITHER"

    log=$(TMPDIR="$PP_TMPDIR" "$PP_CONVERT" "$@" 2>&1)
    status=$?

    # `convert` can exit 0 on a soft error, so check that the BMP is really there.
    if [ $status -ne 0 ] || [ ! -s "$out" ]; then
        result=FAIL
        printf '%s\n' "$file" >> "$PP_STATE/failed"
        {
            printf '=== %s (exit %d)\n' "$file" "$status"
            printf '%s\n' "$log"
        } > "$PP_STATE/logs/$$.log"
        n=$(( $(bump "$PP_STATE/failedn") ))
    else
        result="ok  "
        n=$(( $(bump "$PP_STATE/okn") ))
    fi

    if [ "$PP_QUIET" -eq 0 ]; then
        elapsed=$(( $(date +%s) - PP_START ))
        eta=""
        if [ "$n" -gt 0 ] && [ "$elapsed" -gt 0 ]; then
            left=$(( elapsed * (PP_TOTAL - n) / n ))
            eta=$(printf '  ETA %02d:%02d:%02d' \
                $(( left / 3600 )) $(( left % 3600 / 60 )) $(( left % 60 )))
        fi
        printf '[%*d/%d] %s  %s%s\n' "${#PP_TOTAL}" "$n" "$PP_TOTAL" "$result" "$file" "$eta"
    fi
    exit 0
fi

# ------------------------------------------------------------------- defaults
CONVERT="./convert"
INPUT_DIR="."
JOBS=""
MODE="scale"
DIRECTION=""
DITHER=""
FORCE=0
RECURSIVE=0
DRYRUN=0
QUIET=0

usage() {
    cat <<EOF
converterTo7color_all.sh v$VERSION - parallel batch converter for PhotoPainter

  Converts every .jpg/.jpeg/.png/.bmp of a folder with Waveshare's \`convert\`,
  keeping N conversions running at the same time. Each output is written next
  to its source image as <name>_<mode>_output.bmp.

Usage: $0 [options]

  -i DIR      folder with the images to convert (default: current folder)
  -c PATH     path to the \`convert\` executable (default: ./convert, then DIR/convert)
  -j N        parallel jobs (default: number of CPU cores, here: $(detect_cpus))
  -m MODE     scale | cut                      (default: scale)
  -d DIR      landscape | portrait             (default: auto, from image size)
  -D N        dithering: 0 = none, 3 = Floyd-Steinberg (default: converter's own)
  -r          recurse into sub-folders
  -f          re-convert images that already have an up-to-date .bmp
  -n          dry run: only list what would be converted
  -q          quiet: no per-image line, only the final summary
  -h          this help

Examples:
  $0                        # every image in this folder, all cores
  $0 -i ~/Pictures/pp -j 8  # 8 conversions at a time
  $0 -r -m cut              # recursive, crop instead of scale-and-pad
EOF
}

detect_cpus() {
    if command -v sysctl >/dev/null 2>&1 && sysctl -n hw.ncpu >/dev/null 2>&1; then
        sysctl -n hw.ncpu
    elif command -v nproc >/dev/null 2>&1; then
        nproc
    else
        echo 4
    fi
}

die() { printf 'Error: %s\n' "$1" >&2; exit 1; }

while getopts "i:c:j:m:d:D:rfnqh" opt; do
    case "$opt" in
        i) INPUT_DIR=$OPTARG ;;
        c) CONVERT=$OPTARG ;;
        j) JOBS=$OPTARG ;;
        m) MODE=$OPTARG ;;
        d) DIRECTION=$OPTARG ;;
        D) DITHER=$OPTARG ;;
        r) RECURSIVE=1 ;;
        f) FORCE=1 ;;
        n) DRYRUN=1 ;;
        q) QUIET=1 ;;
        h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

case "$MODE" in scale|cut) ;; *) die "-m must be 'scale' or 'cut' (got '$MODE')" ;; esac
case "$DIRECTION" in ""|landscape|portrait) ;; *) die "-d must be 'landscape' or 'portrait'" ;; esac
case "$DITHER" in ""|0|3) ;; *) die "-D must be 0 (none) or 3 (Floyd-Steinberg)" ;; esac
[ -d "$INPUT_DIR" ] || die "folder not found: $INPUT_DIR"

# ------------------------------------------------------------ convert binary
if [ ! -e "$CONVERT" ] && [ -e "$INPUT_DIR/convert" ]; then
    CONVERT="$INPUT_DIR/convert"
fi
[ -e "$CONVERT" ] || die "'convert' executable not found (looked for '$CONVERT'). Use -c PATH."
if [ ! -x "$CONVERT" ]; then
    chmod +x "$CONVERT" 2>/dev/null || die "'$CONVERT' is not executable (chmod +x it)."
fi
case "$CONVERT" in /*) ;; *) CONVERT="$(cd "$(dirname "$CONVERT")" && pwd)/$(basename "$CONVERT")" ;; esac

if [ -z "$JOBS" ]; then
    JOBS=$(detect_cpus)
fi
case "$JOBS" in ''|*[!0-9]*) die "-j must be a number" ;; esac
[ "$JOBS" -ge 1 ] || die "-j must be >= 1"

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# ---------------------------------------------------------------- file list
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pp7c.XXXXXX") || die "cannot create temp folder"
cleanup() { rm -rf "$STATE"; }
trap cleanup EXIT
trap 'echo; echo "Interrupted - already converted files are kept."; cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
mkdir -p "$STATE/logs" "$STATE/tmp"
: > "$STATE/progress"; : > "$STATE/skipped"; : > "$STATE/failed"
: > "$STATE/okn"; : > "$STATE/failedn"

LIST="$STATE/files"
if [ "$RECURSIVE" -eq 1 ]; then
    DEPTH=""
else
    DEPTH="-maxdepth 1"
fi

# Skip the converter's own output (*_scale_output.bmp / *_cut_output.bmp),
# otherwise every run would convert its previous results again, and skip the
# AppleDouble junk files (._name.jpg) that SD cards and USB sticks collect.
# shellcheck disable=SC2086
find "$INPUT_DIR" $DEPTH -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.bmp' \) \
    ! -iname '*_scale_output.bmp' ! -iname '*_cut_output.bmp' ! -name '._*' \
    -print0 > "$LIST"

TOTAL=$(( $(tr -dc '\0' < "$LIST" | wc -c) ))

if [ "$TOTAL" -eq 0 ]; then
    echo "No image to convert in '$INPUT_DIR'."
    exit 0
fi

if [ "$DRYRUN" -eq 1 ]; then
    printf 'Dry run: %d image(s) would be converted with %d parallel jobs.\n\n' "$TOTAL" "$JOBS"
    tr '\0' '\n' < "$LIST"
    exit 0
fi

printf 'PhotoPainter batch: %d image(s), %d parallel jobs, mode=%s%s\n' \
    "$TOTAL" "$JOBS" "$MODE" "${DIRECTION:+, dir=$DIRECTION}"

START=$(date +%s)

export PP_CONVERT="$CONVERT"
export PP_MODE="$MODE"
export PP_DIR="$DIRECTION"
export PP_DITHER="$DITHER"
export PP_FORCE="$FORCE"
export PP_STATE="$STATE"
export PP_TMPDIR="$STATE/tmp"
export PP_TOTAL="$TOTAL"
export PP_START="$START"
export PP_QUIET="$QUIET"

# xargs is the scheduler: it keeps exactly $JOBS workers alive and starts the
# next image as soon as one of them exits - no batching, no idle cores.
xargs -0 -n 1 -P "$JOBS" "$SELF" --worker < "$LIST"

# ------------------------------------------------------------------- summary
END=$(date +%s)
ELAPSED=$(( END - START ))
[ "$ELAPSED" -gt 0 ] || ELAPSED=1
SKIPPED=$(( $(wc -c < "$STATE/skipped") ))
FAILED=$(( $(wc -l < "$STATE/failed") ))
OK=$(( $(wc -c < "$STATE/okn") ))

printf '\n----------------------------------------------------------------\n'
printf 'Converted : %d\n' "$OK"
printf 'Skipped   : %d (already up to date, use -f to redo them)\n' "$SKIPPED"
printf 'Failed    : %d\n' "$FAILED"
printf 'Time      : %02d:%02d:%02d for %d image(s) (%s img/min)\n' \
    $(( ELAPSED / 3600 )) $(( ELAPSED % 3600 / 60 )) $(( ELAPSED % 60 )) \
    "$TOTAL" "$(( OK * 60 / ELAPSED ))"

if [ "$FAILED" -gt 0 ]; then
    printf '\nFailed images:\n'
    sed 's/^/  /' "$STATE/failed"
    printf '\nConverter output for the failures:\n'
    cat "$STATE"/logs/*.log 2>/dev/null | sed 's/^/  /'
    exit 1
fi

printf '\nDone.\n'
