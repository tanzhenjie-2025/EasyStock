from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q, Count, Prefetch, OuterRef, Subquery, Sum

import logging
import json

from accounts.models import Permission
from accounts.views import permission_required, create_operation_log
from bill.models import Order
from area_manage.models import Area, AreaGroup
from customer_manage.models import Customer

import openpyxl
from django.http import HttpResponse
from io import BytesIO

from summary.views import export_to_excel

# 配置日志
logger = logging.getLogger(__name__)


# ===================== 工具函数（统一优化）=====================
def format_datetime(dt):
    """统一时间格式化，消除循环中重复strftime，降低CPU消耗"""
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else ''


# ===================== 常量定义 =====================
PAGE_SIZE_MAX = 50
AREA_PAGE_SIZE = 15
ALLOW_SORT_AREA = ['name', 'id', 'create_time']
ALLOW_SORT_GROUP = ['name', 'create_time', 'id']

# ===================== 缓存配置 =====================
CACHE_AREA_PAGE = 300
CACHE_GROUP_PAGE = 300
CACHE_DETAIL_PAGE = 300
CACHE_API_LIST = 300
CACHE_API_DETAIL = 300
CACHE_STATISTICS = 600

CACHE_PREFIX = {
    "AREA_LIST": "area_list_",
    "AREA_DETAIL": "area_detail_",
    "GROUP_LIST": "group_list_",
    "GROUP_DETAIL": "group_detail_",
    "AREA_STAT": "area_stats_",
    "GROUP_STAT": "group_stats_",
}

# 🔥 这里必须和 customer_manage/views.py 里定义的完全一致
CACHE_KEY_AREA_LIST_FOR_CUSTOMER = "global:area_list_for_customer"

# ===================== 缓存工具函数 =====================
def generate_cache_key(request, prefix: str, *args) -> str:
    user_id = request.user.id
    params = "_".join([str(arg) for arg in args])
    return f"{prefix}{user_id}_{params}"

def clear_area_cache(area_id: int = None):
    """
    清理区域相关缓存
    """
    if area_id:
        cache.delete(f"{CACHE_PREFIX['AREA_STAT']}{area_id}")
        cache.delete(f"{CACHE_PREFIX['AREA_DETAIL']}{area_id}")

    # 1. 清理区域管理自身的列表缓存
    cache.delete_pattern(f"{CACHE_PREFIX['AREA_LIST']}*")

    # 🔥 2. 关键修改：精准清理客户管理页面的区域列表缓存
    cache.delete(CACHE_KEY_AREA_LIST_FOR_CUSTOMER)
    logger.info(f"已清理区域缓存，包括跨App Key: {CACHE_KEY_AREA_LIST_FOR_CUSTOMER}")

def clear_group_cache(group_id: int = None):
    if group_id:
        cache.delete(f"{CACHE_PREFIX['GROUP_STAT']}{group_id}")
        cache.delete(f"{CACHE_PREFIX['GROUP_DETAIL']}{group_id}")
    cache.delete_pattern(f"{CACHE_PREFIX['GROUP_LIST']}*")


# ===================== 统计函数 =====================
def get_area_statistics(area_id):
    cache_key = f"{CACHE_PREFIX['AREA_STAT']}{area_id}"
    cache_data = cache.get(cache_key)
    if cache_data:
        return cache_data
    try:
        data = {'customer_count': Customer.objects.filter(area_id=area_id).count()}
        cache.set(cache_key, data, CACHE_STATISTICS)
        return data
    except Exception as e:
        logger.error(f"获取区域{area_id}统计数据失败：{str(e)}")
        return {'customer_count': 0}


