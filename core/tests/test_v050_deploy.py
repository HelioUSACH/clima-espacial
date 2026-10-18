"""D-090: an API key sent as ?api_key=... must never reach a log. gunicorn
logs the path without the query string, nginx doesn't log /hapi/ and
/api/ at all, and GoatCounter doesn't import them."""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _exec_start(unit: str) -> str:
    text = re.sub(r"\\\n", " ", (DEPLOY / "systemd" / unit).read_text())
    return re.search(r"^ExecStart=(.+)$", text, re.M).group(1)


def test_gunicorn_access_log_has_no_query_string_or_referrer():
    cmd = _exec_start("swx-web.service")
    match = re.search(r"--access-logformat '([^']+)'", cmd)
    assert match, "swx-web.service must set --access-logformat explicitly (gunicorn's default logs %(r)s)"
    fmt = match.group(1)
    # systemd: every literal % is written %%, or it is taken as a specifier.
    assert re.search(r"(?<!%)%\(", fmt) is None, fmt
    fmt = fmt.replace("%%", "%")
    assert "%(U)s" in fmt
    for leaky in ("%(r)s", "%(q)s", "%(f)s", "%({"):
        assert leaky not in fmt, f"{leaky} would log the query string, a referrer or a header"


def _location(conf: str, prefix: str) -> str:
    match = re.search(r"^location " + re.escape(prefix) + r" \{\n(.*?)^\}", conf, re.M | re.S)
    assert match, f"no location {prefix}"
    return match.group(1)


def test_nginx_data_locations_are_not_logged_and_keep_their_own_cache_headers():
    conf = (DEPLOY / "nginx-clima-espacial.conf").read_text()
    for prefix, upstream in (("/clima-espacial/hapi", "http://127.0.0.1:8100/hapi;"),
                             ("/clima-espacial/api/", "http://127.0.0.1:8100/api/;")):
        body = _location(conf, prefix)
        assert "access_log off;" in body
        assert f"proxy_pass {upstream}" in body
        assert "proxy_set_header X-Real-IP $remote_addr;" in body  # the keyless throttle's client IP
        assert "add_header" not in body  # the responses set Cache-Control themselves
    assert re.search(r"^gzip_types .*\btext/csv\b", conf, re.M)


def test_goatcounter_ignores_data_paths():
    cmd = _exec_start("goatcounter-import.service")
    assert '-exclude "path:glob:/clima-espacial/hapi/**"' in cmd
    assert '-exclude "path:glob:/clima-espacial/api/**"' in cmd


def test_hapi_verify_script_exists_and_targets_the_portal():
    script = (DEPLOY / "hapi-verify.sh").read_text()
    assert "hapi-server/verifier-nodejs" in script
    assert "https://helio.fisica.usach.cl/clima-espacial/hapi" in script
