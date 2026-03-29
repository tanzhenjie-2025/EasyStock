from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
# ========== 缓存核心导入 ==========
from django.views.decorators.cache import cache_page
from django.core.cache import cache

import logging
import json
from django.db.models import Q, Count, Prefetch
from accounts.models import Permission
from accounts.views import permission_required, create_operation_log, get_client_ip
from bill.models import Area, AreaGroup, Customer, Order

# 配置日志
logger = logging.getLogger(__name__)

# ===================== 常量定义（统一维护，避免硬编码）=====================
PAGE_SIZE_MAX = 50  # 全局最大分页容量，防止OOM
AREA_PAGE_SIZE = 15
ALLOW_SORT_AREA = ['name', 'id', 'create_time']
ALLOW_SORT_GROUP = ['name', 'create_time', 'id']

# ===================== 缓存常量配置 =====================
# 页面缓存时长（纯模板页面）
CACHE_AREA_PAGE = 300  # 区域管理主页面 5分钟
CACHE_GROUP_PAGE = 300  # 区域组管理主页面 5分钟
CACHE_DETAIL_PAGE = 300  # 详情页面 5分钟

# API接口缓存时长（高频调用）
CACHE_API_LIST = 300  # 列表接口 5分钟
CACHE_API_DETAIL = 300  # 详情接口 5分钟

# 统计函数缓存时长（公共复用）
CACHE_STATISTICS = 600  # 统计数据 10分钟


# ===================== 纯统计函数（添加缓存，公共复用）=====================
def get_area_statistics(area_id):
    """获取单个区域的统计数据 - 带缓存优化"""
    # 缓存键：按区域ID唯一标识
    cache_key = f"area_stats_{area_id}"
    cache_data = cache.get(cache_key)

    if cache_data:
        return cache_data

    try:
        data = {'customer_count': Customer.objects.filter(area_id=area_id).count()}
        # 缓存10分钟
        cache.set(cache_key, data, CACHE_STATISTICS)
        return data
    except Exception as e:
        logger.error(f"获取区域{area_id}统计数据失败：{str(e)}")
        return {'customer_count': 0}


