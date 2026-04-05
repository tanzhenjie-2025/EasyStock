# bill/urls.py
from django.urls import path
from . import views

# 必加：命名空间（避免reverse冲突）
app_name = 'bill'

urlpatterns = [
    path('', views.index, name='index'),  # 开单主页
    path('search-product/', views.search_product, name='search_product'),  # 商品检索
    path('search-customer/', views.search_customer, name='search_customer'),  # 客户检索
    path('save-order/', views.save_order, name='save_order'),  # 保存订单
    path('print/<str:order_no>/', views.print_order, name='print_order'),  # 打印页面

    path('orders/', views.order_list, name='order_list'),  # 订单列表（查单）

    # ========== 仅修复：订单详情添加detail（解决404的核心修改） ==========
    path('orders/detail/<str:order_no>/', views.order_detail, name='order_detail'),  # 订单详情

    # ========== 你原来的订单操作路径（完全保留，不修改） ==========
    path('orders/cancel/<str:order_no>/', views.cancel_order, name='cancel_order'),

    path('orders/settle/<str:order_no>/', views.settle_order, name='settle_order'),  # 标记结清
    path('orders/unsettle/<str:order_no>/', views.unsettle_order, name='unsettle_order'),  # 撤销结清
    path('orders/batch-settle/', views.batch_settle_order, name='batch_settle_order'),  # 批量结清

    # ========== 你原来的reopen-edit路径（完全保留，不修改） ==========
    path('reopen-edit/<str:order_no>/', views.reopen_order_edit, name='reopen_order_edit'),  # 重开编辑页面

path('get-customer-recent-products/', views.get_customer_recent_products, name='get_customer_recent_products'),
]