"""M1 ships the data/API **registration stub**, not the full API (D-029):
this captures interest and basic sector info for FONDEF/FIU reporting. It
does not issue API keys yet — that is M2 work alongside the HAPI endpoint
(docs/DECISIONS.md D-048)."""

from django.db import models


class RegistrationRequest(models.Model):
    SECTOR_CHOICES = [
        ("power", "Operador eléctrico / power grid"),
        ("mining_gnss", "Minería / GNSS"),
        ("satellites", "Satélites"),
        ("meteorological", "Servicio meteorológico / DMC"),
        ("research", "Investigación / universidad"),
        ("other", "Otro"),
    ]

    name = models.CharField("Nombre", max_length=200)
    email = models.EmailField("Correo")
    institution = models.CharField("Institución", max_length=200)
    sector = models.CharField("Sector", max_length=32, choices=SECTOR_CHOICES)
    intended_use = models.TextField("Uso previsto", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.institution})"
