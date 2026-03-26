from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from datetime import datetime
# 新增：导入分页组件
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage

# 替换原有导入：移除is_boss/is_operator，导入RBAC权限组件
from accounts.views import permission_required  # RBAC权限装饰器
from accounts.models import User, PERM_LOG_VIEW, PERM_LOG_VIEW_ALL  # 日志权限常量
from .models import OperationLog


# RBAC权限控制：必须拥有【查看个人日志】权限才能访问
@permission_required(PERM_LOG_VIEW)
def log_list(request):
    """日志列表页 - 支持多条件叠加筛选 + 分页（20条/页）"""
    # 1. 获取筛选参数
    operator_id = request.GET.get('operator_id', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    operation_type = request.GET.get('operation_type', '')
    object_type = request.GET.get('object_type', '')

    # 分页参数（固定20条/页）
    page = request.GET.get('page', 1)
    page_size = 20

    # 2. 初始化查询集（select_related 已优化连表查询）
    logs = OperationLog.objects.select_related('operator').all()

    # 权限控制：仅拥有【查看所有日志】权限的用户能看全部，否则只能看自己的
    if not request.user.has_permission(PERM_LOG_VIEW_ALL):
        logs = logs.filter(operator=request.user)

    # 3. 叠加筛选逻辑
    if operator_id and operator_id.isdigit():
        if request.user.has_permission(PERM_LOG_VIEW_ALL):
            logs = logs.filter(operator_id=operator_id)
        else:
            operator_id = str(request.user.id)
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
            end = datetime(end.year, end.month, end.day, 23, 59, 59)
            logs = logs.filter(operation_time__lte=end)
        except:
            pass

    # 操作行为/对象类型筛选
    if operation_type:
        logs = logs.filter(operation_type=operation_type)
    if object_type:
        logs = logs.filter(object_type=object_type)

    # ✅ 分页逻辑
    paginator = Paginator(logs, page_size)
    try:
        logs_page = paginator.page(page)
    except PageNotAnInteger:
        logs_page = paginator.page(1)
    except EmptyPage:
        logs_page = paginator.page(paginator.num_pages)

    # 4. 获取筛选下拉框数据
    if request.user.has_permission(PERM_LOG_VIEW_ALL):
        operators = User.objects.filter(is_active=True).order_by('username')
    else:
        operators = User.objects.filter(id=request.user.id)

    operation_types = OperationLog.OPERATION_TYPE_CHOICES
    object_types = OperationLog.OBJECT_TYPE_CHOICES

    # 5. 上下文
    context = {
        'logs': logs_page,  # 分页后数据
        'paginator': paginator,
        'page_obj': logs_page,
        'operators': operators,
        'operation_types': operation_types,
        'object_types': object_types,
        'operator_id': operator_id,
        'start_date': start_date,
        'end_date': end_date,
        'operation_type': operation_type,
        'object_type': object_type,
        'can_view_all_logs': request.user.has_permission(PERM_LOG_VIEW_ALL)
    }
    return render(request, 'operation_log/log_list.html', context)


# RBAC权限控制：必须拥有【查看个人日志】权限才能访问
@permission_required(PERM_LOG_VIEW)
def log_detail(request, log_id):
    """日志详情页（适配RBAC权限）"""
    log = get_object_or_404(OperationLog, id=log_id)

    # 权限控制：非管理员只能看自己的日志
    if not request.user.has_permission(PERM_LOG_VIEW_ALL) and log.operator != request.user:
        return redirect('/operation-log/')

    context = {
        'log': log,
        'can_view_all_logs': request.user.has_permission(PERM_LOG_VIEW_ALL)
    }
    return render(request, 'operation_log/log_detail.html', context)