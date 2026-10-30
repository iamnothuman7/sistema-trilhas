"""Event-scoped sponsor management and archived participation history."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.cache import never_cache
from uuid import UUID
from django.http import Http404

from apps.core.permissions import management_required, accessible_events, can_manage_event
from apps.core.utils import log_audit
from .forms import SponsorForm
from .models import Sponsor, SponsorProfile
from .sponsor_images import sponsor_upload_transaction
from .sponsorships import (available_profiles, brand_values, can_edit_profile, decorate_balance,
    ensure_profile, sponsor_totals, sponsorships_with_balances, sync_profile)
from .forms_sponsorships import (SponsorProfileForm, SponsorLinkForm, SponsorAgreementForm, SponsorReceiptForm, SponsorReversalForm)
from apps.finance.models import SponsorPayment
from apps.finance.sponsorships import record_sponsor_payment, reverse_sponsor_payment, update_sponsor_agreement


@management_required
@require_GET
@never_cache
def sponsor_list(request, event_id):
    return _sponsor_hub(request, request.management_event)


def _sponsor_hub(request, event=None):
    """One brand card with scoped event participations and financial access."""
    events = list(accessible_events(request.user, "sponsors").order_by("-starts_at"))
    for choice in events:
        choice.can_content = can_manage_event(request.user, choice, "content")
        choice.can_finance = can_manage_event(request.user, choice, "finance")
    if event is None and request.GET.get("event_id") not in (None, "", "all"):
        try:
            selected = UUID(request.GET["event_id"])
        except (ValueError, TypeError):
            raise Http404("Evento não encontrado.")
        event = next((choice for choice in events if choice.pk == selected), None)
        if event is None:
            raise PermissionDenied("Sua conta não tem acesso aos patrocinadores deste evento.")
    if event:
        request.management_event = event
        request.session["management_event_id"] = str(event.pk)
    request.management_all_events = event is None
    scopes = {choice.pk: choice for choice in events}
    archived=request.GET.get('archived')=='1'
    sponsors = list(sponsorships_with_balances(Sponsor.objects.filter(event__in=[event] if event else events,archived_at__isnull=not archived)))
    for sponsor in sponsors:
        decorate_balance(sponsor)
        sponsor.can_finance = scopes[sponsor.event_id].can_finance
        sponsor.can_content = scopes[sponsor.event_id].can_content
        sponsor.can_remove = sponsor.can_content and (sponsor.can_finance or (not sponsor.agreed_amount_cents and not sponsor.payments.exists()))
    financial_sponsors = [sponsor for sponsor in sponsors if sponsor.can_finance]
    query = request.GET.get("q", "").strip()[:150]
    status = request.GET.get("status", "")
    by_profile = {}
    for sponsor in sponsors:
        by_profile.setdefault(sponsor.profile_id, []).append(sponsor)
    cards = []
    brands = list(available_profiles(request.user,include_archived=archived).order_by("name", "pk"))
    # Preserve access to older rows without creating or changing data on a GET.
    brands.extend(sponsor for sponsor in sponsors if sponsor.profile_id is None)
    for brand in brands:
        legacy = isinstance(brand, Sponsor)
        participations = [brand] if legacy else by_profile.get(brand.pk, [])
        if archived and not brand.archived_at and not participations:continue
        if event and not participations:
            continue
        if query and query.casefold() not in " ".join(str(getattr(brand, field, "") or "") for field in ("name", "contact_name", "contact_email", "contact_phone")).casefold():
            continue
        if status:
            participations = [s for s in participations if
                (status == "public" and s.is_public) or (status == "hidden" and not s.is_public) or
                (s.can_finance and s.payment_status == status)]
            if not participations:
                continue
        linked_events = set(brand.participations.filter(event__in=events).values_list("event_id", flat=True)) if not legacy else {brand.event_id}
        cards.append({"brand": brand, "profile": None if legacy else brand,
            'can_remove':not legacy and can_edit_profile(request.user,brand) and all(can_manage_event(request.user,s.event,'finance') or (not s.agreed_amount_cents and not s.payments.exists()) for s in brand.participations.select_related('event').all()),
            "can_edit": not legacy and can_edit_profile(request.user, brand), "participations": participations,
            "link_events": [choice for choice in events if not legacy and not brand.archived_at and choice.can_content and
                choice.organization_id == brand.organization_id and choice.pk not in linked_events]})
    page = Paginator(cards, 24).get_page(request.GET.get("page"))
    return render(request, "gestao/sponsors/bank.html", {"event": event, "events": events,
        "cards": page, "page_obj": page, "q": query, "status": status,'archived':archived,
        "totals": sponsor_totals(financial_sponsors),
        "can_finance": any(choice.can_finance for choice in events if event is None or choice.pk == event.pk),
        "can_create": any(choice.can_content for choice in events),
        "can_content": event is not None and can_manage_event(request.user, event, "content"),
        "total_count": len(cards)})


def _sponsor_form(request, sponsor):
    event = request.management_event
    creating = sponsor.pk is None
    form = SponsorForm(request.POST or None, request.FILES or None, instance=sponsor)
    brand_locked = sponsor.profile_id and not can_edit_profile(request.user, sponsor.profile)
    if brand_locked:
        for name in brand_values(sponsor):
            if name in form.fields:
                form.fields[name].disabled = True
    agreement_form = SponsorAgreementForm(request.POST or None, prefix="agreement") if creating and can_manage_event(request.user, event, "finance") else None
    if request.method == "POST" and form.is_valid() and (agreement_form is None or agreement_form.is_valid()):
        with sponsor_upload_transaction():
            saved = form.save(commit=False)
            saved.event = event
            if agreement_form:
                data = agreement_form.cleaned_data
                saved.agreed_amount_cents, saved.due_at = data["amount"], data["due_at"]
                saved.agreement_notes = data["agreement_notes"]
            saved.save(update_fields=None if creating else [*brand_values(saved), "tier", "sort_order", "is_public"])
            profile = ensure_profile(saved)
            if not brand_locked:
                for name, value in brand_values(saved).items():
                    setattr(profile, name, value)
                profile.save()
                sync_profile(profile)
            log_audit(
                actor=request.user, event_id=event.pk,
                action="SPONSOR_CREATED" if creating else "SPONSOR_UPDATED",
                entity_type="Sponsor", entity_id=saved.pk,
                diff={"fields": list(form.changed_data), "is_public": saved.is_public,
                    **({"agreed_amount_cents": saved.agreed_amount_cents} if agreement_form else {})}, request=request,
            )
        messages.success(request, "Patrocinador cadastrado." if creating else "Patrocinador atualizado.")
        return redirect("gestao:sponsor_list", event_id=event.pk)
    return render(request, "gestao/sponsors/form.html", {
        "event": event, "sponsor": sponsor, "form": form, "creating": creating,
        "agreement_form": agreement_form, "brand_locked": brand_locked,
    }, status=400 if request.method == "POST" else 200)


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_create(request, event_id):
    return _sponsor_form(request, Sponsor(event=request.management_event))


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_edit(request, event_id, sponsor_id):
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event,archived_at__isnull=True)
    return _sponsor_form(request, sponsor)


@management_required
@require_GET
@never_cache
def sponsor_bank(request):
    return _sponsor_hub(request)


def _profile_form(request, profile):
    from django import forms
    from apps.core.models import Organization
    creating = profile.pk is None
    organizations = Organization.objects.filter(pk__in=accessible_events(request.user, "content").values("organization_id"))
    if not organizations.exists() or not creating and not can_edit_profile(request.user, profile):
        raise PermissionDenied("Para alterar uma marca compartilhada, você precisa de acesso ao conteúdo de todos os eventos vinculados.")
    form = SponsorProfileForm(request.POST or None, request.FILES or None, instance=profile)
    if creating and organizations.count() > 1:
        form.fields["organization"] = forms.ModelChoiceField(label="Organização", queryset=organizations, widget=forms.Select(attrs={"class": "form-control"}))
    if request.method == "POST" and form.is_valid():
        with sponsor_upload_transaction():
            saved = form.save(commit=False)
            if creating:
                saved.organization = form.cleaned_data.get("organization") or organizations.first()
            saved.save()
            sync_profile(saved)
            log_audit(actor=request.user, action="SPONSOR_PROFILE_CREATED" if creating else "SPONSOR_PROFILE_UPDATED",
                entity_type="SponsorProfile", entity_id=saved.pk, diff={"fields": list(form.changed_data)}, request=request)
        messages.success(request, "Cadastro salvo em Patrocinadores. A imagem e os links foram atualizados nos eventos vinculados.")
        return redirect("gestao:sponsor_bank")
    participations = profile.participations.filter(event__in=accessible_events(request.user, "sponsors")).select_related("event") if not creating else []
    return render(request, "gestao/sponsors/profile_form.html", {"form": form, "profile": profile, "creating": creating,
        "participations": participations}, status=400 if request.method == "POST" else 200)


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_profile_create(request):
    return _profile_form(request, SponsorProfile())


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_profile_edit(request, profile_id):
    return _profile_form(request, get_object_or_404(available_profiles(request.user, "content",include_archived=True), pk=profile_id))


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_link(request, event_id):
    event = request.management_event
    form = SponsorLinkForm(request.POST or None, event=event, initial={"profile": request.GET.get("profile")})
    agreement = SponsorAgreementForm(request.POST or None, prefix="agreement") if can_manage_event(request.user, event, "finance") else None
    if request.method == "POST" and form.is_valid() and (agreement is None or agreement.is_valid()):
        data = form.cleaned_data
        try:
            with transaction.atomic():
                profile = SponsorProfile.objects.select_for_update().get(pk=data["profile"].pk, organization=event.organization)
                saved = Sponsor.objects.create(event=event, profile=profile, tier=data["tier"], sort_order=data["sort_order"],
                    is_public=data["is_public"], **brand_values(profile),
                    **({"agreed_amount_cents": agreement.cleaned_data["amount"], "due_at": agreement.cleaned_data["due_at"],
                        "agreement_notes": agreement.cleaned_data["agreement_notes"]} if agreement else {}))
                log_audit(actor=request.user, event_id=event.pk, action="SPONSOR_LINKED", entity_type="Sponsor", entity_id=saved.pk,
                    diff={"profile_id": profile.pk, "is_public": saved.is_public, "agreed_amount_cents": saved.agreed_amount_cents}, request=request)
        except IntegrityError:
            form.add_error("profile", "Esta marca já está vinculada ao evento. Volte à lista para editar a participação.")
        else:
            messages.success(request, "Patrocinador vinculado a esta edição.")
            return redirect("gestao:sponsor_detail", event_id=event.pk, sponsor_id=saved.pk)
    return render(request, "gestao/sponsors/link.html", {"event": event, "form": form, "agreement_form": agreement}, status=400 if request.method == "POST" else 200)


def _detail_context(request, sponsor):
    event = request.management_event
    can_finance = can_manage_event(request.user, event, "finance")
    return {"event": event, "sponsor": decorate_balance(sponsor), "can_finance": can_finance,
        "can_content": can_manage_event(request.user, event, "content"),
        "can_edit_brand": sponsor.profile_id and can_edit_profile(request.user, sponsor.profile),
        "payments": sponsor.payments.select_related("created_by", "reversed_by") if can_finance else []}


@management_required
@require_GET
@never_cache
def sponsor_detail(request, event_id, sponsor_id):
    sponsor = get_object_or_404(Sponsor.objects.select_related("profile"), pk=sponsor_id, event=request.management_event)
    return render(request, "gestao/sponsors/detail.html", _detail_context(request, sponsor))


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_agreement(request, event_id, sponsor_id):
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event)
    form = SponsorAgreementForm(request.POST or None, sponsor=sponsor)
    if request.method == "POST" and form.is_valid():
        try:
            update_sponsor_agreement(event=request.management_event, sponsor_id=sponsor.pk, data=form.cleaned_data, actor=request.user, request=request)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Acordo atualizado. Nenhum recebimento foi registrado por esta alteração.")
            return redirect("gestao:sponsor_detail", event_id=event_id, sponsor_id=sponsor.pk)
    return render(request, "gestao/sponsors/operation.html", {**_detail_context(request, sponsor), "form": form,
        "heading": "Acordo de patrocínio", "intro": "Defina o valor e as condições desta participação.", "submit_label": "Salvar acordo"}, status=400 if request.method == "POST" else 200)


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_receipt(request, event_id, sponsor_id):
    sponsor = decorate_balance(get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event))
    form = SponsorReceiptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            _, created = record_sponsor_payment(event=request.management_event, sponsor_id=sponsor.pk, data=form.cleaned_data, actor=request.user, request=request)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Baixa registrada no patrocínio e no financeiro principal." if created else "Este recebimento já foi registrado. Nenhum valor foi duplicado.")
            return redirect("gestao:sponsor_detail", event_id=event_id, sponsor_id=sponsor.pk)
    return render(request, "gestao/sponsors/operation.html", {**_detail_context(request, sponsor), "form": form,
        "heading": "Registrar recebimento", "intro": "Registre o valor já recebido. Você pode dar baixas parciais até quitar o acordo.",
        "receipt_blocked": sponsor.balance_cents is None or sponsor.balance_cents <= 0,
        "submit_label": "Confirmar recebimento"}, status=400 if request.method == "POST" else 200)


@management_required
@require_http_methods(["GET", "POST"])
@never_cache
def sponsor_reversal(request, event_id, sponsor_id, payment_id):
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event)
    payment = get_object_or_404(SponsorPayment, pk=payment_id, sponsorship=sponsor)
    form = SponsorReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _, changed = reverse_sponsor_payment(event=request.management_event, sponsor_id=sponsor.pk, payment_id=payment.pk,
            actor=request.user, reason=form.cleaned_data["reason"], request=request)
        messages.success(request, "Baixa revertida. O saldo e o financeiro principal foram ajustados." if changed else "Esta baixa já estava revertida.")
        return redirect("gestao:sponsor_detail", event_id=event_id, sponsor_id=sponsor.pk)
    return render(request, "gestao/sponsors/operation.html", {**_detail_context(request, sponsor), "form": form, "payment": payment,
        "heading": "Reverter baixa", "intro": "O lançamento original será preservado e o valor voltará ao saldo a receber.",
        "submit_label": "Confirmar reversão"}, status=400 if request.method == "POST" else 200)
