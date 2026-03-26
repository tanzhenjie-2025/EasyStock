from django.db import models
from django.utils import timezone
from pypinyin import lazy_pinyin
import datetime
# 新增：关联accounts的User模型
from accounts.models import User
from django.urls import reverse


class Product(models.Model):
    """商品表（含拼音检索字段）"""
    # name 唯一 → 自动生成唯一索引
    name = models.CharField('商品名称', max_length=100, unique=True)
    # 【新增索引】拼音全拼（高频检索字段）
    pinyin_full = models.CharField('全拼', max_length=200, blank=True, db_index=True)
    # 【新增索引】拼音首字母（核心检索字段）
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True, db_index=True)
    # 【新增索引】库存（筛选库存常用）
    stock = models.IntegerField('库存数量', default=77, db_index=True)
    # 【新增索引】单价（价格筛选常用）
    price = models.DecimalField('单价', max_digits=10, decimal_places=2, db_index=True)
    unit = models.CharField('单位', max_length=20, default='件')
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)  # 排序常用→加索引

    def save(self, *args, **kwargs):
        """保存时自动生成拼音字段"""
        self.pinyin_full = ''.join(lazy_pinyin(self.name, style=0))
        self.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.name, style=0)])
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '商品'
        verbose_name_plural = '商品管理'
        # 【新增联合索引】优化拼音组合查询（最常用）
        indexes = [
            models.Index(fields=['pinyin_abbr', 'pinyin_full']),
        ]


# 新增：商品别名表
class ProductAlias(models.Model):
    """商品别名表（一个商品可对应多个别名）"""
    # product 外键 → Django自动生成索引
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='关联商品',
        related_name='aliases',
        null=True,
        blank=True
    )
    # alias_name 唯一 → 自动生成唯一索引
    alias_name = models.CharField('别名', max_length=100, unique=True)
    # 【新增索引】别名全拼（检索用）
    alias_pinyin_full = models.CharField('别名全拼', max_length=200, blank=True, db_index=True)
    # 【新增索引】别名首字母（核心检索）
    alias_pinyin_abbr = models.CharField('别名拼音首字母', max_length=50, blank=True, db_index=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)

    def save(self, *args, **kwargs):
        """保存别名时自动生成拼音字段（和商品表逻辑一致）"""
        self.alias_pinyin_full = ''.join(lazy_pinyin(self.alias_name, style=0))
        self.alias_pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.alias_name, style=0)])
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product.name} - 别名：{self.alias_name}'

    class Meta:
        verbose_name = '商品别名'
        verbose_name_plural = '商品别名管理'
        # 【新增联合索引】优化「商品+别名」组合查询
        indexes = [
            models.Index(fields=['product', 'alias_name']),
        ]


# ===================== 区域 & 汇总分组 模块 =====================
class Area(models.Model):
    """区域（如：A区、B区、C区、D区...）"""
    name = models.CharField('区域名称', max_length=50, unique=True, db_index=True)  # 加索引
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)  # 加索引

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '区域'
        verbose_name_plural = '区域管理'


class AreaGroup(models.Model):
    """区域组（自定义组合：A+B、A+C、B+D 等）"""
    name = models.CharField('组名', max_length=50, unique=True, db_index=True)  # 加索引
    areas = models.ManyToManyField(Area, verbose_name='包含区域')
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)  # 加索引
    update_time = models.DateTimeField(auto_now=True)  # 新增字段！原代码缺少

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '区域组'
        verbose_name_plural = '区域组管理'
        # 联合索引，优化搜索性能
        indexes = [
            models.Index(fields=['name', 'create_time']),
        ]

# ========== 新增：统计缓存模型 ==========
class AreaStatisticsCache(models.Model):
    """区域统计缓存表（仅统计客户数量）"""
    area = models.OneToOneField(
        Area,
        on_delete=models.CASCADE,
        verbose_name='关联区域',
        related_name='stats_cache'
    )
    customer_count = models.IntegerField('客户数量', default=0)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '区域统计缓存'
        verbose_name_plural = '区域统计缓存管理'

    def __str__(self):
        return f'{self.area.name} - 统计缓存'

class AreaGroupStatisticsCache(models.Model):
    """区域组统计缓存表（仅统计客户数量）"""
    group = models.OneToOneField(
        AreaGroup,
        on_delete=models.CASCADE,
        verbose_name='关联区域组',
        related_name='stats_cache'
    )
    customer_count = models.IntegerField('客户数量', default=0)
    area_count = models.IntegerField('包含区域数', default=0)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '区域组统计缓存'
        verbose_name_plural = '区域组统计缓存管理'

    def __str__(self):
        return f'{self.group.name} - 统计缓存'

# ===================== 原有模型（保持不变） =====================
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


