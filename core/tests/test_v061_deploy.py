"""v0.6.1 deploy pieces (D-103): the image locations' argument whitelist
(format, size, style), the loop's and replays' frame locations, their rate
zone, the cache size, and GoatCounter skipping the frames. Both nginx files
were also checked with `nginx -t` (nginx 1.24 in a container) and exercised
against the flare-nrt stub; these tests keep their shape."""

import re
from pathlib import Path

from core.tests.test_v060_deploy import HTTP, SNIPPET, _code, _locations

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
ISO = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)"
RENDER = rf'~ "^/clima-espacial/productos/usach\.flare\.nrt/render/{ISO}/(171|171-diff|gradcam-heat)\.webp$"'
# 131 Å joined the replay frames in v0.6.2 (flare-nrt 1.2.0, D-107).
REPLAY = rf'~ "^/clima-espacial/productos/usach\.flare\.nrt/replay/(\d{{1,9}})/{ISO}/(131|171|gradcam-heat)\.webp$"'
# An event's peak set (v0.6.2, flare-nrt 1.2.0, D-112).
PEAK = (r'~ "^/clima-espacial/productos/usach\.flare\.nrt/replay/(\d{1,9})/peak/'
        r'(131|171|304|1600|gradcam-heat|gradcam-overlay)\.webp$"')
IMG = r"~ ^/clima-espacial/productos/usach\.flare\.nrt/img/(131|171|304|1600)\.png$"


def test_argument_maps_refuse_anything_else():
    http = _code(HTTP)
    for name, values in (("format", ("png", "webp")), ("size", ("512", "1024")), ("style", ("overlay", "heat"))):
        block = re.search(rf"map \$arg_{name} \$swx_flare_{name} \{{(.*?)\}}", http, re.S).group(1)
        for value in values:
            assert re.search(rf"^\s*{value}\s+{value};", block, re.M), (name, value)
        assert re.search(r'^\s*default\s+"";', block, re.M), name  # anything else -> "" -> 400
    assert 'map "$swx_flare_kind:$swx_flare_size" $swx_flare_size_ok' in http
    assert '"diff:1024" 0;' in http  # 1024 px only with kind=img


def test_image_location_checks_and_forwards_only_whitelisted_arguments():
    locs = _locations(SNIPPET)
    body = locs[IMG]
    for check in ('if ($swx_flare_format = "") { return 400; }', 'if ($swx_flare_size = "") { return 400; }',
                  "if ($swx_flare_size_ok = 0) { return 400; }"):
        assert check in body
    assert "?kind=$swx_flare_kind&format=$swx_flare_format&size=$swx_flare_size;" in body
    assert 'proxy_cache_key "img/$swx_flare_wave/$swx_flare_kind/$swx_flare_format/$swx_flare_size/latest";' in body
    for key, loc in locs.items():
        if "gradcam" in key and ".webp" not in key:
            assert 'if ($swx_flare_style = "") { return 400; }' in loc
            assert "style=$swx_flare_style&format=$swx_flare_format" in loc
            assert "$swx_flare_style/$swx_flare_format" in loc  # in the cache key


def test_frame_locations_are_strict_cached_immutable_and_rate_limited():
    locs = _locations(SNIPPET)
    for key, upstream in ((RENDER, "/api/render/$swx_flare_rslot/$swx_flare_rname.webp"),
                          (REPLAY, "/api/events/$swx_flare_event/frames/$swx_flare_eslot/$swx_flare_ename.webp"),
                          (PEAK, "/api/events/$swx_flare_pevent/peak/$swx_flare_pname.webp")):
        body = locs[key]
        assert f"proxy_pass http://127.0.0.1:5050{upstream};" in body
        assert "proxy_cache swx_flare;" in body and "proxy_cache_use_stale error timeout updating" in body
        assert "limit_req zone=swx_flare_frames burst=400 nodelay;" in body
        assert body.count('add_header Cache-Control "public, max-age=31536000, immutable";') == 1
        assert "proxy_hide_header Cache-Control;" in body
        assert "$args" not in body and "$is_args" not in body and "$request_uri" not in body
    assert r"(\d{1,9})" in REPLAY and r"(\d{1,9})" in PEAK  # event id: digits only
    assert 'proxy_cache_key "replay/$swx_flare_pevent/peak/$swx_flare_pname";' in locs[PEAK]


def test_rate_zones_and_cache_size():
    http = _code(HTTP)
    assert "limit_req_zone $binary_remote_addr zone=swx_flare_img:10m rate=30r/s;" in http
    assert "limit_req_zone $binary_remote_addr zone=swx_flare_frames:10m rate=100r/s;" in http
    assert "max_size=1g" in http
    stills = [b for k, b in _locations(SNIPPET).items() if ("img" in k or "gradcam" in k) and ".webp" not in k]
    assert stills and all("limit_req zone=swx_flare_img burst=100 nodelay;" in b for b in stills)


def test_goatcounter_skips_the_frames():
    unit = (DEPLOY / "systemd" / "goatcounter-import.service").read_text()
    for glob in ("/clima-espacial/productos/usach.flare.nrt/render/**",
                 "/clima-espacial/productos/usach.flare.nrt/replay/**"):
        assert f'-exclude "path:glob:{glob}"' in unit


def test_planning_folder_is_ignored():
    ignore = (DEPLOY.parent / ".gitignore").read_text()
    section = ignore.split("# --- Private planning record", 1)[1].split("# ---", 1)[0]
    assert "/planning/" in section.splitlines()
