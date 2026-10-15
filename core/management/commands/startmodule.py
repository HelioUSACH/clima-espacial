import re
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "modules" / "_template"
MODULES_DIR = TEMPLATE_DIR.parent
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".txt"}


class Command(BaseCommand):
    help = "Copy modules/_template/ into modules/<name>/, replacing MODULE_NAME with <name>."

    def add_arguments(self, parser):
        parser.add_argument("name", help="lowercase, e.g. 'swpc' or 'magnetometers' (must match ^[a-z][a-z0-9_]*$)")

    def handle(self, *args, **options):
        name = options["name"]
        if not NAME_RE.match(name):
            raise CommandError(f"invalid module name {name!r}: expected {NAME_RE.pattern}")

        target = MODULES_DIR / name
        if target.exists():
            raise CommandError(f"{target} already exists")
        if not TEMPLATE_DIR.exists():
            raise CommandError(f"template not found at {TEMPLATE_DIR}")

        shutil.copytree(TEMPLATE_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".gitkeep"))
        # Re-create empty dirs that ignore_patterns dropped (e.g. tests/fixtures/, which has
        # only a .gitkeep to make an otherwise-empty directory exist in git).
        for empty_dir in TEMPLATE_DIR.rglob("*"):
            if empty_dir.is_dir() and not any(empty_dir.iterdir()):
                relative = empty_dir.relative_to(TEMPLATE_DIR)
                (target / relative).mkdir(parents=True, exist_ok=True)
                (target / relative / ".gitkeep").touch()

        for path in target.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                text = path.read_text()
                path.write_text(text.replace("MODULE_NAME", name))

        self.stdout.write(self.style.SUCCESS(f"Created {target}. Next: read {target / 'README.md'}."))
