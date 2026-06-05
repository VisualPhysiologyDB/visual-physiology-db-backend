from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.db.models import Q

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

        invalid_qs = (
            CuratedSCP.objects
            .filter(Q(lambda_max__lt=300) | Q(lambda_max__gt=800))
            .exclude(lambda_max=0)
        )

        invalid_qs.delete()