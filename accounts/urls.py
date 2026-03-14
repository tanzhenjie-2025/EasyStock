from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('user-list/', views.user_list, name='user_list'),  # 老板权限
    path('user-add/', views.user_add, name='user_add'),     # 新增：添加用户
    path('user-edit/<int:user_id>/', views.user_edit, name='user_edit'),  # 编辑用户
    path('user-delete/<int:user_id>/', views.user_delete, name='user_delete'),  # 删除用户
    path('no-permission/', views.no_permission, name='no_permission'),  # 新增：无权限页
]