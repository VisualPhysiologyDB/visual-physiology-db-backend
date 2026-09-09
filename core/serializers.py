import math
import re
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError as ModelValidationError
from rest_framework import serializers
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission, VisualAcuity, SubmissionReceipt, ACUITY_MEASUREMENTS
from .bibliography import classify_identifier, reference_link
from .validators import positive_finite


def clean_reference_identifier(value):
    identified = classify_identifier(value)
    return identified['doi'] or (value or '').strip()


class ReferenceSerializer(serializers.ModelSerializer):
    link = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()

    def get_link(self, obj):
        return reference_link(obj)

    def get_notes(self, obj):
        # Defence for old databases before the privacy migration has run.
        return re.sub(r'^.*Submitter email:.*(?:\n|$)', '', obj.notes or '', flags=re.M | re.I).strip() or None

    class Meta:
        model = Reference
        fields = ['refid', 'doi', 'link', 'source_url', 'title', 'publication_date', 'online_date', 'print_date', 'year_of_publication', 'measurement_methods', 'mom_raw', 'raw_citation', 'identifier_kind', 'notes', 'status']
        read_only_fields = fields


class PublicRelationsMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Public nested serializers must not leak pending relational metadata.
        for field in ('reference', 'opsin'):
            related = getattr(instance, field, None)
            if related is not None and related.status != 'APPROVED':
                data[field] = None
        return data


class OpsinSerializer(PublicRelationsMixin, serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)

    class Meta:
        model = Opsin
        fields = ['opsinid', 'gene_family', 'phylum', 'genus', 'species', 'accession', 'dna_sequence', 'protein_sequence', 'reference', 'status']
        read_only_fields = fields


class HeterologousDataSerializer(PublicRelationsMixin, serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)
    opsin = OpsinSerializer(read_only=True)

    class Meta:
        model = HeterologousData
        fields = ['hetid', 'opsin', 'mutations', 'lambda_max', 'error', 'cell_culture', 'reference', 'status', 'is_inferred', 'inference_source']
        read_only_fields = fields


class CuratedSCPSerializer(PublicRelationsMixin, serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)

    class Meta:
        model = CuratedSCP
        fields = ['scpid', 'genus', 'species', 'phylum', 'photoreceptor_type', 'cell_subtype', 'lambda_max', 'error', 'chromophore', 'notes', 'reference', 'status']
        read_only_fields = fields


class VisualAcuitySerializer(PublicRelationsMixin, serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)

    class Meta:
        model = VisualAcuity
        fields = ['acuid', 'genus', 'species', 'eye_type', *ACUITY_MEASUREMENTS, 'reference', 'feller_ref_id', 'notes', 'source_dataset', 'source_record_id', 'source_data', 'quality_flags', 'status']
        read_only_fields = fields


class DataSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSubmission
        fields = '__all__'


