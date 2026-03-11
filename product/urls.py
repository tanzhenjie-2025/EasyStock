from django.urls import path
from . import views

urlpatterns = [
    # 商品管理模块路由
    path('product-manage/', views.product_manage, name='product_manage'),  # 商品管理主页面
    path('product/add/', views.product_add, name='product_add'),  # 新增商品
    path('product/edit/<int:pk>/', views.product_edit, name='product_edit'),  # 编辑商品
    path('product/delete/<int:pk>/', views.product_delete, name='product_delete'),  # 删除商品
    path('alias/add/', views.alias_add, name='alias_add'),  # 新增别名
    path('alias/delete/<int:pk>/', views.alias_delete, name='alias_delete'),  # 删除别名
]