def get_group_statistics(group_id):
    """获取区域组的统计数据 - 带缓存优化"""
    # 缓存键：按区域组ID唯一标识
    cache_key = f"group_stats_{group_id}"
    cache_data = cache.get(cache_key)

    if cache_data:
        return cache_data

    try:
        group = get_object_or_404(AreaGroup, pk=group_id)
        area_ids = group.areas.values_list('id', flat=True)
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()
        data = {'customer_count': customer_count, 'area_count': len(area_ids)}
        # 缓存10分钟
        cache.set(cache_key, data, CACHE_STATISTICS)
        return data
    except Exception as e:
        logger.error(f"获取区域组{group_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'area_count': 0}


# ===================== 区域管理 CRUD（添加缓存+缓存失效）=====================
@csrf_exempt
@login_required
@permission_required('area_view')
@cache_page(CACHE_API_LIST)  # 核心列表接口缓存 5分钟
def area_list(request):
    """获取所有区域列表（支持关键词搜索+分页）【缓存优化】"""
    try:
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = max(int(request.GET.get('page', 1)), 1)

        # 排序白名单
        if sort_by not in ALLOW_SORT_AREA:
            sort_by = 'name'

        # 仅查询需要的字段，高性能
        areas = Area.objects.only('id', 'name', 'remark', 'create_time')

        if keyword:
            areas = areas.filter(Q(name__icontains=keyword) | Q(remark__icontains=keyword))

        areas = areas.order_by(f'-{sort_by}' if sort_order == 'desc' else sort_by)
        total = areas.count()
        start = (page - 1) * AREA_PAGE_SIZE
        end = start + AREA_PAGE_SIZE
        areas_page = areas[start:end]

        # 批量聚合统计客户数（1次查询，无缓存，无冗余）
        area_ids = [a.id for a in areas_page]
        customer_count_map = dict(
            Customer.objects.filter(area_id__in=area_ids)
            .values('area_id')
            .annotate(count=Count('id'))
            .values_list('area_id', 'count')
        )

        result = [
            {
                'id': a.id,
                'name': a.name,
                'remark': a.remark or '',
                'customer_count': customer_count_map.get(a.id, 0),
                'create_time': a.create_time.strftime('%Y-%m-%d %H:%M:%S')
            }
            for a in areas_page
        ]

        return JsonResponse({
            'code': 1,
            'data': result,
            'pagination': {
                'total': total,
                'page': page,
                'page_size': AREA_PAGE_SIZE,
                'total_pages': (total + AREA_PAGE_SIZE - 1) // AREA_PAGE_SIZE
            }
        }, content_type='application/json')

    except Exception as e:
        logger.error(f"查询区域列表失败：{str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
@cache_page(CACHE_API_DETAIL)  # 详情接口缓存 5分钟
def area_detail_api(request, pk):
    """区域详情接口【缓存优化】"""
    try:
        # 预加载反向关联，消除额外查询
        area = get_object_or_404(Area.objects.prefetch_related('areagroup_set'), pk=pk)
        customer_count = get_area_statistics(pk)['customer_count']

        # 基础查询
        customers = Customer.objects.filter(area_id=pk).values('id', 'name', 'phone')
        order_count = Order.objects.filter(area_id=pk).exclude(status='cancelled').aggregate(
            total=Coalesce(Count('id'), 0))['total']
        related_groups = area.areagroup_set.values('id', 'name')

        data = {
            'id': area.id,
            'name': area.name,
            'code': '',
            'parent_name': '',
            'remark': area.remark or '',
            'create_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'update_time': area.update_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(area,
                                                                                     'update_time') else area.create_time.strftime(
                '%Y-%m-%d %H:%M:%S'),
            'customer_count': customer_count,
            'order_count': order_count,
            'customers': list(customers),
            'related_groups': list(related_groups)
        }
        return JsonResponse({'code': 1, 'data': data}, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_add')
def area_add(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '区域已存在'}, content_type='application/json')

            area = Area.objects.create(name=name, remark=remark)
            create_operation_log(request=request, op_type='create', obj_type='area', obj_id=area.id, obj_name=area.name,
                                 detail=f"新增区域：名称={area.name}")

            # 🔥 缓存失效：新增区域后清除所有缓存
            cache.clear()
            return JsonResponse({'code': 1, 'msg': '添加成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_edit')
def area_edit(request, pk):
    try:
        area = get_object_or_404(Area, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '区域名重复'}, content_type='application/json')

            area.name = name
            area.remark = remark
            area.save()
            create_operation_log(request=request, op_type='update', obj_type='area', obj_id=area.id, obj_name=area.name,
                                 detail=f"编辑区域")

            # 🔥 缓存失效：编辑区域后清除所有缓存
            cache.clear()
            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_delete')
def area_delete(request, pk):
    try:
        area = get_object_or_404(Area, pk=pk)
        area.delete()
        create_operation_log(request=request, op_type='delete', obj_type='area', obj_id=pk, obj_name=area.name,
                             detail=f"删除区域")

        # 🔥 缓存失效：删除区域后清除所有缓存
        cache.clear()
        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 区域组管理（添加缓存+缓存失效）=====================
@csrf_exempt
@login_required
@permission_required('area_view')
@cache_page(CACHE_API_LIST)  # 核心列表接口缓存 5分钟
def group_list(request):
    """区域组列表【缓存优化】"""
    try:
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = max(int(request.GET.get('page', 1)), 1)

        # 🔥 修复1：强制限制最大分页容量，防止OOM
        page_size = min(int(request.GET.get('page_size', 20)), PAGE_SIZE_MAX)
        # 排序白名单
        if sort_by not in ALLOW_SORT_GROUP:
            sort_by = 'name'

        # 🔥 修复2：优化预加载，仅加载需要的字段
        groups = AreaGroup.objects.prefetch_related(
            Prefetch('areas', queryset=Area.objects.only('id', 'name'))
        )

        # 搜索过滤
        if keyword:
            groups = groups.filter(
                Q(name__icontains=keyword) | Q(remark__icontains=keyword) | Q(areas__name__icontains=keyword)
            ).distinct()

        # 排序+分页
        sort_by = f'-{sort_by}' if sort_order == 'desc' else sort_by
        groups = groups.order_by(sort_by)
        total = groups.count()
        start = (page - 1) * page_size
        end = start + page_size
        groups_page = groups[start:end]

        # 🔥 修复3：批量统计客户数，1次查询替代N次循环查询
        all_area_ids = []
        group_area_map = {}
        for g in groups_page:
            area_ids = [a.id for a in g.areas.all()]
            group_area_map[g.id] = area_ids
            all_area_ids.extend(area_ids)

        # 批量聚合统计
        customer_count_map = dict(
            Customer.objects.filter(area_id__in=all_area_ids)
            .values('area_id')
            .annotate(count=Count('id'))
            .values_list('area_id', 'count')
        )

        # 构造结果
        result = []
        for g in groups_page:
            area_ids = group_area_map[g.id]
            # 汇总客户数
            customer_count = sum([customer_count_map.get(aid, 0) for aid in area_ids])
            area_count = len(area_ids)

            result.append({
                'id': g.id, 'name': g.name, 'remark': g.remark or '',
                'area_ids': area_ids, 'area_names': [a.name for a in g.areas.all()],
                'customer_count': customer_count, 'area_count': area_count,
                'create_time': g.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                'update_time': g.update_time.strftime('%Y-%m-%d %H:%M:%S')
            })

        return JsonResponse({
            'code': 1, 'data': result,
            'pagination': {'total': total, 'page': page, 'page_size': page_size,
                           'total_pages': (total + page_size - 1) // page_size}
        }, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组列表失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
@cache_page(CACHE_API_DETAIL)  # 详情接口缓存 5分钟
def group_detail_api(request, pk):
    """区域组详情【缓存优化】"""
    try:
        # 优化预加载
        group = get_object_or_404(AreaGroup.objects.prefetch_related('areas'), pk=pk)
        stats = get_group_statistics(pk)
        customer_count = stats['customer_count']
        area_count = stats['area_count']
        areas_qs = group.areas.all()

        # 🔥 修复：批量统计区域客户数，1次查询替代循环N次
        area_ids = [a.id for a in areas_qs]
        area_customer_map = dict(
            Customer.objects.filter(area_id__in=area_ids)
            .values('area_id')
            .annotate(count=Count('id'))
            .values_list('area_id', 'count')
        )

        areas = [
            {'id': a.id, 'name': a.name, 'customer_count': area_customer_map.get(a.id, 0)}
            for a in areas_qs
        ]

        data = {
            'id': group.id, 'name': group.name, 'remark': group.remark or '',
            'create_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'update_time': group.update_time.strftime('%Y-%m-%d %H:%M:%S'),
            'area_count': area_count, 'area_names': [a.name for a in areas_qs],
            'customer_count': customer_count, 'areas': areas
        }
        return JsonResponse({'code': 1, 'data': data}, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_add')
def group_add(request):
    if request.method == 'POST':
        try:
            # 统一解析参数
            is_json = request.content_type == 'application/json'
            data = json.loads(request.body) if is_json else request.POST
            name = data.get('name', '').strip()
            remark = data.get('remark', '').strip()
            area_ids = data.get('area_ids', []) if is_json else data.getlist('area_ids[]')

            # 参数校验
            if not name or AreaGroup.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '组名不能为空/已存在'}, content_type='application/json')

            # 🔥 修复：合并重复查询，1次查询获取id+name，消除2次DB请求
            valid_areas = list(Area.objects.filter(id__in=area_ids).only('id', 'name'))
            valid_area_ids = [a.id for a in valid_areas]
            valid_area_names = [a.name for a in valid_areas]
            area_names_str = ','.join(valid_area_names) if valid_area_names else '无'

            # 创建+关联
            g = AreaGroup.objects.create(name=name, remark=remark)
            g.areas.set(valid_area_ids)

            create_operation_log(
                request=request, op_type='create', obj_type='area_group',
                obj_id=g.id, obj_name=g.name,
                detail=f"新增区域组：{g.name}，包含区域：{area_names_str}"
            )

            # 🔥 缓存失效：新增区域组后清除所有缓存
            cache.clear()
            return JsonResponse({'code': 1, 'msg': '创建成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域组失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'创建失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_edit')
def group_edit(request, pk):
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        if request.method == 'POST':
            # 统一解析参数
            is_json = request.content_type == 'application/json'
            data = json.loads(request.body) if is_json else request.POST
            name = data.get('name', '').strip()
            remark = data.get('remark', '').strip()
            area_ids = data.get('area_ids', []) if is_json else data.getlist('area_ids[]')

            # 参数校验
            if not name or AreaGroup.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '组名不能为空/重复'}, content_type='application/json')

            # 🔥 修复：合并重复查询，1次查询替代2次
            valid_areas = list(Area.objects.filter(id__in=area_ids).only('id', 'name'))
            valid_area_ids = [a.id for a in valid_areas]

            # 更新数据
            old_name, old_remark = g.name, g.remark or ''
            g.name = name
            g.remark = remark
            g.save()
            g.areas.set(valid_area_ids)

            create_operation_log(
                request=request, op_type='update', obj_type='area_group',
                obj_id=g.id, obj_name=g.name,
                detail=f"编辑区域组：{old_name}→{name}"
            )

            # 🔥 缓存失效：编辑区域组后清除所有缓存
            cache.clear()
            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'修改失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_delete')
def group_delete(request, pk):
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        g.delete()
        create_operation_log(request=request, op_type='delete', obj_type='area_group', obj_id=pk, obj_name=g.name,
                             detail=f"删除区域组")

        # 🔥 缓存失效：删除区域组后清除所有缓存
        cache.clear()
        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口（添加页面缓存）=====================
@login_required
@cache_page(CACHE_AREA_PAGE)  # 纯模板页面缓存 5分钟
def area_page(request):
    return render(request, 'area_manage/area.html')


@login_required
@cache_page(CACHE_GROUP_PAGE)  # 纯模板页面缓存 5分钟
def group_page(request):
    return render(request, 'area_manage/group.html')


@login_required
@cache_page(CACHE_DETAIL_PAGE)  # 详情页面缓存 5分钟
def area_detail_page(request, pk):
    area = get_object_or_404(Area, pk=pk)
    customer_count = get_area_statistics(pk)['customer_count']
    related_groups = AreaGroup.objects.filter(areas=area)
    area_data = {
        'id': area.id, 'name': area.name, 'code': '', 'parent_name': '',
        'remark': area.remark or '', 'customer_count': customer_count,
        'create_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
        'update_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S')
    }
    return render(request, 'area_manage/area_detail.html', {'area': area_data, 'related_groups': related_groups})


@login_required
@cache_page(CACHE_DETAIL_PAGE)  # 详情页面缓存 5分钟
def group_detail_page(request, pk):
    group = get_object_or_404(AreaGroup, pk=pk)
    customer_count = get_group_statistics(pk)['customer_count']
    group_data = {
        'id': group.id, 'name': group.name, 'area_names': [a.name for a in group.areas.all()],
        'remark': group.remark or '', 'customer_count': customer_count,
        'create_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
        'update_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S')
    }
    return render(request, 'area_manage/group_detail.html', {'group': group_data})