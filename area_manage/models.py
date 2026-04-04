from django.db import models

class ActiveManager(models.Manager):
    """自定义管理器，默认只返回 is_active=True 的数据"""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Area(models.Model):
    """区域（如：A区、B区、C区、D区...）"""
    name = models.CharField('区域名称', max_length=50, unique=True, db_index=True)
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)
    update_time = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    # 软删除标记
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    # 管理器
    objects = models.Manager()  # 默认管理器（返回所有）
    active_objects = ActiveManager()  # 自定义管理器（仅返回启用）

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        """🔥 重写删除方法：软删除"""
        self.is_active = False
        self.save()

    class Meta:
        verbose_name = '区域'
        verbose_name_plural = '区域管理'


class AreaGroup(models.Model):
    """区域组（自定义组合：A+B、A+C、B+D 等）"""
    name = models.CharField('组名', max_length=50, unique=True, db_index=True)
    areas = models.ManyToManyField(Area, verbose_name='包含区域')
    remark = models.CharField('备注', max_length=100, blank=True)
    create_time = models.DateTimeField(auto_now_add=True, db_index=True)
    update_time = models.DateTimeField(auto_now=True)
    # 软删除标记
    is_active = models.BooleanField('是否启用', default=True, db_index=True)

    # 管理器
    objects = models.Manager()
    active_objects = ActiveManager()

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        """ 重写删除方法：软删除"""
        self.is_active = False
        self.save()

    class Meta:
        verbose_name = '区域组'
        verbose_name_plural = '区域组管理'
        indexes = [
            models.Index(fields=['name', 'create_time']),
            models.Index(fields=['id']),
        ]