from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import HeterologousData, Opsin
from core.source_references import (
    MNM_SOURCE_DATASET,
    build_scp_reference_by_maxid,
    clean_source_value,
    ensure_source_publication_references,
    load_compendium_by_comp_id,
    parse_float,
    reference_for_compendium_row,
)

class Command(BaseCommand):
    help = 'Imports MNM pipeline predictions into HeterologousData table'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)
        parser.add_argument(
            '--compendium-csv',
            type=str,
            default=None,
            help='Path to the VPOD_in_vivo_v1.0 CSV used to resolve MNM comp_db_id source references',
        )

    def handle(self, *args, **kwargs):
        compendium_csv = kwargs.get('compendium_csv')
        if compendium_csv is None:
            compendium_csv = Path(settings.BASE_DIR) / 'VPOD_in_vivo_1.0_2026-03-13_12-54-05.csv'
        compendium_csv = Path(compendium_csv)
        if not compendium_csv.exists():
            self.stdout.write(self.style.ERROR(
                f"Compendium CSV not found: {compendium_csv}. Pass --compendium-csv."
            ))
            return

        compendium_by_id = load_compendium_by_comp_id(compendium_csv)
        source_references = ensure_source_publication_references()
        scp_reference_by_maxid = build_scp_reference_by_maxid()

        df = pd.read_csv(kwargs['csv_path']).fillna('')
        imported = 0
        unresolved_comp_ids = 0
        unresolved_references = 0
        multi_reference_rows = 0

        for row_index, row in df.iterrows():
            mnm_id = clean_source_value(row.get('mnm_id')) or str(row_index)
            comp_db_id = clean_source_value(row.get('comp_db_id'))
            compendium_row = compendium_by_id.get(comp_db_id)
            reference = None
            source_column = None
            source_columns = []

            if compendium_row is None:
                unresolved_comp_ids += 1
            else:
                reference, source_column, source_columns = reference_for_compendium_row(
                    compendium_row,
                    source_references=source_references,
                    scp_reference_by_maxid=scp_reference_by_maxid,
                )
                if len(source_columns) > 1:
                    multi_reference_rows += 1
                if source_column and reference is None:
                    unresolved_references += 1

            accession = clean_source_value(row.get('Accession'))
            protein_sequence = clean_source_value(row.get('Protein'))
            gene_family = clean_source_value(row.get('Gene_Description'))

            opsin = None
            if accession:
                opsin = Opsin.objects.filter(accession__iexact=accession).order_by('opsinid').first()
            if opsin is None and protein_sequence:
                opsin = Opsin.objects.filter(protein_sequence=protein_sequence).order_by('opsinid').first()

            if opsin is None:
                opsin = Opsin.objects.create(
                    accession=accession or None,
                    protein_sequence=protein_sequence or None,
                    gene_family=gene_family or None,
                    genus=clean_source_value(row.get('Genus')) or None,
                    species=clean_source_value(row.get('Species')) or None,
                    phylum=clean_source_value(row.get('Phylum')) or None,
                    reference=reference,
                    status='APPROVED',
                )
            else:
                changed = False
                for field, value in {
                    'protein_sequence': protein_sequence,
                    'gene_family': gene_family,
                    'genus': clean_source_value(row.get('Genus')),
                    'species': clean_source_value(row.get('Species')),
                    'phylum': clean_source_value(row.get('Phylum')),
                }.items():
                    if value and getattr(opsin, field) in (None, ''):
                        setattr(opsin, field, value)
                        changed = True
                if reference and opsin.reference_id is None:
                    opsin.reference = reference
                    changed = True
                if opsin.submitted_by_id is None and opsin.status != 'APPROVED':
                    opsin.status = 'APPROVED'
                    changed = True
                if changed:
                    opsin.save()

            lmax = parse_float(row.get('prediction_value'))
            if lmax is None:
                lmax = parse_float(row.get('LambdaMax'))

            source_value = clean_source_value(compendium_row.get(source_column)) if compendium_row is not None and source_column else ''
            inference_source = (
                f"MNM | comp_db_id={comp_db_id or 'unresolved'} | "
                f"source={source_column or 'unresolved'}:{source_value or 'unresolved'}"
            )

            HeterologousData.objects.update_or_create(
                source_dataset=MNM_SOURCE_DATASET,
                source_record_id=mnm_id,
                defaults={
                    'opsin': opsin,
                    'reference': reference,
                    'lambda_max': lmax if lmax is not None else 0.0,
                    'mutations': "Wildtype (Inferred)",
                    'is_inferred': True,
                    'inference_source': inference_source[:100],
                    'status': 'APPROVED',
                }
            )
            imported += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully imported {imported} MNM records.'))
        if multi_reference_rows:
            self.stdout.write(self.style.WARNING(
                f"{multi_reference_rows} compendium rows had multiple source columns; used SOURCE_REFERENCE_COLUMNS order."
            ))
        if unresolved_comp_ids:
            self.stdout.write(self.style.WARNING(
                f"{unresolved_comp_ids} MNM records had comp_db_id values missing from {compendium_csv}."
            ))
        if unresolved_references:
            self.stdout.write(self.style.WARNING(
                f"{unresolved_references} MNM records matched compendium rows but no source Reference could be resolved."
            ))