class SubmissionCreateSerializer(serializers.Serializer):
    submission_type = serializers.CharField(required=False, allow_blank=True)
    data_type = serializers.CharField(required=False, allow_blank=True)
    relevance = serializers.CharField(required=False, allow_blank=True, max_length=200)
    doi = serializers.CharField(required=True, allow_blank=False, max_length=2000)
    year_of_publication = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=9999)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=20000)
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
    eye_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    cpd = serializers.FloatField(required=False, allow_null=True, validators=[positive_finite])
    body_length_cm = serializers.FloatField(required=False, allow_null=True, validators=[positive_finite])
    interommatidial_angle_deg = serializers.FloatField(required=False, allow_null=True, validators=[positive_finite])
    acceptance_angle_deg = serializers.FloatField(required=False, allow_null=True, validators=[positive_finite])
    lens_diameter_mm = serializers.FloatField(required=False, allow_null=True, validators=[positive_finite])

    DATA_TYPE_LABELS = {'heterologous': 'Heterologous', 'heterologous expression': 'Heterologous', 'scp': 'SCP', 'single-cell photometry': 'SCP', 'single cell photometry': 'SCP', 'acuity': 'Visual Acuity', 'visual acuity': 'Visual Acuity'}

    def validate(self, attrs):
        forbidden = {'status', 'approved_by', 'submitted_by', 'source_dataset', 'source_record_id', 'reference_id', 'duplicate_of', 'duplicate_of_id'} & self.initial_data.keys()
        if forbidden:
            raise serializers.ValidationError({k: 'This field cannot be set by a public submitter.' for k in forbidden})
        submission_type = (attrs.get('submission_type') or '').upper().strip()
        if not submission_type:
            submission_type = 'DATA' if any(attrs.get(f) not in (None, '') for f in ('lambda_max', 'genus', 'species', 'accession', 'cpd')) else 'PUBLICATION'
        if submission_type not in {'PUBLICATION', 'DATA'}:
            raise serializers.ValidationError({'submission_type': 'Use PUBLICATION or DATA.'})
        attrs['submission_type'] = submission_type
        identified = classify_identifier(attrs.get('doi'))
        if not identified['doi'] and not identified['source_url']:
            raise serializers.ValidationError({'doi': 'Supply a DOI or an http(s) source URL.'})
        if identified['doi'] and len(identified['doi']) > 255:
            raise serializers.ValidationError({'doi': 'DOI is too long.'})
        attrs['doi'] = identified['doi'] or identified['source_url']
        if submission_type == 'PUBLICATION':
            attrs['relevance'] = attrs.get('relevance') or attrs.get('data_type') or 'Both / unclear'
            return attrs
        data_type = self.DATA_TYPE_LABELS.get((attrs.get('data_type') or '').lower().strip())
        if not data_type:
            raise serializers.ValidationError({'data_type': 'Use Heterologous, SCP, or Visual Acuity.'})
        attrs['data_type'] = data_type
        required = ['genus', 'species', 'cpd' if data_type == 'Visual Acuity' else 'lambda_max']
        missing = {f: 'Required for this data type.' for f in required if attrs.get(f) in (None, '')}
        if missing:
            raise serializers.ValidationError(missing)
        if data_type != 'Visual Acuity':
            lmax = attrs['lambda_max']
            if not math.isfinite(lmax) or (lmax != 0 and not 300 <= lmax <= 800):
                raise serializers.ValidationError({'lambda_max': 'Use 300–800 nm, or the legacy 0 sentinel.'})
        error = attrs.get('error')
        if error is not None and (not math.isfinite(error) or error < 0):
            raise serializers.ValidationError({'error': 'Use a finite nonnegative error.'})
        return attrs

    def get_or_create_reference(self, attrs, submitted_by):
        identifier = classify_identifier(attrs['doi'])
        normalized = identifier['doi'] or identifier['source_url']
        # Compare canonical DOIs across legacy URL/case variants; prefer approved,
        # then pending, then rejected, and lowest refid within each status.
        matches = []
        for ref in Reference.objects.order_by('refid'):
            other = classify_identifier(ref.doi)
            if normalized == (other['doi'] or other['source_url'] or ref.source_url):
                matches.append(ref)
        if matches:
            return min(matches, key=lambda r: ({'APPROVED': 0, 'PENDING': 1, 'REJECTED': 2}[r.status], r.pk))
        return Reference.objects.create(doi=identifier['doi'], source_url=identifier['source_url'],
            identifier_kind=identifier['kind'], year_of_publication=attrs.get('year_of_publication'),
            notes=attrs.get('notes'), status='PENDING', submitted_by=submitted_by)

    def get_or_create_opsin(self, attrs, ref, user):
        fields = ('gene_family', 'phylum', 'genus', 'species', 'accession', 'dna_sequence', 'protein_sequence')
        supplied = {f: attrs.get(f) or None for f in fields}
        # Reuse only a compatible approved record; never fill fields on an existing
        # published opsin from unreviewed public data.
        if supplied['accession']:
            for obj in Opsin.objects.filter(accession__iexact=supplied['accession'], status='APPROVED').order_by('pk'):
                if all(v is None or getattr(obj, f) == v for f, v in supplied.items()):
                    return obj
        return Opsin.objects.create(**supplied, reference=ref, submitted_by=user, status='PENDING')

    @transaction.atomic
    def create(self, validated_data):
        user = validated_data.pop('submitted_by', None)
        attrs = validated_data
        ref = self.get_or_create_reference(attrs, user)
        result = {'submission_type': attrs['submission_type'], 'reference_id': ref.pk, 'status': 'PENDING'}
        if attrs['submission_type'] == 'PUBLICATION':
            result.update(relevance=attrs['relevance'], reference_status=ref.status)
        else:
            data_type = attrs['data_type']
            shared = {'reference': ref, 'submitted_by': user, 'status': 'PENDING'}
            if data_type == 'Visual Acuity':
                observation = VisualAcuity.objects.create(**shared, **{f: attrs.get(f) for f in ('genus', 'species', 'eye_type', 'notes', *ACUITY_MEASUREMENTS)})
            elif data_type == 'Heterologous':
                opsin = self.get_or_create_opsin(attrs, ref, user)
                observation = HeterologousData.objects.create(**shared, opsin=opsin, **{f: attrs.get(f) for f in ('lambda_max', 'mutations', 'error', 'cell_culture')})
                result['opsin_id'] = opsin.pk
            else:
                try:
                    with transaction.atomic():
                        observation = CuratedSCP.objects.create(**shared, **{f: attrs.get(f) for f in ('genus', 'species', 'phylum', 'photoreceptor_type', 'cell_subtype', 'lambda_max', 'error', 'chromophore', 'notes')})
                except ModelValidationError as exc:
                    raise serializers.ValidationError(exc.message_dict) from exc
                except IntegrityError as exc:
                    raise serializers.ValidationError({'lambda_max': 'This observation conflicts with an existing record. Check species, wavelength and publication.'}) from exc
            result.update(data_type=data_type, record_id=observation.pk)
        receipt = SubmissionReceipt.objects.create(reference=ref, submitter_email=attrs.get('submitter_email'), payload={k: v for k, v in attrs.items() if k != 'submitter_email'}, result=result)
        result['receipt_id'] = receipt.pk
        return result
