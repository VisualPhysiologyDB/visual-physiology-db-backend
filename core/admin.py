from django.contrib import admin
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission, VisualAcuity, ReferenceMetadataAudit, SubmissionReceipt

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
    list_display = ('refid', 'title', 'doi', 'publication_date', 'year_of_publication', 'status')
    search_fields = ('=refid', 'doi', 'title', 'source_url', 'raw_citation', 'mom_raw', 'measurement_methods', 'notes')
    readonly_fields = ('source_data',)

    def save_model(self, request, obj, form, change):
        from django.utils import timezone
        from .metadata_recovery import digest
        from .management.commands.enrich_references import FIELDS
        previous = Reference.objects.get(pk=obj.pk) if change else None
        super().save_model(request, obj, form, change)
        for field in FIELDS:
            old = getattr(previous, field) if previous else None
            new = getattr(obj, field)
            if old != new:
                stamp = timezone.now()
                ReferenceMetadataAudit.objects.create(reference=obj, field=field, original_value=old,
                    recovered_value=new, provider=f'admin user {request.user.pk}', evidence_url=f'/admin/core/reference/{obj.pk}/change/',
                    retrieved_at=stamp, decision='curator', applied=True, reason='Administrator edit',
                    run_id=str(stamp), fingerprint=digest([obj.pk, field, str(stamp), old, new]))

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
    list_display = ('scpid', 'get_organism', 'phylum', 'photoreceptor_type', 'lambda_max', 'reference', 'status', 'source_dataset', 'duplicate_of')
    search_fields = ('genus', 'species', 'phylum', 'reference__doi', 'source_record_id', 'notes')
    list_filter = ('status', ('duplicate_of', admin.EmptyFieldListFilter), 'source_dataset', 'photoreceptor_type', 'chromophore')
    autocomplete_fields = ('reference', 'duplicate_of')

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


@admin.register(VisualAcuity)
class VisualAcuityAdmin(ApprovalModelAdmin):
    list_display = ('acuid', 'genus', 'species', 'eye_type', 'cpd', 'reference', 'status', 'source_record_id')
    list_filter = ('status', 'eye_type', 'source_dataset')
    search_fields = ('genus', 'species', 'source_record_id', 'reference__doi', 'reference__title', 'notes')
    readonly_fields = ('source_data', 'import_baseline', 'quality_flags')
    autocomplete_fields = ('reference',)


class ImmutableAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferenceMetadataAudit)
class ReferenceMetadataAuditAdmin(ImmutableAdmin):
    list_display = ('reference', 'field', 'decision', 'provider', 'retrieved_at', 'applied')
    list_filter = ('decision', 'provider', 'applied')
    search_fields = ('=reference__refid', 'field', 'reason', 'run_id')


@admin.register(SubmissionReceipt)
class SubmissionReceiptAdmin(ImmutableAdmin):
    list_display = ('id', 'reference', 'created_at', 'submitter_email')
    list_filter = ('created_at',)
    search_fields = ('reference__doi', 'reference__title', 'submitter_email', 'payload')

# Private literature inbox and run history; no public API registration.
from . import discovery_admin
