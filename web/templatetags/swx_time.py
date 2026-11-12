"""`{% load swx_time %}`: `{{ when|ut_time }}` renders a datetime as the
site's `<time>` element with its UT text (web/timefmt.py, D-105).

    {{ when|ut_time }}          12/11 21:34 UT      (date and time)
    {{ when|ut_time:"t" }}      21:34 UT            (time only)
    {{ when|ut_time:"ts" }}     21:34:10 UT         (time with seconds)
    {{ when|ut_time:"dts" }}    12/11 21:34:10 UT

None (or anything that isn't a datetime) renders as "", so
`{{ when|ut_time|default:"—" }}` works.
"""

import datetime as dt

from django import template

from web import timefmt

register = template.Library()


@register.filter(is_safe=True)
def ut_time(value, fmt="dt"):
    if not isinstance(value, dt.datetime):
        return ""
    date, seconds = timefmt.FORMATS.get(fmt, timefmt.FORMATS["dt"])
    return timefmt.time_html(value, date=date, seconds=seconds)
