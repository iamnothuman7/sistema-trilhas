from django import forms

from .rankings import MISSING_KEYS, STATE_CODES, display_name, group_key, validate_alias_mapping


class RankingAliasForm(forms.Form):
    source_name = forms.CharField(label="Nome informado", max_length=120)
    canonical_name = forms.CharField(label="Exibir como", max_length=120)
    reason = forms.CharField(label="Motivo da correção (opcional)", max_length=300, required=False)

    def __init__(self, *args, event, kind="team", **kwargs):
        super().__init__(*args, **kwargs)
        self.event, self.kind = event, kind
        if kind == "city":
            self.fields["source_name"].label = "Cidade informada"
            self.fields["source_name"].max_length = 100
            self.fields["canonical_name"].max_length = 100
            self.fields["state"] = forms.ChoiceField(label="UF da cidade", choices=[("", "UF não informada")] + [(code, code) for code in STATE_CODES], required=False)
            self.order_fields(["source_name", "state", "canonical_name", "reason"])
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        if kind == "city":
            for name in ("source_name", "canonical_name"):
                self.fields[name].widget.attrs["maxlength"] = 100
        self.fields["source_name"].widget.attrs["list"] = "rankingCities" if kind == "city" else "rankingTeams"

    def clean(self):
        cleaned = super().clean()
        for field in ("source_name", "canonical_name"):
            if field in cleaned:
                cleaned[field] = display_name(cleaned[field])
                if group_key(cleaned[field]) in MISSING_KEYS:
                    self.add_error(field, "Informe um nome real.")
                if self.kind == "city" and len(cleaned[field]) > 100:
                    self.add_error(field, "Use até 100 caracteres para o nome da cidade.")
        if not self.errors:
            validate_alias_mapping(self.event, self.kind, group_key(cleaned["source_name"]), cleaned["canonical_name"], state=cleaned.get("state", ""))
        return cleaned
