import django_filters
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from .models import Reference, Opsin, HeterologousData, CuratedSCP, DataSubmission, VisualAcuity
from .serializers import ReferenceSerializer, OpsinSerializer, HeterologousDataSerializer, CuratedSCPSerializer, DataSubmissionSerializer, SubmissionCreateSerializer, VisualAcuitySerializer


class SubmissionModelViewSet(viewsets.ReadOnlyModelViewSet):
    """Public collections are always published-only, including staff browsing the site.

    All writes are through the moderated submission serializer or Django admin.
    """
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return self.queryset.filter(status='APPROVED').order_by('pk')


class ReferenceViewSet(SubmissionModelViewSet):
    queryset = Reference.objects.all()
    serializer_class = ReferenceSerializer
    filterset_fields = ['doi', 'year_of_publication']
    search_fields = ['=refid', 'doi', 'source_url', 'title', 'publication_date', 'year_of_publication', 'mom_raw', 'measurement_methods', 'raw_citation', 'notes']


class OpsinViewSet(SubmissionModelViewSet):
    queryset = Opsin.objects.select_related('reference').all()
    serializer_class = OpsinSerializer
    filterset_fields = ['gene_family', 'genus', 'species', 'accession', 'reference__refid', 'reference__doi']
    search_fields = ['genus', 'species', 'gene_family', 'accession']


class HeterologousDataViewSet(SubmissionModelViewSet):
    queryset = HeterologousData.objects.select_related('opsin', 'reference', 'opsin__reference').filter(duplicate_of__isnull=True)
    serializer_class = HeterologousDataSerializer
    filterset_fields = ['opsin__gene_family', 'opsin__phylum', 'opsin__genus', 'opsin__species', 'opsin__accession', 'reference__doi']


class CuratedSCPViewSet(SubmissionModelViewSet):
    queryset = CuratedSCP.objects.select_related('reference').filter(duplicate_of__isnull=True)
    serializer_class = CuratedSCPSerializer
    filterset_fields = ['genus', 'species', 'phylum', 'reference__doi']
    search_fields = ['genus', 'species', 'photoreceptor_type', 'notes']


class AcuityFilter(django_filters.FilterSet):
    cpd_min = django_filters.NumberFilter(field_name='cpd', lookup_expr='gte')
    cpd_max = django_filters.NumberFilter(field_name='cpd', lookup_expr='lte')
    cpd_missing = django_filters.BooleanFilter(field_name='cpd', lookup_expr='isnull')
    class Meta:
        model = VisualAcuity
        fields = ['genus', 'species', 'eye_type', 'reference__refid', 'source_dataset']


class VisualAcuityViewSet(SubmissionModelViewSet):
    queryset = VisualAcuity.objects.select_related('reference').all()
    serializer_class = VisualAcuitySerializer
    filterset_class = AcuityFilter
    search_fields = ['genus', 'species', 'eye_type', 'notes', 'source_record_id', 'source_data']


class SubmissionPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return view.action == 'create' or bool(request.user and request.user.is_staff)


class DataSubmissionViewSet(viewsets.ModelViewSet):
    """POST creates pending relational records; legacy inbox actions are staff-only."""
    queryset = DataSubmission.objects.all()
    serializer_class = DataSubmissionSerializer
    permission_classes = [SubmissionPermission]

    def get_serializer_class(self):
        return SubmissionCreateSerializer if self.action == 'create' else DataSubmissionSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save(submitted_by=request.user if request.user.is_authenticated else None)
        return Response(result, status=status.HTTP_201_CREATED)
