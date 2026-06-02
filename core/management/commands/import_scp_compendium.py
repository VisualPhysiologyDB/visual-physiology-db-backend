import os
import pandas as pd
from django.core.management.base import BaseCommand
from core.models import CuratedSCP

class Command(BaseCommand):
    help = 'Imports in-vivo SCP Compendium data into VPOD'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to VPOD_in_vivo_1.0 CSV file')

    def handle(self, *args, **kwargs):
        csv_path = kwargs['csv_path']
        self.stdout.write(f"Importing SCP Compendium from {csv_path}...")
        
        try:
            df = pd.read_csv(csv_path).fillna('')
            count = 0
            for _, row in df.iterrows():
                full_species = str(row.get('Full_Species', '')).strip()
                genus, species = '', ''
                if ' ' in full_species:
                    parts = full_species.split(' ', 1)
                    genus, species = parts[0], parts[1]
                else:
                    genus = full_species

                lmax_raw = str(row.get('LambdaMax', '')).replace('.', '', 1)
                lmax = float(row['LambdaMax']) if lmax_raw.isdigit() else None

                # Create the CuratedSCP record
                CuratedSCP.objects.create(
                    genus=genus,
                    species=species,
                    lambda_max=lmax,
                    
                    status='APPROVED',
                    # Maps accession to notes or reference if needed, as CuratedSCP lacks an accession field currently
                    notes=f"Accession: {row.get('Accession', '')} | Source: in_vivo compendium"
                )
                count += 1

            self.stdout.write(self.style.SUCCESS(f"Successfully imported {count} SCP records."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing SCP Data: {e}"))