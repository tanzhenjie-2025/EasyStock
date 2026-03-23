from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import logging
from django.db import models
from django.db.models import Q, Sum
# 复用用户模块的核心依赖
from accounts.models import Permission
from accounts.views import permission_required, create_operation_log, get_client_ip

# 复用bill里的模型
from bill.models import Area, AreaGroup, Customer, Order

# 配置日志
logger = logging.getLogger(__name__)


# ========== 新增：统计工具函数 ==========
def get_area_statistics(area_id):
    """获取单个区域的统计数据"""
    try:
        # 1. 客户数量
        customer_count = Customer.objects.filter(area_id=area_id).count()

        # 2. 累计金额（排除作废订单）
        valid_orders = Order.objects.filter(
            area_id=area_id,
            status__ne='cancelled'  # 排除作废单
        )
        total_amount = valid_orders.aggregate(
            total=  Sum('total_amount')
        )['total'] or 0

        return {
            'customer_count': customer_count,
            'total_amount': float(total_amount)
        }
    except Exception as e:
        logger.error(f"获取区域{area_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'total_amount': 0.0}


def get_group_statistics(group_id):
    """获取区域组的统计数据"""
    try:
        # 获取区域组包含的所有区域ID
        group = get_object_or_404(AreaGroup, pk=group_id)
        area_ids = group.areas.values_list('id', flat=True)

        # 1. 客户数量
        customer_count = Customer.objects.filter(area_id__in=area_ids).count()

        # 2. 累计金额（排除作废订单）
        valid_orders = Order.objects.filter(
            area_id__in=area_ids,
            status__ne='cancelled'
        )
        total_amount = valid_orders.aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        return {
            'customer_count': customer_count,
            'total_amount': float(total_amount),
            'area_count': len(area_ids)
        }
    except Exception as e:
        logger.error(f"获取区域组{group_id}统计数据失败：{str(e)}")
        return {'customer_count': 0, 'total_amount': 0.0, 'area_count': 0}


# ===================== 区域管理 CRUD（带RBAC权限） =====================
@login_required
@permission_required('area_view')
@csrf_exempt
def area_list(request):
    """获取所有区域列表（支持关键词搜索，新增统计数据）"""
    try:
        # 获取搜索关键词
        keyword = request.GET.get('keyword', '').strip()
        # 获取排序参数
        sort_by = request.GET.get('sort', 'name')  # 默认按名称排序
        sort_order = request.GET.get('order', 'asc')  # asc/desc

        # 基础查询
        areas = Area.objects.all()

        # 关键词过滤（匹配区域名或备注）
        if keyword:
            areas = areas.filter(
                models.Q(name__icontains=keyword) | models.Q(remark__icontains=keyword)
            )

        # 排序处理
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        areas = areas.order_by(sort_by)

        result = []
        for a in areas:
            # 获取统计数据
            stats = get_area_statistics(a.id)
            result.append({
                'id': a.id,
                'name': a.name,
                'remark': a.remark if a.remark else '',
                'customer_count': stats['customer_count'],
                'total_amount': stats['total_amount']
            })
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域列表失败：{str(e)}")
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


@login_required
@permission_required('area_view')
@csrf_exempt
def area_detail_api(request, pk):
    """区域详情接口"""
    try:
        area = get_object_or_404(Area, pk=pk)
        stats = get_area_statistics(pk)

        # 获取该区域的客户列表（简要信息）
        customers = Customer.objects.filter(area_id=pk).values('id', 'name', 'phone')

        # 获取该区域的有效订单数
        order_count = Order.objects.filter(
            area_id=pk,
            status__ne='cancelled'
        ).count()

        data = {
            'id': area.id,
            'name': area.name,
            'remark': area.remark if area.remark else '',
            'create_time': area.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'customer_count': stats['customer_count'],
            'total_amount': stats['total_amount'],
            'order_count': order_count,
            'customers': list(customers)
        }
        return JsonResponse(data, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@login_required
@permission_required('area_add')
@csrf_exempt
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

            return JsonResponse({'code': 1, 'msg': '添加成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@login_required
@permission_required('area_edit')
@csrf_exempt
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

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


@login_required
@permission_required('area_delete')
@csrf_exempt
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

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        logger.error(f"删除区域失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 区域组管理 CRUD（带RBAC权限） =====================
@login_required
@permission_required('area_view')
@csrf_exempt
def group_list(request):
    """获取所有区域组列表（支持关键词搜索，新增统计数据）"""
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
                models.Q(name__icontains=keyword) |  # 匹配组名
                models.Q(remark__icontains=keyword) |  # 匹配备注
                models.Q(areas__name__icontains=keyword)  # 匹配包含的区域名
            ).distinct()  # 多对多关联去重

        # 排序处理
        if sort_order == 'desc':
            sort_by = f'-{sort_by}'
        groups = groups.order_by(sort_by)

        data = []
        for g in groups:
            # 获取统计数据
            stats = get_group_statistics(g.id)
            data.append({
                'id': g.id,
                'name': g.name,
                'remark': g.remark if g.remark else '',
                'area_ids': [a.id for a in g.areas.all()],
                'area_names': [a.name for a in g.areas.all()],
                'customer_count': stats['customer_count'],
                'total_amount': stats['total_amount'],
                'area_count': stats['area_count']
            })
        return JsonResponse(data, safe=False, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组列表失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, safe=False, content_type='application/json')


@login_required
@permission_required('area_view')
@csrf_exempt
def group_detail_api(request, pk):
    """区域组详情接口"""
    try:
        group = get_object_or_404(AreaGroup, pk=pk)
        stats = get_group_statistics(pk)

        # 获取包含的区域详情
        areas = []
        for area in group.areas.all():
            area_stats = get_area_statistics(area.id)
            areas.append({
                'id': area.id,
                'name': area.name,
                'customer_count': area_stats['customer_count'],
                'total_amount': area_stats['total_amount']
            })

        data = {
            'id': group.id,
            'name': group.name,
            'remark': group.remark if group.remark else '',
            'create_time': group.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'area_count': stats['area_count'],
            'customer_count': stats['customer_count'],
            'total_amount': stats['total_amount'],
            'areas': areas
        }
        return JsonResponse(data, content_type='application/json')
    except Exception as e:
        logger.error(f"查询区域组{pk}详情失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, content_type='application/json')


@login_required
@permission_required('area_add')
@csrf_exempt
def group_add(request):
    """新增区域组（需area_add权限）"""
    if request.method == 'POST':
        try:
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

            return JsonResponse({'code': 1, 'msg': '创建成功'}, content_type='application/json')
        except Exception as e:
            logger.error(f"新增区域组失败：{str(e)}")
            return JsonResponse({'code': 0, 'msg': f'创建失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@login_required
@permission_required('area_edit')
@csrf_exempt
def group_edit(request, pk):
    """编辑区域组（需area_edit权限）"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        if request.method == 'POST':
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

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        logger.error(f"编辑区域组失败：{str(e)}")
        return JsonResponse({'code': 0, 'msg': f'修改失败：{str(e)}'}, content_type='application/json')


@login_required
@permission_required('area_delete')
@csrf_exempt
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
    """区域详情页面"""
    return render(request, 'area_manage/area_detail.html', {'area_id': pk})


@login_required
def group_detail_page(request, pk):
    """区域组详情页面"""
    return render(request, 'area_manage/group_detail.html', {'group_id': pk})