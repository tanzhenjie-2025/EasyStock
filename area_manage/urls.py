from django.urls import path
from . import views

app_name = 'area_manage'  # 命名空间

urlpatterns = [
    # 页面入口
    path('area/', views.area_page, name='area_page'),
    path('group/', views.group_page, name='group_page'),
    path('area/detail/<int:pk>/', views.area_detail_page, name='area_detail_page'),
    path('group/detail/<int:pk>/', views.group_detail_page, name='group_detail_page'),

    # 区域CRUD
    path('api/area/list/', views.area_list, name='area_list'),
    path('api/area/add/', views.area_add, name='area_add'),
    path('api/area/edit/<int:pk>/', views.area_edit, name='area_edit'),
    path('api/area/delete/<int:pk>/', views.area_delete, name='area_delete'),
    path('api/area/enable/<int:pk>/', views.area_enable, name='area_enable'),  # 新增：启用路由
    path('api/area/detail/<int:pk>/', views.area_detail_api, name='area_detail_api'),

    # 区域组CRUD
    path('api/group/list/', views.group_list, name='group_list'),
    path('api/group/add/', views.group_add, name='group_add'),
    path('api/group/edit/<int:pk>/', views.group_edit, name='group_edit'),
    path('api/group/delete/<int:pk>/', views.group_delete, name='group_delete'),
    path('api/group/enable/<int:pk>/', views.group_enable, name='group_enable'),  # 新增：启用路由
    path('api/group/detail/<int:pk>/', views.group_detail_api, name='group_detail_api'),
    # 区域导出导入
    path('api/area/import/', views.area_import, name='area_import'),
    path('api/area/export/', views.area_export, name='area_export'),
    #     区域组导入导出
    path('api/group/import/', views.group_import, name='group_import'),
    path('api/group/export/', views.group_export, name='group_export'),
]