# EasyStock\urls.py 此注释用于标识代码段别删
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('bill/', include('bill.urls')),
    path('area-manage/', include('area_manage.urls')),
    path('summary/', include('summary.urls')),
    path('customer-manage/', include('customer_manage.urls')),
    path('product/', include('product.urls')),
    path('accounts/', include('accounts.urls')),
    # 新增操作日志路由
    path('operation-log/', include('operation_log.urls')),
]