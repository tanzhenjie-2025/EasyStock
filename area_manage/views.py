from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import logging
import json
from django.db.models import Q, Count
from accounts.models import Permission
from accounts.views import permission_required, create_operation_log, get_client_ip
from bill.models import (
    Area, AreaGroup, Customer, Order,
    AreaStatisticsCache, AreaGroupStatisticsCache
)

# 配置日志
logger = logging.getLogger(__name__)


def get_area_statistics(area_id):
    """获取单个区域的统计数据 - 仅统计该区域下的总客户数量"""
    try:
        customer_count = Customer.objects.filter(area_id=area_id).count()
        return {'customer_count': customer_count}
    except Exception as e:
        logger.error(f"获取区域{area_id}统计数据失败：{str(e)}")
        return {'customer_count': 0}


def get_group_statistics(group_id):
    """获取区域组的统计数据 - 仅统计该组下的总客户数量"""
    try:
        group = get_object_or_404(AreaGroup, pk=group_id)
        area_ids = group.areas.values_list('id', flat=True)
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()
        return {'customer_count': customer_count, 'area_count': len(area_ids)}
    except Exception as e:
        logger.error(f"获取区域组{group_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'area_count': 0}


def refresh_area_statistics_cache():
    """刷新所有区域的统计缓存（仅客户数量）"""
    try:
        logger.info("开始刷新区域统计缓存...")
        for area in Area.objects.all():
            stats = get_area_statistics(area.id)
            AreaStatisticsCache.objects.update_or_create(
                area=area,
                defaults={'customer_count': stats['customer_count']}
            )
        logger.info("区域统计缓存刷新完成")
    except Exception as e:
        logger.error(f"刷新区域统计缓存失败：{str(e)}")


def refresh_group_statistics_cache():
    """刷新所有区域组的统计缓存（仅客户数量+区域数量）"""
    try:
        logger.info("开始刷新区域组统计缓存...")
        for group in AreaGroup.objects.all():
            stats = get_group_statistics(group.id)
            AreaGroupStatisticsCache.objects.update_or_create(
                group=group,
                defaults={
                    'customer_count': stats['customer_count'],
                    'area_count': stats['area_count']
                }
            )
        logger.info("区域组统计缓存刷新完成")
    except Exception as e:
        logger.error(f"刷新区域组统计缓存失败：{str(e)}")


def refresh_all_statistics_cache():
    """刷新所有统计缓存（对外暴露的统一函数）"""
    try:
        refresh_area_statistics_cache()
        refresh_group_statistics_cache()
    except Exception as e:
        logger.error(f"刷新所有统计缓存失败：{str(e)}")


# ===================== 区域管理 CRUD =====================
@csrf_exempt
@login_required
@permission_required('area_view')
def area_list(request):
    """获取所有区域列表（支持关键词搜索+分页+批量缓存）【性能优化版】"""
    try:
        # ===================== 1. 安全参数处理（性能+安全双保障）=====================
        # 强制限制：每页最多20条（满足你的需求），不允许前端修改
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = max(int(request.GET.get('page', 1)), 1)  # 页码最小为1
        PAGE_SIZE_MAX = 20  # 硬编码限制，核心需求

        # 排序白名单：防止SQL注入 + 非法字段报错
        ALLOW_SORT_FIELDS = ['name', 'id', 'create_time']
        if sort_by not in ALLOW_SORT_FIELDS:
            sort_by = 'name'

        # ===================== 2. 高性能查询（仅查需要的字段，懒加载）=====================
        # only()：只查询使用的字段，大幅减少数据库IO
        areas = Area.objects.only('id', 'name', 'remark', 'create_time')

        # 关键词搜索（已有索引，性能拉满）
        if keyword:
            areas = areas.filter(Q(name__icontains=keyword) | Q(remark__icontains=keyword))

        # 排序处理
        areas = areas.order_by(f'-{sort_by}' if sort_order == 'desc' else sort_by)

        # ===================== 3. 精准分页（仅查询当前页数据，无全表加载）=====================
        total = areas.count()  # 计数走索引，高效
        start = (page - 1) * PAGE_SIZE_MAX
        end = start + PAGE_SIZE_MAX
        # 核心：数据库层面切片，仅查询20条数据，不会加载全表
        areas_page = areas[start:end]

        # ===================== 4. 批量缓存优化（减少查询次数）=====================
        # 生成当前页区域ID列表（生成器表达式，内存占用更低）
        area_ids = (a.id for a in areas_page)
        # 批量查询缓存，仅取需要的字段
        cache_map = {
            item['area_id']: item['customer_count']
            for item in AreaStatisticsCache.objects.filter(area_id__in=area_ids)
            .values('area_id', 'customer_count')
        }

        # 批量统计无缓存的区域（1次聚合查询，性能最优）
        no_cache_area_ids = [a.id for a in areas_page if a.id not in cache_map]
        if no_cache_area_ids:
            # 高效聚合：直接返回id->count字典
            no_cache_stats = dict(
                Customer.objects.filter(area_id__in=no_cache_area_ids)
                .values('area_id')
                .annotate(count=Count('id'))
                .values_list('area_id', 'count')
            )
            # 批量创建缓存（1次插入，替代循环N次）
            cache_objs = [
                AreaStatisticsCache(area_id=aid, customer_count=no_cache_stats.get(aid, 0))
                for aid in no_cache_area_ids
            ]
            AreaStatisticsCache.objects.bulk_create(cache_objs)
            # 合并缓存数据
            cache_map.update(no_cache_stats)

        # ===================== 5. 快速构造返回数据=====================
        result = [
            {
                'id': a.id,
                'name': a.name,
                'remark': a.remark or '',
                'customer_count': cache_map.get(a.id, 0),
                'create_time': a.create_time.strftime('%Y-%m-%d %H:%M:%S')
            }
            for a in areas_page
        ]

        # ===================== 6. 返回分页结果=====================
        return JsonResponse({
            'code': 1,
            'data': result,
            'pagination': {
                'total': total,
                'page': page,
                'page_size': PAGE_SIZE_MAX,
                'total_pages': (total + PAGE_SIZE_MAX - 1) // PAGE_SIZE_MAX
            }
        }, content_type='application/json')

    except Exception as e:
        logger.error(f"查询区域列表失败：{str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
def area_detail_api(request, pk):
    """区域详情接口（仅统计总客户数量）"""
    try:
        area = get_object_or_404(Area, pk=pk)
        try:
            cache = area.stats_cache
            customer_count = cache.customer_count
        except AreaStatisticsCache.DoesNotExist:
            stats = get_area_statistics(pk)
            customer_count = stats['customer_count']

        customers = Customer.objects.filter(area_id=pk).values('id', 'name', 'phone')
        order_count = Order.objects.filter(area_id=pk).exclude(status='cancelled').count()
        related_groups = AreaGroup.objects.filter(areas=area).values('id', 'name')

        data = {
            'id': area.id,
            'name': area.name,
            'code': '',
            'parent_name': '',
            'remark': area.remark if area.remark else '',
            'create_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'update_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
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
    """新增区域（需area_add权限）"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '区域已存在'}, content_type='application/json')

            area = Area.objects.create(name=name, remark=remark)
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='area',
                obj_id=area.id,
                obj_name=area.name,
                detail=f"新增区域：名称={area.name}，备注={remark if remark else '无'}"
            )
            refresh_area_statistics_cache()

            return JsonResponse({'code': 1, 'msg': '添加成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_edit')
def area_edit(request, pk):
    """编辑区域（需area_edit权限）"""
    try:
        area = get_object_or_404(Area, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '区域名重复'}, content_type='application/json')

            old_name = area.name
            old_remark = area.remark if area.remark else '无'
            area.name = name
            area.remark = remark
            area.save()

            create_operation_log(
                request=request,
                op_type='update',
                obj_type='area',
                obj_id=area.id,
                obj_name=area.name,
                detail=f"编辑区域：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}"
            )
            refresh_area_statistics_cache()

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_delete')
def area_delete(request, pk):
    """删除区域（需area_delete权限）"""
    try:
        area = get_object_or_404(Area, pk=pk)
        area_name = area.name
        area_remark = area.remark if area.remark else '无'

        area.delete()
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='area',
            obj_id=pk,
            obj_name=area_name,
            detail=f"删除区域：ID={pk}，名称={area_name}，备注={area_remark}"
        )
        refresh_area_statistics_cache()
        refresh_group_statistics_cache()

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
def group_list(request):
    """获取所有区域组列表（支持关键词搜索+分页+批量缓存）【优化版】"""
    try:
        # 1. 安全获取参数
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))

        # 排序白名单：防SQL注入 + 非法字段报错
        ALLOW_SORT_FIELDS = ['name', 'create_time', 'id']
        if sort_by not in ALLOW_SORT_FIELDS:
            sort_by = 'name'

        # 2. 核心优化：预加载多对多数据，彻底解决N+1查询！！！
        groups = AreaGroup.objects.all().prefetch_related('areas')

        # 关键词搜索
        if keyword:
            groups = groups.filter(
                Q(name__icontains=keyword) |
                Q(remark__icontains=keyword) |
                Q(areas__name__icontains=keyword)
            ).distinct()

        # 排序
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        groups = groups.order_by(sort_by)

        # 3. 分页处理
        total = groups.count()
        start = (page - 1) * page_size
        end = start + page_size
        groups_page = groups[start:end]

        # 4. 批量缓存（和area_list保持一致，批量查询+批量创建）
        group_ids = [g.id for g in groups_page]
        cache_map = {
            cache.group_id: cache
            for cache in AreaGroupStatisticsCache.objects.filter(group_id__in=group_ids)
        }
        no_cache_group_ids = [g.id for g in groups_page if g.id not in cache_map]

        no_cache_stats = {}
        if no_cache_group_ids:
            # 批量统计（极致性能）
            no_cache_stats = batch_get_group_statistics(no_cache_group_ids)
            # 批量插入缓存
            cache_objs = [
                AreaGroupStatisticsCache(
                    group_id=group_id,
                    customer_count=no_cache_stats[group_id]['customer_count'],
                    area_count=no_cache_stats[group_id]['area_count']
                ) for group_id in no_cache_group_ids
            ]
            AreaGroupStatisticsCache.objects.bulk_create(cache_objs)

        # 5. 构造数据（预加载后无额外查询）
        result = []
        for g in groups_page:
            cache = cache_map.get(g.id)
            if cache:
                customer_count = cache.customer_count
                area_count = cache.area_count
            else:
                stats = no_cache_stats.get(g.id, {'customer_count': 0, 'area_count': 0})
                customer_count = stats['customer_count']
                area_count = stats['area_count']

            result.append({
                'id': g.id,
                'name': g.name,
                'remark': g.remark if g.remark else '',
                'area_ids': [a.id for a in g.areas.all()],
                'area_names': [a.name for a in g.areas.all()],
                'customer_count': customer_count,
                'area_count': area_count,
                'create_time': g.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                # 修复BUG：原代码错误使用create_time
                'update_time': g.update_time.strftime('%Y-%m-%d %H:%M:%S')
            })

        # 返回分页数据
        return JsonResponse({
            'code': 1,
            'data': result,
            'pagination': {
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': (total + page_size - 1) // page_size
            }
        }, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组列表失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
def group_detail_api(request, pk):
    """区域组详情接口（仅统计总客户数量）"""
    try:
        group = get_object_or_404(AreaGroup, pk=pk)
        try:
            cache = group.stats_cache
            customer_count = cache.customer_count
            area_count = cache.area_count
        except AreaGroupStatisticsCache.DoesNotExist:
            stats = get_group_statistics(pk)
            customer_count = stats['customer_count']
            area_count = stats['area_count']

        areas = []
        for area in group.areas.all():
            try:
                area_cache = area.stats_cache
                area_customer_count = area_cache.customer_count
            except AreaStatisticsCache.DoesNotExist:
                area_stats = get_area_statistics(area.id)
                area_customer_count = area_stats['customer_count']

            areas.append({
                'id': area.id,
                'name': area.name,
                'customer_count': area_customer_count
            })

        data = {
            'id': group.id,
            'name': group.name,
            'remark': group.remark if group.remark else '',
            'create_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'update_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'area_count': area_count,
            'area_names': [a.name for a in group.areas.all()],
            'customer_count': customer_count,
            'areas': areas
        }
        return JsonResponse({'code': 1, 'data': data}, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_add')
def group_add(request):
    """新增区域组（需area_add权限）- 支持JSON参数解析"""
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
                name = data.get('name', '').strip()
                remark = data.get('remark', '').strip()
                area_ids = data.get('area_ids', [])
            else:
                name = request.POST.get('name', '').strip()
                remark = request.POST.get('remark', '').strip()
                area_ids = request.POST.getlist('area_ids[]')

            if not name:
                return JsonResponse({'code': 0, 'msg': '组名不能为空'}, content_type='application/json')
            if AreaGroup.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '组名已存在'}, content_type='application/json')

            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            valid_area_names = Area.objects.filter(id__in=valid_area_ids).values_list('name', flat=True)
            area_names_str = ','.join(valid_area_names) if valid_area_names else '无'

            g = AreaGroup.objects.create(name=name, remark=remark)
            g.areas.set(valid_area_ids)

            create_operation_log(
                request=request,
                op_type='create',
                obj_type='area_group',
                obj_id=g.id,
                obj_name=g.name,
                detail=f"新增区域组：名称={g.name}，备注={remark if remark else '无'}，包含区域={area_names_str}（ID：{','.join(map(str, valid_area_ids))}）"
            )
            refresh_group_statistics_cache()

            return JsonResponse({'code': 1, 'msg': '创建成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域组失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'创建失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_edit')
def group_edit(request, pk):
    """编辑区域组（需area_edit权限）- 支持JSON参数解析"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        if request.method == 'POST':
            if request.content_type == 'application/json':
                data = json.loads(request.body)
                name = data.get('name', '').strip()
                remark = data.get('remark', '').strip()
                area_ids = data.get('area_ids', [])
            else:
                name = request.POST.get('name', '').strip()
                remark = request.POST.get('remark', '').strip()
                area_ids = request.POST.getlist('area_ids[]')

            if not name:
                return JsonResponse({'code': 0, 'msg': '组名不能为空'}, content_type='application/json')
            if AreaGroup.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '组名重复'}, content_type='application/json')

            old_name = g.name
            old_remark = g.remark if g.remark else '无'
            old_area_ids = [a.id for a in g.areas.all()]
            old_area_names = [a.name for a in g.areas.all()]
            old_area_names_str = ','.join(old_area_names) if old_area_names else '无'

            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            valid_area_names = Area.objects.filter(id__in=valid_area_ids).values_list('name', flat=True)
            new_area_names_str = ','.join(valid_area_names) if valid_area_names else '无'

            g.name = name
            g.remark = remark
            g.save()
            g.areas.set(valid_area_ids)

            create_operation_log(
                request=request,
                op_type='update',
                obj_type='area_group',
                obj_id=g.id,
                obj_name=g.name,
                detail=f"编辑区域组：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}，原包含区域={old_area_names_str}→新包含区域={new_area_names_str}"
            )
            refresh_group_statistics_cache()

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'修改失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_delete')
def group_delete(request, pk):
    """删除区域组（需area_delete权限）"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        group_name = g.name
        group_remark = g.remark if g.remark else '无'
        area_ids = [a.id for a in g.areas.all()]
        area_names = [a.name for a in g.areas.all()]
        area_names_str = ','.join(area_names) if area_names else '无'

        g.delete()
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='area_group',
            obj_id=pk,
            obj_name=group_name,
            detail=f"删除区域组：ID={pk}，名称={group_name}，备注={group_remark}，包含区域={area_names_str}"
        )
        refresh_group_statistics_cache()

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口 =====================
@login_required
def area_page(request):
    """区域管理页面（需登录）"""
    return render(request, 'area_manage/area.html')


@login_required
def group_page(request):
    """区域组管理页面（需登录）"""
    return render(request, 'area_manage/group.html')


@login_required
def area_detail_page(request, pk):
    """区域详情页面（仅显示总客户数量）"""
    area = get_object_or_404(Area, pk=pk)
    try:
        cache = area.stats_cache
        customer_count = cache.customer_count
    except AreaStatisticsCache.DoesNotExist:
        stats = get_area_statistics(pk)
        customer_count = stats['customer_count']

    related_groups = AreaGroup.objects.filter(areas=area)
    area_data = {
        'id': area.id,
        'name': area.name,
        'code': '',
        'parent_name': '',
        'remark': area.remark or '',
        'customer_count': customer_count,
        'create_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
        'update_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S')
    }

    return render(request, 'area_manage/area_detail.html', {
        'area': area_data,
        'related_groups': related_groups
    })


@login_required
def group_detail_page(request, pk):
    """区域组详情页面（仅显示总客户数量）"""
    group = get_object_or_404(AreaGroup, pk=pk)
    try:
        cache = group.stats_cache
        customer_count = cache.customer_count
    except AreaGroupStatisticsCache.DoesNotExist:
        stats = get_group_statistics(pk)
        customer_count = stats['customer_count']

    group_data = {
        'id': group.id,
        'name': group.name,
        'area_names': [a.name for a in group.areas.all()],
        'remark': group.remark or '',
        'customer_count': customer_count,
        'create_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
        'update_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S')
    }

    return render(request, 'area_manage/group_detail.html', {
        'group': group_data
    })


# 新增：批量获取区域组统计数据
def batch_get_group_statistics(group_ids):
    """批量查询多个区域组的统计数据，性能远超循环调用"""
    from django.db.models import Count
    # 批量获取所有组的区域ID
    group_area_map = {}
    group_areas = AreaGroup.objects.filter(id__in=group_ids).prefetch_related('areas')
    for group in group_areas:
        area_ids = list(group.areas.values_list('id', flat=True))
        group_area_map[group.id] = area_ids

    # 批量统计客户数量
    result = {}
    for group_id, area_ids in group_area_map.items():
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()
        result[group_id] = {
            'customer_count': customer_count,
            'area_count': len(area_ids)
        }
    return result