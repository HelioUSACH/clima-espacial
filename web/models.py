"""M1 ships the data/API **registration stub**, not the full API (D-029):
this captures interest and basic sector info for FONDEF/FIU reporting. It
does not issue API keys yet — that is M2 work alongside the HAPI endpoint
(docs/DECISIONS.md D-048).

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.institution})"
