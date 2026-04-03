from django.db import models

# Create your models here.
from product.models import Product

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

