from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.views import ReferenceViewSet, OpsinViewSet, HeterologousDataViewSet

# Set up the API router
router = DefaultRouter()
router.register(r'references', ReferenceViewSet)
router.register(r'opsins', OpsinViewSet)
router.register(r'heterologous', HeterologousDataViewSet)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)), # Expose the API under the /api/ path
]
