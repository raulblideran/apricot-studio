#!/usr/bin/env bash
# Apricot Studio's test suite.
#
#   ./run-tests.sh           everything, including real ffmpeg exports
#   ./run-tests.sh --fast    unit and command tests only, no encoding
#   ./run-tests.sh -v        verbose, one line per test
#
# Uses only the standard library's unittest plus PyQt6.QtTest, both already
# present on the system, so there is nothing to install.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FAST=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --fast) FAST=1 ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) ARGS+=("$arg") ;;
    esac
done

for tool in python3 ffmpeg ffprobe; do
    command -v "$tool" >/dev/null || { echo "error: $tool not found" >&2; exit 1; }
done
python3 -c 'import PyQt6.QtWidgets' 2>/dev/null || {
    echo "error: PyQt6 is required" >&2; exit 1; }

# Widget tests need some platform. Without a display, offscreen still exercises
# everything except the main window, which is skipped rather than faked.
if [[ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]]; then
    export QT_QPA_PLATFORM=offscreen
    echo "no display — running Qt tests offscreen; window tests will skip"
fi
export QT_LOGGING_RULES="qt.multimedia.*=false;qt.core.qfuture.*=false"

if (( FAST )); then
    echo "running fast tests (no encoding)"
    PATTERNS=(test_units.py test_commands.py test_theme.py test_sandbox.py
              test_edges.py test_gui.py)
else
    PATTERNS=(test_units.py test_commands.py test_theme.py test_sandbox.py
              test_edges.py test_gui.py test_encode.py)
fi

start=$(date +%s)
status=0
log=$(mktemp)
trap 'rm -f "$log"' EXIT

for pattern in "${PATTERNS[@]}"; do
    # Run to a file and read the exit code directly. Piping into a filter and
    # reading PIPESTATUS afterwards is fragile -- a trailing `|| true` silently
    # resets it, and the suite then reports success while tests are failing.
    if ! python3 -m unittest discover -s tests -t . -p "$pattern" \
            "${ARGS[@]+"${ARGS[@]}"}" >"$log" 2>&1; then
        status=1
    fi
    grep -vE '^(Input #0|Duration:|  |Stream #|\[.*@ 0x|No sample format)' "$log" || true
done
elapsed=$(( $(date +%s) - start ))

echo
if (( status == 0 )); then
    echo "all tests passed in ${elapsed}s"
else
    echo "TESTS FAILED (${elapsed}s)" >&2
fi
exit $status
