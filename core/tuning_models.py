"""Curated evidence and immutable sequence snapshots for the beta tuning mapper."""
import math
import re
from django.db import models
from django.core.exceptions import ValidationError
from .models import ApprovalModel


class TuningProtein(ApprovalModel):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    family = models.CharField(max_length=20, choices=[('C_OPSIN','Ciliary visual opsin'),('R_OPSIN','Rhabdomeric visual opsin'),('OTHER','Other / unsure')])
    subtype = models.CharField(max_length=50, blank=True)
    accession = models.CharField(max_length=512, blank=True)
    sequence = models.TextField()
    numbering_note = models.TextField()
    provenance = models.JSONField(default=dict)
    structure = models.JSONField(default=dict, blank=True, help_text='Pinned structure ID/file, chain and sequence-to-coordinate map')

    def clean(self):
        super().clean()
        if not isinstance(self.sequence,str) or not re.fullmatch('[ACDEFGHIKLMNPQRSTVWYX]+', self.sequence):
            raise ValidationError({'sequence':'Use an ungapped uppercase protein sequence.'})

    def __str__(self):
        return self.name


class TuningEvidence(ApprovalModel):
    key = models.SlugField(max_length=180, unique=True)
    protein = models.ForeignKey(TuningProtein, on_delete=models.PROTECT, related_name='tuning_evidence')
    title = models.CharField(max_length=250)
    subtype = models.CharField(max_length=80)
    family = models.CharField(max_length=20, choices=TuningProtein._meta.get_field('family').choices)
    organism = models.CharField(max_length=200)
    category = models.CharField(max_length=16, choices=[('MEASURED','Measured'),('LITERATURE','Literature supported'),('PROPOSED','Proposed candidate')])
    changes = models.JSONField(help_text='List of {position, from, to}; null from/to means a site-level claim, not an established substitution.')
    original_notation = models.TextField(blank=True)
    baseline_label = models.TextField(blank=True, help_text='Exact comparator construct, including background mutations/chimerism')
    wild_type_assay = models.ForeignKey('core.HeterologousData', on_delete=models.PROTECT, null=True, blank=True, related_name='tuning_comparators')
    mutant_assay = models.ForeignKey('core.HeterologousData', on_delete=models.PROTECT, null=True, blank=True, related_name='tuning_mutants')
    baseline_nm = models.FloatField(null=True, blank=True)
    mutant_nm = models.FloatField(null=True, blank=True)
    reported_shift_nm = models.FloatField(null=True, blank=True, help_text='Only an explicitly published signed shift; never a predicted target effect')
    conditions = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    source_locator = models.TextField(help_text='Figure/table/page or source row identifying this assertion')
    release = models.CharField(max_length=60)
    references = models.ManyToManyField('core.Reference', through='TuningCitation')

    @property
    def shift_nm(self):
        if self.baseline_nm is not None and self.mutant_nm is not None:
            return round(self.mutant_nm - self.baseline_nm, 8)
        return self.reported_shift_nm

    def clean(self):
        super().clean()
        if re.search(r'chimer(?:a|ic)',self.original_notation+' '+self.baseline_label,re.I):
            raise ValidationError('Chimeric constructs are outside the beta catalogue scope.')
        if not isinstance(self.changes, list) or not self.changes:
            raise ValidationError({'changes':'At least one explicit coordinate is required.'})
        protein=getattr(self,'protein',None)
        if protein is None:raise ValidationError({'protein':'Choose the source numbering protein.'})
        positions=set()
        for c in self.changes:
            if not isinstance(c,dict) or type(c.get('position')) is not int or c['position'] < 1 or c['position'] > len(protein.sequence):
                raise ValidationError({'changes':'Positions must index the specified reference protein (1-based).'})
            if c['position'] in positions:raise ValidationError({'changes':'Each site must occur once per construct.'})
            positions.add(c['position'])
            for k in ('from','to'):
                if c.get(k) is not None and not re.fullmatch('[ACDEFGHIKLMNPQRSTVWY]',str(c[k])):
                    raise ValidationError({'changes':f'{k} must be one standard amino acid or null.'})
        for k in ('baseline_nm','mutant_nm','reported_shift_nm'):
            value=getattr(self,k)
            if value is not None and (not math.isfinite(value) or (k!='reported_shift_nm' and value<=0)):
                raise ValidationError({k:'Use a finite value; wavelengths must be positive.'})
        if (self.baseline_nm is None) != (self.mutant_nm is None):
            raise ValidationError('Supply both comparator and mutant wavelengths, or neither.')
        if self.category=='MEASURED' and (self.shift_nm is None or not self.baseline_label):
            raise ValidationError('Measured evidence requires an explicit comparator and measured/reported shift.')
        if self.reported_shift_nm is not None and self.baseline_nm is not None and abs(self.shift_nm-self.reported_shift_nm)>0.01:
            raise ValidationError('Reported shift conflicts with the supplied wavelength pair.')
        if self.category=='PROPOSED' and self.shift_nm is not None:
            raise ValidationError('A proposed site must not carry an experimentally attributed shift.')
        for assay in (self.wild_type_assay,self.mutant_assay):
            if assay and assay.is_inferred:
                raise ValidationError('An inferred wavelength cannot be experimental tuning evidence.')
            if assay and re.search(r'chimer(?:a|ic)',assay.mutations or '',re.I):
                raise ValidationError('Chimeric source assays are outside beta scope.')

    def __str__(self):
        return self.title


