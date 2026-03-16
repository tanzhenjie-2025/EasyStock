from django.db import models
from pypinyin import lazy_pinyin
import datetime
# 新增：关联accounts的User模型
from accounts.models import User
from django.urls import reverse



class Product(models.Model):
    """商品表（含拼音检索字段）"""
    name = models.CharField('商品名称', max_length=100, unique=True)
    pinyin_full = models.CharField('全拼', max_length=200, blank=True)  # 如：niunai
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True)  # 如：nn
    stock = models.IntegerField('库存数量', default=77)
    price = models.DecimalField('单价', max_digits=10, decimal_places=2)
    unit = models.CharField('单位', max_length=20, default='件')
    create_time = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        """保存时自动生成拼音字段"""
        # 生成全拼（去掉声调）
        self.pinyin_full = ''.join(lazy_pinyin(self.name, style=0))
        # 生成首字母
        self.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.name, style=0)])
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '商品'
        verbose_name_plural = '商品管理'

# 新增：商品别名表
class ProductAlias(models.Model):
    """商品别名表（一个商品可对应多个别名）"""
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='关联商品',
        related_name='aliases',  # 反向关联：商品对象.aliases 可获取所有别名
    # ========== 新增：临时允许为空 ==========
    null = True,
    blank = True

    )
    alias_name = models.CharField('别名', max_length=100, unique=True)  # 别名唯一，避免重复
    alias_pinyin_full = models.CharField('别名全拼', max_length=200, blank=True)
    alias_pinyin_abbr = models.CharField('别名拼音首字母', max_length=50, blank=True)
    create_time = models.DateTimeField(auto_now_add=True)

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



# ===================== 区域 & 汇总分组 模块 =====================
class Area(models.Model):
    """区域（如：A区、B区、C区、D区...）"""
    name = models.CharField('区域名称', max_length=50, unique=True)
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '区域'
        verbose_name_plural = '区域管理'


class AreaGroup(models.Model):
    """区域组（自定义组合：A+B、A+C、B+D 等）"""
    name = models.CharField('组名', max_length=50, unique=True)
    areas = models.ManyToManyField(Area, verbose_name='包含区域')
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = '区域组'
        verbose_name_plural = '区域组管理'

# bill/models.py 末尾新增
class Customer(models.Model):
    """客户信息表"""
    name = models.CharField('客户名称', max_length=100, unique=True)  # 客户名唯一
    area = models.ForeignKey(
        Area,
        on_delete=models.SET_NULL,
        null=True,
        blank=False,  # 必须选择区域
        verbose_name='所属区域'
    )
    phone = models.CharField('联系电话', max_length=20, unique=True)  # 电话唯一且必填
    remark = models.CharField('备注', max_length=200, blank=True, default='')  # 备注默认为空
    create_time = models.DateTimeField('创建时间', auto_now_add=True)

    def __str__(self):
        return f'{self.name} ({self.phone})'

    class Meta:
        verbose_name = '客户'
        verbose_name_plural = '客户管理'
        ordering = ['-create_time']  # 按创建时间倒序

# bill/models.py 末尾新增
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
    custom_price = models.DecimalField('客户专属价', max_digits=10, decimal_places=2)  # 专属价格
    remark = models.CharField('定价备注', max_length=200, blank=True, default='')  # 如："熟客优惠价"
    create_time = models.DateTimeField('创建时间', auto_now_add=True)
    update_time = models.DateTimeField('更新时间', auto_now=True)

    def __str__(self):
        return f'{self.customer.name} - {self.product.name} - ¥{self.custom_price}'

    class Meta:
        verbose_name = '客户专属价格'
        verbose_name_plural = '客户专属价格管理'
        unique_together = ('customer', 'product')  # 核心约束：一个客户一个商品只能有一个专属价
        ordering = ['-create_time']

# ===================== 给原有 Order 加区域 =====================
# 请把你原来的 Order 替换成下面这个
# 修改原有Order模型，新增customer外键

# 其他模型保持不变，重点修改Order模型
class Order(models.Model):
    """订单表（三联单主表）- 新增作废/重开字段"""
    # 扩展订单状态选项
    ORDER_STATUS = (
        ('pending', '未打印'),
        ('printed', '已打印'),
        ('cancelled', '作废'),  # 新增：作废
        ('reopened', '重开')  # 新增：重开
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

    # 新增：作废相关字段
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

    # 新增：重开相关字段（关联直接来源的原订单）
    original_order = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='直接来源订单',
        related_name='reopened_orders'
    )

    # 新增：结清相关字段
    is_settled = models.BooleanField('是否结清', default=False)  # 核心标识
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

    # 新增：撤销结清相关字段（仅老板可操作）
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

    def save(self, *args, **kwargs):
        """自动生成订单编号（年月日+随机数）"""
        if not self.order_no:
            date_str = datetime.datetime.now().strftime('%Y%m%d')
            last_order = Order.objects.filter(order_no__startswith=date_str).last()
            if last_order:
                seq = int(last_order.order_no[-4:]) + 1
            else:
                seq = 1
            self.order_no = f'{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    # 新增：获取完整溯源链条
    def get_full_trace_chain(self):
        """递归获取完整溯源链条：[当前单, 上一级单, 最早原始单]"""
        chain = [self]
        current = self
        while current.original_order:
            current = current.original_order
            chain.append(current)
        return chain

    # 新增：生成溯源链条展示字符串
    def get_trace_chain_display(self):
        """返回带跳转链接的溯源链条，如：C202403150003 → B202403150002 → A202403150001"""
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

class OrderItem(models.Model):
    """订单明细表（三联单明细）"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, verbose_name='关联订单', related_name='items')
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='商品',
        # ========== 新增：临时允许为空 ==========
        null=True,
        blank=True
    )
    quantity = models.IntegerField('数量', default=1)
    amount = models.DecimalField('小计金额', max_digits=10, decimal_places=2, null=True, blank=True)

    def save(self, *args, **kwargs):
        """保存时自动计算小计，并更新商品库存"""
        # 新增：避免product为空时报错
        if self.product:
            # 计算小计
            self.amount = self.product.price * self.quantity
            # 更新库存（扣减）
            product = self.product
            product.stock -= self.quantity
            product.save()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = '订单明细'
        verbose_name_plural = '订单明细管理'

# ========== 新增：每日销售汇总模型（极简版） ==========
class DailySalesSummary(models.Model):
    """每日销售汇总表（仅统计订单销售数据，适配补货）"""
    summary_date = models.DateField('汇总日期')  # 要汇总的日期
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name='商品',
        related_name='daily_summaries',
        # ========== 临时允许为空 ==========
        null=True,
        blank=True
    )
    sale_quantity = models.IntegerField('销售数量', default=0)  # 当日该商品总销量
    is_manual = models.BooleanField('是否手动汇总', default=False)  # 区分自动/手动
    create_time = models.DateTimeField('汇总生成时间', auto_now_add=True)

    class Meta:
        verbose_name = '每日销售汇总'
        verbose_name_plural = '每日销售汇总管理'
        unique_together = ('summary_date', 'product')  # 修复后的字段
        indexes = [
            models.Index(fields=['summary_date']),  # 优化按日期查询
        ]

    def __str__(self):
        # 新增：避免product为空时报错
        product_name = self.product.name if self.product else "无商品"
        product_unit = self.product.unit if self.product else ""
        return f'{self.summary_date} - {product_name} - 销售{self.sale_quantity}{product_unit}'

