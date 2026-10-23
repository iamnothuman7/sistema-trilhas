"""Read-only eligibility shared by participant documents and event operations."""
from apps.checkout.models import OrderStatus
from apps.events.models import EventStatus
from .models import CommercialState


def eligibility_reason(registration):
    """Empty means eligible. Never issue, rotate or restore credentials in a read."""
    reg = registration
    if reg.event.status in (EventStatus.CANCELED, EventStatus.ARCHIVED):
        return "O evento está cancelado ou arquivado e não aceita credenciamento ou retirada."
    if reg.commercial_state != CommercialState.ACTIVE:
        return "Esta inscrição não está ativa."
    if reg.source_order_line.order.status != OrderStatus.PAID:
        return "O pedido não possui pagamento confirmado no estado atual."
    from apps.payments.services import required_documents_for_registration
    documents = {document.pk for document in required_documents_for_registration(reg)}
    accepted = set(reg.terms_acceptances.filter(
        accepting_user__person_links__person_id=reg.person_id,
    ).values_list("document_version_id", flat=True))
    if not documents or not documents <= accepted:
        return "O titular precisa aceitar todos os documentos vigentes na área do participante."
    if not reg.is_eligible_for_qr():
        return "A inscrição ainda não está liberada para credenciamento."
    credential = getattr(reg, "credential", None)
    if not credential:
        return "A credencial ainda não foi emitida. Consulte a organização."
    if not credential.is_valid:
        return "A credencial foi revogada ou expirou. Consulte a organização."
    return ""


def credential_is_available(registration):
    return not eligibility_reason(registration)
