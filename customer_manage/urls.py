# customer_manage\urls.py 此注释用于标识代码段别删
from django.urls import path
from . import views

urlpatterns = [
    # 页面路由
    path('customer/page/', views.customer_page, name='customer_page'),
    # 数据接口
    path('customer/list/', views.customer_list, name='customer_list'),
    path('customer/add/', views.customer_add, name='customer_add'),
    path('customer/edit/<int:pk>/', views.customer_edit, name='customer_edit'),
    path('customer/delete/<int:pk>/', views.customer_delete, name='customer_delete'),
    # 辅助接口
    path('area/list/', views.area_list_for_customer, name='area_list_for_customer'),
# customer_manage/urls.py 新增
path('price/page/', views.customer_price_page, name='customer_price_page'),
path('price/list/', views.customer_price_list, name='customer_price_list'),
path('price/add/', views.customer_price_add, name='customer_price_add'),
path('price/edit/<int:pk>/', views.customer_price_edit, name='customer_price_edit'),
path('price/delete/<int:pk>/', views.customer_price_delete, name='customer_price_delete'),
# 辅助接口：获取所有商品（用于下拉选择）
path('product/list/', views.product_list_for_price, name='product_list_for_price'),
path('customer/search/', views.search_customer_for_price, name='search_customer_for_price'),
    path('product/search/', views.search_product_for_price, name='search_product_for_price'),

    # 新增客户详情
    path('customer/detail/<int:pk>/', views.customer_detail, name='customer_detail'),
    path('customer/detail/page/<int:pk>/', views.customer_detail_page, name='customer_detail_page'),

    # 还款登记
    path('repayment/register/', views.repayment_register, name='repayment_register'),
    path('repayment/page/', views.repayment_page, name='repayment_page'),
path('area/list/for/price/', views.area_list_for_price, name='area_list_for_price'),

# 新增客户消费TOP30路由
path('customer/sales-rank/page/', views.customer_sales_rank_page, name='customer_sales_rank_page'),
path('customer/sales-rank/data/', views.customer_sales_rank_data, name='customer_sales_rank_data'),
#     客户信息导出导出
path('api/customer/export/', views.customer_export, name='customer_export'),
    path('api/customer/import/', views.customer_import, name='customer_import'),
# 新增：客户专属价格导入导出
    path('api/price/export/', views.customer_price_export, name='customer_price_export'),
    path('api/price/import/', views.customer_price_import, name='customer_price_import'),
]

