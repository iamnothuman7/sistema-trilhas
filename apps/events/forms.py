from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from PIL import Image

from .models import Sponsor
from .validators import validate_sponsor_logo_dimensions, validate_sponsor_logo_size
from .sponsor_images import image_references, make_sponsor_preview, sponsor_upload_transaction, track_sponsor_upload


class SponsorLogoField(forms.ImageField):
    def to_python(self, data):
        if data not in (None, False):
            validate_sponsor_logo_size(data)
        upload = super().to_python(data)
        if upload and upload.image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValidationError("Envie uma imagem PNG, JPG ou WebP.")
        if upload:
            validate_sponsor_logo_dimensions(*upload.image.size)
        return upload


class SponsorForm(forms.ModelForm):
    logo_image = SponsorLogoField(
        label="Logo ou foto", required=False,
        help_text="PNG, JPG ou WebP, até 15 MB. Sem imagem, a página mostra um espaço reservado para esta marca.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp", "class": "form-control"}),
    )

    class Meta:
        model = Sponsor
        fields = ("name", "tier", "logo_image", "instagram_url", "website", "sort_order", "is_public")
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
        self._previous_images = image_references(self.instance)
        self._new_preview = None
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "form-control")
            if field.help_text:
                field.widget.attrs["aria-describedby"] = f"id_{name}_helptext"

    def clean_logo_image(self):
        upload = self.cleaned_data["logo_image"]
        if isinstance(upload, UploadedFile):
            try:
                # Prepare only submitted uploads, in memory; no storage writes
                # occur until the validated form is saved.
                self._new_preview = make_sponsor_preview(upload)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ValidationError("Não foi possível preparar a imagem. Envie outra imagem PNG, JPG ou WebP.") from exc
        return upload

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self._new_preview is not None:
            instance.logo_preview, instance.logo_preview_width, instance.logo_preview_height = self._new_preview
        elif self.cleaned_data.get("logo_image") is False:
            instance.logo_preview = ""
            instance.logo_preview_width = instance.logo_preview_height = None
        if commit:
            with sponsor_upload_transaction():
                if self._new_preview is not None:
                    track_sponsor_upload(instance, self._previous_images)
                instance.save()
                self.save_m2m()
        elif self._new_preview is not None:
            track_sponsor_upload(instance, self._previous_images)
        return instance


class SponsorAdminForm(SponsorForm):
    class Meta(SponsorForm.Meta):
        fields = ("event", *SponsorForm.Meta.fields, "approved_at")
