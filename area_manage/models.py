from django.db import models

# Create your models here.
class Area(models.Model):
    """区域（如：A区、B区、C区、D区...）"""
    name = models.CharField('区域名称', max_length=50, unique=True, db_index=True)  # 加索引
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)  # 加索引
    update_time = models.DateTimeField(auto_now=True, verbose_name='更新时间')

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
        indexes = [
            models.Index(fields=['name', 'create_time']),
            # 🔥 优化：多对多查询专用索引（加速 areas.values_list()）
            models.Index(fields=['id']),
        ]