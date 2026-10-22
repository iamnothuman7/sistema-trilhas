from django.contrib import admin
from .models import FinancialEntry, Vendor, Expense

@admin.register(FinancialEntry)
class FinancialEntryAdmin(admin.ModelAdmin):
    list_display = ("recognized_at", "event", "kind", "amount_cents_signed", "source_reference", "description")
    list_filter = ("kind", "event", "recognized_at")
    search_fields = ("source_reference", "description")
    readonly_fields = [f.name for f in FinancialEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "contact_name", "contact_phone", "pix_key")
    search_fields = ("name", "contact_name", "contact_phone")

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("description", "event", "vendor", "category", "amount_cents", "status", "due_at", "paid_at")
    list_filter = ("status", "category", "event")
    search_fields = ("description", "vendor__name")