# ===================== 订单模型（核心修改） =====================
class Order(models.Model):
    """订单表（三联单主表）- 新增作废/重开字段"""
    ORDER_STATUS = (
        ('pending', '未打印'),
        ('printed', '已打印'),
        ('cancelled', '作废'),
        ('reopened', '重开')
    )
    order_no = models.CharField('订单编号', max_length=30, unique=True, blank=True)
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='订单区域')
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='所属客户'
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='开单人',
        related_name='created_orders'
    )
    create_time = models.DateTimeField('开单时间', auto_now_add=True)
    total_amount = models.DecimalField('总金额', max_digits=12, decimal_places=2, default=0)
    status = models.CharField('状态', max_length=10, choices=ORDER_STATUS, default='pending')

    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='作废人',
        related_name='cancelled_orders'
    )
    cancelled_time = models.DateTimeField('作废时间', null=True, blank=True)
    cancelled_reason = models.CharField('作废原因', max_length=500, null=True, blank=True)

    original_order = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='直接来源订单',
        related_name='reopened_orders'
    )

    is_settled = models.BooleanField('是否结清', default=False)
    settled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='结清人',
        related_name='settled_orders'
    )
    settled_time = models.DateTimeField('结清时间', null=True, blank=True)
    settled_remark = models.TextField('结清备注', null=True, blank=True, help_text='收款方式、账户、转账时间等')

    unsettled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='撤销结清人',
        related_name='unsettled_orders'
    )
    unsettled_time = models.DateTimeField('撤销结清时间', null=True, blank=True)
    unsettled_remark = models.TextField('撤销结清备注', null=True, blank=True)

    def get_overdue_days(self):
        if self.is_settled:
            return 0

        order_date = self.create_time.date()
        today = datetime.date.today()
        overdue_days = (today - order_date).days
        return max(overdue_days, 0)

    def save(self, *args, **kwargs):
        if not self.order_no:
            date_str = datetime.datetime.now().strftime('%Y%m%d')
            last_order = Order.objects.filter(order_no__startswith=date_str).last()
            if last_order:
                seq = int(last_order.order_no[-4:]) + 1
            else:
                seq = 1
            self.order_no = f'{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    def get_full_trace_chain(self):
        chain = [self]
        current = self
        while current.original_order:
            current = current.original_order
            chain.append(current)
        return chain

    def get_trace_chain_display(self):
        chain = self.get_full_trace_chain()
        link_list = []
        for order in chain:
            detail_url = reverse("order_detail", args=[order.order_no])
            link_list.append(f'<a href="{detail_url}" class="alert-link">{order.order_no}</a>')
        return ' → '.join(link_list)

    def __str__(self):
        return self.order_no

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单管理'
        indexes = [
            # 🔥 排行榜核心索引：状态 + 时间 + 地区（精准匹配筛选条件）
            models.Index(fields=['status', 'create_time', 'area']),
            # 原有索引保留
            models.Index(fields=['customer', 'status', '-create_time']),
            models.Index(fields=['area', 'status', 'create_time']),
        ]


class OrderItem(models.Model):
    """订单明细表（三联单明细）"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, verbose_name='关联订单', related_name='items')
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='商品',
        null=True,
        blank=True
    )
    quantity = models.IntegerField('数量', default=1)
    amount = models.DecimalField('小计金额', max_digits=10, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.product:
            self.amount = self.product.price * self.quantity
            product = self.product
            product.stock -= self.quantity
            product.save()
        super().save(*args, **kwargs)

    # bill/models.py → OrderItem 类 Meta
    class Meta:
        verbose_name = '订单明细'
        verbose_name_plural = '订单明细管理'
        indexes = [
            # 🔥 排行榜覆盖索引：关联订单 + 商品 + 聚合字段（数据库直接从索引取数）
            models.Index(fields=['order', 'product', 'quantity', 'amount']),
        ]


class DailySalesSummary(models.Model):
    """每日销售汇总表"""
    summary_date = models.DateField('汇总日期')
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='商品',
        related_name='daily_summaries',
        null=True,
        blank=True
    )
    sale_quantity = models.IntegerField('销售数量', default=0)
    is_manual = models.BooleanField('是否手动汇总', default=False)
    create_time = models.DateTimeField('汇总生成时间', auto_now_add=True)

    class Meta:
        verbose_name = '每日销售汇总'
        verbose_name_plural = '每日销售汇总管理'
        unique_together = ('summary_date', 'product')
        indexes = [
            models.Index(fields=['summary_date']),
        ]

    def __str__(self):
        product_name = self.product.name if self.product else "无商品"
        product_unit = self.product.unit if self.product else ""
        return f'{self.summary_date} - {product_name} - 销售{self.sale_quantity}{product_unit}'


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