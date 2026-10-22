from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def money(cents):
    """Format integer cents without float conversion or locale dependence."""
    try:
        value = Decimal(str(cents or 0)) / Decimal(100)
        return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (InvalidOperation, TypeError, ValueError):
        return "0,00"


@register.filter
def percentage(value, total):
    try:
        return max(0, min(100, round(Decimal(str(value)) * 100 / Decimal(str(total)))))
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def subtract(value, other):
    try:
        return int(value or 0) - int(other or 0)
    except (TypeError, ValueError):
        return 0
