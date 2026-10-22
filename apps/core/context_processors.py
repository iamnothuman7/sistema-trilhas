from datetime import datetime
from apps.core.models import Organization

def global_context(request):
    """
    Context processor global para templates Django.
    Injeta a organização padrão, ano vigente e permissões básicas.
    """
    org = Organization.objects.first()
    if not org:
        # Cria organização padrão caso ainda não exista
        org = Organization(
            name="Equipe Morcego",
            city="Tianguá",
            state="CE",
            support_email="contato@equipemorcego.com.br",
            phone="(88) 99999-9999"
        )

    user_memberships = []
    if request.user.is_authenticated:
        from apps.identity.models import EventMembership
        user_memberships = EventMembership.objects.filter(
            user=request.user,
            revoked_at__isnull=True
        ).select_related("event")

    return {
        "organization": org,
        "current_year": datetime.now().year,
        "user_memberships": user_memberships,
        "current_event": getattr(request, "current_event", None),
        "is_event_subdomain": getattr(request, "is_event_subdomain", False),
    }
