from rest_framework import serializers
from .models import Reference, Opsin, HeterologousData

class ReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reference
        fields = ['refid', 'doi', 'year_of_publication', 'notes', 'status']

class OpsinSerializer(serializers.ModelSerializer):
    # Read-only fully nested reference for GET requests
    reference = ReferenceSerializer(read_only=True) 
    
    # Write-only ID for POST/PUT requests
    reference_id = serializers.PrimaryKeyRelatedField(
        queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = Opsin
        fields = [
            'opsinid', 'gene_family', 'phylum', 'genus', 'species', 
            'accession', 'dna_sequence', 'protein_sequence', 
            'reference', 'reference_id', 'status'
        ]

class HeterologousDataSerializer(serializers.ModelSerializer):
    # Read-only fully nested data for informative API responses
    reference = ReferenceSerializer(read_only=True)
    opsin = OpsinSerializer(read_only=True)
    
    # Write-only IDs to allow easy form submissions from the front-end
    reference_id = serializers.PrimaryKeyRelatedField(
        queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True
    )
    opsin_id = serializers.PrimaryKeyRelatedField(
        queryset=Opsin.objects.all(), source='opsin', write_only=True, required=False, allow_null=True
    )

    class Meta:
        model = HeterologousData
        fields = [
            'hetid', 'opsin', 'opsin_id', 'mutations', 'lambda_max', 
            'error', 'cell_culture', 'reference', 'reference_id', 'status'
        ]