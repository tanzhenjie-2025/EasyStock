from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.db.models.signals import post_migrate
from django.dispatch import receiver

# ========== 全局常量（去硬编码） ==========
# 固定角色编码
ROLE_SUPER_ADMIN = 'super_admin'
ROLE_ADMIN = 'admin'
ROLE_OPERATOR = 'operator'

# ========== 权限分类（方便前端分组展示） ==========
PERMISSION_CATEGORIES = (
    ('order', '订单管理'),  # 开单模块核心分类
    ('product', '商品管理'),
    ('user', '用户管理'),
    ('customer', '客户管理'),
    ('system', '系统管理'),
)

# ========== 权限常量汇总（开单+区域+客户+日志+商品） ==========
# 1. 开单模块权限编码
PERM_ORDER_CREATE = 'order_create'  # 开单（创建订单）
PERM_ORDER_VIEW = 'order_view'  # 查看订单/库存
PERM_ORDER_PRINT = 'order_print'  # 打印订单
PERM_ORDER_CANCEL = 'order_cancel'  # 作废订单
PERM_ORDER_REOPEN = 'order_reopen'  # 重开订单
PERM_ORDER_SETTLE = 'order_settle'  # 标记订单结清
PERM_ORDER_UNSETTLE = 'order_unsettle'  # 撤销订单结清
PERM_ORDER_SUMMARY = 'order_summary'  # 销售汇总查看/生成
PERM_PRODUCT_SEARCH = 'product_search'  # 商品/客户搜索
# 1. 开单模块权限编码（新增）
PERM_ORDER_VIEW_OTHERS = 'order_view_others'  # 查看他人订单
PERM_ORDER_CANCEL_OWN = 'order_cancel_own'    # 作废自己的订单
PERM_ORDER_CANCEL_OTHERS = 'order_cancel_others'  # 作废他人订单
PERM_ORDER_CANCEL_ANY = 'order_cancel_any'    # 作废任意订单（超级管理员）

# 2. 区域管理权限编码
PERM_AREA_VIEW = 'area_view'  # 查看区域/区域组
PERM_AREA_ADD = 'area_add'  # 新增区域/区域组
PERM_AREA_EDIT = 'area_edit'  # 编辑区域/区域组
PERM_AREA_DELETE = 'area_delete'  # 删除区域/区域组

# 3. 客户管理权限编码
PERM_CUSTOMER_VIEW = 'customer_view'  # 查看客户
PERM_CUSTOMER_ADD = 'customer_add'  # 新增客户
PERM_CUSTOMER_EDIT = 'customer_edit'  # 编辑客户
PERM_CUSTOMER_DELETE = 'customer_delete'  # 删除客户
PERM_CUSTOMER_REPAYMENT = 'customer_repayment'  # 还款登记
PERM_CUSTOMER_PRICE_VIEW = 'customer_price_view'  # 查看客户价格
PERM_CUSTOMER_PRICE_ADD = 'customer_price_add'  # 新增客户价格
PERM_CUSTOMER_PRICE_EDIT = 'customer_price_edit'  # 编辑客户价格
PERM_CUSTOMER_PRICE_DELETE = 'customer_price_delete'  # 删除客户价格
PERM_CUSTOMER_SALES_RANK = 'customer_sales_rank'  # 查看客户消费TOP30排行

# 4. 日志管理权限编码
PERM_LOG_VIEW = 'log_view'  # 查看自己的操作日志
PERM_LOG_VIEW_ALL = 'log_view_all'  # 查看所有用户的操作日志

# 5. 商品管理权限编码
PERM_PRODUCT_VIEW = 'product_view'  # 查看商品列表
PERM_PRODUCT_ADD = 'product_add'  # 新增商品
PERM_PRODUCT_EDIT = 'product_edit'  # 编辑商品
PERM_PRODUCT_DELETE = 'product_delete'  # 删除商品
PERM_PRODUCT_ALIAS_ADD = 'product_alias_add'  # 新增商品别名
PERM_PRODUCT_ALIAS_DELETE = 'product_alias_delete'  # 删除商品别名
PERM_PRODUCT_IMPORT = 'product_import'  # 批量导入商品
PERM_PRODUCT_STOCK_OP = 'product_stock_operation'  # 商品出入库操作
PERM_PRODUCT_DETAIL = 'product_detail'  # 查看商品详情（含销量/客户价）
PERM_PRODUCT_SALES_RANK = 'product_sales_rank'  # 查看商品销售排行

