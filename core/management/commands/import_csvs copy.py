import os
import pandas as pd
from django.core.management.base import BaseCommand
from core.models import Reference, Opsin, HeterologousData

class Command(BaseCommand):
    help = 'Imports VPOD data from CSV files into the database'

    def add_arguments(self, parser):
        parser.add_argument('csv_dir', type=str, help='The path to the folder containing your CSV files')

    def handle(self, *args, **kwargs):
        csv_dir = kwargs['csv_dir']

        # 1. Import References
        ref_path = os.path.join(csv_dir, 'references.csv')
        self.stdout.write(f"Importing References from {ref_path}...")
        try:
            df_refs = pd.read_csv(ref_path).fillna('')
            for _, row in df_refs.iterrows():
                # We use get_or_create to avoid duplicates if you run the script twice
                Reference.objects.get_or_create(
                    refid=row['refid'],
                    defaults={
                        'doi': row['DOI'] if row['DOI'] else None,
                        'year_of_publication': int(row['YOP']) if str(row['YOP']).isdigit() else None,
                        'notes': row['notes'],
                        'status': 'APPROVED' # Auto-approve legacy data
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported References."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing References: {e}"))

        # 2. Import Opsins
        opsin_path = os.path.join(csv_dir, 'opsins.csv')
        self.stdout.write(f"Importing Opsins from {opsin_path}...")
        try:
            df_opsins = pd.read_csv(opsin_path).fillna('')
            for _, row in df_opsins.iterrows():
                # Try to link to a reference, but allow it to be None if it fails
                ref_obj = None
                if str(row['refid']).isdigit():
                    ref_obj = Reference.objects.filter(refid=int(row['refid'])).first()

                Opsin.objects.get_or_create(
                    opsinid=row['opsinid'],
                    defaults={
                        'gene_family': row['GeneFamily'],
                        'phylum': row['Phylum'],
                        'class': row['Class'],
                        'genus': row['Genus'],
                        'species': row['Species'],
                        'accession': row['Accession'],
                        'dna_sequence': row['DNA'],
                        'protein_sequence': row['Protein'],
                        'reference': ref_obj,
                        'status': 'APPROVED'
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported Opsins."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing Opsins: {e}"))

        # 3. Import Heterologous Data
        het_path = os.path.join(csv_dir, 'heterologous.csv')
        self.stdout.write(f"Importing Heterologous Data from {het_path}...")
        try:
            df_het = pd.read_csv(het_path).fillna('')
            for _, row in df_het.iterrows():
                ref_obj = None
                if str(row['refid']).isdigit():
                    ref_obj = Reference.objects.filter(refid=int(row['refid'])).first()

                HeterologousData.objects.get_or_create(
                    hetid=row['hetid'],
                    defaults={
                        'genus': row['Genus'],
                        'species': row['Species'],
                        'accession': row['Accession'],
                        'mutations': row['Mutations'],
                        'lambda_max': float(row['LambdaMax']) if str(row['LambdaMax']).replace('.','',1).isdigit() else 0.0,
                        'error': float(row['error']) if str(row['error']).replace('.','',1).isdigit() else None,
                        'cell_culture': row['CellCulture'],
                        'reference': ref_obj,
                        'status': 'APPROVED'
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported Heterologous Data."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing Heterologous Data: {e}"))
            
        self.stdout.write(self.style.SUCCESS('Data import complete!'))