from rest_framework import serializers
from .models import Reference, Opsin, HeterologousData

class ReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reference
        fields = ['refid', 'doi', 'year_of_publication', 'notes']

class OpsinSerializer(serializers.ModelSerializer):
    # This embeds the reference data directly into the API response
    reference = ReferenceSerializer(read_only=True) 

    class Meta:
        model = Opsin
        fields = ['opsinid', 'gene_family', 'phylum', 'genus', 'species', 'accession', 'dna_sequence', 'protein_sequence', 'reference']

class HeterologousDataSerializer(serializers.ModelSerializer):
    reference = ReferenceSerializer(read_only=True)

    class Meta:
        model = HeterologousData
        fields = ['hetid', 'genus', 'species', 'accession', 'mutations', 'lambda_max', 'error', 'cell_culture', 'reference']
