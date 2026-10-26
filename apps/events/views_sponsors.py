"""Event-scoped sponsor management; no destructive actions."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.cache import never_cache

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
    event = request.management_event
    sponsors = [decorate_balance(item) for item in sponsorships_with_balances(event.sponsors.all())]
    totals = sponsor_totals(sponsors)
    query = request.GET.get("q", "").strip()[:150]
    status = request.GET.get("status", "")
    can_finance = can_manage_event(request.user, event, "finance")
    filtered = [s for s in sponsors if (not query or query.casefold() in s.name.casefold()) and
        (not status or status == "public" and s.is_public or status == "hidden" and not s.is_public or
         can_finance and s.payment_status == status)]
    page = Paginator(filtered, 24).get_page(request.GET.get("page"))
    return render(request, "gestao/sponsors/list.html", {"event": event, "sponsors": page, "page_obj": page,
        "totals": totals, "q": query, "status": status, "can_finance": can_finance,
        "can_content": can_manage_event(request.user, event, "content"), "total_count": len(sponsors)})


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
    sponsor = get_object_or_404(Sponsor, pk=sponsor_id, event=request.management_event)
    return _sponsor_form(request, sponsor)


@management_required
@require_GET
@never_cache
def sponsor_bank(request):
    events = accessible_events(request.user, "sponsors")
    profiles = available_profiles(request.user).annotate(event_count=Count("participations", filter=Q(participations__event__in=events))).order_by("name", "pk")
    query = request.GET.get("q", "").strip()[:150]
    if query:
        profiles = profiles.filter(Q(name__icontains=query) | Q(contact_name__icontains=query) | Q(contact_email__icontains=query))
    page = Paginator(profiles, 24).get_page(request.GET.get("page"))
    for profile in page:
        profile.can_edit = can_edit_profile(request.user, profile)
    event_choices = list(events.order_by("-starts_at"))
    for event in event_choices:
        event.can_content = can_manage_event(request.user, event, "content")
    return render(request, "gestao/sponsors/bank.html", {"profiles": page, "page_obj": page, "q": query,
        "can_create": accessible_events(request.user, "content").exists(), "events": event_choices})


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
        messages.success(request, "Marca salva no banco de patrocinadores. A imagem e os links foram atualizados nas participações vinculadas.")
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
    return _profile_form(request, get_object_or_404(available_profiles(request.user, "content"), pk=profile_id))


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
