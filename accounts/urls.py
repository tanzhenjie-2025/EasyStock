from django.urls import path
from . import views

app_name = 'accounts'  # 命名空间

urlpatterns = [
    # 认证相关
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('force-change-password/', views.force_change_password, name='force_change_password'),

    # 个人中心
    path('profile/', views.profile, name='profile'),

    # 用户管理
    path('user-list/', views.user_list, name='user_list'),
    path('user-add/', views.user_add, name='user_add'),
    path('user-edit/<int:user_id>/', views.user_edit, name='user_edit'),
    path('user-toggle-status/<int:user_id>/', views.user_toggle_status, name='user_toggle_status'),
    path('reset-password/<int:user_id>/', views.reset_password, name='reset_password'),
path('user-detail/<int:user_id>/', views.user_detail, name='user_detail'),

    # RBAC核心

    path('role-permission/<str:role_code>/', views.role_permission_config, name='role_permission_config'),

    # 其他
    path('no-permission/', views.no_permission, name='no_permission'),

# 用户导入导出
    path('api/user/import/', views.user_import, name='user_import'),
    path('api/user/export/', views.user_export, name='user_export'),
]