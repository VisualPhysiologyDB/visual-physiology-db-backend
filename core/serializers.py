from rest_framework import serializers
from django.db import transaction
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission


def clean_reference_identifier(value):
    value = (value or '').strip()
    doi_prefixes = (
        'https://doi.org/',
        'http://doi.org/',
        'https://dx.doi.org/',
        'http://dx.doi.org/',
        'doi:',
    )
    lowered = value.lower()
    for prefix in doi_prefixes:
        if lowered.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def append_note(instance, note):
    note = (note or '').strip()
    if not note:
        return
    current = instance.notes or ''
    if note in current:
        return
    instance.notes = f"{current}\n\n{note}".strip()
    instance.save()


def first_non_blank(*values):
    for value in values:
        if value not in (None, ''):
            return value
    return None

class ReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reference
        fields = ['refid', 'doi', 'year_of_publication', 'notes', 'status']

class OpsinSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True) 
    reference_id = serializers.PrimaryKeyRelatedField(queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True)

    class Meta:
        model = Opsin
        fields = ['opsinid', 'gene_family', 'phylum', 'genus', 'species', 'accession', 'dna_sequence', 'protein_sequence', 'reference', 'reference_id', 'status']

class HeterologousDataSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)
    opsin = OpsinSerializer(read_only=True)
    reference_id = serializers.PrimaryKeyRelatedField(queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True)
    opsin_id = serializers.PrimaryKeyRelatedField(queryset=Opsin.objects.all(), source='opsin', write_only=True, required=False, allow_null=True)

    class Meta:
        model = HeterologousData
        fields = ['hetid', 'opsin', 'opsin_id', 'mutations', 'lambda_max', 'error', 'cell_culture', 'reference', 'reference_id', 'status', 'is_inferred', 'inference_source']

