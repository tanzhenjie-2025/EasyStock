# summary/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('page/', views.summary_page, name='summary_page'),
    path('by-group/', views.summary_by_group, name='summary_by_group'),
]