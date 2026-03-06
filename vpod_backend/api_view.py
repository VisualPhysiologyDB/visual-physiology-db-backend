from rest_framework import viewsets
from .models import Reference, Opsin, HeterologousData
from .serializers import ReferenceSerializer, OpsinSerializer, HeterologousDataSerializer

class ApprovedModelViewSet(viewsets.ReadOnlyModelViewSet):
    """
    A base ViewSet that ensures the public API ONLY ever serves APPROVED records.
    """
    def get_queryset(self):
        return self.queryset.filter(status='APPROVED')

class ReferenceViewSet(ApprovedModelViewSet):
    queryset = Reference.objects.all()
    serializer_class = ReferenceSerializer

class OpsinViewSet(ApprovedModelViewSet):
    queryset = Opsin.objects.all()
    serializer_class = OpsinSerializer
    filterset_fields = ['gene_family', 'genus', 'species'] # Allows querying like /api/opsins/?genus=Apis

class HeterologousDataViewSet(ApprovedModelViewSet):
    queryset = HeterologousData.objects.all()
    serializer_class = HeterologousDataSerializer
    filterset_fields = ['genus', 'species']
