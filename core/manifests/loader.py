"""Load and validate product manifests (docs/ARCHITECTURE.md §4.1).

The manifest YAML files under `modules/*/manifests/` are the source of
truth for a product's shape; `core.swxkit` keeps the `Product`/`ProductVersion`
rows in sync with whatever a producer last published against.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"

_DURATION_RE = re.compile(r"^(\d+)(s|m|h|d)$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ManifestError(ValueError):
    """Raised with every problem found in one manifest file, not just the first."""

    def __init__(self, path, errors):
        self.path = path
        self.errors = list(errors)
        super().__init__(f"{path}: " + "; ".join(self.errors))


def parse_duration(value: str) -> int:
    """'180s' / '15m' / '1h' / '7d' -> seconds."""
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"invalid duration {value!r} (expected e.g. '180s', '15m', '1h', '7d')")
    amount, unit = match.groups()
    return int(amount) * _DURATION_SECONDS[unit]


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def discover_manifest_paths(base_dir: Path | None = None) -> list[Path]:
    base_dir = Path(base_dir) if base_dir else REPO_ROOT
    return sorted(
        path
        for path in (base_dir / "modules").glob("*/manifests/*.yaml")
        # modules/_template/ is the skeleton `startmodule` copies, not a real module.
        if not path.parent.parent.name.startswith("_")
    )


# path -> ((mtime_ns, size), manifest). Every publish re-reads its product's
# manifest (by design: an edit takes effect on the next run), but parsing and
# schema-validating every YAML each time cost ~70 ms per lookup, which a
# forecast replay calling it a few times per issue turned into minutes. A
# changed file has a new mtime/size, so an edit still takes effect at once.
_LOADED: dict[Path, tuple[tuple[int, int], dict]] = {}


def load_manifest(path: Path) -> dict:
    """Parse + schema-validate one manifest file. Raises ManifestError with
    every problem found, not just the first (friendlier in CI). Cached per
    file until it changes on disk; callers get their own copy."""
    path = Path(path)
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _LOADED.get(path)
    if cached and cached[0] == key:
        return copy.deepcopy(cached[1])
    data = _load_manifest_uncached(path)
    _LOADED[path] = (key, data)
    return copy.deepcopy(data)


def _load_manifest_uncached(path: Path) -> dict:
    errors: list[str] = []

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ManifestError(path, [f"invalid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ManifestError(path, ["manifest must be a YAML mapping"])

    validator = Draft202012Validator(load_schema())
    errors.extend(sorted(e.message for e in validator.iter_errors(data)))

    if not errors:
        durations = [data["cadence"], data["stale_after"], *data.get("forecast", {}).get("horizons", [])]
        for value in durations:
            try:
                parse_duration(value)
            except ValueError as exc:
                errors.append(str(exc))

    if errors:
        raise ManifestError(path, errors)

    try:
        relative_path = path.relative_to(REPO_ROOT)
    except ValueError:
        relative_path = path
    data["_module"] = path.parent.parent.name  # modules/<name>/manifests/x.yaml
    data["_path"] = str(relative_path)
    return data


def discover_manifests(base_dir: Path | None = None) -> dict[str, dict]:
    """Load every manifest under modules/*/manifests/. Raises ManifestError
    (with every broken file's errors) if any fails to load, or on a
    duplicate product id across two files."""
    manifests: dict[str, dict] = {}
    all_errors: list[str] = []
    for path in discover_manifest_paths(base_dir):
        try:
            manifest = load_manifest(path)
        except ManifestError as exc:
            all_errors.append(str(exc))
            continue
        product_id = manifest["id"]
        if product_id in manifests:
            all_errors.append(
                f"{path}: duplicate product id {product_id!r}, "
                f"already defined by {manifests[product_id]['_path']}"
            )
            continue
        manifests[product_id] = manifest
    if all_errors:
        raise ManifestError("(multiple files)", all_errors)
    return manifests


# A forecast's target must be something observed, so it can be verified.
FORECAST_TARGET_KINDS = ("timeseries", "index")


def forecast_target_errors(manifest: dict, manifests: dict[str, dict]) -> list[str]:
    """Cross-manifest check for one `kind: forecast` manifest (D-071): its
    `forecast.target.product` must exist, be observed (timeseries/index) and
    have `target.field` among its parameters. [] for any other kind."""
    if manifest.get("kind") != "forecast":
        return []
    target = manifest["forecast"]["target"]
    other = manifests.get(target["product"])
    if other is None:
        return [f"forecast.target.product {target['product']!r} has no manifest"]
    errors = []
    if other["kind"] not in FORECAST_TARGET_KINDS:
        errors.append(
            f"forecast.target.product {target['product']!r} is kind {other['kind']!r}, "
            f"expected one of {list(FORECAST_TARGET_KINDS)}"
        )
    fields = [p["name"] for p in other.get("parameters", [])]
    if target["field"] not in fields:
        errors.append(f"forecast.target.field {target['field']!r} is not a parameter of {target['product']!r} {fields}")
    return errors


def get_manifest(product_id: str, base_dir: Path | None = None) -> dict:
    manifests = discover_manifests(base_dir)
    try:
        return manifests[product_id]
    except KeyError:
        raise ManifestError(product_id, [f"no manifest found for product id {product_id!r}"]) from None
