# product/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # 商品管理主页面
    path('product-manage/', views.product_manage, name='product_manage'),

    # 商品CRUD接口
    path('add/', views.product_add, name='product_add'),
    path('edit/<int:pk>/', views.product_edit, name='product_edit'),
    path('edit/data/<int:pk>/', views.product_edit_data, name='product_edit_data'),
    path('delete/<int:pk>/', views.product_delete, name='product_delete'),

    # 别名CRUD接口
    path('alias/add/', views.alias_add, name='alias_add'),
    path('alias/delete/<int:pk>/', views.alias_delete, name='alias_delete'),

    # 商品导入接口
    path('import/', views.product_import, name='product_import'),
    path('api/product/export/', views.product_export, name='product_export'),

    # 快速出入库接口
    path('quick-stock/', views.quick_stock_operation, name='quick_stock_operation'),

    # 商品详情路由
    path('detail/<int:pk>/', views.product_detail, name='product_detail'),

    # 销售排行（独立页面 + 数据接口）
    path('sales-rank/', views.sales_rank, name='sales_rank'),
    path('sales-rank/data/', views.sales_rank_data, name='sales_rank_data'),
]