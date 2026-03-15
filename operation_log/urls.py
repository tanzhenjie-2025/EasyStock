from django.urls import path
from . import views

urlpatterns = [
    path('', views.log_list, name='log_list'),          # 日志列表
    path('detail/<int:log_id>/', views.log_detail, name='log_detail'),  # 日志详情
]