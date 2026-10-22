from django import forms


class DateRangeForm(forms.Form):
    date_start = forms.DateField(label="De", required=False, input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    date_end = forms.DateField(label="Até", required=False, input_formats=["%Y-%m-%d"], widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def clean(self):
        data = super().clean()
        if data.get("date_start") and data.get("date_end") and data["date_start"] > data["date_end"]:
            raise forms.ValidationError("A data inicial deve ser anterior ou igual à data final.")
        return data
