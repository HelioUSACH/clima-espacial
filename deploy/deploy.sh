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
REPO_URL="https://github.com/HelioUSACH/clima-espacial.git"
DESTINO=/opt/swx
ARCHIVO_ENV=/etc/swx/swx.env
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -f $ARCHIVO_ENV ]] || { echo "$ARCHIVO_ENV is missing: run provision.sh first." >&2; exit 1; }
id swx >/dev/null 2>&1 || { echo "User 'swx' is missing: run provision.sh first." >&2; exit 1; }

echo "==> Fetching $TAG"
git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"

echo "==> Installing code (root-owned, read-only to the service user)"
install -d -o root -g root -m 755 "$DESTINO"
rsync -rlt --delete --chown=root:root --chmod=D755,F644 \
    --exclude='.git/' \
    "$WORK/src/" "$DESTINO/"

echo "==> venv + dependencies"
[[ -x "$DESTINO/venv/bin/python" ]] || python3 -m venv "$DESTINO/venv"
"$DESTINO/venv/bin/pip" install --quiet --disable-pip-version-check -r "$DESTINO/requirements.txt"

echo "==> migrate + collectstatic"
set -a; . "$ARCHIVO_ENV"; set +a
: "${STATIC_ROOT:?STATIC_ROOT is not set in $ARCHIVO_ENV}" "${ALLOWED_HOSTS:?ALLOWED_HOSTS is not set in $ARCHIVO_ENV}"
export PYTHONDONTWRITEBYTECODE=1
cd "$DESTINO"
runuser -u swx -- venv/bin/python manage.py migrate --noinput
install -d -o root -g root -m 755 "$STATIC_ROOT"
venv/bin/python manage.py collectstatic --noinput --clear -v 0

echo "==> systemd units"
install -o root -g root -m 644 "$DESTINO"/deploy/systemd/*.service "$DESTINO"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet --now swx-web swx-dump.timer
for timer in "$DESTINO"/deploy/systemd/swx-collect@*.timer; do
    systemctl enable --quiet --now "$(basename "$timer")"
done
systemctl restart swx-web

echo "==> Smoke test (straight to gunicorn, pretending to be nginx)"
# Unprefixed path: nginx's proxy_pass strips "/clima-espacial" before this
# point (see deploy/nginx-clima-espacial.conf), so hitting gunicorn directly
# must use the same unprefixed path Django's urlpatterns actually match.
curl --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 \
    -H "Host: ${ALLOWED_HOSTS%%,*}" -H "X-Forwarded-Proto: https" \
    "http://127.0.0.1:8100/estado/" > /dev/null
echo "Deployed $TAG."
