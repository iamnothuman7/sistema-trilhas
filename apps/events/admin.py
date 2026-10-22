from django.contrib import admin
from .models import (
    Event, EventTheme, EventPageBlock, EventScheduleItem,
    Route, RouteCheckpoint, Sponsor, LegalDocumentVersion
)

class EventThemeInline(admin.StackedInline):
    model = EventTheme
    can_delete = False

class EventScheduleItemInline(admin.TabularInline):
    model = EventScheduleItem
    extra = 1

class RouteCheckpointInline(admin.TabularInline):
    model = RouteCheckpoint
    extra = 1

class SponsorInline(admin.TabularInline):
    model = Sponsor
    extra = 1

class LegalDocumentVersionInline(admin.StackedInline):
    model = LegalDocumentVersion
    extra = 0
    readonly_fields = ("sha256", "published_at")

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "edition_number", "type", "status", "city", "starts_at", "max_attendees")
    list_filter = ("status", "type", "city", "state")
    search_fields = ("title", "slug", "city")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [EventThemeInline, EventScheduleItemInline, SponsorInline, LegalDocumentVersionInline]

@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "distance_km", "difficulty", "public_flag")
    inlines = [RouteCheckpointInline]

@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "tier", "sort_order")
    list_filter = ("tier", "event")

@admin.register(LegalDocumentVersion)
class LegalDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("event", "kind", "version_number", "published_at", "sha256")
    readonly_fields = ("sha256", "published_at")
