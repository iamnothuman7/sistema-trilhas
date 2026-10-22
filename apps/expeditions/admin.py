from django.contrib import admin
from .models import ExpeditionDay, Room, RoomAssignment, TransportVehicle, TransportAssignment

class RoomAssignmentInline(admin.TabularInline):
    model = RoomAssignment
    extra = 1

@admin.register(ExpeditionDay)
class ExpeditionDayAdmin(admin.ModelAdmin):
    list_display = ("day_number", "title", "event", "origin_city", "destination_city", "starts_at")
    list_filter = ("event",)

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("property_label", "room_code", "night_date", "capacity", "event")
    list_filter = ("event", "night_date")
    inlines = [RoomAssignmentInline]

@admin.register(TransportVehicle)
class TransportVehicleAdmin(admin.ModelAdmin):
    list_display = ("label", "event", "total_seats", "driver_name", "driver_phone")
    list_filter = ("event",)
