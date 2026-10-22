from django.contrib import admin
from .models import (
    Product, Lot, ProductVariant, AddOn, Coupon,
    CapacityResource, CapacityHold, InventoryItem, InventoryMovement
)

class LotInline(admin.TabularInline):
    model = Lot
    extra = 1

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "kind", "active", "includes_shirt", "includes_kit")
    list_filter = ("kind", "event", "active")
    search_fields = ("name",)
    inlines = [LotInline, ProductVariantInline]

@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ("title", "product", "event", "price_cents", "current_sold", "max_quantity", "active", "opens_at", "closes_at")
    list_filter = ("event", "active")
    search_fields = ("title", "product__name")

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code_upper", "event", "kind", "value_cents", "percent_off", "used_count", "max_uses", "active")
    list_filter = ("event", "active")
    search_fields = ("code_upper",)

@admin.register(CapacityResource)
class CapacityResourceAdmin(admin.ModelAdmin):
    list_display = ("type", "event", "reference_id", "capacity_total", "reserved_count", "committed_count")
    list_filter = ("type", "event")

@admin.register(CapacityHold)
class CapacityHoldAdmin(admin.ModelAdmin):
    list_display = ("order_id", "resource", "quantity", "status", "expires_at", "created_at")
    list_filter = ("status",)

@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "variant_code", "physical_quantity", "event")
    list_filter = ("event",)
