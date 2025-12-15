"""URL configuration for dashboarding app."""

from django.urls import path
from . import views

urlpatterns = [
    # Conversational endpoint
    path('chat/', views.ChatView.as_view(), name='chat'),
    
    # Index endpoints
    path('indexes/', views.IndexListView.as_view(), name='index-list'),
    path('indexes/<str:index_id>/schema/', views.IndexSchemaView.as_view(), name='index-schema'),
    path('indexes/<str:index_id>/enriched-schema/', views.EnrichedSchemaView.as_view(), name='enriched-schema'),
    path('indexes/<str:index_id>/export/', views.IndexExportView.as_view(), name='index-export'),
    path('indexes/<str:index_id>/sample-values/<str:field_name>/', views.FieldSampleValuesView.as_view(), name='field-sample-values'),
    
    # Schema endpoints
    path('schemas/', views.AllSchemasView.as_view(), name='all-schemas'),
    path('schemas/reload/', views.ReloadSchemasView.as_view(), name='reload-schemas'),
    
    # Query endpoints
    path('query/generate/', views.GenerateQueryView.as_view(), name='generate-query'),
    path('query/execute/', views.ExecuteQueryView.as_view(), name='execute-query'),
    path('query/export/', views.QueryExportView.as_view(), name='export-query'),
    path('query/build-prompt/', views.BuildPromptView.as_view(), name='build-prompt'),
    
    # Utility endpoints
    path('mega-filters/', views.MegaFiltersView.as_view(), name='mega-filters'),
    path('health/', views.HealthCheckView.as_view(), name='health-check'),
]

