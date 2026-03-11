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
    
    # Automatically set the 'submitted_by' user when an admin creates a record manually
    def save_model(self, request, obj, form, change):
        if getattr(obj, 'submitted_by', None) is None:
            obj.submitted_by = request.user
        obj.save()


@admin.register(Reference)
class ReferenceAdmin(ApprovalModelAdmin):
    list_display = ('refid', 'doi', 'year_of_publication', 'status')
    search_fields = ('doi', 'notes')


# INLINE VIEW: Allows adding/editing HeterologousData directly inside the Opsin page
class HeterologousDataInline(admin.TabularInline):
    model = HeterologousData
    extra = 1 # Number of empty rows to display
    fields = ('lambda_max', 'mutations', 'cell_culture', 'reference', 'status')


@admin.register(Opsin)
class OpsinAdmin(ApprovalModelAdmin):
    list_display = ('opsinid', 'genus', 'species', 'gene_family', 'accession', 'status')
    search_fields = ('genus', 'species', 'accession')
    list_filter = ('status', 'gene_family', 'phylum')
    inlines = [HeterologousDataInline] # Show related Heterologous records below


@admin.register(HeterologousData)
class HeterologousDataAdmin(ApprovalModelAdmin):
    list_display = ('hetid', 'get_opsin_organism', 'lambda_max', 'status')
    
    # Search uses the double-underscore to search related fields
    search_fields = ('opsin__genus', 'opsin__species', 'opsin__accession')
    list_filter = ('status', 'opsin__gene_family')

    # Custom column since genus/species are no longer directly on this model
    @admin.display(description='Organism', ordering='opsin__genus')
    def get_opsin_organism(self, obj):
        if obj.opsin:
            return f"{obj.opsin.genus} {obj.opsin.species}"
        return "Unknown"