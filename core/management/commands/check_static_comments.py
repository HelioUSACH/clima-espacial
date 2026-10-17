"""Catches a v0.2.0 production bug at CI time instead of in a browser: a
`/* ... */` CSS/JS comment closes at its *first* `*/`, so a stray `*/`
written as ordinary prose inside a multi-line comment block (here:
"--chart-*/--status-*" in a sentence) ends the comment early. Everything
from there to the *next* `*/` becomes literal CSS/JS instead of being
stripped — in base.css this silently dropped the entire base `:root` token
block, so every chart var resolved empty (found interactively, a real
browser against real seeded data, not just reasoned about: Kp bars and
every reference line rendered colourless, `getComputedStyle` returned "").

Detection: a `/* ... */` span (matched non-greedily to its first `*/`) that
opens and closes on *different* lines, where the line right after the
closing `*/` still starts with a bare `*` (a comment-continuation marker,
`web/static/.../x.js` style). Legitimate code is never written starting
with a bare `*` — there's no false-positive case this would catch by
accident, same reasoning as `check_templates`."""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
CONTINUATION_RE = re.compile(r"^\s*\*")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def find_premature_closes(static_files):
    """[(path, line_number), ...] for every /* */ comment that closes
    early — multi-line, with a bare-`*` continuation line right after the
    close. Pure function, no filesystem globbing, so it's testable against
    a crafted file list directly."""
    failures = []
    for path in static_files:
        text = Path(path).read_text(encoding="utf-8")
        lines = text.split("\n")
        for match in COMMENT_RE.finditer(text):
            start_line = text.count("\n", 0, match.start())
            end_line = text.count("\n", 0, match.end())
            if start_line == end_line:
                continue  # a one-line comment can't close "early"
            if end_line + 1 < len(lines) and CONTINUATION_RE.match(lines[end_line + 1]):
                failures.append((path, start_line + 1))
    return failures


class Command(BaseCommand):
    help = "Fail if any vendored-adjacent CSS/JS file has a /* */ comment that closes early."

    def handle(self, *args, **options):
        static_files = sorted(REPO_ROOT.glob("*/static/**/*.css")) + sorted(REPO_ROOT.glob("*/static/**/*.js"))
        static_files = [p for p in static_files if "/vendor/" not in str(p)]
        if not static_files:
            self.stdout.write(self.style.WARNING("No static CSS/JS files found."))
            return

        failures = [
            f"{Path(path).relative_to(REPO_ROOT)}:{line}" for path, line in find_premature_closes(static_files)
        ]

        if failures:
            joined = "\n  ".join(failures)
            raise CommandError(f"Comment closes early (a literal */ inside /* */) in:\n  {joined}")
        self.stdout.write(self.style.SUCCESS(f"{len(static_files)} static file(s) checked, no early comment closes found."))
