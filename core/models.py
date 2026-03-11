from django.db import models
from django.contrib.auth.models import User

class ApprovalModel(models.Model):
    """
    Abstract base model that adds an approval workflow to any inherited model.
    """
    STATUS_CHOICES = (
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved (Published)'),
        ('REJECTED', 'Rejected'),
    )
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Track who submitted the data and who approved it
    submitted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_submissions")
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_approvals")

    class Meta:
        abstract = True

# --- VPOD Specific Models ---

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
    #class_phylo = models.CharField(max_length=100, blank=True, null=True)
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    accession = models.CharField(max_length=100, blank=True, null=True)
    dna_sequence = models.TextField(blank=True, null=True)
    protein_sequence = models.TextField(blank=True, null=True)
    
    # Relational link to the Reference table
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='opsins')

    def __str__(self):
        return f"{self.genus} {self.species} ({self.gene_family})"

class HeterologousData(ApprovalModel):
    hetid = models.AutoField(primary_key=True)
    genus = models.CharField(max_length=100, blank=True, null=True)
    species = models.CharField(max_length=100, blank=True, null=True)
    accession = models.CharField(max_length=100, blank=True, null=True)
    mutations = models.CharField(max_length=255, blank=True, null=True)
    lambda_max = models.FloatField(help_text="Wavelength of maximum absorbance")
    error = models.FloatField(blank=True, null=True)
    cell_culture = models.CharField(max_length=100, blank=True, null=True)
    
    # Relational link to the Reference table
    reference = models.ForeignKey(Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='heterologous_records')

    def __str__(self):
        return f"{self.genus} {self.species} - {self.lambda_max}nm"
