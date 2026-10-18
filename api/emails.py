"""Optional delivery of a new API key by email (D-086).

Only when SMTP is configured (`SWX_EMAIL_HOST`, settings.SWX_EMAIL_ENABLED).
The key is shown on screen first either way; a send that fails is logged
and reported on the page, never raised, so it can't cost the user the key.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext

from . import export

logger = logging.getLogger(__name__)

SENT, FAILED, OFF = "sent", "failed", "off"


def quickstart(request, key: str) -> dict:
    """Ready-to-paste examples with the user's key (thanks page, email)."""
    datasets = export.datasets()
    ds = next((d for d in datasets if d.id == "noaa.rtsw.mag"), datasets[0] if datasets else None)
    dataset_id = ds.id if ds else "DATASET"
    stop = timezone.now().astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = stop - dt.timedelta(days=7)
    start_s, stop_s = export.fmt_time(start), export.fmt_time(stop)
    with translation.override("es"):
        hapi_base = request.build_absolute_uri(reverse("hapi:landing"))
        csv_url = request.build_absolute_uri(reverse("api:download_csv", args=[dataset_id]))
    hapi_data = f"{hapi_base}/data?dataset={dataset_id}&start={start_s}&stop={stop_s}"
    return {
        "dataset": dataset_id,
        "hapi_base": hapi_base,
        "curl": f'curl -H "X-API-Key: {key}" -o {dataset_id}.csv "{csv_url}?start={start_s}&stop={stop_s}"',
        "hapi_url": f"{hapi_data}&api_key={key}",
        "python": (
            "import io\n"
            "import pandas as pd\n"
            "import requests\n\n"
            f'r = requests.get("{hapi_base}/data",\n'
            f'                 params={{"dataset": "{dataset_id}", "start": "{start_s}", "stop": "{stop_s}"}},\n'
            f'                 headers={{"X-API-Key": "{key}"}}, timeout=60)\n'
            "r.raise_for_status()\n"
            "df = pd.read_csv(io.StringIO(r.text), header=None)\n"
        ),
    }


def send_key_email(request, *, to: str, name: str, key: str, lang: str) -> str:
    """SENT, FAILED (logged) or OFF (no SMTP configured: nothing tried)."""
    if not settings.SWX_EMAIL_ENABLED:
        return OFF
    with translation.override(lang):
        context = {"name": name, "key": key, "qs": quickstart(request, key),
                   "data_api_url": request.build_absolute_uri(reverse("web:data_api"))}
        subject = gettext("Tu llave de API de Clima Espacial USACH")
        body = render_to_string("api/email_key.txt", context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
    except Exception:  # SMTP down, auth, timeout (EMAIL_TIMEOUT): the page still shows the key
        logger.exception("Could not send the API key email")
        return FAILED
    return SENT
