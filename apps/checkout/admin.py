from django.contrib import admin
from .models import Order, OrderLine, PriceQuote, OrderCoupon, ReceivableDue, CheckoutSubmission

class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    readonly_fields = ("intended_person_name", "product", "lot", "unit_cents", "allocated_discount_cents", "net_cents")

class ReceivableDueInline(admin.TabularInline):
    model = ReceivableDue
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("merchant_reference", "purchaser_name", "purchaser_email", "event", "status", "total_cents", "created_at", "expires_at")
    list_filter = ("status", "event")
    search_fields = ("merchant_reference", "purchaser_name", "purchaser_email", "purchaser_document")
    inlines = [OrderLineInline, ReceivableDueInline]

@admin.register(PriceQuote)
class PriceQuoteAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "gross_cents", "discount_cents", "total_cents", "expires_at", "created_at")
    list_filter = ("event",)

@admin.register(ReceivableDue)
class ReceivableDueAdmin(admin.ModelAdmin):
    list_display = ("order", "due_type", "amount_cents", "allocated_paid_cents", "status", "due_at")
    list_filter = ("status", "due_type")
