from django.contrib import admin

from .forms_raffles import MotorcycleRaffleAdminForm
from .models_raffles import MotorcycleRaffle, RaffleInterest


@admin.register(MotorcycleRaffle)
class MotorcycleRaffleAdmin(admin.ModelAdmin):
    form = MotorcycleRaffleAdminForm
    list_display = ("title", "event", "is_public", "interest_enabled", "updated_at")
    list_filter = ("is_public", "interest_enabled", "event")
    search_fields = ("title", "event__title", "motorcycle_model")
    readonly_fields = ("created_at", "updated_at")


@admin.register(RaffleInterest)
class RaffleInterestAdmin(admin.ModelAdmin):
    list_display = ("raffle", "user", "created_at")
    list_filter = ("raffle__event",)
    search_fields = ("user__email", "user__full_name", "raffle__title")
    readonly_fields = ("raffle", "user", "consent_text", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
