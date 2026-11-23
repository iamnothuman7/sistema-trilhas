"""
Management command to safely reset test registrations, orders, and holds.
Usage:
    python manage.py reset_test_registrations --dry-run
    python manage.py reset_test_registrations --force
    python manage.py reset_test_registrations --event-slug 3-trilhao-tiangua-2027 --force
"""
from django.core.management.base import BaseCommand, CommandError
from apps.events.models import Event
from apps.registrations.reset_service import reset_test_registrations, get_reset_preview


class Command(BaseCommand):
    help = "Safely resets test registrations, orders, and capacity holds while preserving catalog and events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-slug",
            type=str,
            help="Filter reset to a specific event slug. If omitted, resets all events.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the reset and display counts without modifying the database.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Execute the reset without interactive confirmation.",
        )

    def handle(self, *args, **options):
        event_slug = options.get("event_slug")
        dry_run = options.get("dry_run", False)
        force = options.get("force", False)

        event = None
        if event_slug:
            try:
                event = Event.objects.get(slug=event_slug)
                self.stdout.write(f"Target Event: {event.title} ({event.slug})")
            except Event.DoesNotExist:
                raise CommandError(f"Event with slug '{event_slug}' not found.")
        else:
            self.stdout.write("Target: ALL EVENTS")

        preview = get_reset_preview(event)
        self.stdout.write(self.style.WARNING("=== RESET PREVIEW ==="))
        for key, val in preview.items():
            self.stdout.write(f"  {key}: {val}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] No database changes were made."))
            return

        if not force:
            confirm = input("\nType 'ZERAR' to confirm permanent reset of test registrations: ")
            if confirm.strip() != "ZERAR":
                self.stdout.write(self.style.ERROR("Reset cancelled."))
                return

        self.stdout.write(self.style.NOTICE("Executing reset..."))
        stats = reset_test_registrations(event=event, dry_run=False)
        self.stdout.write(self.style.SUCCESS("\n=== RESET COMPLETED SUCCESSFULLY ==="))
        for key, val in stats.items():
            self.stdout.write(f"  {key}: {val}")
