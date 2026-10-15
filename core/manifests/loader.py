"""Load and validate product manifests (docs/ARCHITECTURE.md §4.1).

The manifest YAML files under `modules/*/manifests/` are the source of
truth for a product's shape; `core.swxkit` keeps the `Product`/`ProductVersion`
rows in sync with whatever a producer last published against.
"""

from __future__ import annotations

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


def load_manifest(path: Path) -> dict:
    """Parse + schema-validate one manifest file. Raises ManifestError with
    every problem found, not just the first (friendlier in CI)."""
    path = Path(path)
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
        for field in ("cadence", "stale_after"):
            try:
                parse_duration(data[field])
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


def get_manifest(product_id: str, base_dir: Path | None = None) -> dict:
    manifests = discover_manifests(base_dir)
    try:
        return manifests[product_id]
    except KeyError:
        raise ManifestError(product_id, [f"no manifest found for product id {product_id!r}"]) from None
