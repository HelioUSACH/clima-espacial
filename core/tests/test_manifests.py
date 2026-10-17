import pytest
import yaml

from core.manifests.loader import ManifestError, load_manifest, parse_duration


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("180s", 180), ("15m", 900), ("1h", 3600), ("7d", 604800)],
)
def test_parse_duration_valid(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "180", "15 m", "1hour", "-5m"])
def test_parse_duration_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)


VALID_MANIFEST = {
    "id": "usach.test.widget",
    "version": 1,
    "kind": "timeseries",
    "title": {"es": "Prueba", "en": "Test"},
    "maturity": "experimental",
    "cadence": "60s",
    "stale_after": "15m",
    "producer": {"type": "collector"},
    "source": {"provider": "Test"},
}


def _write(tmp_path, data, name="product.yaml"):
    module_dir = tmp_path / "modules" / "demo" / "manifests"
    module_dir.mkdir(parents=True)
    path = module_dir / name
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_manifest_valid(tmp_path):
    path = _write(tmp_path, VALID_MANIFEST)
    manifest = load_manifest(path)
    assert manifest["id"] == "usach.test.widget"
    assert manifest["_module"] == "demo"
    assert manifest["_path"].endswith("modules/demo/manifests/product.yaml")


@pytest.mark.parametrize(
    ("broken_field", "broken_value"),
    [
        ("id", "NotAValidId"),
        ("kind", "not-a-kind"),
        ("maturity", "very-mature"),
        ("cadence", "soon"),
        ("stale_after", "later"),
    ],
)
def test_load_manifest_invalid_field(tmp_path, broken_field, broken_value):
    data = dict(VALID_MANIFEST)
    data[broken_field] = broken_value
    path = _write(tmp_path, data)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_load_manifest_missing_required_field(tmp_path):
    data = dict(VALID_MANIFEST)
    del data["source"]
    path = _write(tmp_path, data)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_load_manifest_rejects_unknown_field(tmp_path):
    data = dict(VALID_MANIFEST)
    data["unexpected"] = "nope"
    path = _write(tmp_path, data)
    with pytest.raises(ManifestError):
        load_manifest(path)


# 18-HANDOFF-v0.2.0.md §2/§3: the product page's "Qué muestra" (description)
# and "Ficha del producto" (facts) -- optional, validated when present.
def test_load_manifest_with_description_and_facts(tmp_path):
    data = dict(VALID_MANIFEST)
    data["description"] = {"es": "Descripción.", "en": "Description."}
    data["facts"] = {
        "source": {"es": "Proveedor", "en": "Provider"},
        "license": {"es": "Dominio público", "en": "Public domain"},
    }
    path = _write(tmp_path, data)
    manifest = load_manifest(path)
    assert manifest["description"]["en"] == "Description."
    assert manifest["facts"]["license"]["es"] == "Dominio público"


def test_load_manifest_facts_rejects_unknown_key(tmp_path):
    data = dict(VALID_MANIFEST)
    data["facts"] = {"not_a_recognised_fact": {"es": "x", "en": "y"}}
    path = _write(tmp_path, data)
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_load_manifest_description_requires_both_languages(tmp_path):
    data = dict(VALID_MANIFEST)
    data["description"] = {"es": "Solo español"}
    path = _write(tmp_path, data)
    with pytest.raises(ManifestError):
        load_manifest(path)
