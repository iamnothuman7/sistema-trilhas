from django.contrib import admin
from .models import NotificationOutbox

@admin.register(NotificationOutbox)
class NotificationOutboxAdmin(admin.ModelAdmin):
    list_display = ("recipient_name", "type", "event", "status", "attempts", "created_at", "sent_at")
    list_filter = ("status", "type", "event")
    search_fields = ("recipient_name", "recipient_email", "recipient_phone", "dedupe_key")
