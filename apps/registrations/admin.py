from django.contrib import admin
from .models import (
    Registration, TermsAcceptance, RegistrationCredential,
    RegistrationTransfer, Entitlement, Consumption, WaitlistEntry
)

class EntitlementInline(admin.TabularInline):
    model = Entitlement
    extra = 0

class TermsAcceptanceInline(admin.TabularInline):
    model = TermsAcceptance
    extra = 0
    readonly_fields = ("document_version", "accepting_user", "accepted_at", "evidence_hash", "ip_address")

@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("registration_number", "person", "event", "category_name", "shirt_size", "commercial_state", "operational_state", "created_at")
    list_filter = ("event", "commercial_state", "operational_state", "category_name")
    search_fields = ("registration_number", "person__full_name", "person__email", "person__phone", "team_name")
    inlines = [EntitlementInline, TermsAcceptanceInline]

@admin.register(RegistrationCredential)
class RegistrationCredentialAdmin(admin.ModelAdmin):
    list_display = ("registration", "raw_token", "version", "issued_at", "revoked_at")
    search_fields = ("registration__person__full_name", "raw_token")

@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ("title", "registration", "benefit_type", "quantity_total", "quantity_used", "state")
    list_filter = ("benefit_type", "state")

@admin.register(Consumption)
class ConsumptionAdmin(admin.ModelAdmin):
    list_display = ("entitlement", "quantity", "action", "operator", "occurred_at")
    list_filter = ("action", "occurred_at")
