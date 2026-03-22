# summary/urls.py 完整代码
from django.urls import path
from . import views

urlpatterns = [
    # 原有商品汇总相关
    path('page/', views.summary_page, name='summary_page'),
    path('by-group/', views.summary_by_group, name='summary_by_group'),
    path('group/list/', views.group_list, name='group_list'),

    # 新增客户金额汇总相关
    path('customer-page/', views.customer_summary_page, name='customer_summary_page'),
    path('customer-by-group/', views.summary_customer_by_group, name='summary_customer_by_group'),

    # ========== 新增导出接口 ==========
    path('export-product/', views.export_product_summary, name='export_product_summary'),
    path('export-customer/', views.export_customer_summary, name='export_customer_summary'),

# 新增：金额汇总详情页路由
    path('detail/<int:customer_id>/', views.customer_amount_detail_page, name='customer_amount_detail_page'),  # 详情页页面
    path('api/order-source/<int:customer_id>/', views.get_customer_order_source, name='get_customer_order_source'),  # 订单来源数据接口

]