from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from core.models import CuratedSCP


class Command(BaseCommand):
    help = "Remove duplicate records with identical genus, species, and lambda_max."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually delete duplicates. Without this flag, only prints what would be deleted.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]

        duplicate_groups = list(
            CuratedSCP.objects
            .values("genus", "species", "lambda_max")
            .annotate(row_count=Count("scpid"))
            .filter(row_count__gt=1)
            .order_by("-row_count")
        )

        if not duplicate_groups:
            self.stdout.write(self.style.SUCCESS("No duplicates found."))
            return

        self.stdout.write(f"Found {len(duplicate_groups)} duplicate groups.")

        total_to_delete = 0

        with transaction.atomic():
            for group in duplicate_groups:
                qs = (
                    CuratedSCP.objects
                    .filter(
                        genus=group["genus"],
                        species=group["species"],
                        lambda_max=group["lambda_max"],
                    )
                    .order_by("scpid")
                )

                keeper = qs.first()
                duplicates = qs.exclude(scpid=keeper.scpid)
                duplicate_ids = list(duplicates.values_list("scpid", flat=True))
                total_to_delete += len(duplicate_ids)

                self.stdout.write(
                    f"Group: genus={group['genus']}, "
                    f"species={group['species']}, "
                    f"lambda_max={group['lambda_max']} | "
                    f"keep id={keeper.scpid}, delete ids={duplicate_ids}"
                )

                if commit:
                    duplicates.delete()

            if not commit:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING(
                        f"Dry run only. Would delete {total_to_delete} rows. "
                        f"Run again with --commit to apply."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"Deleted {total_to_delete} duplicate rows.")
                )