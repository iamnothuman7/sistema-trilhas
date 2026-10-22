from django.contrib import admin
from .models import CheckinRecord, FieldRecord, OccurrenceRecord

@admin.register(CheckinRecord)
class CheckinRecordAdmin(admin.ModelAdmin):
    list_display = ("registration", "event", "checkin_type", "operator", "occurred_at")
    list_filter = ("event", "checkin_type", "occurred_at")
    search_fields = ("registration__person__full_name", "registration__registration_number")

@admin.register(FieldRecord)
class FieldRecordAdmin(admin.ModelAdmin):
    list_display = ("registration", "kind", "checkpoint", "operator", "occurred_at")
    list_filter = ("kind",)

@admin.register(OccurrenceRecord)
class OccurrenceRecordAdmin(admin.ModelAdmin):
    list_display = ("summary", "event", "severity", "staff", "resolved", "created_at")
    list_filter = ("severity", "resolved", "event")
    search_fields = ("summary", "details", "location")
