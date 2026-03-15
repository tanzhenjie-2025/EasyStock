from django.urls import path
from . import views

urlpatterns = [
    # 商品管理主页面
    path('product-manage/', views.product_manage, name='product_manage'),

    # 商品数据接口
    path('manage/data/', views.product_manage_data, name='product_manage_data'),

    # 商品CRUD接口
    path('add/', views.product_add, name='product_add'),
    path('edit/<int:pk>/', views.product_edit, name='product_edit'),  # 处理POST编辑
    path('edit/data/<int:pk>/', views.product_edit_data, name='product_edit_data'),  # 新增：GET获取商品详情
    path('delete/<int:pk>/', views.product_delete, name='product_delete'),

    # 别名CRUD接口
    path('alias/add/', views.alias_add, name='alias_add'),
    path('alias/delete/<int:pk>/', views.alias_delete, name='alias_delete'),

    # 新增：商品导入接口
    path('import/', views.product_import, name='product_import'),
]