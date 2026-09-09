from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower, Trim, Coalesce
from .validators import positive_finite, partial_date, MAX_FINITE

class ApprovalModel(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved (Published)'),
        ('REJECTED', 'Rejected'),
    )
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_submissions")
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_approvals")

    class Meta:
        abstract = True

class Reference(ApprovalModel):
    refid = models.AutoField(primary_key=True)
    doi = models.TextField(null=True, blank=True, help_text="DOI; legacy identifiers are retained until audited normalization")
    year_of_publication = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    title = models.TextField(null=True, blank=True)
    publication_date = models.CharField(max_length=10, null=True, blank=True, validators=[partial_date], help_text="YYYY, YYYY-MM or YYYY-MM-DD; selected bibliographic date")
    online_date = models.CharField(max_length=10, null=True, blank=True, validators=[partial_date])
    print_date = models.CharField(max_length=10, null=True, blank=True, validators=[partial_date])
    source_url = models.URLField(max_length=2000, null=True, blank=True)
    raw_citation = models.TextField(null=True, blank=True)
    identifier_kind = models.CharField(max_length=30, null=True, blank=True)
    mom_raw = models.TextField(null=True, blank=True, help_text="Original source MOM, including uncertainty")
    measurement_methods = models.JSONField(null=True, blank=True, help_text="List of name, kind, uncertain, raw; source assertions, not bibliographic inference")
    source_data = models.JSONField(default=dict, blank=True, help_text="Original CSV columns; used to protect metadata on reimport")

    def __str__(self):
        return f"Ref {self.refid}: {self.doi or 'No DOI'}"

class Opsin(ApprovalModel):
    opsinid = models.AutoField(primary_key=True)
    gene_family = models.CharField(max_length=100, blank=True, null=True)
    phylum = models.CharField(max_length=100, blank=True, null=True)
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    accession = models.CharField(max_length=100, blank=True, null=True)
    dna_sequence = models.TextField(blank=True, null=True)
    protein_sequence = models.TextField(blank=True, null=True)
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='opsins')

    def __str__(self):
        return f"{self.genus} {self.species} ({self.gene_family})"

class HeterologousData(ApprovalModel):
    hetid = models.AutoField(primary_key=True)
    opsin = models.ForeignKey(Opsin, on_delete=models.CASCADE, null=True, blank=True, related_name='heterologous_records')
    mutations = models.CharField(max_length=255, blank=True, null=True)
    lambda_max = models.FloatField(help_text="Wavelength of maximum absorbance")
    error = models.FloatField(blank=True, null=True)
    cell_culture = models.CharField(max_length=100, blank=True, null=True)
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='heterologous_assays')

    # NEW FIELDS for MNM integration:
    is_inferred = models.BooleanField(default=False, help_text="Computationally inferred via MNM pipeline")
    inference_source = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. OPTICS, MNM")
    source_dataset = models.CharField(max_length=100, blank=True, null=True)
    source_record_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(lambda_max__gte=300) & models.Q(lambda_max__lte=800) | models.Q(lambda_max=0.0),
                name='valid_lambda_max_range'
            ),
            models.UniqueConstraint(
                fields=['source_dataset', 'source_record_id'],
                name='unique_heterologous_source_record'
            ),
        ]

    def __str__(self):
        opsin_name = f"{self.opsin.genus} {self.opsin.species}" if self.opsin else "Unknown Opsin"
        return f"{opsin_name} - {self.lambda_max}nm"

