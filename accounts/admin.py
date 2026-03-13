from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

# 自定义用户后台（显示拓展字段）
class CustomUserAdmin(UserAdmin):
    # 列表显示字段
    list_display = ('username', 'user_code', 'phone', 'name', 'is_active', 'is_staff', 'date_joined')
    # 搜索字段
    search_fields = ('username', 'user_code', 'phone', 'first_name', 'last_name')
    # 筛选字段
    list_filter = ('is_active', 'is_staff', 'groups')
    # 详情页字段分组
    fieldsets = (
        ('基础信息', {'fields': ('username', 'password', 'user_code', 'phone', 'address')}),
        ('个人信息', {'fields': ('first_name', 'last_name', 'email')}),
        ('权限配置', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('时间信息', {'fields': ('last_login', 'date_joined')}),
    )
    # 新增用户字段
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'user_code', 'phone', 'is_active', 'is_staff'),
        }),
    )
    # 只读字段
    readonly_fields = ('last_login', 'date_joined')

admin.site.register(User, CustomUserAdmin)