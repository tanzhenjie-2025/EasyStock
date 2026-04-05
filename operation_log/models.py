# operation_log\models.py
from django.db import models
from accounts.models import User
from django.utils import timezone

class OperationLog(models.Model):
    """操作日志模型"""
    OPERATION_TYPE_CHOICES = (
        ('create', '新增'), ('update', '修改'), ('delete', '禁用'),
        ('query', '查询'), ('import', '导入'), ('export', '导出'),
        ('create_order', '开单'), ('cancel_order', '作废订单'), ('reopen_order', '重开订单'),
        ('enable_user', '启用用户'), ('disable_user', '禁用用户'),
        ('login', '登录'), ('logout', '登出'),
        ('reset_password', '重置密码'), ('change_password', '修改密码'),
        ('settle_order', '标记订单结清'), ('unsettle_order', '撤销订单结清'),
        ('batch_settle_order', '批量结清订单'), ('repayment_register', '还款登记'),
    )

    OBJECT_TYPE_CHOICES = (
        ('product', '商品'), ('product_alias', '商品别名'), ('area', '区域'),
        ('area_group', '区域组'), ('customer', '客户'), ('customer_price', '客户专属价'),
        ('user', '用户'), ('order', '订单'), ('daily_summary', '销售汇总'), ('repayment', '还款记录'),
    )

    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, verbose_name='操作人', related_name='operation_logs'
    )
    operation_time = models.DateTimeField('操作时间', default=timezone.now)
    operation_type = models.CharField('操作行为', max_length=20, choices=OPERATION_TYPE_CHOICES)
    object_type = models.CharField('操作对象类型', max_length=20, choices=OBJECT_TYPE_CHOICES)
    object_id = models.CharField('操作对象ID', max_length=50, blank=True, null=True)
    object_name = models.CharField('操作对象名称', max_length=100, blank=True, null=True)
    operation_detail = models.TextField('操作详情', blank=True, null=True)
    ip_address = models.CharField('操作IP', max_length=50, blank=True, null=True)

    class Meta:
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志管理'
        ordering = ['-operation_time']
        indexes = [
            # ✅ 修复：正确的联合索引语法（匹配你的查询+排序，性能拉满）
            models.Index(fields=['operator', 'operation_type', 'object_type', 'operation_time']),
        ]

    def __str__(self):
        return f'[{self.operation_time.strftime("%Y-%m-%d %H:%M")}] {self.operator.name if self.operator else "未知用户"} - {self.get_operation_type_display()} {self.get_object_type_display()}'