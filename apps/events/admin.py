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
    can_delete = False

class LegalDocumentVersionInline(admin.StackedInline):
    model = LegalDocumentVersion
    extra = 0
    readonly_fields = ("sha256", "published_at")


class SponsorUploadAdminMixin:
    def _sync_sponsor_brand(self, obj):
        from .sponsorships import ensure_profile, brand_values, sync_profile
        if isinstance(obj, Sponsor):
            profile = ensure_profile(obj)
            for name, value in brand_values(obj).items():
                setattr(profile, name, value)
            profile.save()
            sync_profile(profile)

    def save_model(self, request, obj, form, change):
        if isinstance(obj, Sponsor) and change:
            # Branding edits cannot overwrite receipts/agreements saved meanwhile.
            obj.save(update_fields=[*[name for name in form.Meta.fields if name != "event"], "logo_preview", "logo_preview_width", "logo_preview_height"])
        else:
            super().save_model(request, obj, form, change)
        self._sync_sponsor_brand(obj)

    def save_formset(self, request, form, formset, change):
        if formset.model is not Sponsor:
            return super().save_formset(request, form, formset, change)
        instances = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for obj in instances:
            obj.save(update_fields=["name", "tier", "website", "logo_image", "logo_preview", "logo_preview_width", "logo_preview_height", "instagram_url", "sort_order", "is_public"] if obj.pk else None)
            self._sync_sponsor_brand(obj)
        formset.save_m2m()

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

    def get_readonly_fields(self, request, obj=None):
        return ("event",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(LegalDocumentVersion)
class LegalDocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("event", "kind", "version_number", "published_at", "sha256")
    readonly_fields = ("sha256", "published_at")


from . import admin_raffles  # noqa: E402,F401
