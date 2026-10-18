#!/usr/bin/env bash
# Deploys one tagged release of Clima Espacial USACH to the helio VM.
# Runs entirely server-side, straight from GitHub — never from a local
# working copy — so it can never pick up anything outside what that tag's
# commit has tracked (in particular: never the private planning record,
# which this repository's working tree keeps alongside the code but never
# commits — see .gitignore and docs/DECISIONS.md D-047).
#
# Usage (on the server, as root):
#   sudo bash deploy.sh v0.1.0
#
# One-time server preparation is deploy/provision.sh.
set -euo pipefail

TAG="${1:?Usage: deploy.sh <git-tag>}"
REPO_URL="${SWX_DEPLOY_REPO:-https://github.com/HelioUSACH/clima-espacial.git}"
# SWX_DEPLOY_SANDBOX is for core/tests/test_deploy_script.py only: it puts
# every path below under a scratch directory and skips the root check, with
# stub commands on PATH. sudo resets the environment, so a real deploy never
# sees it.
SANDBOX="${SWX_DEPLOY_SANDBOX:-}"
DESTINO=$SANDBOX/opt/swx
ARCHIVO_ENV=$SANDBOX/etc/swx/swx.env
UNIT_DIR=$SANDBOX/etc/systemd/system
SNIPPET_LIVE=$SANDBOX/etc/nginx/snippets/clima-espacial.conf

[[ -n $SANDBOX || $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -f $ARCHIVO_ENV ]] || { echo "$ARCHIVO_ENV is missing: run provision.sh first." >&2; exit 1; }
id swx >/dev/null 2>&1 || { echo "User 'swx' is missing: run provision.sh first." >&2; exit 1; }

# D-077: the script Victor starts is the *previous* release's
# /opt/swx/deploy/deploy.sh. The rsync below replaces that file, but bash
# keeps reading the copy it opened, so every later step would be the old
# release's logic (v0.4.0 never enabled swx-verify.timer this way). So: clone
# the tag, then run the tag's own deploy.sh on that clone and stop here.
#
# The hand-over is a child process, not `exec`, so this run's EXIT trap
# removes the clone after the child finishes. A child that knows
# SWX_DEPLOY_WORK (0.4.1 on) reuses the clone; an older one (a rollback)
# ignores it, clones again into its own temp dir and cleans that up itself.
# Either way no temp dir is left behind.
if [[ -z ${SWX_DEPLOY_REEXEC:-} ]]; then
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    echo "==> Fetching $TAG"
    git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"
    echo "==> Re-running $TAG's own deploy.sh"
    rc=0
    SWX_DEPLOY_REEXEC=1 SWX_DEPLOY_WORK="$WORK" bash "$WORK/src/deploy/deploy.sh" "$TAG" || rc=$?
    exit "$rc"
fi

# The hand-over run: the parent owns $SWX_DEPLOY_WORK and removes it.
WORK="${SWX_DEPLOY_WORK:?SWX_DEPLOY_REEXEC is set without SWX_DEPLOY_WORK}"
CLONED="$(git -C "$WORK/src" describe --tags --exact-match 2>/dev/null || true)"
[[ $CLONED == "$TAG" ]] || { echo "$WORK/src is at '${CLONED:-no tag}', not $TAG: refusing to deploy it." >&2; exit 1; }

echo "==> Installing code (root-owned, read-only to the service user)"
install -d -o root -g root -m 755 "$DESTINO"
# --exclude='venv/': otherwise --delete wipes it every deploy (it never
# exists in the freshly-cloned source) — full downtime + reinstall on every
# release, confirmed by reproducing it locally with plain rsync before
# writing this exclude.
rsync -rlt --delete --chown=root:root --chmod=D755,F644 \
    --exclude='.git/' --exclude='venv/' \
    "$WORK/src/" "$DESTINO/"

echo "==> venv + dependencies"
[[ -x "$DESTINO/venv/bin/python" ]] || python3 -m venv "$DESTINO/venv"
"$DESTINO/venv/bin/pip" install --quiet --disable-pip-version-check -r "$DESTINO/requirements.txt"

echo "==> migrate + collectstatic"
set -a
# shellcheck source=/dev/null
. "$ARCHIVO_ENV"
set +a
: "${STATIC_ROOT:?STATIC_ROOT is not set in $ARCHIVO_ENV}" "${ALLOWED_HOSTS:?ALLOWED_HOSTS is not set in $ARCHIVO_ENV}"
export PYTHONDONTWRITEBYTECODE=1
cd "$DESTINO"
runuser -u swx -- venv/bin/python manage.py migrate --noinput
install -d -o root -g root -m 755 "$STATIC_ROOT"
venv/bin/python manage.py collectstatic --noinput --clear -v 0

