"""D-071: `kind: forecast` and its manifest block -- the schema, and the
cross-manifest target check `validate_manifests` runs."""

import copy

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from core.manifests import loader
from core.manifests.loader import ManifestError, forecast_target_errors, load_manifest

FORECAST = {
    "id": "usach.test.kpmodel",
    "version": 1,
    "kind": "forecast",
    "title": {"es": "Modelo", "en": "Model"},
    "maturity": "experimental",
    "cadence": "1h",
    "stale_after": "3h",
    "producer": {"type": "collector"},
    "source": {"provider": "Test"},
    "model_card": "docs/products/usach.test.kpmodel.md",
    "forecast": {
        "target": {"product": "noaa.kp", "field": "kp"},
        "horizons": ["3h", "6h"],
        "uncertainty": {"type": "bounds", "level": 0.8},
        "sites": False,
    },
}
KP = {"id": "noaa.kp", "kind": "index", "parameters": [{"name": "kp"}, {"name": "a_running"}]}


def _write(tmp_path, data, name="product.yaml"):
    module_dir = tmp_path / "modules" / "demo" / "manifests"
    module_dir.mkdir(parents=True, exist_ok=True)
    path = module_dir / name
    path.write_text(yaml.safe_dump(data))
    return path


def _variant(**changes):
    data = copy.deepcopy(FORECAST)
    for dotted, value in changes.items():
        node, *path = dotted.split("__")
        target = data
        keys = [node, *path]
        for key in keys[:-1]:
            target = target[key]
        if value is _DELETE:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return data


_DELETE = object()


def test_valid_forecast_manifest(tmp_path):
    manifest = load_manifest(_write(tmp_path, FORECAST))
    assert manifest["kind"] == "forecast"
    assert manifest["forecast"]["uncertainty"] == {"type": "bounds", "level": 0.8}


@pytest.mark.parametrize("changes, fragment", [
    ({"forecast": _DELETE}, "'forecast' is a required property"),
    ({"model_card": _DELETE}, "'model_card' is a required property"),
    ({"forecast__horizons": []}, "should be non-empty"),
    ({"forecast__horizons": ["3 hours"]}, "does not match"),
    ({"forecast__uncertainty": {"type": "bounds"}}, "'level' is a required property"),
    ({"forecast__uncertainty": {"type": "ensemble"}}, "is not one of"),
    ({"forecast__sites": "no"}, "is not of type 'boolean'"),
    ({"forecast__issue_cadence": "1h"}, "Additional properties are not allowed"),
    ({"forecast__target": {"product": "noaa.kp"}}, "'field' is a required property"),
])
def test_invalid_forecast_block(tmp_path, changes, fragment):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, _variant(**changes)))
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_forecast_block_is_forbidden_on_other_kinds(tmp_path):
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, _variant(kind="timeseries")))
    assert any("should not be valid" in e for e in exc.value.errors), exc.value.errors


def test_quantiles_is_reserved_but_accepted_by_the_schema(tmp_path):
    manifest = load_manifest(_write(tmp_path, _variant(forecast__uncertainty={"type": "quantiles"})))
    assert manifest["forecast"]["uncertainty"]["type"] == "quantiles"


def test_none_uncertainty_needs_no_level(tmp_path):
    load_manifest(_write(tmp_path, _variant(forecast__uncertainty={"type": "none"})))


@pytest.mark.parametrize("target, others, fragment", [
    ({"product": "noaa.kp", "field": "kp"}, {"noaa.kp": KP}, None),
    ({"product": "noaa.nope", "field": "kp"}, {"noaa.kp": KP}, "has no manifest"),
    ({"product": "noaa.kp", "field": "dst"}, {"noaa.kp": KP}, "is not a parameter"),
    ({"product": "noaa.scales", "field": "kp"}, {"noaa.scales": dict(KP, id="noaa.scales", kind="event")}, "is kind 'event'"),
])
def test_cross_manifest_target_check(target, others, fragment):
    manifest = _variant(forecast__target=target)
    errors = forecast_target_errors(manifest, others)
    if fragment is None:
        assert errors == []
    else:
        assert any(fragment in e for e in errors), errors


def test_cross_check_ignores_other_kinds():
    assert forecast_target_errors({"kind": "timeseries"}, {}) == []


def test_validate_manifests_reports_a_bad_target(tmp_path, monkeypatch):
    _write(tmp_path, _variant(forecast__target={"product": "noaa.nope", "field": "kp"}))
    monkeypatch.setattr(loader, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        "core.management.commands.validate_manifests.discover_manifest_paths",
        lambda: loader.discover_manifest_paths(tmp_path),
    )
    with pytest.raises(CommandError):
        call_command("validate_manifests")


def test_the_repos_own_manifests_pass_the_cross_check():
    manifests = loader.discover_manifests()
    assert manifests["usach.kp.persistence"]["kind"] == "forecast"
    for manifest in manifests.values():
        assert forecast_target_errors(manifest, manifests) == []
