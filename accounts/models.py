from django.db import models
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """
    拓展用户模型（继承Django AbstractUser）
    保留原生字段：username/password/email/is_active/is_staff等
    新增拓展字段：用户编号（追责）、电话、地址等
    """
    # 核心追责字段：用户编号（唯一）
    user_code = models.CharField('用户编号', max_length=20, unique=True, help_text='开单人唯一编号，用于追责')
    # 拓展字段
    phone = models.CharField('联系电话', max_length=20, blank=True, null=True)
    address = models.CharField('地址', max_length=200, blank=True, null=True)

    # 权限关联（兼容Django原生权限体系）
    groups = models.ManyToManyField(
        Group,
        verbose_name=_('groups'),
        blank=True,
        related_name='accounts_user_set',  # 避免反向关联冲突
        related_query_name='user',
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name=_('user permissions'),
        blank=True,
        related_name='accounts_user_set',  # 避免反向关联冲突
        related_query_name='user',
    )

    class Meta:
        verbose_name = '开单人账户'
        verbose_name_plural = '开单人账户管理'
        ordering = ['-date_joined']  # 按注册时间倒序

    def __str__(self):
        return f'{self.user_code} - {self.username}（{self.get_full_name() or self.name}）'

    # 便捷属性：兼容前端显示姓名
    @property
    def name(self):
        return self.first_name + self.last_name if self.first_name or self.last_name else self.username