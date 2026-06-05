import os
import pandas as pd
from django.core.management.base import BaseCommand
from core.models import CuratedSCP
from core.source_references import (
    VPOD_IN_VIVO_SOURCE_DATASET,
    build_scp_reference_by_maxid,
    clean_source_value,
    ensure_source_publication_references,
    parse_float,
    reference_for_compendium_row,
)

class Command(BaseCommand):
    help = 'Imports in-vivo SCP Compendium data into VPOD'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to VPOD_in_vivo_1.0 CSV file')

    def handle(self, *args, **kwargs):
        csv_path = kwargs['csv_path']
        self.stdout.write(f"Importing SCP Compendium from {csv_path}...")
        
        try:
            df = pd.read_csv(csv_path).fillna('')
            source_references = ensure_source_publication_references()
            scp_reference_by_maxid = build_scp_reference_by_maxid()
            count = 0
            unresolved_references = 0
            multi_reference_rows = 0
            for row_index, row in df.iterrows():
                comp_db_id = clean_source_value(row.get('comp_db_id')) or str(row_index)
                full_species = str(row.get('Full_Species', '')).strip()
                genus, species = '', ''
                if ' ' in full_species:
                    parts = full_species.split(' ', 1)
                    genus, species = parts[0], parts[1]
                else:
                    genus = full_species

                lmax = parse_float(row.get('LambdaMax'))
                reference, source_column, source_columns = reference_for_compendium_row(
                    row,
                    source_references=source_references,
                    scp_reference_by_maxid=scp_reference_by_maxid,
                )
                if len(source_columns) > 1:
                    multi_reference_rows += 1
                if source_column and reference is None:
                    unresolved_references += 1

                note_parts = [
                    f"Accession: {clean_source_value(row.get('Accession')) or 'Unknown'}",
                    f"Source: {VPOD_IN_VIVO_SOURCE_DATASET}",
                    f"comp_db_id: {comp_db_id or 'Unknown'}",
                ]
                if source_column:
                    source_value = clean_source_value(row.get(source_column))
                    note_parts.append(f"source_column: {source_column}")
                    note_parts.append(f"source_value: {source_value or 'Unknown'}")
                if source_column and reference is None:
                    note_parts.append("reference_resolution: unresolved")

                CuratedSCP.objects.update_or_create(
                    source_dataset=VPOD_IN_VIVO_SOURCE_DATASET,
                    source_record_id=comp_db_id,
                    defaults={
                        'genus': genus,
                        'species': species,
                        'lambda_max': lmax,
                        'reference': reference,
                        'status': 'APPROVED',
                        'notes': ' | '.join(note_parts),
                    }
                )
                count += 1

            self.stdout.write(self.style.SUCCESS(f"Successfully imported {count} SCP records."))
            self.stdout.write(f"Source publications ensured: {', '.join(source_references.keys())}.")
            if multi_reference_rows:
                self.stdout.write(self.style.WARNING(
                    f"{multi_reference_rows} rows had multiple source columns; used SOURCE_REFERENCE_COLUMNS order."
                ))
            if unresolved_references:
                self.stdout.write(self.style.WARNING(
                    f"{unresolved_references} rows had source columns but no resolvable Reference."
                ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error importing SCP Data: {e}"))