class TuningCitation(models.Model):
    evidence = models.ForeignKey(TuningEvidence,on_delete=models.CASCADE,related_name='citations')
    reference = models.ForeignKey('core.Reference',on_delete=models.PROTECT)
    role = models.CharField(max_length=20,choices=[('PRIMARY','Primary experiment'),('REVIEW','Review'),('CONTRADICTS','Contradictory/no-effect evidence')])
    locator = models.TextField(blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['evidence','reference','role'],name='unique_tuning_citation')]


class TuningAudit(models.Model):
    created_at=models.DateTimeField(auto_now_add=True)
    actor=models.CharField(max_length=100)
    object_type=models.CharField(max_length=30)
    object_key=models.CharField(max_length=180)
    before=models.JSONField(null=True)
    after=models.JSONField()
    reason=models.TextField()


class TuningCandidate(models.Model):
    """Repeatable extraction, separate from published scientific assertions."""
    assay = models.OneToOneField('core.HeterologousData', on_delete=models.PROTECT, related_name='tuning_candidate')
    fingerprint = models.CharField(max_length=64)
    analysis = models.JSONField(default=dict)
    outcome = models.CharField(max_length=50, db_index=True)
    decision = models.CharField(max_length=10, default='PENDING', choices=ApprovalModel.STATUS_CHOICES)
    selection = models.CharField(max_length=100, blank=True, help_text='A supported comparator/numbering option from the analysis.')
    numbering_confirmed = models.BooleanField(default=False, help_text='I checked the publication’s numbering against the source protein.')
    conditions_confirmed = models.BooleanField(default=False, help_text='I checked that comparator and mutant use compatible constructs, chromophore, pH and measurement conditions.')
    cross_publication_confirmed = models.BooleanField(default=False, help_text='For a WT from another publication: I checked both sources and justified this cross-study comparison in the review note.')
    review_note = models.TextField(blank=True, help_text='Cite the publication figure/table/page and explain the confirmed numbering and comparator.')
    reviewed_fingerprint = models.CharField(max_length=64, blank=True)
    stale = models.BooleanField(default=False, help_text='Source inputs changed after extraction/review; re-extract and review before publication.')
    evidence = models.OneToOneField(TuningEvidence, null=True, blank=True, on_delete=models.PROTECT, related_name='extracted_candidate')
    reviewed_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    approval_mode = models.CharField(max_length=10, blank=True, choices=[('AUTO', 'Automatic sequence match'), ('MANUAL', 'Curator approval')])
    approval_policy = models.JSONField(default=dict, blank=True)
    require_condition_match = models.BooleanField(default=False, help_text='Optional: require matching, nonblank cell culture, purification and spectrum labels before approval.')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Het {self.assay_id}: {self.analysis.get("notation", "")} ({self.outcome})'

    def clean(self):
        super().clean()
        if self.decision == 'APPROVED':
            from .tuning_extraction import validate_review
            validate_review(self)