# --- NEW Serializers ---
class CuratedSCPSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)
    reference_id = serializers.PrimaryKeyRelatedField(queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = CuratedSCP
        fields = ['scpid', 'genus', 'species', 'phylum', 'photoreceptor_type', 'cell_subtype', 'lambda_max', 'error', 'chromophore', 'notes', 'reference', 'reference_id', 'status']

class DataSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSubmission
        fields = '__all__'


class SubmissionCreateSerializer(serializers.Serializer):
    submission_type = serializers.CharField(required=False, allow_blank=True)
    data_type = serializers.CharField(required=False, allow_blank=True)
    relevance = serializers.CharField(required=False, allow_blank=True)

    doi = serializers.CharField(required=True, allow_blank=False, max_length=255)
    year_of_publication = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    submitter_email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)

    phylum = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    genus = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    species = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    accession = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    mutations = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=255)
    gene_family = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    dna_sequence = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    protein_sequence = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    lambda_max = serializers.FloatField(required=False, allow_null=True)
    error = serializers.FloatField(required=False, allow_null=True)
    cell_culture = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    photoreceptor_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    cell_subtype = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    chromophore = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=50)

    DATA_TYPE_LABELS = {
        'heterologous': 'Heterologous',
        'heterologous expression': 'Heterologous',
        'scp': 'SCP',
        'single-cell photometry': 'SCP',
        'single cell photometry': 'SCP',
    }
    RELEVANCE_LABELS = {
        'heterologous': 'Heterologous',
        'heterologous expression': 'Heterologous',
        'scp': 'SCP',
        'single-cell photometry': 'SCP',
        'single cell photometry': 'SCP',
        'both': 'Both / unclear',
        'both / unclear': 'Both / unclear',
        'unclear': 'Both / unclear',
        'both/unclear': 'Both / unclear',
    }

    def validate(self, attrs):
        submission_type = (attrs.get('submission_type') or '').strip().upper()
        if not submission_type:
            has_detailed_fields = any(
                attrs.get(field) not in (None, '')
                for field in ('lambda_max', 'genus', 'species', 'accession', 'photoreceptor_type')
            )
            submission_type = 'DATA' if has_detailed_fields else 'PUBLICATION'
        if submission_type not in {'PUBLICATION', 'DATA'}:
            raise serializers.ValidationError({'submission_type': 'Use PUBLICATION or DATA.'})
        attrs['submission_type'] = submission_type

        attrs['doi'] = clean_reference_identifier(attrs.get('doi'))
        if not attrs['doi']:
            raise serializers.ValidationError({'doi': 'A DOI or stable reference URL is required.'})

        if submission_type == 'PUBLICATION':
            relevance_key = (attrs.get('relevance') or attrs.get('data_type') or 'unclear').strip().lower()
            attrs['relevance'] = self.RELEVANCE_LABELS.get(relevance_key, attrs.get('relevance') or attrs.get('data_type') or 'Both / unclear')
            return attrs

        data_type_key = (attrs.get('data_type') or '').strip().lower()
        data_type = self.DATA_TYPE_LABELS.get(data_type_key)
        if data_type is None:
            raise serializers.ValidationError({'data_type': 'Use Heterologous or SCP for detailed data submissions.'})
        attrs['data_type'] = data_type

        required_fields = ['genus', 'species', 'lambda_max']
        missing = [field for field in required_fields if attrs.get(field) in (None, '')]
        if missing:
            raise serializers.ValidationError({field: 'This field is required for detailed data submissions.' for field in missing})

        if data_type == 'Heterologous':
            lambda_max = attrs.get('lambda_max')
            if lambda_max != 0.0 and not 200 <= lambda_max <= 800:
                raise serializers.ValidationError({'lambda_max': 'Heterologous lambda_max must be between 200 and 800 nm, or 0.0.'})

        return attrs

    def submission_notes(self, attrs, extra_label=None):
        lines = ['Submitted through public VPOD data-submission form.']
        if extra_label:
            lines.append(extra_label)
        if attrs.get('submitter_email'):
            lines.append(f"Submitter email: {attrs['submitter_email']}")
        if attrs.get('notes'):
            lines.append(f"Submission notes: {attrs['notes']}")
        return "\n".join(lines)

    def get_or_create_reference(self, attrs, submitted_by, extra_label=None):
        doi = attrs['doi']
        reference = Reference.objects.filter(doi__iexact=doi).order_by('refid').first()
        notes = self.submission_notes(attrs, extra_label)

        if reference is None:
            return Reference.objects.create(
                doi=doi,
                year_of_publication=attrs.get('year_of_publication'),
                notes=notes,
                status='PENDING',
                submitted_by=submitted_by,
            )

        changed = False
        if attrs.get('year_of_publication') and not reference.year_of_publication:
            reference.year_of_publication = attrs['year_of_publication']
            changed = True
        if reference.notes is None:
            reference.notes = ''
            changed = True
        if notes not in (reference.notes or ''):
            reference.notes = f"{reference.notes}\n\n{notes}".strip()
            changed = True
        if changed:
            reference.save()
        return reference

    def get_or_create_opsin(self, attrs, reference, submitted_by):
        accession = (attrs.get('accession') or '').strip()
        protein_sequence = (attrs.get('protein_sequence') or '').strip()
        opsin = None

        if accession:
            opsin = Opsin.objects.filter(accession__iexact=accession).order_by('opsinid').first()
        if opsin is None and protein_sequence:
            opsin = Opsin.objects.filter(protein_sequence=protein_sequence).order_by('opsinid').first()

        field_values = {
            'gene_family': attrs.get('gene_family'),
            'phylum': attrs.get('phylum'),
            'genus': attrs.get('genus'),
            'species': attrs.get('species'),
            'accession': accession or None,
            'dna_sequence': attrs.get('dna_sequence'),
            'protein_sequence': protein_sequence or None,
            'reference': reference,
        }

        if opsin is None:
            return Opsin.objects.create(
                status='PENDING',
                submitted_by=submitted_by,
                **field_values,
            )

        changed = False
        for field, value in field_values.items():
            if value in (None, ''):
                continue
            if field == 'reference':
                if opsin.reference_id is None:
                    opsin.reference = value
                    changed = True
                continue
            if getattr(opsin, field) in (None, ''):
                setattr(opsin, field, value)
                changed = True
        if changed:
            opsin.save()
        return opsin

    @transaction.atomic
    def create(self, validated_data):
        submitted_by = validated_data.pop('submitted_by', None)
        submission_type = validated_data['submission_type']

        if submission_type == 'PUBLICATION':
            relevance = validated_data.get('relevance') or 'Both / unclear'
            reference = self.get_or_create_reference(
                validated_data,
                submitted_by,
                extra_label=f"Potential relevance: {relevance}",
            )
            return {
                'submission_type': submission_type,
                'status': reference.status,
                'reference_id': reference.refid,
                'relevance': relevance,
            }

        reference = self.get_or_create_reference(
            validated_data,
            submitted_by,
            extra_label=f"Detailed data type: {validated_data['data_type']}",
        )

        if validated_data['data_type'] == 'Heterologous':
            opsin = self.get_or_create_opsin(validated_data, reference, submitted_by)
            heterologous = HeterologousData.objects.create(
                opsin=opsin,
                reference=reference,
                mutations=validated_data.get('mutations'),
                lambda_max=validated_data['lambda_max'],
                error=validated_data.get('error'),
                cell_culture=validated_data.get('cell_culture'),
                status='PENDING',
                submitted_by=submitted_by,
            )
            return {
                'submission_type': submission_type,
                'data_type': validated_data['data_type'],
                'status': heterologous.status,
                'reference_id': reference.refid,
                'opsin_id': opsin.opsinid,
                'record_id': heterologous.hetid,
            }

        scp = CuratedSCP.objects.create(
            reference=reference,
            genus=validated_data.get('genus'),
            species=validated_data.get('species'),
            phylum=validated_data.get('phylum'),
            photoreceptor_type=validated_data.get('photoreceptor_type'),
            cell_subtype=validated_data.get('cell_subtype'),
            lambda_max=validated_data['lambda_max'],
            error=validated_data.get('error'),
            chromophore=validated_data.get('chromophore'),
            notes=validated_data.get('notes'),
            status='PENDING',
            submitted_by=submitted_by,
        )
        return {
            'submission_type': submission_type,
            'data_type': validated_data['data_type'],
            'status': scp.status,
            'reference_id': reference.refid,
            'record_id': scp.scpid,
        }
