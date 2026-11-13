"""Times as the site prints them (D-105): UT text inside a `<time>` element.

Every time rendered server-side goes through `time_html` (or the `ut_time`
template filter, `web/templatetags/swx_time.py`):

    <time datetime="2026-11-12T21:34:00Z" data-ut="dt">12/11 21:34 UT</time>

The text is what the page always showed (UT, 24 h, dd/mm in ES and
"12 Nov" in EN), so with JavaScript off nothing changes. `localtime.js`
reads `datetime` and adds the visitor's own local time next to it, in the
browser's time zone; nothing about the visitor is sent anywhere. `data-ut`
says which parts the UT text has, so the local text can match it:
`dt` date and time, `t` time only, `ts` time with seconds, `dts` date and
time with seconds. `data-stack` (a table cell, D-108) puts the local
time on a second line, small and muted, without the parentheses.
"""

from __future__ import annotations

import datetime as dt

from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import get_language

MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
FORMATS = {"dt": (True, False), "t": (False, False), "ts": (False, True), "dts": (True, True)}


def current_lang() -> str:
    return "en" if get_language() == "en" else "es"


def as_utc(when: dt.datetime) -> dt.datetime:
    return (when if when.tzinfo else when.replace(tzinfo=dt.UTC)).astimezone(dt.UTC)


def iso_z(when: dt.datetime) -> str:
    """'2026-11-12T21:34:00Z' (seconds, no fraction)."""
    return as_utc(when).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_key(*, date: bool = True, seconds: bool = False) -> str:
    return ("d" if date else "") + ("ts" if seconds else "t")


def ut_text(when: dt.datetime, lang: str, *, date: bool = True, seconds: bool = False) -> str:
    """'12/11 21:34' (ES) / '12 Nov 21:34' (EN), UT, without the ' UT'."""
    when = as_utc(when)
    clock = f"{when:%H:%M:%S}" if seconds else f"{when:%H:%M}"
    if not date:
        return clock
    day = f"{when.day:02d} {MONTHS_EN[when.month - 1]}" if lang == "en" else f"{when:%d/%m}"
    return f"{day} {clock}"


def time_html(when: dt.datetime | None, lang: str | None = None, *, date: bool = True,
              seconds: bool = False, stacked: bool = False) -> SafeString | str:
    """`<time datetime="…Z" data-ut="…">… UT</time>`, or "" for None.
    `stacked`: the local time goes on a second line (`data-stack`)."""
    if when is None:
        return ""
    lang = lang or current_lang()
    return format_html('<time datetime="{}" data-ut="{}"{}>{} UT</time>', iso_z(when),
                       fmt_key(date=date, seconds=seconds), mark_safe(" data-stack") if stacked else "",
                       ut_text(when, lang, date=date, seconds=seconds))