# ========== 合并角色+权限初始化（核心修复） ==========
@receiver(post_migrate, dispatch_uid='init_accounts_data')
def init_accounts_data(sender, **kwargs):
    """
    合并角色和权限初始化逻辑，确保：
    1. 先创建固定角色
    2. 再创建权限
    3. 最后绑定角色权限
    """
    if sender.name == 'accounts':
        # ========== 第一步：创建固定角色（幂等） ==========
        role_data = [
            (ROLE_SUPER_ADMIN, '超级管理员', '拥有系统所有权限，无需手动配置'),
            (ROLE_ADMIN, '管理员', '拥有订单、商品、用户管理等核心权限'),
            (ROLE_OPERATOR, '开单人', '仅拥有开单、查看订单等基础权限'),
        ]
        for code, name, desc in role_data:
            Role.objects.get_or_create(
                code=code,
                defaults={'name': name, 'description': desc}
            )

        # ========== 第二步：创建所有权限（幂等） ==========
        perm_data = [
            # 开单模块
            ('order_create', '创建订单', 'order', '开单（创建订单）'),
            ('order_view', '查看订单', 'order', '查看订单列表、详情、库存'),
            ('order_print', '打印订单', 'order', '打印订单小票'),
            ('order_cancel', '作废订单', 'order', '作废已创建的订单'),
            ('order_reopen', '重开订单', 'order', '重开已作废的订单'),
            ('order_settle', '标记结清', 'order', '标记订单为已结清'),
            ('order_unsettle', '撤销结清', 'order', '撤销订单的结清状态'),
            ('order_summary', '销售汇总', 'order', '销售汇总查看/生成'),
            ('product_search', '商品搜索', 'order', '搜索商品/客户'),
            ('order_view_others', '查看他人订单', 'order', '查看其他员工创建的订单'),
            ('order_cancel_own', '作废自己订单', 'order', '作废自己创建的未结清订单'),
            ('order_cancel_others', '作废他人订单', 'order', '作废其他员工创建的订单'),
            ('order_cancel_any', '作废任意订单', 'order', '作废系统中任意订单（仅超级管理员）'),

            # 区域管理
            ('area_view', '查看区域', 'system', '查看区域列表'),
            ('area_add', '新增区域', 'system', '新增区域'),
            ('area_edit', '编辑区域', 'system', '编辑区域'),
            ('area_delete', '删除区域', 'system', '删除区域'),

            # 客户管理
            ('customer_view', '查看客户', 'customer', '查看客户列表/详情'),
            ('customer_add', '新增客户', 'customer', '新增客户'),
            ('customer_edit', '编辑客户', 'customer', '编辑客户'),
            ('customer_delete', '删除客户', 'customer', '删除客户'),
            ('customer_repayment', '还款登记', 'customer', '客户还款登记'),
            ('customer_price_view', '查看客户价格', 'customer', '查看客户专属价格'),
            ('customer_price_add', '新增客户价格', 'customer', '新增客户专属价格'),
            ('customer_price_edit', '编辑客户价格', 'customer', '编辑客户专属价格'),
            ('customer_price_delete', '删除客户价格', 'customer', '删除客户专属价格'),
            ('customer_sales_rank', '查看客户消费排行', 'customer', '查看客户消费TOP30排行（仅超级管理员可见）'),

            # 日志管理
            ('log_view', '查看个人日志', 'system', '仅查看自己的操作日志'),
            ('log_view_all', '查看所有日志', 'system', '查看系统所有用户的操作日志'),

            # 商品管理
            ('product_view', '查看商品', 'product', '查看商品列表及基础信息'),
            ('product_add', '新增商品', 'product', '添加新商品'),
            ('product_edit', '编辑商品', 'product', '修改商品信息（名称/价格/库存等）'),
            ('product_delete', '删除商品', 'product', '删除商品及关联别名'),
            ('product_alias_add', '新增商品别名', 'product', '为商品添加别名'),
            ('product_alias_delete', '删除商品别名', 'product', '删除商品别名'),
            ('product_import', '导入商品', 'product', '批量导入商品数据'),
            ('product_stock_operation', '商品出入库', 'product', '快速调整商品库存'),
            ('product_detail', '商品详情', 'product', '查看商品销量/客户价等详情'),
            ('product_sales_rank', '销售排行查看', 'product', '查看商品销售TOP30排行'),
        ]

        for code, name, category, desc in perm_data:
            Permission.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'category': category,
                    'description': desc,
                    'is_active': True
                }
            )

        # ========== 第三步：绑定角色权限 ==========
        # 开单人角色
        operator_role = Role.objects.get(code=ROLE_OPERATOR)
        operator_perms = Permission.objects.filter(code__in=[
            'order_create', 'order_view', 'order_print', 'order_reopen',
            'product_search', 'order_settle', 'area_view',
            'customer_view', 'customer_repayment', 'customer_price_view',
            'log_view', 'product_view', 'product_detail',
            'order_cancel_own'
        ])
        operator_role.permissions.set(operator_perms)

        # 管理员角色
        admin_role = Role.objects.get(code=ROLE_ADMIN)
        admin_perms = Permission.objects.filter(
            Q(category='order') | Q(category='product') |
            Q(code__in=[
                'area_view', 'area_add', 'area_edit', 'area_delete',
                'customer_view', 'customer_add', 'customer_edit', 'customer_delete',
                'customer_repayment', 'customer_price_view', 'customer_price_add',
                'customer_price_edit', 'customer_price_delete',
                'log_view', 'log_view_all',
                'order_view_others', 'order_cancel_own', 'order_cancel_others',
                'product_sales_rank',
            ])
        )
        admin_role.permissions.set(admin_perms)

