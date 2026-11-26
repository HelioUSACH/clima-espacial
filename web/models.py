"""Data/API registration (D-029): name, institution, sector and intended
use, for usage statistics by sector in the project's reporting. Since
v0.5.0 a valid registration also gets an API key (`api.ApiKey`, D-086).

Stores personal data (name, email, institution) — D-051/P6: the form shows
a short privacy notice and requires explicit consent before this can be
submitted; `consent_given` records that the checkbox was ticked (an audit
trail, not just a UI gate)."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class RegistrationRequest(models.Model):
    # 12-HANDOFF item 4: these were plain Spanish strings, so the EN page
    # (/clima-espacial/en/datos-api/) showed Spanish field labels and sector
    # choices regardless of language — gettext_lazy() defers translation to
    # render time, same as every other user-facing string (D-008).
    SECTOR_CHOICES = [
        ("power", _("Operador eléctrico / power grid")),
        ("mining_gnss", _("Minería / GNSS")),
        ("satellites", _("Satélites")),
        ("meteorological", _("Servicio meteorológico / DMC")),
        ("research", _("Investigación / universidad")),
        ("other", _("Otro")),
    ]

    name = models.CharField(_("Nombre"), max_length=200)
    email = models.EmailField(_("Correo"))
    institution = models.CharField(_("Institución"), max_length=200)
    sector = models.CharField(_("Sector"), max_length=32, choices=SECTOR_CHOICES)
    intended_use = models.TextField(_("Uso previsto"), blank=True, default="")
    consent_given = models.BooleanField(
        _("Consentimiento"),
        default=False,
        help_text=_("El usuario aceptó el aviso de privacidad al enviar el formulario."),
    )
    # The page language at registration (D-086): an API key issued later
    # from the admin is emailed in the same language.
    language = models.CharField(max_length=8, default="es", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.institution})"


class FreshnessAlert(models.Model):
    """The stale-product e-mail's memory (v0.6.4, D-119), one row per
    product, written only by `manage.py check_freshness`
    (web/freshness.py). `state` is what the last check saw ("ok", "stale",
    "no_data"); `notified` is the state the recipients were last told
    about (set without a mail on a product's first check, so a deploy never
    mails); `since` is when the current stale spell began."""

    product_id = models.CharField(max_length=128, primary_key=True)
    state = models.CharField(max_length=16)
    notified = models.CharField(max_length=16)
    since = models.DateTimeField(null=True, blank=True)
    last_mail_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField()

    class Meta:
        ordering = ["product_id"]

    def __str__(self):
        return f"{self.product_id}: {self.state} (notified {self.notified})"