# --- NEW: Single Cell Photometry (SCP) Model ---
class CuratedSCP(ApprovalModel):
    scpid = models.AutoField(primary_key=True)
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    phylum = models.CharField(max_length=100, blank=True, null=True)
    photoreceptor_type = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Rod, Cone, LWS, SWS")
    cell_subtype =  models.CharField(max_length=100, blank=True, null=True, help_text="e.g. single, double")
    lambda_max = models.FloatField(help_text="Wavelength of maximum absorbance", null=True, blank=True)
    error = models.FloatField(blank=True, null=True)
    chromophore = models.CharField(max_length=50, blank=True, null=True, help_text="e.g. A1, A2")
    notes = models.TextField(blank=True, null=True)
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='scp_assays')
    source_dataset = models.CharField(max_length=100, blank=True, null=True)
    source_record_id = models.CharField(max_length=100, blank=True, null=True)
    duplicate_of = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
        related_name='retained_duplicates', help_text='Retained source duplicate; excluded from the public MSP/SCP table. No source data or approval history is deleted.')

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(lambda_max__gte=300) & models.Q(lambda_max__lte=800) | models.Q(lambda_max=0.0),
                name='valid_lambda_max_range_scp'
            ),
            models.UniqueConstraint(
                fields=['source_dataset', 'source_record_id'],
                name='unique_scp_source_record'
            ),
            models.UniqueConstraint(
                Lower(Trim(Coalesce('genus', models.Value('')))),
                Lower(Trim(Coalesce('species', models.Value('')))),
                models.F('lambda_max'), Coalesce('reference', models.Value(-1)),
                condition=models.Q(duplicate_of__isnull=True, lambda_max__gt=0),
                name='unique_scp_taxon_wavelength_ref',
            ),
        ]

    def clean(self):
        super().clean()
        from .scp_duplicates import duplicate_of, observation_key
        if self.duplicate_of_id:
            target = self.duplicate_of
            if target.pk == self.pk or target.duplicate_of_id or observation_key(self) is None or observation_key(self) != observation_key(target):
                raise ValidationError({'duplicate_of': 'Choose the retained primary record for the same species, measured wavelength and publication.'})
        elif duplicate := duplicate_of(self):
            raise ValidationError({'lambda_max': f'SCP record {duplicate.pk} already has this species and wavelength from the same publication. A different publication is allowed.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    
    def __str__(self):
        return f"SCP: {self.genus} {self.species} - {self.lambda_max}nm"

# --- User Data Submission Inbox ---
class DataSubmission(models.Model):
    """
    A flat holding table for public user submissions. 
    Admins review these and manually create the validated relational records.
    """
    STATUS_CHOICES = (('PENDING', 'Pending Review'), ('APPROVED', 'Integrated'), ('REJECTED', 'Rejected'))
    SUBMISSION_TYPES = (('PUBLICATION', 'Publication Suggestion'), ('DATA', 'Direct Data Entry'))
    
    # Task 1: Tracking the two-tiered forms
    submission_type = models.CharField(max_length=20, choices=SUBMISSION_TYPES, default='DATA')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    
    # Task 2: Removing Opsin (Sequence-only) from expected data types
    data_type = models.CharField(max_length=50, blank=True, null=True, help_text="Heterologous or SCP")
    
    phylum = models.CharField(max_length=100, blank=True, null=True)
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    accession = models.CharField(max_length=100, blank=True, null=True, help_text="If your sequence contains a mutation, format your accession as Acc_x#y (i.e NM_001014890_A292S)")
    mutations = models.CharField(max_length=255, blank=True, null=True)
    gene_family = models.CharField(max_length=100, blank=True, null=True)
    dna_sequence = models.TextField(blank=True, null=True)
    protein_sequence = models.TextField(blank=True, null=True)  
    lambda_max = models.FloatField(null=True, blank=True)
    error = models.FloatField(blank=True, null=True)
    cell_culture = models.CharField(max_length=100, blank=True, null=True)
  
    doi = models.CharField(max_length=255, help_text="Required for Publication Suggestion", default='No DOI')
    notes = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    submitter_email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return f"[{self.status}] {self.get_submission_type_display()} - {self.doi or self.genus}"


ACUITY_MEASUREMENTS = ('body_length_cm', 'interommatidial_angle_deg', 'acceptance_angle_deg', 'cpd', 'lens_diameter_mm')


class VisualAcuity(ApprovalModel):
    acuid = models.AutoField(primary_key=True)
    genus = models.CharField(max_length=100, null=True, blank=True)
    species = models.CharField(max_length=100, null=True, blank=True)
    eye_type = models.CharField(max_length=100, null=True, blank=True)
    body_length_cm = models.FloatField(null=True, blank=True, validators=[positive_finite], help_text="BL (cm); source-specific body-length convention may be unknown")
    interommatidial_angle_deg = models.FloatField(null=True, blank=True, validators=[positive_finite], help_text="Δϕ (degrees), angular separation of adjacent optical axes")
    acceptance_angle_deg = models.FloatField(null=True, blank=True, validators=[positive_finite], help_text="Δρ (degrees), photoreceptor acceptance angle; source conventions may differ")
    cpd = models.FloatField(null=True, blank=True, validators=[positive_finite], help_text="Visual acuity in cycles per degree, preserved as supplied")
    lens_diameter_mm = models.FloatField(null=True, blank=True, validators=[positive_finite])
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='acuity_observations')
    feller_ref_id = models.CharField(max_length=100, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    source_dataset = models.CharField(max_length=100, null=True, blank=True)
    source_record_id = models.CharField(max_length=100, null=True, blank=True)
    source_data = models.JSONField(default=dict, blank=True, help_text="All original columns, including raw taxonomic text")
    import_baseline = models.JSONField(default=dict, blank=True, help_text="Last imported normalized values for three-way updates")
    quality_flags = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['source_dataset', 'source_record_id'], name='unique_acuity_source_record'),
            models.CheckConstraint(condition=(models.Q(source_dataset__isnull=False) & ~models.Q(source_dataset='')) | models.Q(cpd__isnull=False), name='acuity_submission_requires_cpd'),
        ] + [models.CheckConstraint(condition=models.Q(**{f'{field}__isnull': True}) | (models.Q(**{f'{field}__gt': 0}) & models.Q(**{f'{field}__lte': MAX_FINITE})), name=f'acuity_valid_{field}') for field in ACUITY_MEASUREMENTS]

    def clean(self):
        super().clean()
        if not self.source_dataset and self.cpd is None:
            raise ValidationError({'cpd': 'CPD is required for a new observation.'})
        if bool(self.source_dataset) != bool(self.source_record_id):
            raise ValidationError('Source dataset and record ID must be supplied together.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Acuity {self.acuid}: {self.genus or ''} {self.species or ''} ({self.cpd} cycles/degree)"


class ReferenceMetadataAudit(models.Model):
    reference = models.ForeignKey(Reference, on_delete=models.PROTECT, related_name='metadata_audits')
    field = models.CharField(max_length=50)
    original_value = models.JSONField(null=True, blank=True)
    recovered_value = models.JSONField(null=True, blank=True)
    provider = models.CharField(max_length=100)
    evidence_url = models.TextField(blank=True)
    retrieved_at = models.DateTimeField()
    decision = models.CharField(max_length=20, help_text="verified, candidate, conflict, classified, curator")
    applied = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    run_id = models.CharField(max_length=100)
    fingerprint = models.CharField(max_length=64, unique=True)


class SubmissionReceipt(models.Model):
    """Private evidence for new relational submissions, including suggestions reusing a reference."""
    created_at = models.DateTimeField(auto_now_add=True)
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True)
    submitter_email = models.EmailField(null=True, blank=True)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)

# Register the private discovery models with Django while keeping their schema together.
from .discovery_models import (DiscoveryRun, DiscoveryCheckpoint, LiteratureCandidate,
    DiscoveryIdentifier, DiscoveryEvidence, DiscoveryReviewEvent)
