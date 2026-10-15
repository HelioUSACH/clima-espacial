from pathlib import Path

from core.manifests.loader import load_manifest

MANIFEST = Path(__file__).resolve().parent.parent / "manifests" / "example.yaml"


def test_manifest_is_valid():
    manifest = load_manifest(MANIFEST)
    assert manifest["id"] == "usach.MODULE_NAME.example"


# Add a real test once collectors.py fetches a live source: load a recorded
# fixture from tests/fixtures/, run the adapter against it, assert the shape
# matches the manifest's parameters (see modules/swpc/tests/ for examples).
