#!/usr/bin/env bash
# Run the HAPI community's verifier (https://github.com/hapi-server/verifier-nodejs)
# against this portal's HAPI server (D-088). Not part of CI and needs no
# sudo: run it from any machine with git, Node.js >= 16, npm, curl and
# python3.
#
#   bash deploy/hapi-verify.sh [HAPI_URL] [--no-wait]
#
# HAPI_URL defaults to the production server. The verifier can't send an
# API key, so it runs on the keyless preview (D-083), which allows 30
# requests per hour per client. One dataset takes about 20, so by default
# the script verifies one dataset per clock hour and waits in between (a
# full run of 5 datasets takes about 4-5 hours; leave it in tmux/screen).
# --no-wait runs them back to back: use it against a dev server started with
# SWX_API_KEYLESS_PER_HOUR=100000, or expect 429 failures after the first.
#
# The full report of each dataset is written to ./hapi-verify-<dataset>.txt;
# the script exits non-zero if any dataset has a failure (✗).
set -euo pipefail

URL="https://helio.fisica.usach.cl/clima-espacial/hapi"
WAIT=1
for arg in "$@"; do
    case "$arg" in
        --no-wait) WAIT=0 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        *) URL="${arg%/}" ;;
    esac
done

for tool in git node npm curl python3; do
    command -v "$tool" > /dev/null || { echo "hapi-verify: needs $tool" >&2; exit 2; }
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "==> Fetching the verifier into $WORK"
git clone --quiet --depth 1 --recurse-submodules https://github.com/hapi-server/verifier-nodejs "$WORK/verifier"
(cd "$WORK/verifier" && npm install --silent --no-audit --no-fund > /dev/null)
echo "    verifier commit: $(git -C "$WORK/verifier" rev-parse --short HEAD)"

mapfile -t DATASETS < <(curl --fail --silent --show-error "$URL/catalog" \
    | python3 -c 'import json, sys; print("\n".join(d["id"] for d in json.load(sys.stdin)["catalog"]))')
(( ${#DATASETS[@]} )) || { echo "hapi-verify: $URL/catalog lists no datasets" >&2; exit 1; }
echo "==> $URL: ${#DATASETS[@]} dataset(s): ${DATASETS[*]}"

FAILED=()
for i in "${!DATASETS[@]}"; do
    ds="${DATASETS[$i]}"
    if (( WAIT && i > 0 )); then
        # The keyless throttle counts per clock hour: start the next dataset
        # just after the hour turns.
        now=$(date -u +%s)
        sleep_for=$(( 3600 - now % 3600 + 30 ))
        echo "    waiting ${sleep_for}s for the next clock hour (keyless limit)..."
        sleep "$sleep_for"
    fi
    report="hapi-verify-${ds}.txt"
    echo "==> Verifying $ds -> $report"
    if node "$WORK/verifier/verify.js" --url "$URL" --dataset "^${ds//./\\.}\$" > "$report" 2>&1; then
        status=ok
    else
        status=FAILED
        FAILED+=("$ds")
    fi
    summary="$(grep -m 1 'End of validation tests' "$report" || echo 'no summary line (see report)')"
    echo "    $status: $summary"
done

if (( ${#FAILED[@]} )); then
    echo "==> Failures in: ${FAILED[*]} (search the reports for ✗)" >&2
    exit 1
fi
echo "==> All datasets passed (warnings, if any, are in the reports)."
