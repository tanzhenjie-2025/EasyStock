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
]