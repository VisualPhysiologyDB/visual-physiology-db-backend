"""vpod_backend URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
#from django.contrib import admin
#from django.urls import path

#urlpatterns = [
#    path('admin/', admin.site.urls),
#]

from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from core.views import ReferenceViewSet, OpsinViewSet, HeterologousDataViewSet
from django.views.generic import TemplateView

# Set up the API router
router = DefaultRouter()
router.register(r'references', ReferenceViewSet)
router.register(r'opsins', OpsinViewSet)
router.register(r'heterologous', HeterologousDataViewSet)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)), # Expose the API under the /api/ path,
    path('', TemplateView.as_view(template_name='index.html'), name='home'), # Serve a simple homepage
]


