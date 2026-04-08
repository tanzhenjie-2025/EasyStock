from django.db import models
from pypinyin import lazy_pinyin

# ====================== 新增：软删除管理器 ======================
class SoftDeleteManager(models.Manager):
    """默认只查询未删除（is_active=True）的数据"""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)

class Product(models.Model):
    """商品表（含拼音检索字段 + 双库存）"""
    name = models.CharField('商品名称', max_length=100, unique=True)
    pinyin_full = models.CharField('全拼', max_length=200, blank=True, db_index=True)
    pinyin_abbr = models.CharField('拼音首字母', max_length=50, blank=True, db_index=True)
    # 双库存核心字段
    stock_system = models.IntegerField('系统库存', default=9999, db_index=True)  # 原stock → 系统库存
    stock_actual = models.IntegerField('实际库存', default=0, db_index=True)  # 新增：实际库存
    price = models.DecimalField('单价', max_digits=10, decimal_places=2, db_index=True)
    unit = models.CharField('单位', max_length=20, default='件')
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)

    # ====================== 新增：软删除字段 ======================
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    # ====================== 新增：软删除管理器 ======================
    objects = SoftDeleteManager()
    all_objects = models.Manager()

    def save(self, *args, **kwargs):
        """保存时自动生成拼音字段"""
        self.pinyin_full = ''.join(lazy_pinyin(self.name, style=0))
        self.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(self.name, style=0)])
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """软删除"""
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