"""Catches the v0.1.1 production bug (10-STATUS-AND-NEXT.md §2) at CI time
instead of in a browser: Django's `{# ... #}` comment tag only works on a
single line — written across a line break, it is printed as literal page
text instead of being stripped, which previously broke every chart (the
leaked text contained a <script> tag that swallowed the real uPlot include).

There is never a valid reason for a `{# #}` span to cross a newline (use
`{% comment %}...{% endcomment %}` for a multi-line comment instead), so
this check has no false-positive case to guard against.
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def find_multiline_comments(template_files):
    """[(relative-or-given-path, line_number), ...] for every {# #} span
    that crosses a newline. Pure function, no filesystem globbing, so it's
    testable against a crafted file list directly."""
    failures = []
    for path in template_files:
        text = Path(path).read_text(encoding="utf-8")
        for match in COMMENT_RE.finditer(text):
            if "\n" in match.group():
                line_number = text.count("\n", 0, match.start()) + 1
                failures.append((path, line_number))
    return failures


class Command(BaseCommand):
    help = "Fail if any template has a {# #} comment spanning more than one line."

    def handle(self, *args, **options):
        template_files = sorted(REPO_ROOT.glob("*/templates/**/*.html"))
        if not template_files:
            self.stdout.write(self.style.WARNING("No template files found."))
            return

        failures = [
            f"{Path(path).relative_to(REPO_ROOT)}:{line}"
            for path, line in find_multiline_comments(template_files)
        ]

        if failures:
            joined = "\n  ".join(failures)
            raise CommandError(
                f"Multi-line {{# #}} comment(s) found (use {{% comment %}}...{{% endcomment %}} instead):\n  {joined}"
            )
        self.stdout.write(self.style.SUCCESS(f"{len(template_files)} template(s) checked, no multi-line {{# #}} found."))
