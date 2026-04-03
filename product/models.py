from django.db import models
from pypinyin import lazy_pinyin

class Product(models.Model):
    """商品表（含拼音检索字段）"""
    # name 唯一 → 自动生成唯一索引
    name = models.CharField('商品名称', max_length=100, unique=True)
    # 拼音全拼（高频检索字段）
    pinyin_full = models.CharField('全拼', max_length=200, blank=True, db_index=True)
    # 拼音首字母（核心检索字段）
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True, db_index=True)
    # 库存（筛选库存常用）
    stock = models.IntegerField('库存数量', default=77, db_index=True)
    # 单价（价格筛选常用）
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
        # 【联合索引】优化拼音组合查询（最常用）
        indexes = [
            models.Index(fields=['pinyin_abbr', 'pinyin_full']),
            # 🔥 核心优化：商品管理页 覆盖索引（排序+查询字段全包含，零回表）
            models.Index(fields=['name', 'id', 'price', 'unit', 'stock']),
        ]

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
    # 【索引】别名全拼（检索用）
    alias_pinyin_full = models.CharField('别名全拼', max_length=200, blank=True, db_index=True)
    # 【索引】别名首字母（核心检索）
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
        # 【联合索引】优化「商品+别名」组合查询
        indexes = [
            models.Index(fields=['product', 'alias_name']),
            models.Index(fields=['alias_pinyin_abbr', 'alias_pinyin_full']),
        ]
