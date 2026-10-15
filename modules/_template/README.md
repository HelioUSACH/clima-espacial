# modules/MODULE_NAME

Copy of `modules/_template/`, made by `manage.py startmodule MODULE_NAME`.

A module may add manifests, collectors/services, templates for its own
product cards, and tests with recorded fixtures. It may **not** edit `core/`,
another module, `climaespacial/` (settings) or `deploy/` — see
`docs/modules/README.md` for the full contract.

## Checklist

1. Edit `manifests/example.yaml` (rename the file to match the product):
   set `id`, `title`, `cadence`/`stale_after`, `source`, `parameters`.
2. Implement a real collector in `collectors.py` and register it in
   `COLLECTORS` under the manifest's `id`.
3. Record a real fixture of the provider's payload under
   `tests/fixtures/` and write a test against it (`tests/test_example.py`
   is a starting point).
4. `python manage.py validate_manifests` must pass.
5. `python manage.py collect <product_id>` should run end to end against a
   local database.
