from django.contrib import admin
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission

@admin.action(description='Approve selected records')
def approve_records(modeladmin, request, queryset):
    queryset.update(status='APPROVED', approved_by=request.user)

@admin.action(description='Reject selected records')
def reject_records(modeladmin, request, queryset):
    queryset.update(status='REJECTED', approved_by=request.user)

class ApprovalModelAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'status', 'created_at', 'submitted_by')
    list_filter = ('status', 'created_at')
    actions = [approve_records, reject_records]
    
    def save_model(self, request, obj, form, change):
        if getattr(obj, 'submitted_by', None) is None:
            obj.submitted_by = request.user
        obj.save()

@admin.register(Reference)
class ReferenceAdmin(ApprovalModelAdmin):
    list_display = ('refid', 'doi', 'year_of_publication', 'status')
    search_fields = ('doi', 'notes')

class HeterologousDataInline(admin.TabularInline):
    model = HeterologousData
    extra = 1

@admin.register(Opsin)
class OpsinAdmin(ApprovalModelAdmin):
    list_display = ('opsinid', 'genus', 'species', 'gene_family', 'accession', 'status')
    search_fields = ('genus', 'species', 'accession')
    list_filter = ('status', 'gene_family', 'phylum')
    inlines = [HeterologousDataInline] 

@admin.register(HeterologousData)
class HeterologousDataAdmin(ApprovalModelAdmin):
    list_display = ('hetid', 'get_opsin_organism', 'lambda_max', 'reference', 'status', 'is_inferred', 'source_dataset')
    search_fields = ('opsin__genus', 'opsin__species', 'opsin__accession', 'reference__doi', 'source_record_id', 'inference_source')
    list_filter = ('status', 'is_inferred', 'source_dataset', 'opsin__gene_family')

    @admin.display(description='Organism', ordering='opsin__genus')
    def get_opsin_organism(self, obj):
        return f"{obj.opsin.genus} {obj.opsin.species}" if obj.opsin else "Unknown"

@admin.register(CuratedSCP)
class CuratedSCPAdmin(ApprovalModelAdmin):
    list_display = ('scpid', 'get_organism', 'phylum', 'photoreceptor_type', 'lambda_max', 'reference', 'status', 'source_dataset')
    search_fields = ('genus', 'species', 'phylum', 'reference__doi', 'source_record_id', 'notes')
    list_filter = ('status', 'source_dataset', 'photoreceptor_type', 'chromophore')

    @admin.display(description='Organism', ordering='genus')
    def get_organism(self, obj):
        return f"{obj.genus} {obj.species}"

@admin.register(DataSubmission)
class DataSubmissionAdmin(admin.ModelAdmin):
    list_display = ('data_type', 'genus', 'species', 'lambda_max', 'doi', 'status', 'submitted_at')
    list_filter = ('status', 'data_type', 'submitted_at')
    search_fields = ('genus', 'species', 'doi', 'notes', 'submitter_email')
    
    @admin.action(description='Mark selected as Integrated')
    def mark_integrated(self, request, queryset):
        queryset.update(status='APPROVED')
    
    actions = [mark_integrated]
