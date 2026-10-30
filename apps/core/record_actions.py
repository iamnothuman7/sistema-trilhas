"""Shared confirmation and stale-record checks for management actions."""
import hashlib
import json
from django import forms


def record_version(record, fields=None):
    names=fields or [field.attname for field in record._meta.concrete_fields]
    values={name:str(getattr(record,name) or '') for name in names}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


class RecordConfirmationForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    reason=forms.CharField(label='Motivo',min_length=5,max_length=500,widget=forms.Textarea(attrs={'class':'form-control','rows':3}))
    confirmed=forms.BooleanField(label='Conferi o registro e confirmo esta ação.')
