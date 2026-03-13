from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('bill/', include('bill.urls')),
    path('area-manage/', include('area_manage.urls')),
    path('summary/', include('summary.urls')),
    # 新增客户管理路由
    path('customer-manage/', include('customer_manage.urls')),
]