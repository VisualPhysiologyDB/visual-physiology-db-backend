from django.contrib import admin
from .models import Reference, Opsin, HeterologousData

# Custom admin action to approve records in bulk
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
    
    # Automatically set the 'submitted_by' user when a caretaker creates a record in the admin
    def save_model(self, request, obj, form, change):
        if getattr(obj, 'submitted_by', None) is None:
            obj.submitted_by = request.user
        obj.save()

@admin.register(Reference)
class ReferenceAdmin(ApprovalModelAdmin):
    list_display = ('refid', 'doi', 'year_of_publication', 'status')
    search_fields = ('doi', 'notes')

@admin.register(Opsin)
class OpsinAdmin(ApprovalModelAdmin):
    list_display = ('opsinid', 'genus', 'species', 'gene_family', 'accession', 'status')
    search_fields = ('genus', 'species', 'accession')
    list_filter = ('status', 'gene_family', 'phylum')

@admin.register(HeterologousData)
class HeterologousDataAdmin(ApprovalModelAdmin):
    list_display = ('hetid', 'genus', 'species', 'lambda_max', 'status')
    search_fields = ('genus', 'species', 'accession')

