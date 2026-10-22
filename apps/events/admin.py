from django.contrib import admin
from .forms import SponsorAdminForm, SponsorForm
from .sponsor_images import sponsor_upload_transaction
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
    form = SponsorForm
    extra = 1

class LegalDocumentVersionInline(admin.StackedInline):
    model = LegalDocumentVersion
    extra = 0
    readonly_fields = ("sha256", "published_at")


class SponsorUploadAdminMixin:
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "POST":
            # Covers standalone sponsor saves, event inlines and admin logging.
            with sponsor_upload_transaction():
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

@admin.register(Event)
class EventAdmin(SponsorUploadAdminMixin, admin.ModelAdmin):
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
class SponsorAdmin(SponsorUploadAdminMixin, admin.ModelAdmin):
    form = SponsorAdminForm
    list_display = ("name", "event", "tier", "sort_order")
    list_filter = ("tier", "event")
    search_fields = ("name", "event__title")

@admin.register(LegalDocumentVersion)
class LegalDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("event", "kind", "version_number", "published_at", "sha256")
    readonly_fields = ("sha256", "published_at")


from . import admin_raffles  # noqa: E402,F401
