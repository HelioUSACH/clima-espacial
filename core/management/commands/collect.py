import importlib

from django.core.management.base import BaseCommand, CommandError

from core.manifests.loader import ManifestError, get_manifest


class Command(BaseCommand):
    help = (
        "Run a module's collector for one product: manage.py collect <product_id>. "
        "Looks up the product's manifest to find which module owns it, then calls "
        "modules.<module>.collectors.COLLECTORS[<product_id>]()."
    )

    def add_arguments(self, parser):
        parser.add_argument("product_id")

    def handle(self, *args, **options):
        product_id = options["product_id"]
        try:
            manifest = get_manifest(product_id)
        except ManifestError as exc:
            raise CommandError(str(exc)) from exc

        module_name = manifest["_module"]
        dotted = f"modules.{module_name}.collectors"
        try:
            collectors = importlib.import_module(dotted)
        except ImportError as exc:
            raise CommandError(f"{dotted} could not be imported: {exc}") from exc

        try:
            collect_fn = collectors.COLLECTORS[product_id]
        except AttributeError:
            raise CommandError(f"{dotted} has no COLLECTORS mapping") from None
        except KeyError:
            available = ", ".join(sorted(collectors.COLLECTORS)) or "(none)"
            raise CommandError(f"{dotted}.COLLECTORS has no entry for {product_id!r}. Available: {available}") from None

        self.stdout.write(f"collecting {product_id} …")
        n = collect_fn()
        self.stdout.write(self.style.SUCCESS(f"collected {product_id}" + (f" ({n} point(s))" if n is not None else "")))
