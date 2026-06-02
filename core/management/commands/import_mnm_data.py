import pandas as pd
from django.core.management.base import BaseCommand
from core.models import HeterologousData, Opsin

class Command(BaseCommand):
    help = 'Imports MNM pipeline predictions into HeterologousData table'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)

    def handle(self, *args, **kwargs):
        df = pd.read_csv(kwargs['csv_path']).fillna('')
        for _, row in df.iterrows():
            opsin, _ = Opsin.objects.get_or_create(
                accession=row['Accession'],
                protein_sequence=row['Protein'],
                gene_family=row['Gene_Description'],
                defaults={'genus': row['Genus'], 'species': row['Species'], 'phylum': row['Phylum']},
                
            )
            HeterologousData.objects.create(
                opsin=opsin,
                lambda_max=float(row['LambdaMax']) if row['LambdaMax'] else None,
                mutations="Wildtype (Inferred)",
                is_inferred=True,
                inference_source=f"Inferred w/ MNM Pipeline | Source: Lmax Compendium - Entry  {row.get('comp_db_id', '')}",
                status='APPROVED'
            )
        self.stdout.write(self.style.SUCCESS('Successfully imported MNM records.'))