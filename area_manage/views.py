from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import logging
import json
# 移除Decimal导入（不再使用金额计算）
from django.db import models
from django.db.models import Q  # 移除Sum（不再计算金额）
# 复用用户模块的核心依赖
from accounts.models import Permission
from accounts.views import permission_required, create_operation_log, get_client_ip

# 复用bill里的模型
from bill.models import (
    Area, AreaGroup, Customer, Order,
    AreaStatisticsCache, AreaGroupStatisticsCache  # 新增缓存模型
)

# 配置日志
logger = logging.getLogger(__name__)


def get_area_statistics(area_id):
    """获取单个区域的统计数据 - 仅统计该区域下的总客户数量（移除订单关联）"""
    try:
        # 核心修改：直接统计该区域下的所有客户数量（不管是否有订单）
        customer_count = Customer.objects.filter(area_id=area_id).count()

        return {
            'customer_count': customer_count  # 移除total_amount字段
        }
    except Exception as e:
        logger.error(f"获取区域{area_id}统计数据失败：{str(e)}")
        return {'customer_count': 0}


def get_group_statistics(group_id):
    """获取区域组的统计数据 - 仅统计该组下的总客户数量（移除订单关联）"""
    try:
        group = get_object_or_404(AreaGroup, pk=group_id)
        area_ids = group.areas.values_list('id', flat=True)

        # 核心修改：直接统计该组下所有区域的客户总数（不管是否有订单）
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()

        return {
            'customer_count': customer_count,
            'area_count': len(area_ids)  # 保留区域数量，移除total_amount
        }
    except Exception as e:
        logger.error(f"获取区域组{group_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'area_count': 0}


# ========== 预计算缓存函数（适配仅统计客户数量） ==========
def refresh_area_statistics_cache():
    """刷新所有区域的统计缓存（仅客户数量）"""
    try:
        logger.info("开始刷新区域统计缓存...")
        for area in Area.objects.all():
            stats = get_area_statistics(area.id)
            # 更新或创建缓存（移除total_amount字段）
            AreaStatisticsCache.objects.update_or_create(
                area=area,
                defaults={
                    'customer_count': stats['customer_count']
                }
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
            # 更新或创建缓存（移除total_amount字段）
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


# ===================== 区域管理 CRUD（带RBAC权限） =====================
# 修复：装饰器顺序 - csrf_exempt 必须在最外层
@csrf_exempt
@login_required
@permission_required('area_view')
def area_list(request):
    """获取所有区域列表（支持关键词搜索，使用预计算缓存）"""
    try:
        keyword = request.GET.get('keyword', '').strip()
        sort_by = request.GET.get('sort', 'name')
        sort_order = request.GET.get('order', 'asc')

        areas = Area.objects.all()
        if keyword:
            areas = areas.filter(Q(name__icontains=keyword) | Q(remark__icontains=keyword))
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        areas = areas.order_by(sort_by)

        # ========== 优化点1：批量获取缓存，减少查询 ==========
        area_ids = [a.id for a in areas]
        # 批量查询已有缓存
        cache_map = {
            cache.area_id: cache
            for cache in AreaStatisticsCache.objects.filter(area_id__in=area_ids)
        }
        # 收集无缓存的区域ID
        no_cache_area_ids = [a.id for a in areas if a.id not in cache_map]

        # 批量计算无缓存的区域客户数（1次查询替代多次）
        no_cache_stats = {}
        if no_cache_area_ids:
            # 用annotate批量统计，仅1次数据库查询
            from django.db.models import Count
            stats_query = Customer.objects.filter(area_id__in=no_cache_area_ids).values('area_id').annotate(
                count=Count('id'))
            no_cache_stats = {item['area_id']: item['count'] for item in stats_query}
            # 批量创建缓存
            cache_objs = [
                AreaStatisticsCache(area_id=area_id, customer_count=no_cache_stats.get(area_id, 0))
                for area_id in no_cache_area_ids
            ]
            AreaStatisticsCache.objects.bulk_create(cache_objs)

        # 构造返回数据
        result = []
        for a in areas:
            # 优先从缓存取，无则从批量计算结果取
            cache = cache_map.get(a.id)
            if cache:
                customer_count = cache.customer_count
            else:
                customer_count = no_cache_stats.get(a.id, 0)

            result.append({
                'id': a.id,
                'name': a.name,
                'remark': a.remark if a.remark else '',
                'customer_count': customer_count,
                'create_time': a.create_time.strftime('%Y-%m-%d %H:%M:%S')
            })
        return JsonResponse({'code': 1, 'data': result}, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域列表失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
@login_required
@permission_required('area_view')
def area_detail_api(request, pk):
    """区域详情接口（仅统计总客户数量）"""
    try:
        area = get_object_or_404(Area, pk=pk)
        # 读取缓存数据
        try:
            cache = area.stats_cache
            customer_count = cache.customer_count
        except AreaStatisticsCache.DoesNotExist:
            stats = get_area_statistics(pk)
            customer_count = stats['customer_count']

        # 保留客户列表、订单数量（可选，若不需要可移除）
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
            'customer_count': customer_count,  # 仅保留客户数量
            'order_count': order_count,  # 可选：保留订单数量（不需要可删除）
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

            # 新增区域
            area = Area.objects.create(name=name, remark=remark)

            # 记录操作日志（复用用户模块的日志函数）
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='area',
                obj_id=area.id,
                obj_name=area.name,
                detail=f"新增区域：名称={area.name}，备注={remark if remark else '无'}"
            )

            # 新增后刷新缓存
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

            # 保存修改前的信息
            old_name = area.name
            old_remark = area.remark if area.remark else '无'

            # 更新区域信息
            area.name = name
            area.remark = remark
            area.save()

            # 记录日志
            create_operation_log(
                request=request,
                op_type='update',
                obj_type='area',
                obj_id=area.id,
                obj_name=area.name,
                detail=f"编辑区域：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}"
            )

            # 编辑后刷新缓存
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
        # 保存删除前的信息
        area_name = area.name
        area_remark = area.remark if area.remark else '无'

        # 删除区域
        area.delete()

        # 记录日志
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='area',
            obj_id=pk,
            obj_name=area_name,
            detail=f"删除区域：ID={pk}，名称={area_name}，备注={area_remark}"
        )

        # 删除后刷新缓存
        refresh_area_statistics_cache()
        refresh_group_statistics_cache()  # 区域组缓存也需要刷新

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 区域组管理 CRUD（带RBAC权限） =====================
@csrf_exempt
@login_required
@permission_required('area_view')
def group_list(request):
    """获取所有区域组列表（支持关键词搜索，仅统计总客户数量）"""
    try:
        # 获取搜索关键词
        keyword = request.GET.get('keyword', '').strip()
        # 获取排序参数
        sort_by = request.GET.get('sort', 'name')  # 默认按名称排序
        sort_order = request.GET.get('order', 'asc')  # asc/desc

        # 基础查询
        groups = AreaGroup.objects.all()

        # 关键词过滤（匹配组名、备注、包含的区域名）
        if keyword:
            groups = groups.filter(
                Q(name__icontains=keyword) |  # 匹配组名
                Q(remark__icontains=keyword) |  # 匹配备注
                Q(areas__name__icontains=keyword)  # 匹配包含的区域名
            ).distinct()  # 多对多关联去重

        # 排序处理
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        groups = groups.order_by(sort_by)

        data = []
        for g in groups:
            # 读取缓存数据，没有则初始化
            try:
                cache = g.stats_cache
                customer_count = cache.customer_count
                area_count = cache.area_count
            except AreaGroupStatisticsCache.DoesNotExist:
                # 缓存不存在时，实时计算并创建缓存（兜底）
                stats = get_group_statistics(g.id)
                customer_count = stats['customer_count']
                area_count = stats['area_count']
                AreaGroupStatisticsCache.objects.create(
                    group=g,
                    customer_count=customer_count,
                    area_count=area_count
                )

            data.append({
                'id': g.id,
                'name': g.name,
                'remark': g.remark if g.remark else '',
                'area_ids': [a.id for a in g.areas.all()],
                'area_names': [a.name for a in g.areas.all()],
                'customer_count': customer_count,  # 仅保留客户数量
                'area_count': area_count,  # 保留区域数量
                'create_time': g.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                'update_time': g.create_time.strftime('%Y-%m-%d %H:%M:%S')
            })
        # 统一返回格式
        return JsonResponse({'code': 1, 'data': data}, content_type='application/json')
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
        # 读取缓存数据
        try:
            cache = group.stats_cache
            customer_count = cache.customer_count
            area_count = cache.area_count
        except AreaGroupStatisticsCache.DoesNotExist:
            stats = get_group_statistics(pk)
            customer_count = stats['customer_count']
            area_count = stats['area_count']

        # 获取包含的区域详情（仅保留客户数量）
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
            'customer_count': customer_count,  # 仅保留客户数量
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
            # 解析JSON参数（前端传递JSON）
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

            # 验证区域ID是否有效
            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            valid_area_names = Area.objects.filter(id__in=valid_area_ids).values_list('name', flat=True)
            area_names_str = ','.join(valid_area_names) if valid_area_names else '无'

            # 创建区域组
            g = AreaGroup.objects.create(name=name, remark=remark)
            g.areas.set(valid_area_ids)

            # 记录日志
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='area_group',
                obj_id=g.id,
                obj_name=g.name,
                detail=f"新增区域组：名称={g.name}，备注={remark if remark else '无'}，包含区域={area_names_str}（ID：{','.join(map(str, valid_area_ids))}）"
            )

            # 新增后刷新缓存
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
            # 解析JSON参数
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

            # 保存修改前的信息
            old_name = g.name
            old_remark = g.remark if g.remark else '无'
            old_area_ids = [a.id for a in g.areas.all()]
            old_area_names = [a.name for a in g.areas.all()]
            old_area_names_str = ','.join(old_area_names) if old_area_names else '无'

            # 验证新区域ID是否有效
            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            valid_area_names = Area.objects.filter(id__in=valid_area_ids).values_list('name', flat=True)
            new_area_names_str = ','.join(valid_area_names) if valid_area_names else '无'

            # 更新区域组信息
            g.name = name
            g.remark = remark
            g.save()
            g.areas.set(valid_area_ids)

            # 记录日志
            create_operation_log(
                request=request,
                op_type='update',
                obj_type='area_group',
                obj_id=g.id,
                obj_name=g.name,
                detail=f"编辑区域组：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}，原包含区域={old_area_names_str}（ID：{','.join(map(str, old_area_ids))}）→新包含区域={new_area_names_str}（ID：{','.join(map(str, valid_area_ids))}）"
            )

            # 编辑后刷新缓存
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
        # 保存删除前的信息
        group_name = g.name
        group_remark = g.remark if g.remark else '无'
        area_ids = [a.id for a in g.areas.all()]
        area_names = [a.name for a in g.areas.all()]
        area_names_str = ','.join(area_names) if area_names else '无'

        # 删除区域组
        g.delete()

        # 记录日志
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='area_group',
            obj_id=pk,
            obj_name=group_name,
            detail=f"删除区域组：ID={pk}，名称={group_name}，备注={group_remark}，包含区域={area_names_str}（ID：{','.join(map(str, area_ids))}）"
        )

        # 删除后刷新缓存
        refresh_group_statistics_cache()

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口（带登录校验） =====================
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
    # 读取缓存数据
    try:
        cache = area.stats_cache
        customer_count = cache.customer_count
    except AreaStatisticsCache.DoesNotExist:
        stats = get_area_statistics(pk)
        customer_count = stats['customer_count']

    # 获取关联的区域组
    related_groups = AreaGroup.objects.filter(areas=area)

    # 构造模板需要的area对象（移除total_amount）
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
    # 读取缓存数据
    try:
        cache = group.stats_cache
        customer_count = cache.customer_count
    except AreaGroupStatisticsCache.DoesNotExist:
        stats = get_group_statistics(pk)
        customer_count = stats['customer_count']

    # 构造模板需要的group对象（移除total_amount）
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