# ========== 1. 权限表（替代Django原生Permission） ==========
class Permission(models.Model):
    """自定义权限表（替代Django原生Permission）"""
    code = models.CharField('权限编码', max_length=50, unique=True,
                            help_text='唯一标识，如：order_view、product_edit（小写+下划线）')
    name = models.CharField('权限名称', max_length=100, help_text='展示名称，如：订单查看、商品修改')
    category = models.CharField('权限分类', max_length=20, choices=PERMISSION_CATEGORIES, default='system',
                                help_text='用于前端分组展示')
    description = models.CharField('权限描述', max_length=200, blank=True, null=True, help_text='详细说明该权限的作用')
    create_time = models.DateTimeField('创建时间', auto_now_add=True)
    is_active = models.BooleanField('是否启用', default=True, help_text='禁用后所有角色都无法使用该权限')

    class Meta:
        verbose_name = '权限'
        verbose_name_plural = '权限管理'
        ordering = ['category', 'code']
        unique_together = ('code', 'category')
        # ✅ 新增：高频权限校验联合索引（你的优化）
        indexes = [
            models.Index(fields=['code', 'is_active']),
        ]

    def __str__(self):
        return f'[{self.get_category_display()}] {self.name} ({self.code})'

# ========== 2. 角色表（固定3个，禁止增删/改编码） ==========
class Role(models.Model):
    """固定角色表：超级管理员、管理员、开单人"""
    ROLE_CHOICES = (
        (ROLE_SUPER_ADMIN, '超级管理员'),
        (ROLE_ADMIN, '管理员'),
        (ROLE_OPERATOR, '开单人'),
    )
    code = models.CharField('角色编码', max_length=20, choices=ROLE_CHOICES, unique=True)
    name = models.CharField('角色名称', max_length=50, unique=True)
    permissions = models.ManyToManyField(Permission, verbose_name='拥有权限', blank=True, related_name='roles')
    description = models.CharField('角色描述', max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = '角色管理'
        ordering = ['id']

    def __str__(self):
        return self.name

    # 禁止删除固定角色
    def delete(self, *args, **kwargs):
        if self.code in [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]:
            raise ValidationError('固定角色不允许删除！')
        super().delete(*args, **kwargs)

    # 禁止修改角色编码
    def save(self, *args, **kwargs):
        if self.pk:
            original = self.__class__.objects.get(pk=self.pk)
            if original.code != self.code:
                raise ValidationError('角色编码不允许修改！')
        super().save(*args, **kwargs)

    # 快捷方法：判断是否为超级管理员
    @property
    def is_super_admin(self):
        return self.code == ROLE_SUPER_ADMIN

# ========== 4. 拓展用户表（纯RBAC关联） ==========
class User(AbstractUser):
    """
    拓展用户模型（纯RBAC架构）
    核心：仅关联自定义Role，完全抛弃原生Group/Permission
    """
    # 核心字段
    user_code = models.CharField('用户编号', max_length=20, unique=True, help_text='开单人唯一编号，用于追责')
    # 拓展字段
    phone = models.CharField('联系电话', max_length=20, blank=True, null=True)
    address = models.CharField('地址', max_length=200, blank=True, null=True)
    force_password_change = models.BooleanField(
        '强制修改密码',
        default=False,
        help_text='密码重置后强制用户登录时修改密码'
    )
    # RBAC核心关联：用户→角色（一对一）
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='所属角色',
        related_name='users',
        db_index=True  # ✅ 新增：强制添加索引（你的优化）
    )

    # 彻底移除原生Group/Permission关联（避免冲突）
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name=_('groups'),
        blank=True,
        related_name='accounts_user_ignore',
        related_query_name='user',
        editable=False,
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name=_('user permissions'),
        blank=True,
        related_name='accounts_user_ignore',
        related_query_name='user',
        editable=False,
    )

    class Meta:
        verbose_name = '系统用户'
        verbose_name_plural = '系统用户管理'
        ordering = ['-date_joined']
        indexes = [
            models.Index(fields=['user_code', 'username', 'phone', 'first_name', 'last_name', 'is_active']),
            models.Index(fields=['user_code']),
            models.Index(fields=['phone']),
        ]

    def __str__(self):
        return f'{self.user_code} - {self.username}（{self.name}）'

    # 便捷属性：姓名展示
    @property
    def name(self):
        return self.get_full_name() or self.username

    # ========== 核心RBAC权限判断 ==========
    def has_permission(self, permission_code):
        """
        判断用户是否拥有指定权限
        规则：超级管理员→拥有所有权限；普通用户→继承角色权限
        """
        if self.role and self.role.is_super_admin:
            return True
        if self.role:
            return self.role.permissions.filter(code=permission_code, is_active=True).exists()
        return False

    # 批量权限判断（支持多个权限，满足一个即可）
    def has_any_permission(self, *permission_codes):
        if self.role and self.role.is_super_admin:
            return True
        if self.role:
            return self.role.permissions.filter(code__in=permission_codes, is_active=True).exists()
        return False