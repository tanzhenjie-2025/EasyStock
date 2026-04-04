from django.db import models
from django.utils import timezone
from accounts.models import User
from area_manage.models import Area
from product.models import Product

# ========== 新增：软删除管理器 ==========
class CustomerManager(models.Manager):
    def get_queryset(self):
        # 默认只返回未禁用的客户
        return super().get_queryset().filter(is_active=True)

class Customer(models.Model):
    """客户信息表"""
    # name 唯一 → 自动生成唯一索引
    name = models.CharField('客户名称', max_length=100, unique=True, db_index=True)
    # area 外键 → Django自动生成索引（原索引保留）
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=False,
        verbose_name='所属区域'
    )
    # phone 唯一 → 自动生成唯一索引（高频搜索）
    phone = models.CharField('联系电话', max_length=20, unique=True, db_index=True)
    remark = models.CharField('备注', max_length=200, blank=True, default='')
    create_time = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)  # 排序→加索引

    # ========== 新增：软删除字段 ==========
    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    disabled_time = models.DateTimeField('禁用时间', null=True, blank=True)

    # ========== 新增：管理器 ==========
    objects = CustomerManager()  # 默认管理器：过滤禁用客户
    all_objects = models.Manager()  # 全量管理器：如果需要查询包含禁用的客户

    def __str__(self):
        return f'{self.name} ({self.phone})'

    class Meta:
        verbose_name = '客户'
        verbose_name_plural = '客户管理'
        ordering = ['-create_time']
        # 【优化索引】保留原有area索引 + 新增联合索引（区域+客户名）
        indexes = [
            models.Index(fields=['area']),
            models.Index(fields=['area', 'name']),  # 按区域搜客户，性能翻倍
        ]

class CustomerPrice(models.Model):
    """客户商品专属价格表"""
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        verbose_name='关联客户'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='关联商品'
    )
    custom_price = models.DecimalField('客户专属价', max_digits=10, decimal_places=2, db_index=True)  # 单字段索引
    remark = models.CharField('定价备注', max_length=200, blank=True, default='')
    create_time = models.DateTimeField('创建时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    def __str__(self):
        return f'{self.customer.name} - {self.product.name} - ¥{self.custom_price}'

    class Meta:
        verbose_name = '客户专属价格'
        verbose_name_plural = '客户专属价格管理'
        unique_together = ('customer', 'product')  # 自带唯一索引
        ordering = ['-create_time']
        # 🔥 修复：删除跨表双下划线，使用合法的模型直接字段创建索引
        indexes = [
            # 优化：客户+价格 联合索引（替代原customer__area，完全满足查询需求）
            models.Index(fields=['customer', 'custom_price']),
            # 修复：去掉负号，索引只写字段名
            models.Index(fields=['create_time']),
            # 补充：商品+价格索引（优化商品价格筛选）
            models.Index(fields=['product', 'custom_price']),
        ]

class RepaymentRecord(models.Model):
    """还款记录表"""
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        verbose_name='还款客户'
    )
    repayment_amount = models.DecimalField('还款金额', max_digits=12, decimal_places=2)
    repayment_time = models.DateTimeField('还款时间', default=timezone.now)
    repayment_remark = models.TextField('还款备注', blank=True, null=True)
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='登记人'
    )
    create_time = models.DateTimeField('登记时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    def __str__(self):
        return f'{self.customer.name} - 还款¥{self.repayment_amount} - {self.repayment_time.strftime("%Y-%m-%d")}'

    class Meta:
        verbose_name = '还款记录'
        verbose_name_plural = '还款记录管理'
        ordering = ['-repayment_time']
        indexes = [
            models.Index(fields=['customer', 'repayment_time']),
        ]