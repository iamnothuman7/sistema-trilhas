from datetime import date
from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator


MIN_REPORT_DATE = date(1900, 1, 1)
MAX_REPORT_DATE = date(2199, 12, 31)
DATE_RANGE_MESSAGE = "Informe uma data entre 01/01/1900 e 31/12/2199."
REPORT_DATE_VALIDATORS = [MinValueValidator(MIN_REPORT_DATE, message=DATE_RANGE_MESSAGE),
                          MaxValueValidator(MAX_REPORT_DATE, message=DATE_RANGE_MESSAGE)]


class DateRangeForm(forms.Form):
    date_start = forms.DateField(label="De", required=False, input_formats=["%Y-%m-%d"], validators=REPORT_DATE_VALIDATORS,
                                widget=forms.DateInput(attrs={"type": "date", "class": "form-control", "min": MIN_REPORT_DATE.isoformat(), "max": MAX_REPORT_DATE.isoformat()}))
    date_end = forms.DateField(label="Até", required=False, input_formats=["%Y-%m-%d"], validators=REPORT_DATE_VALIDATORS,
                              widget=forms.DateInput(attrs={"type": "date", "class": "form-control", "min": MIN_REPORT_DATE.isoformat(), "max": MAX_REPORT_DATE.isoformat()}))

    def clean(self):
        data = super().clean()
        if data.get("date_start") and data.get("date_end") and data["date_start"] > data["date_end"]:
            raise forms.ValidationError("A data inicial deve ser anterior ou igual à data final.")
        return data
