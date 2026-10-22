import hashlib
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import (
    Registration, CommercialState, OperationalState,
    TermsAcceptance, RegistrationCredential
)
from apps.events.models import LegalDocumentVersion, DocumentKind
from apps.identity.models import ClaimToken, UserPersonLink

@login_required
def dashboard(request):
    """
    Área do Piloto / Participante.
    Exibe todas as inscrições compradas pelo usuário ou vinculadas à sua pessoa física.
    """
    user = request.user
    linked_person_ids = UserPersonLink.objects.filter(user=user).values_list("person_id", flat=True)

    registrations = Registration.objects.filter(
        models_q := (
            Registration.objects.filter(source_order_line__order__purchaser_user=user) |
            Registration.objects.filter(person_id__in=linked_person_ids) |
            Registration.objects.filter(person__email=user.email)
        )
    ).distinct().select_related("event", "event__theme", "person").order_by("-event__starts_at")

    return render(request, "registrations/minha_conta_dashboard.html", {
        "registrations": registrations,
    })


@login_required
def registration_detail(request, registration_id):
    """
    Detalhes da inscrição, direitos inclusos, situação dos termos e QR Code.
    """
    reg = get_object_or_404(
        Registration.objects.select_related("event", "event__theme", "person", "credential"),
        id=registration_id
    )

    entitlements = reg.entitlements.all()
    terms_accepted = reg.terms_acceptances.all()

    # Documentos que precisam de aceite
    required_docs = LegalDocumentVersion.objects.filter(
        event=reg.event,
        kind__in=[DocumentKind.REGULAMENTO, DocumentKind.TERMO_RESPONSABILIDADE]
    )

    all_accepted = True
    accepted_doc_ids = set(terms_accepted.values_list("document_version_id", flat=True))
    for doc in required_docs:
        if doc.id not in accepted_doc_ids:
            all_accepted = False
            break

    # Se todos aceitos e estava NOT_READY, atualiza para READY
    if all_accepted and reg.commercial_state == CommercialState.ACTIVE and reg.operational_state == OperationalState.NOT_READY:
        reg.operational_state = OperationalState.READY
        reg.save()

    return render(request, "registrations/registration_detail.html", {
        "reg": reg,
        "entitlements": entitlements,
        "required_docs": required_docs,
        "accepted_doc_ids": accepted_doc_ids,
        "all_accepted": all_accepted,
    })


@login_required
def accept_terms(request, registration_id):
    """
    Registra o aceite pessoal dos termos e regulamento com hash de evidência e IP.
    """
    reg = get_object_or_404(Registration.objects.select_related("event"), id=registration_id)

    if request.method == "POST":
        doc_ids = request.POST.getlist("document_version_id")
        ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR"))
        ua = request.META.get("HTTP_USER_AGENT", "")

        for doc_id in doc_ids:
            doc = LegalDocumentVersion.objects.filter(id=doc_id, event=reg.event).first()
            if doc:
                evidence = hashlib.sha256(
                    f"{reg.id}:{doc.sha256}:{request.user.id}:{ip}".encode("utf-8")
                ).hexdigest()

                TermsAcceptance.objects.get_or_create(
                    registration=reg,
                    document_version=doc,
                    accepting_user=request.user,
                    defaults={
                        "evidence_hash": evidence,
                        "ip_address": ip,
                        "user_agent": ua,
                        "accepted_at": timezone.now(),
                    }
                )

        if reg.commercial_state == CommercialState.ACTIVE:
            reg.operational_state = OperationalState.READY
            reg.save()

        messages.success(request, "Termos aceitos com sucesso! Seu QR Code de acesso foi liberado.")

    return redirect("minha_conta:registration_detail", registration_id=reg.id)


@login_required
def credential_view(request, registration_id):
    """
    Exibe a credencial oficial e voucher com QR Code pronto para impressão ou apresentação no celular.
    """
    reg = get_object_or_404(
        Registration.objects.select_related("event", "event__theme", "person", "credential"),
        id=registration_id
    )

    if not reg.is_eligible_for_qr():
        messages.warning(request, "Você precisa confirmar o pagamento e aceitar o regulamento para visualizar o QR Code.")
        return redirect("minha_conta:registration_detail", registration_id=reg.id)

    return render(request, "registrations/credential_card.html", {
        "reg": reg,
    })


def claim_ticket(request, token):
    """
    Permite a um titular reivindicar sua inscrição comprada por outra pessoa em compra coletiva.
    """
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    claim = get_object_or_404(ClaimToken.objects.select_related("registration", "registration__event"), hashed_token=token_hash)

    if not claim.is_valid():
        messages.error(request, "Este link de reivindicação já foi utilizado ou expirou.")
        return redirect("core:home")

    reg = claim.registration

    if not request.user.is_authenticated:
        messages.info(request, "Faça login ou crie sua conta para vincular esta inscrição ao seu nome.")
        return redirect(f"/login/?next=/minha-conta/reivindicar/{token}/")

    # Reivindica para o usuário logado
    claim.used_at = timezone.now()
    claim.save()

    UserPersonLink.objects.get_or_create(user=request.user, person=reg.person)
    messages.success(request, f"Inscrição vinculada com sucesso à sua conta para o evento {reg.event.title}!")
    return redirect("minha_conta:registration_detail", registration_id=reg.id)
