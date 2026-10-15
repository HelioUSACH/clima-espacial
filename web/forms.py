from django import forms

from .models import RegistrationRequest


class RegistrationForm(forms.ModelForm):
    class Meta:
        model = RegistrationRequest
        fields = ["name", "email", "institution", "sector", "intended_use"]
        widgets = {"intended_use": forms.Textarea(attrs={"rows": 3})}
