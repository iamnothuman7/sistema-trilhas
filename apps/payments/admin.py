from django.contrib import admin
from apps.core.private_documents import PrivateDocumentAdminMixin
from .models import ChargeAttempt, ProviderEvent, PaymentAllocation, Refund, ManualPaymentEvidence, FeeStatement, PaymentTransaction

@admin.register(ChargeAttempt)
class ChargeAttemptAdmin(admin.ModelAdmin):
    list_display = ("order", "payment_method", "amount_cents", "state", "provider_order_id", "sent_at", "resolved_at")
    list_filter = ("state", "payment_method", "provider")
    search_fields = ("order__merchant_reference", "provider_order_id", "provider_payment_ref", "idempotency_key")
    readonly_fields = ("idempotency_key", "sent_at", "resolved_at")

@admin.register(ProviderEvent)
class ProviderEventAdmin(admin.ModelAdmin):
    list_display = ("provider_event_id", "event_type", "resource_id", "signature_valid", "observed_at", "processed_at")
    list_filter = ("event_type", "signature_valid")
    search_fields = ("provider_event_id", "resource_id")
    readonly_fields = [f.name for f in ProviderEvent._meta.fields]

@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ("charge_attempt", "order_line", "receivable_due", "amount_cents", "created_at")

@admin.register(Refund)
class RefundAdmin(PrivateDocumentAdminMixin, admin.ModelAdmin):
    list_display = ("order", "amount_cents", "state", "provider_ref", "reason_code", "created_at", "completed_at")
    list_filter = ("state",)

@admin.register(ManualPaymentEvidence)
class ManualPaymentEvidenceAdmin(PrivateDocumentAdminMixin, admin.ModelAdmin):
    list_display = ("order", "method", "amount_cents", "verified_by", "verified_at")
    list_filter = ("method",)

@admin.register(FeeStatement)
class FeeStatementAdmin(admin.ModelAdmin):
    list_display = ("provider_order_id", "expected_cents", "actual_cents", "reconciled_at")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("provider_payment_id", "charge_attempt", "provider_status", "amount_cents", "posted_fee_cents", "posted_refund_cents", "provider_updated_at")
    list_filter = ("provider_status",)
    search_fields = ("provider_payment_id", "charge_attempt__order__merchant_reference")
    readonly_fields = [field.name for field in PaymentTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
