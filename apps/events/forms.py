from django import forms
from django.core.exceptions import ValidationError

from .models import Sponsor
from .validators import validate_sponsor_logo_size


class SponsorLogoField(forms.ImageField):
    def to_python(self, data):
        if data not in (None, False):
            validate_sponsor_logo_size(data)
        upload = super().to_python(data)
        if upload and upload.image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValidationError("Envie uma imagem PNG, JPG ou WebP.")
        return upload


class SponsorForm(forms.ModelForm):
    logo_image = SponsorLogoField(
        label="Logo ou foto", required=False,
        help_text="PNG, JPG ou WebP, até 5 MB. Sem imagem, a página mostra um espaço reservado para esta marca.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp", "class": "form-control"}),
    )

    class Meta:
        model = Sponsor
        fields = ("name", "tier", "logo_image", "instagram_url", "website", "sort_order")
        labels = {"name": "Nome da marca", "tier": "Cota", "website": "Site", "sort_order": "Ordem de exibição"}
        help_texts = {
            "instagram_url": "Use o endereço completo do perfil: https://www.instagram.com/suamarca/.",
            "website": "Opcional. Endereço do site do patrocinador.",
            "sort_order": "Números menores aparecem primeiro.",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Nome do patrocinador"}),
            "instagram_url": forms.URLInput(attrs={"placeholder": "https://www.instagram.com/suamarca/"}),
            "website": forms.URLInput(attrs={"placeholder": "https://www.suamarca.com.br"}),
            "sort_order": forms.NumberInput(attrs={"min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "form-control")
            if field.help_text:
                field.widget.attrs["aria-describedby"] = f"id_{name}_helptext"


class SponsorAdminForm(SponsorForm):
    class Meta(SponsorForm.Meta):
        fields = ("event", *SponsorForm.Meta.fields, "approved_at")
