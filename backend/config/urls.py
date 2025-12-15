"""URL configuration for AI Dashboarding Engine."""

from django.urls import path, include

urlpatterns = [
    path('api/', include('dashboarding.urls')),
]