def get_group_statistics(group_id):
    cache_key = f"{CACHE_PREFIX['GROUP_STAT']}{group_id}"
    cache_data = cache.get(cache_key)
    if cache_data:
        return cache_data
    try:
        # 仅加载必要字段
        group = get_object_or_404(AreaGroup.objects.only('id'), pk=group_id)
        area_ids = group.areas.values_list('id', flat=True)
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()
        data = {'customer_count': customer_count, 'area_count': len(area_ids)}
        cache.set(cache_key, data, CACHE_STATISTICS)
        return data
    except Exception as e:
        logger.error(f"获取区域组{group_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'area_count': 0}


# ===================== 区域管理 CRUD =====================
@csrf_exempt
@login_required
@permission_required('area_view')
def area_list(request):
    try:
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = max(int(request.GET.get('page', 1)), 1)

        if sort_by not in ALLOW_SORT_AREA:
            sort_by = 'name'

        cache_key = generate_cache_key(request, CACHE_PREFIX['AREA_LIST'], keyword, sort_by, sort_order, page)
        cache_data = cache.get(cache_key)
        if cache_data:
            return JsonResponse(cache_data)

        # 🔧 优化：仅加载必要字段
        areas = Area.objects.only('id', 'name', 'remark', 'create_time')
        if keyword:
            areas = areas.filter(Q(name__icontains=keyword) | Q(remark__icontains=keyword))
        areas = areas.order_by(f'-{sort_by}' if sort_order == 'desc' else sort_by)
        total = areas.count()
        start = (page - 1) * AREA_PAGE_SIZE
        end = start + AREA_PAGE_SIZE
        areas_page = areas[start:end]

        area_ids = [a.id for a in areas_page]
        customer_count_map = dict(
            Customer.objects.filter(area_id__in=area_ids)
            .values('area_id')
            .annotate(count=Count('id'))
            .values_list('area_id', 'count')
        )

        # 🔧 优化：统一时间格式化
        result = [
            {
                'id': a.id,
                'name': a.name,
                'remark': a.remark or '',
                'customer_count': customer_count_map.get(a.id, 0),
                'create_time': format_datetime(a.create_time)
            }
            for a in areas_page
        ]
        response_data = {
            'code': 1, 'data': result,
            'pagination': {
                'total': total, 'page': page, 'page_size': AREA_PAGE_SIZE,
                'total_pages': (total + AREA_PAGE_SIZE - 1) // AREA_PAGE_SIZE
            }
        }
        cache.set(cache_key, response_data, CACHE_API_LIST)
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"查询区域列表失败：{str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_view')
def area_detail_api(request, pk):
    try:
        cache_key = generate_cache_key(request, CACHE_PREFIX['AREA_DETAIL'], pk)
        cache_data = cache.get(cache_key)
        if cache_data:
            return JsonResponse(cache_data)

        # 🔧 优化：only()限制字段 + 预加载
        area = get_object_or_404(
            Area.objects.only('id', 'name', 'remark', 'create_time', 'update_time')
            .prefetch_related('areagroup_set'),
            pk=pk
        )
        customer_count = get_area_statistics(pk)['customer_count']
        customers = Customer.objects.filter(area_id=pk).values('id', 'name', 'phone')
        order_count = Order.objects.filter(area_id=pk).exclude(status='cancelled').aggregate(
            total=Coalesce(Count('id'), 0))['total']
        related_groups = area.areagroup_set.values('id', 'name')

        # 🔧 修复：update_time正确赋值，无数据失真
        update_time = format_datetime(area.update_time) if hasattr(area, 'update_time') else format_datetime(
            area.create_time)
        data = {
            'id': area.id, 'name': area.name, 'code': '', 'parent_name': '',
            'remark': area.remark or '',
            'create_time': format_datetime(area.create_time),
            'update_time': update_time,
            'customer_count': customer_count, 'order_count': order_count,
            'customers': list(customers), 'related_groups': list(related_groups)
        }
        response_data = {'code': 1, 'data': data}
        cache.set(cache_key, response_data, CACHE_API_DETAIL)
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"查询区域{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_add')
def area_add(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'})
            if Area.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '区域已存在'})

            area = Area.objects.create(name=name, remark=remark)
            create_operation_log(request=request, op_type='create', obj_type='area',
                                 obj_id=area.id, obj_name=area.name, detail=f"新增区域：{area.name}")

            # 调用增强版的缓存清理
            clear_area_cache()

            return JsonResponse({'code': 1, 'msg': '添加成功'})
        except Exception as e:
            logger.error(f"新增区域失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


@csrf_exempt
@login_required
@permission_required('area_edit')
def area_edit(request, pk):
    try:
        # 🔧 优化：only()限制字段
        area = get_object_or_404(Area.objects.only('id', 'name', 'remark'), pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'})
            if Area.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '区域名重复'})

            area.name = name
            area.remark = remark
            area.save()
            create_operation_log(request=request, op_type='update', obj_type='area',
                                 obj_id=area.id, obj_name=area.name, detail=f"编辑区域")

            # 调用增强版的缓存清理
            clear_area_cache(area_id=pk)

            return JsonResponse({'code': 1, 'msg': '修改成功'})
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})
    except Exception as e:
        logger.error(f"编辑区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_delete')
def area_delete(request, pk):
    try:
        # 🔧 优化：only()限制字段
        area = get_object_or_404(Area.objects.only('id', 'name'), pk=pk)
        area.delete()
        create_operation_log(request=request, op_type='delete', obj_type='area',
                             obj_id=pk, obj_name=area.name, detail=f"删除区域")

        # 调用增强版的缓存清理
        clear_area_cache(area_id=pk)

        return JsonResponse({'code': 1, 'msg': '删除成功'})
    except Exception as e:
        logger.error(f"删除区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ===================== 区域组管理 =====================
@csrf_exempt
@login_required
@permission_required('area_view')
def group_list(request):
    try:
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = max(int(request.GET.get('page', 1)), 1)
        page_size = min(int(request.GET.get('page_size', 20)), PAGE_SIZE_MAX)

        if sort_by not in ALLOW_SORT_GROUP:
            sort_by = 'name'

        cache_key = generate_cache_key(request, CACHE_PREFIX['GROUP_LIST'], keyword, sort_by, sort_order, page,
                                       page_size)
        cache_data = cache.get(cache_key)
        if cache_data:
            return JsonResponse(cache_data)

        # 🔧 核心优化：数据库聚合统计客户数，移除Python层循环sum
        customer_subquery = Customer.objects.filter(
            area_id=OuterRef('areas__id')
        ).values('area_id').annotate(count=Count('id')).values('count')

        # 🔧 优化：only()限制字段 + 预加载 + 数据库聚合
        groups = AreaGroup.objects.only('id', 'name', 'remark', 'create_time', 'update_time')
        groups = groups.prefetch_related(
            Prefetch('areas', queryset=Area.objects.only('id', 'name'))
        ).annotate(
            customer_count=Coalesce(Sum(Subquery(customer_subquery)), 0),
            area_count=Count('areas', distinct=True)
        )

        if keyword:
            groups = groups.filter(
                Q(name__icontains=keyword) | Q(remark__icontains=keyword) | Q(areas__name__icontains=keyword)
            ).distinct()

        sort_by = f'-{sort_by}' if sort_order == 'desc' else sort_by
        groups = groups.order_by(sort_by)
        total = groups.count()
        start = (page - 1) * page_size
        end = start + page_size
        groups_page = groups[start:end]

        # 🔧 优化：统一时间格式化，无冗余计算
        result = [
            {
                'id': g.id, 'name': g.name, 'remark': g.remark or '',
                'area_ids': [a.id for a in g.areas.all()],
                'area_names': [a.name for a in g.areas.all()],
                'customer_count': g.customer_count,
                'area_count': g.area_count,
                'create_time': format_datetime(g.create_time),
                'update_time': format_datetime(g.update_time)
            }
            for g in groups_page
        ]

        response_data = {
            'code': 1, 'data': result,
            'pagination': {'total': total, 'page': page, 'page_size': page_size,
                           'total_pages': (total + page_size - 1) // page_size}
        }
        cache.set(cache_key, response_data, CACHE_API_LIST)
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"查询区域组列表失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_view')
def group_detail_api(request, pk):
    try:
        cache_key = generate_cache_key(request, CACHE_PREFIX['GROUP_DETAIL'], pk)
        cache_data = cache.get(cache_key)
        if cache_data:
            return JsonResponse(cache_data)

        # 🔧 优化：only()限制字段 + 预加载
        group = get_object_or_404(
            AreaGroup.objects.only('id', 'name', 'remark', 'create_time', 'update_time')
            .prefetch_related('areas'),
            pk=pk
        )
        stats = get_group_statistics(pk)
        area_ids = [a.id for a in group.areas.all()]
        area_customer_map = dict(
            Customer.objects.filter(area_id__in=area_ids)
            .values('area_id')
            .annotate(count=Count('id'))
            .values_list('area_id', 'count')
        )

        areas = [{'id': a.id, 'name': a.name, 'customer_count': area_customer_map.get(a.id, 0)}
                 for a in group.areas.all()]
        data = {
            'id': group.id, 'name': group.name, 'remark': group.remark or '',
            'create_time': format_datetime(group.create_time),
            'update_time': format_datetime(group.update_time),
            'area_count': stats['area_count'],
            'area_names': [a.name for a in group.areas.all()],
            'customer_count': stats['customer_count'],
            'areas': areas
        }
        response_data = {'code': 1, 'data': data}
        cache.set(cache_key, response_data, CACHE_API_DETAIL)
        return JsonResponse(response_data)

    except Exception as e:
        logger.error(f"查询区域组{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_add')
def group_add(request):
    if request.method == 'POST':
        try:
            is_json = request.content_type == 'application/json'
            data = json.loads(request.body) if is_json else request.POST
            name = data.get('name', '').strip()
            remark = data.get('remark', '').strip()
            area_ids = data.get('area_ids', []) if is_json else data.getlist('area_ids[]')

            if not name or AreaGroup.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '组名不能为空/已存在'})

            valid_areas = Area.objects.filter(id__in=area_ids).only('id', 'name')
            g = AreaGroup.objects.create(name=name, remark=remark)
            g.areas.set([a.id for a in valid_areas])

            create_operation_log(request=request, op_type='create', obj_type='area_group',
                                 obj_id=g.id, obj_name=g.name, detail=f"新增区域组：{g.name}")
            clear_group_cache()
            return JsonResponse({'code': 1, 'msg': '创建成功'})
        except Exception as e:
            logger.error(f"新增区域组失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'创建失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


@csrf_exempt
@login_required
@permission_required('area_edit')
def group_edit(request, pk):
    try:
        # 🔧 优化：only()限制字段
        g = get_object_or_404(AreaGroup.objects.only('id', 'name', 'remark'), pk=pk)
        if request.method == 'POST':
            is_json = request.content_type == 'application/json'
            data = json.loads(request.body) if is_json else request.POST
            name = data.get('name', '').strip()
            remark = data.get('remark', '').strip()
            area_ids = data.get('area_ids', []) if is_json else data.getlist('area_ids[]')

            if not name or AreaGroup.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '组名不能为空/重复'})

            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            g.name = name
            g.remark = remark
            g.save()
            g.areas.set(valid_area_ids)

            create_operation_log(request=request, op_type='update', obj_type='area_group',
                                 obj_id=g.id, obj_name=g.name, detail=f"编辑区域组")
            clear_group_cache(group_id=pk)
            return JsonResponse({'code': 1, 'msg': '修改成功'})
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})
    except Exception as e:
        logger.error(f"编辑区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'修改失败：{str(e)}'})


@csrf_exempt
@login_required
@permission_required('area_delete')
def group_delete(request, pk):
    try:
        # 🔧 优化：only()限制字段
        g = get_object_or_404(AreaGroup.objects.only('id', 'name'), pk=pk)
        g.delete()
        create_operation_log(request=request, op_type='delete', obj_type='area_group',
                             obj_id=pk, obj_name=g.name, detail=f"删除区域组")
        clear_group_cache(group_id=pk)
        return JsonResponse({'code': 1, 'msg': '删除成功'})
    except Exception as e:
        logger.error(f"删除区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ===================== 页面入口 =====================
from django.views.decorators.cache import cache_page


@login_required
@cache_page(CACHE_AREA_PAGE)
def area_page(request):
    return render(request, 'area_manage/area.html')


@login_required
@cache_page(CACHE_GROUP_PAGE)
def group_page(request):
    return render(request, 'area_manage/group.html')


@login_required
@cache_page(CACHE_DETAIL_PAGE)
def area_detail_page(request, pk):
    # 🔧 优化：only()限制字段 + 修复update_time错误
    area = get_object_or_404(Area.objects.only('id', 'name', 'remark', 'create_time', 'update_time'), pk=pk)
    customer_count = get_area_statistics(pk)['customer_count']
    related_groups = AreaGroup.objects.filter(areas=area)

    update_time = format_datetime(area.update_time) if hasattr(area, 'update_time') else format_datetime(
        area.create_time)
    area_data = {
        'id': area.id, 'name': area.name, 'code': '', 'parent_name': '',
        'remark': area.remark or '', 'customer_count': customer_count,
        'create_time': format_datetime(area.create_time),
        'update_time': update_time  # 🔧 修复：不再错误使用create_time
    }
    return render(request, 'area_manage/area_detail.html', {'area': area_data, 'related_groups': related_groups})


@login_required
@cache_page(CACHE_DETAIL_PAGE)
def group_detail_page(request, pk):
    # 🔧 优化：only()限制字段 + 修复update_time错误
    group = get_object_or_404(AreaGroup.objects.only('id', 'name', 'remark', 'create_time', 'update_time'), pk=pk)
    customer_count = get_group_statistics(pk)['customer_count']

    group_data = {
        'id': group.id, 'name': group.name,
        'area_names': [a.name for a in group.areas.all()],
        'remark': group.remark or '', 'customer_count': customer_count,
        'create_time': format_datetime(group.create_time),
        'update_time': format_datetime(group.update_time)  # 🔧 修复：不再错误使用create_time
    }
    return render(request, 'area_manage/group_detail.html', {'group': group_data})


# ===================== 区域管理：导入导出新增代码 =====================
@csrf_exempt
@login_required
@permission_required('area_add')
def area_import(request):
    """
    区域批量导入：
    读取Excel，跳过表头和序号列，区域名重复则跳过
    """
    if request.method == 'POST':
        try:
            file = request.FILES.get('file')
            if not file:
                return JsonResponse({'code': 0, 'msg': '请选择文件'})

            wb = openpyxl.load_workbook(file)
            ws = wb.active

            imported_count = 0
            skipped_count = 0
            errors = []

            # 从第2行开始遍历（第1行是表头）
            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                # row结构: (序号, 区域名, 备注)
                if len(row) < 2:
                    continue

                # 提取数据，忽略第一列序号
                area_name = str(row[1]).strip() if row[1] else ''
                remark = str(row[2]).strip() if len(row) > 2 and row[2] else ''

                if not area_name:
                    continue

                # 检查是否已存在
                if Area.objects.filter(name=area_name).exists():
                    skipped_count += 1
                    continue

                # 创建新区域
                Area.objects.create(name=area_name, remark=remark)
                imported_count += 1

            # 清理缓存
            clear_area_cache()

            # 记录日志
            create_operation_log(
                request=request, op_type='import', obj_type='area',
                obj_id=0, obj_name='批量导入',
                detail=f"导入成功：新增{imported_count}条，跳过{skipped_count}条重复"
            )

            return JsonResponse({
                'code': 1,
                'msg': f'导入完成！新增 {imported_count} 条，跳过 {skipped_count} 条重复数据'
            })

        except Exception as e:
            logger.error(f"导入区域失败：{str(e)}", exc_info=True)
            return JsonResponse({'code': 0, 'msg': f'导入失败：文件格式错误或数据异常'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


# ========== 区域导出新逻辑（支持字段选择） ==========
@login_required
@permission_required('area_view')
def area_export(request):
    """
    区域批量导出（支持字段选择和自定义字段）
    """
    try:
        if request.method == 'POST':
            # 1. 获取选中的字段
            selected_fields = request.POST.getlist('fields[]')
            if not selected_fields:
                return JsonResponse({'code': 0, 'msg': '请至少选择一个导出字段'})

            # 2. 获取自定义字段
            custom_fields_json = request.POST.get('custom_fields', '[]')
            try:
                custom_fields = json.loads(custom_fields_json)
            except json.JSONDecodeError:
                custom_fields = []

            # 3. 定义表头映射
            headers = {
                'serial': '序号',
                'id': 'ID',
                'name': '区域名',
                'remark': '备注'
            }

            # 4. 查询并格式化数据
            areas = Area.objects.only('id', 'name', 'remark').order_by('id')
            data = []
            seq = 1
            for area in areas:
                data.append({
                    'serial': seq,
                    'id': area.id,
                    'name': area.name,
                    'remark': area.remark or ''
                })
                seq += 1

            # 5. 生成文件名
            date_str = timezone.now().strftime('%Y年%m月%d日')
            file_name = f'{date_str}区域管理导出'

            # 6. 调用通用导出函数（不传 total_row 即无合计）
            response = export_to_excel(
                data=data,
                title='区域列表',
                headers=headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=file_name,
                total_row=None
            )

            # 7. 记录日志
            # 请确保你有 create_operation_log 函数，或者注释掉下面这一段
            # create_operation_log(
            #     request=request, op_type='export', obj_type='area',
            #     obj_id=0, obj_name='批量导出', detail=f"导出区域数据共{len(data)}条"
            # )

            return response
        else:
            return JsonResponse({'code': 0, 'msg': '请求方式错误'})
    except Exception as e:
        logger.error(f"导出区域失败：{str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': '导出失败'})