echo "==> systemd units"
install -o root -g root -m 644 "$DESTINO"/deploy/systemd/*.service "$DESTINO"/deploy/systemd/*.timer "$UNIT_DIR"/
systemctl daemon-reload
systemctl enable --quiet --now swx-web
# Every timer this release ships is enabled; there is no list to forget to
# update (D-077). A timer that must ship disabled goes in SKIP_TIMERS, with
# a comment saying why. Services that deploy.sh doesn't enable (goatcounter*,
# set up by provision.sh) aren't timers, so they're unaffected.
SKIP_TIMERS=()
TIMERS=()
for unit in "$DESTINO"/deploy/systemd/*.timer; do
    name="$(basename "$unit")"
    [[ " ${SKIP_TIMERS[*]} " == *" $name "* ]] || TIMERS+=("$name")
done
systemctl enable --quiet --now "${TIMERS[@]}"
systemctl restart swx-web

echo "==> Smoke test (straight to gunicorn, pretending to be nginx)"
# Unprefixed path: nginx's proxy_pass strips "/clima-espacial" before this
# point (see deploy/nginx-clima-espacial.conf), so hitting gunicorn directly
# must use the same unprefixed path Django's urlpatterns actually match.
GUNICORN=http://127.0.0.1:8100
fetch() {
    curl --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 \
        -H "Host: ${ALLOWED_HOSTS%%,*}" -H "X-Forwarded-Proto: https" "$GUNICORN$1"
}
fetch /estado/ > /dev/null
HOME_HTML="$(fetch /)"

# D-055: the page must reference hashed static files, and the exact file it
# names must exist in STATIC_ROOT — otherwise collectstatic/the manifest
# went wrong and every visitor would get an unstyled page with no charts.
ASSET="$(grep -o 'static/web/js/charts\.[0-9a-f]\{12\}\.js' <<< "$HOME_HTML" | head -n 1 || true)"
[[ -n $ASSET ]] || { echo "Smoke test: / does not reference a hashed charts.js (manifest storage off?)." >&2; exit 1; }
[[ -f "$STATIC_ROOT/${ASSET#static/}" ]] || { echo "Smoke test: $ASSET is referenced but missing from $STATIC_ROOT." >&2; exit 1; }

# One bucketed series through the real view (D-056). A brand-new server
# whose collectors haven't run yet has no products: warn, don't fail.
SERIE_STATUS="$(curl --silent --output "$WORK/serie.json" --write-out '%{http_code}' \
    -H "Host: ${ALLOWED_HOSTS%%,*}" -H "X-Forwarded-Proto: https" \
    "$GUNICORN/productos/noaa.rtsw.mag/serie.json?range=1d")"
case "$SERIE_STATUS" in
    200) venv/bin/python -c 'import json, sys; d = json.load(open(sys.argv[1])); assert d["bucket_seconds"] == 120' \
            "$WORK/serie.json" || { echo "Smoke test: serie.json is not the bucketed contract." >&2; exit 1; } ;;
    404) echo "Smoke test: noaa.rtsw.mag has no data yet (first deploy?) — serie.json not checked." ;;
    *) echo "Smoke test: serie.json returned HTTP $SERIE_STATUS." >&2; exit 1 ;;
esac

# A timer that isn't running must fail the deploy, not pass quietly (D-077).
DOWN=()
for name in "${TIMERS[@]}"; do
    systemctl is-enabled --quiet "$name" && systemctl is-active --quiet "$name" || DOWN+=("$name")
done
if (( ${#DOWN[@]} )); then
    echo "Timers not enabled and active: ${DOWN[*]}" >&2
    exit 1
fi
systemctl list-timers 'swx-*' --no-pager
echo "Deployed $TAG. Now hard-refresh / and one product page in both themes (CONTRIBUTING.md)."

# 22-HANDOFF-v0.3.0.md §2.3: nginx config is a manual step (it edits the
# shared helio vhost), so a changed snippet is easy to miss -- v0.1.3's sat
# uninstalled for two releases. Only remind; never touch nginx from here.
SNIPPET_REPO="$DESTINO/deploy/nginx-clima-espacial.conf"
if ! cmp --silent "$SNIPPET_REPO" "$SNIPPET_LIVE"; then
    echo
    echo "NOTE: $SNIPPET_LIVE differs from this release's $SNIPPET_REPO"
    echo "      (or is missing). To install it:"
    echo "  sudo install -m 644 $SNIPPET_REPO $SNIPPET_LIVE"
    echo "  sudo nginx -t && sudo systemctl reload nginx"
fi
