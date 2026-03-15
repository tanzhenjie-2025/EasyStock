from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from accounts.views import is_boss, is_operator
from .models import OperationLog
from accounts.models import User
from django.db.models import Q
from datetime import datetime


# 登录验证 + 权限控制：仅登录用户可访问
@login_required
def log_list(request):
    """日志列表页 - 支持多条件叠加筛选"""
    # 1. 获取筛选参数
    operator_id = request.GET.get('operator_id', '')  # 操作人筛选
    start_date = request.GET.get('start_date', '')  # 开始时间
    end_date = request.GET.get('end_date', '')  # 结束时间
    operation_type = request.GET.get('operation_type', '')  # 操作行为
    object_type = request.GET.get('object_type', '')  # 操作对象类型

    # 2. 初始化查询集
    logs = OperationLog.objects.select_related('operator').all()

    # 权限控制：老板看所有日志，操作员只看自己的
    if not is_boss(request.user):
        logs = logs.filter(operator=request.user)

    # 3. 叠加筛选逻辑（核心：逐步过滤，实现叠加效果）
    # 操作人筛选
    if operator_id and operator_id.isdigit():
        logs = logs.filter(operator_id=operator_id)

    # 时间范围筛选
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            logs = logs.filter(operation_time__gte=start)
        except:
            pass
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d')
            # 结束日期包含当天所有时间
            end = datetime(end.year, end.month, end.day, 23, 59, 59)
            logs = logs.filter(operation_time__lte=end)
        except:
            pass

    # 操作行为筛选
    if operation_type:
        logs = logs.filter(operation_type=operation_type)

    # 操作对象类型筛选
    if object_type:
        logs = logs.filter(object_type=object_type)

    # 4. 获取筛选下拉框数据（修复：将 order_by('name') 改为实际数据库字段 username/user_code）
    operators = User.objects.filter(is_active=True).order_by('username')  # 改用 username 排序（也可换 user_code）
    operation_types = OperationLog.OPERATION_TYPE_CHOICES  # 操作行为选项
    object_types = OperationLog.OBJECT_TYPE_CHOICES  # 操作对象类型选项

    # 5. 构造上下文
    context = {
        'logs': logs,
        'operators': operators,
        'operation_types': operation_types,
        'object_types': object_types,
        # 回显筛选条件
        'operator_id': operator_id,
        'start_date': start_date,
        'end_date': end_date,
        'operation_type': operation_type,
        'object_type': object_type,
        'is_boss': is_boss(request.user)
    }
    return render(request, 'operation_log/log_list.html', context)


@login_required
def log_detail(request, log_id):
    """日志详情页"""
    log = get_object_or_404(OperationLog, id=log_id)

    # 权限控制：操作员只能看自己的日志
    if not is_boss(request.user) and log.operator != request.user:
        return redirect('/operation-log/')

    context = {
        'log': log,
        'is_boss': is_boss(request.user)
    }
    return render(request, 'operation_log/log_detail.html', context)