from django.db import models
from django.contrib.auth.models import User

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
    doi = models.CharField(max_length=255, null=True, blank=True)
    year_of_publication = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

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

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(lambda_max__gte=200) & models.Q(lambda_max__lte=800) | models.Q(lambda_max=0.0),
                name='valid_lambda_max_range'
            )
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
    notes = models.CharField(max_length=100, blank=True, null=True)
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='scp_assays')
    
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