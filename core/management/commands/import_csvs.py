import os
import pandas as pd
from django.core.management.base import BaseCommand
from core.models import Reference, Opsin, HeterologousData, CuratedSCP
from core.source_references import CURATED_SCP_SOURCE_DATASET, clean_source_value, ensure_source_publication_references

class Command(BaseCommand):
    help = 'Imports VPOD data from CSV files into the database and maps new relations'

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
                # update_or_create ensures any existing null/empty rows get fixed
                Reference.objects.update_or_create(
                    refid=row['refid'],
                    defaults={
                        'doi': row['DOI'] if row['DOI'] else None,
                        'year_of_publication': int(row['YOP']) if str(row['YOP']).isdigit() else None,
                        'notes': row['notes'],
                        'status': 'APPROVED' # Auto-approve legacy data
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported References."))
            source_refs = ensure_source_publication_references()
            self.stdout.write(f"Ensured source publication References: {', '.join(source_refs.keys())}.")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing References: {e}"))

        # 2. Import Opsins
        opsin_path = os.path.join(csv_dir, 'opsins.csv')
        self.stdout.write(f"Importing Opsins from {opsin_path}...")
        try:
            df_opsins = pd.read_csv(opsin_path).fillna('')
            for _, row in df_opsins.iterrows():
                # Try to link to a reference
                ref_obj = None
                if str(row['refid']).replace('.0','',1).isdigit(): # Handle pandas float conversions
                    ref_obj = Reference.objects.filter(refid=int(float(row['refid']))).first()

                Opsin.objects.update_or_create(
                    opsinid=row['opsinid'],
                    defaults={
                        'gene_family': row['GeneFamily'],
                        'phylum': row['Phylum'],
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

        # 3. Import Heterologous Data and Map to Opsins
        het_path = os.path.join(csv_dir, 'heterologous.csv')
        self.stdout.write(f"Importing Heterologous Data from {het_path}...")
        try:
            df_het = pd.read_csv(het_path).fillna('')
            for _, row in df_het.iterrows():
                # Link to reference
                ref_obj = None
                if str(row['refid']).replace('.0','',1).isdigit():
                    ref_obj = Reference.objects.filter(refid=int(float(row['refid']))).first()

                # --- NEW: Link to Opsin object ---
                opsin_obj = Opsin.objects.filter(
                    genus=row['Genus'],
                    species=row['Species'],
                    accession=row['Accession']
                ).first()

                # Fallback just in case accession doesn't match perfectly
                if not opsin_obj:
                    opsin_obj = Opsin.objects.filter(
                        genus=row['Genus'],
                        species=row['Species']
                    ).first()

                HeterologousData.objects.update_or_create(
                    hetid=row['hetid'],
                    defaults={
                        'opsin': opsin_obj, # Saves to the new relational field!
                        'reference': ref_obj,
                        'mutations': row['Mutations'],
                        'lambda_max': float(row['LambdaMax']) if str(row['LambdaMax']).replace('.','',1).isdigit() else 0.0,
                        'error': float(row['error']) if str(row['error']).replace('.','',1).isdigit() else None,
                        'cell_culture': row['CellCulture'],
                        'status': 'APPROVED'
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"Successfully imported and linked Heterologous Data."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing Heterologous Data: {e}"))

        # 4. Import Curated SCP Data and Map to Opsins
        scp_path = os.path.join(csv_dir, 'curated_scp.csv')
        self.stdout.write(f"Importing Curated SCP Data from {scp_path}...")
        if os.path.exists(scp_path):
            try:
                df_scp = pd.read_csv(scp_path).fillna('')
                for _, row in df_scp.iterrows():
                    # Link to reference
                    ref_obj = None
                    if str(row.get('refid', '')).replace('.0','',1).isdigit():
                        ref_obj = Reference.objects.filter(refid=int(float(row['refid']))).first()

                    scp_id = clean_source_value(row.get('scpid')) or clean_source_value(row.get('maxid'))
                    notes = clean_source_value(row.get('Notes')) or None

                    CuratedSCP.objects.update_or_create(
                        scpid=scp_id,
                        defaults={
                            'genus': row.get('Genus', ''),
                            'species': row.get('Species', ''),
                            'phylum': row.get('Phylum', ''),
                            'reference': ref_obj,
                            'photoreceptor_type': row.get('CellType', row.get('photoreceptor_type', '')),
                            'cell_subtype': row.get('CellSubType', row.get('photoreceptor_type', '')),
                            'lambda_max': float(row['LambdaMax']) if str(row.get('LambdaMax', '')).replace('.','',1).isdigit() else None,
                            'error': float(row['error']) if str(row['error']).replace('.','',1).isdigit() else None,
                            'chromophore': row.get('Chromophore', ''),
                            'notes': notes,
                            'source_dataset': CURATED_SCP_SOURCE_DATASET,
                            'source_record_id': scp_id,
                            'status': 'APPROVED'
                        }
                    )
                self.stdout.write(self.style.SUCCESS(f"Successfully imported Curated SCP Data."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error importing Curated SCP Data: {e}"))
        else:
            self.stdout.write(self.style.WARNING(f"File {scp_path} not found. Skipping SCP import."))
            
        self.stdout.write(self.style.SUCCESS('Data import complete!'))
