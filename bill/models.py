from django.db import models
from pypinyin import lazy_pinyin
import datetime

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
        related_name='aliases'  # 反向关联：商品对象.aliases 可获取所有别名
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


class Order(models.Model):
    """订单表（三联单主表）"""
    ORDER_STATUS = (('pending', '未打印'), ('printed', '已打印'))
    order_no = models.CharField('订单编号', max_length=30, unique=True, blank=True)
    create_time = models.DateTimeField('开单时间', auto_now_add=True)
    total_amount = models.DecimalField('总金额', max_digits=12, decimal_places=2, default=0)
    status = models.CharField('状态', max_length=10, choices=ORDER_STATUS, default='pending')

    def save(self, *args, **kwargs):
        """自动生成订单编号（年月日+随机数）"""
        if not self.order_no:
            date_str = datetime.datetime.now().strftime('%Y%m%d')
            # 取当天最后一个订单编号，自增
            last_order = Order.objects.filter(order_no__startswith=date_str).last()
            if last_order:
                seq = int(last_order.order_no[-4:]) + 1
            else:
                seq = 1
            self.order_no = f'{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_no

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单管理'

class OrderItem(models.Model):
    """订单明细表（三联单明细）"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, verbose_name='关联订单', related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name='商品')
    quantity = models.IntegerField('数量', default=1)
    amount = models.DecimalField('小计金额', max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        """保存时自动计算小计，并更新商品库存"""
        # 计算小计
        self.amount = self.product.price * self.quantity
        # 更新库存（扣减）
        product = self.product
        product.stock -= self.quantity
        product.save()
        # 保存明细
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
        related_name='daily_summaries'
    )
    sale_quantity = models.IntegerField('销售数量', default=0)  # 当日该商品总销量
    is_manual = models.BooleanField('是否手动汇总', default=False)  # 区分自动/手动
    create_time = models.DateTimeField('汇总生成时间', auto_now_add=True)

    class Meta:
        verbose_name = '每日销售汇总'
        verbose_name_plural = '每日销售汇总管理'
        unique_together = ('summary_date', 'product')  # 一个日期一个商品仅一条记录（避免重复）
        indexes = [
            models.Index(fields=['summary_date']),  # 优化按日期查询
        ]

    def __str__(self):
        return f'{self.summary_date} - {self.product.name} - 销售{self.sale_quantity}{self.product.unit}'