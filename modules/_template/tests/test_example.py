from pathlib import Path

import pytest

from core.manifests.loader import load_manifest

MANIFEST = Path(__file__).resolve().parent.parent / "manifests" / "example.yaml"


def test_manifest_is_valid():
    # This file is both the skeleton itself (where "MODULE_NAME" is still a
    # literal placeholder — invalid as a product id on purpose, and excluded
    # from discover_manifests() the same way) and, after `manage.py
    # startmodule <name>`, a real module's test with every MODULE_NAME
    # already replaced. Only skip the former.
    if "MODULE_NAME" in MANIFEST.read_text():
        pytest.skip("modules/_template is the skeleton, not a real module — nothing to validate yet")
    manifest = load_manifest(MANIFEST)
    assert manifest["id"] == "usach.MODULE_NAME.example"


# Add a real test once collectors.py fetches a live source: load a recorded
# fixture from tests/fixtures/, run the adapter against it, assert the shape
# matches the manifest's parameters (see modules/swpc/tests/ for examples).
