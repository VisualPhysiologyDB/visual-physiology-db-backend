import csv
from django.db import models
from django.http import HttpResponse
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission
from .serializers import ReferenceSerializer, OpsinSerializer, HeterologousDataSerializer, CuratedSCPSerializer, DataSubmissionSerializer, SubmissionCreateSerializer

class SubmissionModelViewSet(viewsets.ModelViewSet):
    # Allow ANY user to submit data, but reading is restricted to APPROVED (unless admin)
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return self.queryset.all()
        if user.is_authenticated:
            return self.queryset.filter(models.Q(status='APPROVED') | models.Q(submitted_by=user))
        return self.queryset.filter(status='APPROVED')

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(submitted_by=user, status='PENDING')

class ReferenceViewSet(SubmissionModelViewSet):
    queryset = Reference.objects.all()
    serializer_class = ReferenceSerializer

class OpsinViewSet(SubmissionModelViewSet):
    queryset = Opsin.objects.select_related('reference').all()
    serializer_class = OpsinSerializer
    filterset_fields = ['gene_family', 'genus', 'species', 'accession', 'reference__refid', 'reference__doi']

class HeterologousDataViewSet(SubmissionModelViewSet):
    queryset = HeterologousData.objects.select_related('opsin', 'reference').all()
    serializer_class = HeterologousDataSerializer
    filterset_fields = ['opsin__gene_family', 'opsin__phylum', 'opsin__genus', 'opsin__species', 'opsin__accession', 'reference__doi']

class CuratedSCPViewSet(SubmissionModelViewSet):
    queryset = CuratedSCP.objects.select_related('reference').all()
    serializer_class = CuratedSCPSerializer
    filterset_fields = ['genus', 'species', 'phylum', 'reference__doi']

class DataSubmissionViewSet(viewsets.ModelViewSet):
    """
    Public endpoint for web form submissions.

    New submissions are written directly to the real VPOD models in PENDING
    status. The legacy DataSubmission table remains readable to staff for any
    older flat inbox records.
    """
    queryset = DataSubmission.objects.all()
    serializer_class = DataSubmissionSerializer
    permission_classes = [permissions.AllowAny] # Publicly accessible for POST

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return self.queryset.all()
        return self.queryset.none()

    def get_serializer_class(self):
        if self.action == 'create':
            return SubmissionCreateSerializer
        return DataSubmissionSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submitted_by = request.user if request.user.is_authenticated else None
        result = serializer.save(submitted_by=submitted_by)
        return Response(result, status=status.HTTP_201_CREATED)
