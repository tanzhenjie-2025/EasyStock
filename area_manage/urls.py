# area_manage/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # 区域CRUD
    path('area/page/', views.area_page, name='area_page'),
    path('area/list/', views.area_list, name='area_list'),
    path('area/add/', views.area_add, name='area_add'),
    path('area/edit/<int:pk>/', views.area_edit, name='area_edit'),
    path('area/delete/<int:pk>/', views.area_delete, name='area_delete'),

    # 区域组CRUD
    path('group/page/', views.group_page, name='group_page'),
    path('group/list/', views.group_list, name='group_list'),
    path('group/add/', views.group_add, name='group_add'),
    path('group/edit/<int:pk>/', views.group_edit, name='group_edit'),
    path('group/delete/<int:pk>/', views.group_delete, name='group_delete'),
]