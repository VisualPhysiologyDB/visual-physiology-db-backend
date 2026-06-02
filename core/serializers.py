from rest_framework import serializers
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission

class ReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reference
        fields = ['refid', 'doi', 'year_of_publication', 'notes', 'status']

class OpsinSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True) 
    reference_id = serializers.PrimaryKeyRelatedField(queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True)

    class Meta:
        model = Opsin
        fields = ['opsinid', 'gene_family', 'phylum', 'genus', 'species', 'accession', 'dna_sequence', 'protein_sequence', 'reference', 'reference_id', 'status']

class HeterologousDataSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)
    opsin = OpsinSerializer(read_only=True)
    reference_id = serializers.PrimaryKeyRelatedField(queryset=Reference.objects.all(), source='reference', write_only=True, required=False, allow_null=True)
    opsin_id = serializers.PrimaryKeyRelatedField(queryset=Opsin.objects.all(), source='opsin', write_only=True, required=False, allow_null=True)

    class Meta:
        model = HeterologousData
        fields = ['hetid', 'opsin', 'opsin_id', 'mutations', 'lambda_max', 'error', 'cell_culture', 'reference', 'reference_id', 'status']

# --- NEW Serializers ---
class CuratedSCPSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)
    
    class Meta:
        model = CuratedSCP
        fields = ['scpid', 'genus', 'species', 'phylum', 'photoreceptor_type', 'lambda_max', 'error', 'chromophore', 'reference', 'reference_id', 'status']

class DataSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataSubmission
        fields = '__all__'