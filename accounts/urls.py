from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('user-list/', views.user_list, name='user_list'),
    path('user-add/', views.user_add, name='user_add'),
    path('user-edit/<int:user_id>/', views.user_edit, name='user_edit'),
    path('user-toggle-status/<int:user_id>/', views.user_toggle_status, name='user_toggle_status'),
    path('reset-password/<int:user_id>/', views.reset_password, name='reset_password'),  # 新增：重置密码
    path('force-change-password/', views.force_change_password, name='force_change_password'),  # 新增：强制改密码
    path('no-permission/', views.no_permission, name='no_permission'),
]