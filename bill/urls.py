# bill\urls
from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),  # 开单主页
    path('search-product/', views.search_product, name='search_product'),  # 商品检索
    path('search-customer/', views.search_customer, name='search_customer'),  # 客户检索
    path('save-order/', views.save_order, name='save_order'),  # 保存订单
    path('print/<str:order_no>/', views.print_order, name='print_order'),  # 打印页面
    path('stock/', views.stock_list, name='stock_list'),  # 库存查询
    path('orders/', views.order_list, name='order_list'),  # 订单列表（查单）

    # ========== 调整：将所有orders子路径放在动态路径前面 ==========
    path('orders/cancel/<str:order_no>/', views.cancel_order, name='cancel_order'),
    path('orders/reopen/<str:order_no>/', views.reopen_order, name='reopen_order'),  # 保留原接口
    path('orders/settle/<str:order_no>/', views.settle_order, name='settle_order'),  # 标记结清
    path('orders/unsettle/<str:order_no>/', views.unsettle_order, name='unsettle_order'),  # 撤销结清
    path('orders/batch-settle/', views.batch_settle_order, name='batch_settle_order'),  # 批量结清

    # ========== 动态路径移到最后 ==========
    path('orders/<str:order_no>/', views.order_detail, name='order_detail'),  # 订单详情

    path('reopen-edit/<str:order_no>/', views.reopen_order_edit, name='reopen_order_edit'),  # 重开编辑页面
]