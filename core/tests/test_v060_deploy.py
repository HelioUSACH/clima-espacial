"""v0.6.0 deploy pieces (D-100): flare-nrt's images served by nginx from the
service with a cache, the http{}-context file that cache needs, the page's
data endpoint without a second Cache-Control, and GoatCounter not counting
any of it as page views. Both nginx files were also checked with `nginx -t`
(nginx 1.24 in a container, as in D-051); these tests keep their shape."""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SNIPPET = (DEPLOY / "nginx-clima-espacial.conf").read_text()
HTTP = (DEPLOY / "nginx-clima-espacial-http.conf").read_text()

HTTP_ONLY = ("proxy_cache_path", "limit_req_zone", "map ", "upstream ")


def _code(text):
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _locations(text):
    """{location line: body} for every top-level location block."""
    out, code = {}, _code(text)
    for match in re.finditer(r"^location (.+?) *\{\s*\n(.*?)^}", code, re.M | re.S):
        out[match.group(1).strip()] = match.group(2)
    return out


def test_http_context_directives_live_only_in_the_http_file():
    code = _code(SNIPPET)
    for directive in HTTP_ONLY:
        assert not re.search(rf"^\s*{directive}", code, re.M), f"{directive} is http{{}}-only (D-051)"
    http = _code(HTTP)
    assert "proxy_cache_path /var/cache/nginx/clima-espacial-flare" in http and "keys_zone=swx_flare:" in http
    assert "limit_req_zone $binary_remote_addr zone=swx_flare_img:" in http
    assert "map $arg_kind $swx_flare_kind" in http and "map $arg_slot $swx_flare_slot_ok" in http


def _image_locations():
    return {k: v for k, v in _locations(SNIPPET).items() if "usach" in k and ("img" in k or "gradcam" in k)}


def test_image_locations_accept_only_the_documented_paths():
    keys = set(_locations(SNIPPET))
    assert r"~ ^/clima-espacial/productos/usach\.flare\.nrt/img/(131|171|304|1600)\.png$" in keys
    assert "= /clima-espacial/productos/usach.flare.nrt/gradcam/latest.png" in keys
    assert (r'~ "^/clima-espacial/productos/usach\.flare\.nrt/gradcam/'
            r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\.png$"') in keys
    assert "= /clima-espacial/_flare/img-slot" in keys


def test_image_locations_cache_and_bound_the_service():
    bodies = list(_image_locations().values()) + [_locations(SNIPPET)["= /clima-espacial/_flare/img-slot"]]
    assert len(bodies) == 4
    for body in bodies:
        assert "proxy_pass http://127.0.0.1:5050/api/" in body
        assert "proxy_cache swx_flare;" in body and "proxy_cache_key " in body
        assert "proxy_cache_use_stale error timeout updating" in body
        assert "proxy_cache_lock on;" in body and "proxy_cache_background_update on;" in body
        assert "proxy_read_timeout 5s;" in body
        assert "limit_req zone=swx_flare_img" in body
        # The service's own Cache-Control is replaced, never doubled (the D-057 problem).
        assert "proxy_hide_header Cache-Control;" in body and body.count('add_header Cache-Control "public') == 1
        assert "no-cache" not in body
        # Only kind and slot reach the service: the URI is rebuilt, never "$args"/"$is_args".
        assert "$args" not in body and "$is_args" not in body and "$request_uri" not in body
        validity = re.findall(r"proxy_cache_valid 200 (\S+);", body)
        assert validity in (["60s"], ["7d"])


def test_latest_is_60s_and_a_slot_is_long():
    locs = _locations(SNIPPET)
    latest_img = locs[r"~ ^/clima-espacial/productos/usach\.flare\.nrt/img/(131|171|304|1600)\.png$"]
    assert "proxy_cache_valid 200 60s;" in latest_img and "&slot" not in latest_img
    assert 'if ($swx_flare_slot_ok = bad) { return 400; }' in latest_img
    assert 'if ($swx_flare_kind = "") { return 400; }' in latest_img
    slot_img = locs["= /clima-espacial/_flare/img-slot"]
    assert "internal;" in slot_img and "proxy_cache_valid 200 7d;" in slot_img
    assert "kind=$swx_flare_kind&slot=$arg_slot" in slot_img


def test_flare_data_endpoint_has_no_added_cache_control():
    locs = _locations(SNIPPET)
    key = r"~ ^/clima-espacial/((?:en/)?productos/usach\.flare\.nrt/datos/[a-z]+\.json)$"
    assert key in locs
    assert "add_header" not in locs[key] and "proxy_pass http://127.0.0.1:8100;" in locs[key]


def test_goatcounter_skips_flare_images_and_data():
    unit = (DEPLOY / "systemd" / "goatcounter-import.service").read_text()
    for glob in ("/clima-espacial/productos/usach.flare.nrt/img/**",
                 "/clima-espacial/productos/usach.flare.nrt/gradcam/**",
                 "/clima-espacial/**usach.flare.nrt/datos/**"):
        assert f'-exclude "path:glob:{glob}"' in unit
