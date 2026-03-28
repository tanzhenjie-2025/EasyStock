from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.utils import timezone
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage

from accounts.views import permission_required
from accounts.models import User, PERM_LOG_VIEW, PERM_LOG_VIEW_ALL
from .models import OperationLog


@permission_required(PERM_LOG_VIEW)
def log_list(request):
    """日志列表页 - 性能优化版"""
    # 缓存权限判断
    can_view_all = request.user.has_permission(PERM_LOG_VIEW_ALL)

    # 1. 获取筛选参数
    operator_id = request.GET.get('operator_id', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    operation_type = request.GET.get('operation_type', '')
    object_type = request.GET.get('object_type', '')

    # 分页参数
    page = request.GET.get('page', 1)
    page_size = 20

    # 2. 核心优化：仅查询真实数据库字段，移除虚拟属性name
    logs = OperationLog.objects.select_related('operator').only(
        'id', 'operation_time', 'operation_type', 'object_type',
        'object_name', 'operator__id', 'operator__username'  # 修复：name → username
    ).all()

    # 权限过滤
    if not can_view_all:
        logs = logs.filter(operator=request.user)

    # 3. 筛选逻辑
    if operator_id and operator_id.isdigit():
        if can_view_all:
            logs = logs.filter(operator_id=operator_id)
        else:
            logs = logs.filter(operator_id=request.user.id)

    # 时区时间筛选
    if start_date:
        try:
            start = timezone.datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            logs = logs.filter(operation_time__gte=start)
        except ValueError:
            pass
    if end_date:
        try:
            end = timezone.datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            end = end.replace(hour=23, minute=59, second=59)
            logs = logs.filter(operation_time__lte=end)
        except ValueError:
            pass

    if operation_type:
        logs = logs.filter(operation_type=operation_type)
    if object_type:
        logs = logs.filter(object_type=object_type)

    # 4. 分页（保留优化）
    paginator = Paginator(logs, page_size)
    try:
        logs_page = paginator.page(page)
    except PageNotAnInteger:
        logs_page = paginator.page(1)
    except EmptyPage:
        logs_page = paginator.page(paginator.num_pages)

    # 5. 用户下拉框优化：修复name为username
    if can_view_all:
        operators = User.objects.filter(is_active=True).only('id', 'username').order_by('username')
    else:
        operators = User.objects.filter(id=request.user.id).only('id', 'username')

    operation_types = OperationLog.OPERATION_TYPE_CHOICES
    object_types = OperationLog.OBJECT_TYPE_CHOICES

    context = {
        'logs': logs_page,
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
        'can_view_all_logs': can_view_all
    }
    return render(request, 'operation_log/log_list.html', context)


@permission_required(PERM_LOG_VIEW)
def log_detail(request, log_id):
    """日志详情页 - 优化N+1查询"""
    log = get_object_or_404(OperationLog.objects.select_related('operator'), id=log_id)

    # 权限控制
    if not request.user.has_permission(PERM_LOG_VIEW_ALL) and log.operator != request.user:
        return redirect('/operation-log/')

    context = {
        'log': log,
        'can_view_all_logs': request.user.has_permission(PERM_LOG_VIEW_ALL)
    }
    return render(request, 'operation_log/log_detail.html', context)