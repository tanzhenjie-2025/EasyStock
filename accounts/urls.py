from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('user-list/', views.user_list, name='user_list'),  # 老板权限
    path('user-edit/<int:user_id>/', views.user_edit, name='user_edit'),  # 老板权限
]