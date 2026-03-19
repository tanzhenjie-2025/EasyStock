from django.urls import path
from . import views

app_name = 'area_manage'  # 命名空间

urlpatterns = [
    # 页面入口
    path('area/', views.area_page, name='area_page'),
    path('group/', views.group_page, name='group_page'),

    # 区域CRUD
    path('api/area/list/', views.area_list, name='area_list'),
    path('api/area/add/', views.area_add, name='area_add'),
    path('api/area/edit/<int:pk>/', views.area_edit, name='area_edit'),
    path('api/area/delete/<int:pk>/', views.area_delete, name='area_delete'),

    # 区域组CRUD
    path('api/group/list/', views.group_list, name='group_list'),
    path('api/group/add/', views.group_add, name='group_add'),
    path('api/group/edit/<int:pk>/', views.group_edit, name='group_edit'),
    path('api/group/delete/<int:pk>/', views.group_delete, name='group_delete'),
]