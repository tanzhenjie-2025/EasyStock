# EasyStock\urls.py
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic.base import RedirectView

# 组合 Mixin + 重定向视图，形成完整的类视图
class HomeRedirectView(LoginRequiredMixin, RedirectView):
    # 已登录后重定向的目标路由名
    pattern_name = 'bill:index'
    # 使用302临时重定向，避免浏览器缓存
    permanent = False

urlpatterns = [
    # 根路径：未登录自动跳登录页，已登录跳转开单首页
    path(
        '',
        HomeRedirectView.as_view(),
        name='home'
    ),

    path('admin/', admin.site.urls),
    path('bill/', include('bill.urls')),
    path('area-manage/', include('area_manage.urls')),
    path('summary/', include('summary.urls')),
    path('customer-manage/', include('customer_manage.urls')),
    path('product/', include('product.urls')),
    path('accounts/', include('accounts.urls')),
    path('operation-log/', include('operation_log.urls')),
]