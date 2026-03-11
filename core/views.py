import csv
from django.db import models
from django.http import HttpResponse
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from .models import Reference, Opsin, HeterologousData
from .serializers import ReferenceSerializer, OpsinSerializer, HeterologousDataSerializer

class SubmissionModelViewSet(viewsets.ModelViewSet):
    """
    A base ViewSet that:
    - Allows public reading of APPROVED records.
    - Allows authenticated users to POST new records (defaults to PENDING).
    - Allows users to view their own pending/rejected submissions.
    """
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        
        # 1. Staff/Admins can see everything for review
        if user.is_staff:
            return self.queryset.all()
            
        # 2. Authenticated users see APPROVED + their own submissions
        if user.is_authenticated:
            return self.queryset.filter(
                models.Q(status='APPROVED') | models.Q(submitted_by=user)
            )
            
        # 3. Anonymous public users only see APPROVED records
        return self.queryset.filter(status='APPROVED')

    def perform_create(self, serializer):
        # Tie the submission to the logged-in user and enforce PENDING status
        serializer.save(submitted_by=self.request.user, status='PENDING')

class ReferenceViewSet(SubmissionModelViewSet):
    queryset = Reference.objects.all()
    serializer_class = ReferenceSerializer

class OpsinViewSet(SubmissionModelViewSet):
    queryset = Opsin.objects.select_related('reference').all()
    serializer_class = OpsinSerializer
    
    # Allows filtering by relation: /api/opsins/?reference__doi=10.1016...
    filterset_fields = [
        'gene_family', 'genus', 'species', 'accession', 
        'reference__refid', 'reference__doi'
    ]

class HeterologousDataViewSet(SubmissionModelViewSet):
    # Optimize query by pre-fetching related data
    queryset = HeterologousData.objects.select_related('opsin', 'reference').all()
    serializer_class = HeterologousDataSerializer
    
    # Query heterologous records using the attached opsin's phylogeny
    filterset_fields = [
        'opsin__gene_family', 'opsin__phylum', 'opsin__genus', 
        'opsin__species', 'opsin__accession', 'reference__doi'
    ]

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        """
        Custom endpoint to export the filtered dataset as a CSV.
        Useful for generating training data for machine learning.
        Endpoint: /api/heterologous/export_csv/
        """
        # Get the queryset, applying any filters the user passed in the URL
        queryset = self.filter_queryset(self.get_queryset())
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="heterologous_export.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'hetid', 'genus', 'species', 'accession', 'gene_family', 
            'lambda_max', 'mutations', 'cell_culture', 'protein_sequence', 'doi'
        ])
        
        for data in queryset:
            writer.writerow([
                data.hetid,
                data.opsin.genus if data.opsin else '',
                data.opsin.species if data.opsin else '',
                data.opsin.accession if data.opsin else '',
                data.opsin.gene_family if data.opsin else '',
                data.lambda_max,
                data.mutations or '',
                data.cell_culture or '',
                data.opsin.protein_sequence if data.opsin else '',
                data.reference.doi if data.reference else ''
            ])
            
        return response