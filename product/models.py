from django.db import models, transaction
from pypinyin import lazy_pinyin
from django.utils import timezone
from accounts.models import User

# ====================== 新增：软删除管理器 ======================
class SoftDeleteManager(models.Manager):
    """默认只查询未删除（is_active=True）的数据"""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)

# ====================== 新增：商品标签模型 ======================
class ProductTag(models.Model):
    """商品标签（仅禁用，不物理删除）"""
    name = models.CharField('标签名称', max_length=50, unique=True)
    # 标签显示颜色
    color = models.CharField('标签颜色', max_length=20, default='#3498db', help_text='如 #3498db')
    sort_order = models.IntegerField('排序', default=0)
    # 软删除/禁用
    is_active = models.BooleanField('是否启用', default=True, db_index=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    # 软删除管理器
    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = '商品标签'
        verbose_name_plural = '商品标签'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        """软删除：禁用标签"""
        self.is_active = False
        self.save(update_fields=['is_active'])


class Product(models.Model):
    """商品表（含拼音检索字段 + 双库存 + 标签）"""
    name = models.CharField('商品名称', max_length=100, unique=True)
    pinyin_full = models.CharField('全拼', max_length=200, blank=True, db_index=True)
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True, db_index=True)
    stock_system = models.IntegerField('系统库存', default=9999, db_index=True)
    stock_actual = models.IntegerField('实际库存', default=0, db_index=True)
    price = models.DecimalField('单价', max_digits=10, decimal_places=2, db_index=True)
    unit = models.CharField('单位', max_length=20, default='件')
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    # ====================== 核心：商品关联标签 ======================
    tags = models.ManyToManyField(ProductTag, blank=True, verbose_name='商品标签', related_name='products')

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        self.pinyin_full = ''.join(lazy_pinyin(self.name, style=0))
        self.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.name, style=0)])
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.is_active = False
        self.save(update_fields=['is_active'])

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '商品'
        verbose_name_plural = '商品管理'
        indexes = [
            models.Index(fields=['pinyin_abbr', 'pinyin_full']),
            models.Index(fields=['name', 'id', 'price', 'unit', 'stock_system', 'stock_actual']),
            models.Index(fields=['is_active']),
        ]


class ProductAlias(models.Model):
    """商品别名表"""
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='关联商品',
        related_name='aliases',
        null=True,
        blank=True
    )
    alias_name = models.CharField('别名', max_length=100, unique=True)
    alias_pinyin_full = models.CharField('别名全拼', max_length=200, blank=True, db_index=True)
    alias_pinyin_abbr = models.CharField('别名拼音首字母', max_length=50, blank=True, db_index=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        self.alias_pinyin_full = ''.join(lazy_pinyin(self.alias_name, style=0))
        self.alias_pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.alias_name, style=0)])
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.is_active = False
        self.save(update_fields=['is_active'])

    def __str__(self):
        return f'{self.product.name} - 别名：{self.alias_name}'

    class Meta:
        verbose_name = '商品别名'
        verbose_name_plural = '商品别名管理'
        indexes = [
            models.Index(fields=['product', 'alias_name']),
            models.Index(fields=['alias_pinyin_abbr', 'alias_pinyin_full']),
            models.Index(fields=['is_active']),
        ]



# ===================== 入库单模型 =====================
class StockIn(models.Model):
    """入库单表（进货单主表）"""
    STOCK_IN_STATUS = (
        ('pending', '未入库'),
        ('completed', '已入库'),
        ('cancelled', '作废'),
    )
    stock_in_no = models.CharField('入库单号', max_length=30, unique=True, blank=True)
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='入库人', related_name='created_stock_ins'
    )
    create_time = models.DateTimeField('入库时间', auto_now_add=True)
    total_amount = models.DecimalField('总金额', max_digits=12, decimal_places=2, default=0)
    status = models.CharField('状态', max_length=10, choices=STOCK_IN_STATUS, default='pending')

    # 作废字段
    cancelled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='作废人', related_name='cancelled_stock_ins'
    )
    cancelled_time = models.DateTimeField('作废时间', null=True, blank=True)
    cancelled_reason = models.CharField('作废原因', max_length=500, null=True, blank=True)

    def save(self, *args, **kwargs):
        # 自动生成入库单号：日期+4位序号
        if not self.stock_in_no:
            date_str = timezone.now().strftime('%Y%m%d')
            with transaction.atomic():
                last = StockIn.objects.filter(
                    stock_in_no__startswith=date_str
                ).select_for_update(skip_locked=True).order_by('-stock_in_no').first()
                seq = int(last.stock_in_no[-4:]) + 1 if last else 1
                self.stock_in_no = f'{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.stock_in_no

    class Meta:
        verbose_name = '入库单'
        verbose_name_plural = '入库单管理'
        indexes = [
            models.Index(fields=['status', 'creator', 'create_time']),
        ]

# ===================== 入库单明细模型 =====================
class StockInItem(models.Model):
    """入库单明细表"""
    stock_in = models.ForeignKey(StockIn, on_delete=models.CASCADE, verbose_name='关联入库单', related_name='items')
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, verbose_name='商品', null=True, blank=True
    )
    quantity = models.IntegerField('入库数量', default=1)
    amount = models.DecimalField('小计金额', max_digits=10, decimal_places=2, null=True, blank=True)
    # 入库单价快照
    actual_unit_price = models.DecimalField('入库单价', max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = '入库明细'
        verbose_name_plural = '入库明细管理'


# ===================== 商品价格历史表 =====================
class ProductPriceHistory(models.Model):
    """商品标准价变更历史"""
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='关联商品',
        related_name='price_history'
    )
    old_price = models.DecimalField('变更前价格', max_digits=10, decimal_places=2)
    new_price = models.DecimalField('变更后价格', max_digits=10, decimal_places=2)
    remark = models.CharField('变更原因', max_length=200, blank=True, default='')

    # 审计字段
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='操作人'
    )
    create_time = models.DateTimeField('变更时间', auto_now_add=True)

    class Meta:
        verbose_name = '价格变更记录'
        verbose_name_plural = '价格变更记录'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['product', 'create_time']),
        ]

    def __str__(self):
        return f'{self.product.name}: {self.old_price} -> {self.new_price}'
