from django.contrib import admin
from .models import Organization, AuditLog, OutboxMessage

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "support_email", "phone", "updated_at")

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_email", "action", "entity_type", "entity_id", "event_id")
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("actor_email", "entity_id", "reason")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(OutboxMessage)
class OutboxMessageAdmin(admin.ModelAdmin):
    list_display = ("event_name", "aggregate_type", "aggregate_id", "status", "retries", "created_at", "processed_at")
    list_filter = ("status", "event_name")
    search_fields = ("dedupe_key", "aggregate_id")
