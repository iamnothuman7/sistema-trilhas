from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Person, UserPersonLink, EventMembership, ConsentPreference, ClaimToken

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "phone", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_active", "mfa_enabled")
    search_fields = ("email", "full_name", "phone")
    ordering = ("-date_joined",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informações Pessoais", {"fields": ("full_name", "phone")}),
        ("Permissões", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Segurança", {"fields": ("mfa_enabled", "last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "phone", "password1", "password2", "is_staff", "is_active"),
        }),
    )

@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "phone", "city", "state", "team_name", "created_at")
    search_fields = ("full_name", "email", "phone", "document_number", "team_name")
    list_filter = ("state", "city")

@admin.register(EventMembership)
class EventMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "role_code", "event", "created_at", "revoked_at")
    list_filter = ("role_code", "event")
    search_fields = ("user__email", "user__full_name", "event__title")

@admin.register(ConsentPreference)
class ConsentPreferenceAdmin(admin.ModelAdmin):
    list_display = ("person", "channel", "purpose", "value", "version", "recorded_at")

@admin.register(ClaimToken)
class ClaimTokenAdmin(admin.ModelAdmin):
    list_display = ("registration", "sent_to_email", "expires_at", "used_at", "created_at")
