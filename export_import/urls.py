from django.urls import path
from . import views

app_name = 'export_import'
urlpatterns = [
    path('full-export/', views.full_export, name='full_export'),
    path('full-import/', views.full_import, name='full_import'),
path('', views.data_migration_page, name='data_migration_page'),
]