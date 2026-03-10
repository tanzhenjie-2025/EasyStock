from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),  # 开单主页
    path('search-product/', views.search_product, name='search_product'),  # 商品检索
    path('save-order/', views.save_order, name='save_order'),  # 保存订单
    path('print/<str:order_no>/', views.print_order, name='print_order'),  # 打印页面
    path('stock/', views.stock_list, name='stock_list'),  # 库存查询
    path('orders/', views.order_list, name='order_list'),  # 订单记录
    # ========== 新增：汇总相关URL ==========
    path('summary/', views.summary_list, name='summary_list'),  # 汇总列表页
    path('manual-summary/', views.manual_summary, name='manual_summary'),  # 手动汇总接口
    path('auto-summary/', views.auto_summary_task, name='auto_summary_task'),  # 自动汇总接口
]