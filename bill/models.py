from django.db import models, transaction
from django.utils import timezone
import datetime
# 新增：关联accounts的User模型
from accounts.models import User
from django.urls import reverse
from product.models import Product
from area_manage.models import Area
from customer_manage.models import Customer


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
            date_str = timezone.now().strftime('%Y%m%d')
            with transaction.atomic():
                # 只锁最新一行 + 按订单号倒序 + 跳过已锁定行（高并发安全）
                last_order = Order.objects.filter(
                    order_no__startswith=date_str
                ).select_for_update(skip_locked=True).order_by('-order_no').first()

                seq = int(last_order.order_no[-4:]) + 1 if last_order else 1
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
    # 🔥 修复：索引必须放在 Meta 类中！！！
    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单管理'
        indexes = [
            # 原有索引（保留，status 筛选自动复用此索引）
            models.Index(fields=['status', 'is_settled', 'creator', 'create_time']),
            # 🔥 修复2：覆盖索引（聚合+排序 total_amount，消除内存排序+回表）
            models.Index(fields=['status', 'is_settled', 'area', 'customer','create_time']),
            models.Index(fields=['status','area', 'create_time', 'total_amount']),
            models.Index(fields=['create_time']),
            models.Index(fields=['customer', 'status', 'is_settled', 'create_time', 'total_amount']),
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

    # 【新增】价格快照字段
    # 当时的商品标准价
    snapshot_standard_price = models.DecimalField('标准价快照', max_digits=10, decimal_places=2, null=True, blank=True)
    # 当时的客户专属价 (如果有)
    snapshot_customer_price = models.DecimalField('客户价快照', max_digits=10, decimal_places=2, null=True, blank=True)
    # 【新增】开单时实际录入的单价 (方便后续核对)
    actual_unit_price = models.DecimalField('实际单价', max_digits=10, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):
        # 注意：这里的逻辑后续会移到视图层，以保证快照准确
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = '订单明细'
        verbose_name_plural = '订单明细管理'
        indexes = [
            models.Index(fields=['product', 'order', 'quantity', 'amount'])
        ]


