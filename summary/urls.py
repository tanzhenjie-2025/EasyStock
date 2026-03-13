# summary/urls.py 完整代码
from django.urls import path
from . import views

urlpatterns = [
    # 原有商品汇总相关
    path('page/', views.summary_page, name='summary_page'),
    path('by-group/', views.summary_by_group, name='summary_by_group'),
    path('group/list/', views.group_list, name='group_list'),

    # 新增客户金额汇总相关
    path('customer-page/', views.customer_summary_page, name='customer_summary_page'),  # 客户汇总页面
    path('customer-by-group/', views.summary_customer_by_group, name='summary_customer_by_group'),  # 客户汇总接口
]