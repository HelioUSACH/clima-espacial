from django import forms
from django.utils.translation import gettext_lazy as _

from .models import RegistrationRequest


class RegistrationForm(forms.ModelForm):
    consent_given = forms.BooleanField(
        label=_("He leído el aviso de privacidad y acepto que mis datos se usen para este fin."),
        required=True,
        error_messages={"required": _("Debes aceptar el aviso de privacidad para continuar.")},
    )
    # Honeypot (D-086): hidden from people by CSS, filled in by naive bots.
    # A registration with it set is dropped without saving anything.
    website = forms.CharField(required=False, label=_("Deja este campo vacío"),
                              widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}))

    class Meta:
        model = RegistrationRequest
        fields = ["name", "email", "institution", "sector", "intended_use", "consent_given"]
        widgets = {"intended_use": forms.Textarea(attrs={"rows": 3})}
