from django.contrib import admin
from .models import FrameTemplate

@admin.register(FrameTemplate)
class FrameTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "format", "access_policy", "is_active", "created_at")
    list_filter = ("format", "access_policy", "is_active", "event")
    search_fields = ("title", "event__title")
