import hashlib
import json
from decimal import Decimal
from django.utils import timezone

def cents_to_reais(cents: int) -> str:
    """Converte centavos inteiros (ex: 18000) para '180,00'."""
    if cents is None:
        return "0,00"
    val = Decimal(cents) / Decimal(100)
    return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def cents_to_decimal(cents: int) -> Decimal:
    """Retorna Decimal para operações com gateway."""
    if not cents:
        return Decimal("0.00")
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))

def sha256_text(text: str) -> str:
    """Retorna o hash SHA-256 de um texto."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def escape_csv_cell(value: str) -> str:
    """
    Previne CSV Formula Injection (prefixos =, +, -, @, tab, cr).
    """
    if not value:
        return ""
    str_val = str(value)
    if str_val.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + str_val
    return str_val

def log_audit(actor=None, event_id=None, action="", entity_type="", entity_id="", diff=None, reason="", request=None):
    """
    Registra lançamento imutável na tabela de auditoria.
    """
    from apps.core.models import AuditLog
    actor_id = None
    actor_email = None
    ip_addr = None

    if request:
        if hasattr(request, "user") and request.user.is_authenticated:
            actor_id = str(request.user.id)
            actor_email = request.user.email
        ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR"))
        if ip_addr and "," in ip_addr:
            ip_addr = ip_addr.split(",")[0].strip()
    if actor_id is None and actor:
        actor_id = str(getattr(actor, "id", actor))
        actor_email = getattr(actor, "email", None)

    AuditLog.objects.create(
        actor_id=actor_id,
        actor_email=actor_email,
        event_id=event_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        diff_json=diff or {},
        reason=reason,
        ip_address=ip_addr,
        created_at=timezone.now(),
    )
