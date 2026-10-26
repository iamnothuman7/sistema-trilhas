"""Canonical participant CPF validation (format and check digits, not identity proof)."""
import re

from django.core.exceptions import ValidationError


def normalize_cpf(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[0-9.\-\s]+", value):
        raise ValidationError("Informe um CPF válido com 11 números.")
    cpf = re.sub(r"[^0-9]", "", value)
    if len(cpf) != 11 or len(set(cpf)) == 1:
        raise ValidationError("Informe um CPF válido com 11 números.")
    for length in (9, 10):
        digit = (sum(int(cpf[i]) * (length + 1 - i) for i in range(length)) * 10) % 11
        if int(cpf[length]) != (0 if digit == 10 else digit):
            raise ValidationError("Informe um CPF válido; confira os números digitados.")
    return cpf
