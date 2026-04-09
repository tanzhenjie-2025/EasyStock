# product/urls.py
from django.urls import path
from . import views

app_name = 'product'

urlpatterns = [
    # 商品管理主页面
    path('product-manage/', views.product_manage, name='product_manage'),

    # 商品CRUD接口
    path('add/', views.product_add, name='product_add'),
    path('edit/<int:pk>/', views.product_edit, name='product_edit'),
    path('edit/data/<int:pk>/', views.product_edit_data, name='product_edit_data'),
    path('delete/<int:pk>/', views.product_delete, name='product_delete'),
    path('restore/<int:pk>/', views.product_restore, name='product_restore'),  # 新增：启用路由

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

    path('stock/', views.stock_list, name='stock_list'),
    path('inline-update/', views.product_inline_update, name='product_inline_update'),
    path('toggle-status/', views.product_toggle_status, name='product_toggle_status'),
    path('batch-operation/', views.product_batch_operation, name='product_batch_operation'),

    path('stock-calibrate/', views.product_stock_calibrate, name='product_stock_calibrate'),

    # 快速入库首页
    path('stock-in/index/', views.stock_in_index, name='stock_in_index'),
    # 保存入库单
    path('stock-in/save/', views.save_stock_in, name='save_stock_in'),
    # 入库单列表
    path('stock-in/list/', views.stock_in_list, name='stock_in_list'),
    # 入库单详情
    path('stock-in/detail/<str:stock_in_no>/', views.stock_in_detail, name='stock_in_detail'),
    # 作废入库单
    path('stock-in/cancel/<str:stock_in_no>/', views.cancel_stock_in, name='cancel_stock_in'),

# 🔥 新增详情页和统计接口

    path('detail/<int:pk>/stats/', views.product_statistics_api, name='product_stats_api'),
]

