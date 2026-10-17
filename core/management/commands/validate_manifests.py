from django.core.management.base import BaseCommand, CommandError

from core.manifests.loader import (
    ManifestError,
    discover_manifest_paths,
    forecast_target_errors,
    load_manifest,
)


class Command(BaseCommand):
    help = "Validate every product manifest under modules/*/manifests/*.yaml against core/manifests/schema.json."

    def handle(self, *args, **options):
        paths = discover_manifest_paths()
        if not paths:
            self.stdout.write(self.style.WARNING("No manifests found under modules/*/manifests/."))
            return

        seen_ids: dict[str, str] = {}
        loaded: dict[str, dict] = {}
        failures = 0
        for path in paths:
            try:
                manifest = load_manifest(path)
            except ManifestError as exc:
                failures += 1
                self.stderr.write(self.style.ERROR(f"FAIL {path}"))
                for error in exc.errors:
                    self.stderr.write(f"       {error}")
                continue

            product_id = manifest["id"]
            if product_id in seen_ids:
                failures += 1
                self.stderr.write(
                    self.style.ERROR(f"FAIL {path}: duplicate product id {product_id!r} (also in {seen_ids[product_id]})")
                )
                continue
            seen_ids[product_id] = str(path)
            loaded[product_id] = manifest
            self.stdout.write(self.style.SUCCESS(f"ok   {path}  ({product_id} v{manifest['version']})"))

        # Across manifests (D-071): a forecast's target must exist and be observed.
        for manifest in loaded.values():
            for error in forecast_target_errors(manifest, loaded):
                failures += 1
                self.stderr.write(self.style.ERROR(f"FAIL {manifest['_path']}: {error}"))

        if failures:
            raise CommandError(f"{failures} manifest(s) failed validation.")
        self.stdout.write(self.style.SUCCESS(f"{len(seen_ids)} manifest(s) valid."))
