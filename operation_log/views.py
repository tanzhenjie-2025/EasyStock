from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.utils import timezone
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
# ========== 新增：缓存核心模块 ==========
from django.core.cache import cache
import hashlib

from accounts.views import permission_required
from accounts.models import User, PERM_LOG_VIEW, PERM_LOG_VIEW_ALL
from .models import OperationLog

# ========== 日志模块 缓存时长常量（统一管理） ==========
CACHE_LOG_PERM = 3600               # 用户权限缓存：1小时
CACHE_LOG_ENUM = None               # 静态枚举缓存：永久有效
CACHE_LOG_OPERATORS = 600           # 用户下拉框缓存：10分钟
CACHE_LOG_QUERYSET = 60             # 日志查询集/分页缓存：1分钟
CACHE_LOG_DETAIL = None             # 单条日志详情：永久有效
CACHE_LOG_DETAIL_PERM = 3600        # 详情权限校验：1小时

@permission_required(PERM_LOG_VIEW)
def log_list(request):
    """日志列表页 - 全缓存优化版（严格按指定点位缓存）"""
    user_id = request.user.id

    # ===================== 1. 用户权限判断结果缓存（用户级） =====================
    perm_cache_key = f"log_perm_view_all_{user_id}"
    can_view_all = cache.get(perm_cache_key)
    if can_view_all is None:
        can_view_all = request.user.has_permission(PERM_LOG_VIEW_ALL)
        cache.set(perm_cache_key, can_view_all, timeout=CACHE_LOG_PERM)

    # 1. 获取筛选参数
    operator_id = request.GET.get('operator_id', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    operation_type = request.GET.get('operation_type', '')
    object_type = request.GET.get('object_type', '')

    # 分页参数
    page = request.GET.get('page', 1)
    page_size = 20

    # 生成唯一缓存键（权限+所有筛选参数，避免参数乱序冲突）
    cache_params = (
        f"user_{user_id}_perm_{can_view_all}_op_{operator_id}_"
        f"start_{start_date}_end_{end_date}_opt_{operation_type}_obj_{object_type}"
    )
    # MD5压缩缓存键长度
    base_cache_key = hashlib.md5(cache_params.encode('utf-8')).hexdigest()
    queryset_cache_key = f"log_queryset_{base_cache_key}"
    paginator_cache_key = f"log_paginator_{base_cache_key}"
    page_cache_key = f"log_page_{base_cache_key}_{page}"

    # ===================== 4. 筛选后的日志核心查询集缓存（最关键） =====================
    logs = cache.get(queryset_cache_key)
    if logs is None:
        # 核心优化：仅查询真实数据库字段，移除虚拟属性name
        logs = OperationLog.objects.select_related('operator').only(
            'id', 'operation_time', 'operation_type', 'object_type',
            'object_name', 'operator__id', 'operator__username'
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

        # 写入缓存
        cache.set(queryset_cache_key, logs, timeout=CACHE_LOG_QUERYSET)

    # ===================== 5. 分页对象缓存 =====================
    paginator = cache.get(paginator_cache_key)
    logs_page = cache.get(page_cache_key)
    if paginator is None or logs_page is None:
        paginator = Paginator(logs, page_size)
        try:
            logs_page = paginator.page(page)
        except PageNotAnInteger:
            logs_page = paginator.page(1)
        except EmptyPage:
            logs_page = paginator.page(paginator.num_pages)
        # 写入缓存
        cache.set(paginator_cache_key, paginator, timeout=CACHE_LOG_QUERYSET)
        cache.set(page_cache_key, logs_page, timeout=CACHE_LOG_QUERYSET)

    # ===================== 3. 操作用户下拉框数据缓存（分级缓存） =====================
    operators_cache_key = f"log_operators_{user_id}_{can_view_all}"
    operators = cache.get(operators_cache_key)
    if operators is None:
        if can_view_all:
            operators = User.objects.filter(is_active=True).only('id', 'username').order_by('username')
        else:
            operators = User.objects.filter(id=request.user.id).only('id', 'username')
        cache.set(operators_cache_key, operators, timeout=CACHE_LOG_OPERATORS)

    # ===================== 2. 模型静态枚举选项（全局永久缓存） =====================
    # 操作类型枚举
    operation_types = cache.get("log_enum_operation_types")
    if operation_types is None:
        operation_types = OperationLog.OPERATION_TYPE_CHOICES
        cache.set("log_enum_operation_types", operation_types, timeout=CACHE_LOG_ENUM)
    # 对象类型枚举
    object_types = cache.get("log_enum_object_types")
    if object_types is None:
        object_types = OperationLog.OBJECT_TYPE_CHOICES
        cache.set("log_enum_object_types", object_types, timeout=CACHE_LOG_ENUM)

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
    """日志详情页 - 双缓存优化版（日志对象+权限校验）"""
    user_id = request.user.id

    # ===================== 2. 详情页权限校验缓存（用户ID+日志ID维度） =====================
    detail_perm_cache_key = f"log_detail_perm_{user_id}_{log_id}"
    has_perm = cache.get(detail_perm_cache_key)
    if has_perm is None:
        # 原始权限校验逻辑
        can_view_all = request.user.has_permission(PERM_LOG_VIEW_ALL)
        has_perm = can_view_all
        cache.set(detail_perm_cache_key, has_perm, timeout=CACHE_LOG_DETAIL_PERM)

    # 无权限直接跳转
    if not has_perm:
        return redirect('/operation-log/')

    # ===================== 1. 单条日志详情对象缓存（按log_id永久缓存） =====================
    log_cache_key = f"log_detail_{log_id}"
    log = cache.get(log_cache_key)
    if log is None:
        log = get_object_or_404(OperationLog.objects.select_related('operator'), id=log_id)
        cache.set(log_cache_key, log, timeout=CACHE_LOG_DETAIL)

    context = {
        'log': log,
        'can_view_all_logs': has_perm
    }
    return render(request, 'operation_log/log_detail.html', context)