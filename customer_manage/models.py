from django.db import models
from django.utils import timezone
from accounts.models import User
from area_manage.models import Area
from product.models import Product
from pypinyin import lazy_pinyin

# ========== 客户软删除管理器 ==========
class CustomerManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


# ========== 新增：客户联系电话表（一对多） ==========
class CustomerPhone(models.Model):
    """客户联系电话表：一个客户可绑定多个电话，一个电话可归属多个客户"""
    customer = models.ForeignKey(
        'Customer',
        on_delete=models.CASCADE,
        related_name='phones',  # 反向调用：customer.phones.all()
        verbose_name='所属客户'
    )
    phone = models.CharField('联系电话', max_length=20, db_index=True)
    is_primary = models.BooleanField('是否主号码', default=False)
    remark = models.CharField('号码备注', max_length=50, blank=True, default='')  # 如：老板/店员/收货电话
    create_time = models.DateTimeField('创建时间', auto_now_add=True)

    # 同步软删除机制，与客户表保持一致
    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    disabled_time = models.DateTimeField('禁用时间', null=True, blank=True)

    class Meta:
        verbose_name = '客户联系电话'
        verbose_name_plural = '客户联系电话'
        ordering = ['-is_primary', 'create_time']
        # 核心约束：同一客户下不能重复添加同一个电话
        unique_together = ('customer', 'phone')
        indexes = [
            models.Index(fields=['phone']),                # 按手机号反向搜索客户
            models.Index(fields=['customer', 'is_primary']),  # 快速查询客户主号
            models.Index(fields=['customer', 'is_active']),
        ]

    def __str__(self):
        tag = '（主号）' if self.is_primary else ''
        return f'{self.phone}{tag}'


class Customer(models.Model):
    """客户信息表（增加拼音检索）"""
    name = models.CharField('客户名称', max_length=100, unique=True, db_index=True)
    # 新增拼音字段
    pinyin_full = models.CharField('全拼', max_length=200, blank=True, db_index=True)
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True, db_index=True)

    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=False,
        verbose_name='所属区域'
    )
    remark = models.CharField('备注', max_length=200, blank=True, default='')
    create_time = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    # 软删除字段
    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    disabled_time = models.DateTimeField('禁用时间', null=True, blank=True)

    # 管理器
    objects = CustomerManager()
    all_objects = models.Manager()

    # ---------- 快捷属性 ----------
    @property
    def primary_phone(self) -> str:
        primary = self.phones.filter(is_primary=True, is_active=True).first()
        if primary:
            return primary.phone
        first = self.phones.filter(is_active=True).first()
        return first.phone if first else ''

    @property
    def all_phones(self) -> list:
        return list(self.phones.filter(is_active=True).values_list('phone', flat=True))

    def save(self, *args, **kwargs):
        """自动生成拼音字段"""
        if self.name:
            self.pinyin_full = ''.join(lazy_pinyin(self.name, style=0))
            self.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.name, style=0)])
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} ({self.primary_phone})'

    class Meta:
        verbose_name = '客户'
        verbose_name_plural = '客户管理'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['area']),
            models.Index(fields=['area', 'name']),
            models.Index(fields=['area', 'is_active']),
            # 新增拼音联合索引，加速模糊查询
            models.Index(fields=['pinyin_full', 'pinyin_abbr']),
        ]


# ========== 以下原有模型保持不变 ==========
class CustomerPriceManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


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
    custom_price = models.DecimalField('客户专属价', max_digits=10, decimal_places=2, db_index=True)
    remark = models.CharField('定价备注', max_length=200, blank=True, default='')
    create_time = models.DateTimeField('创建时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    disabled_time = models.DateTimeField('禁用时间', null=True, blank=True)

    objects = CustomerPriceManager()
    all_objects = models.Manager()

    def __str__(self):
        return f'{self.customer.name} - {self.product.name} - ¥{self.custom_price}'

    class Meta:
        verbose_name = '客户专属价格'
        verbose_name_plural = '客户专属价格管理'
        unique_together = ('customer', 'product')
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['customer', 'custom_price']),
            models.Index(fields=['create_time']),
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
            models.Index(fields=['customer', 'repayment_amount']),
        ]