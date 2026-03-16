from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
# 新增：导入日志模型和时间工具
from operation_log.models import OperationLog
from django.utils import timezone

# 复用bill里的模型（表仍在bill，无需重复建）
from bill.models import Area, AreaGroup


# ========== 新增：通用日志记录函数（核心） ==========
def create_operation_log(request, operation_type, object_type, object_id=None, object_name=None, operation_detail=None):
    """
    封装操作日志记录逻辑，容错处理（日志失败不影响主业务）
    :param request: 请求对象（获取用户/IP）
    :param operation_type: 操作类型（对应OperationLog的OPERATION_TYPE_CHOICES）
    :param object_type: 操作对象类型（对应OperationLog的OBJECT_TYPE_CHOICES）
    :param object_id: 操作对象ID
    :param object_name: 操作对象名称
    :param operation_detail: 操作详情（便于追溯）
    """
    # 获取客户端IP（兼容代理场景）
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    ip_address = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')

    # 容错处理：日志记录失败仅打印错误，不中断主流程
    try:
        OperationLog.objects.create(
            operator=request.user if request.user.is_authenticated else None,  # 当前登录用户
            operation_time=timezone.now(),
            operation_type=operation_type,
            object_type=object_type,
            object_id=str(object_id) if object_id else None,
            object_name=object_name,
            operation_detail=operation_detail,
            ip_address=ip_address
        )
    except Exception as e:
        print(f"【区域管理日志记录失败】：{str(e)}")


# ===================== 区域管理 CRUD =====================
@csrf_exempt
def area_list(request):
    """获取所有区域列表"""
    try:
        # 验证模型是否能正常查询
        areas = Area.objects.all().order_by('name')
        # 处理null值（避免JSON序列化失败）
        result = []
        for a in areas:
            result.append({
                'id': a.id,
                'name': a.name,
                'remark': a.remark if a.remark else ''  # 处理None值
            })
        # 强制返回JSON，设置content_type
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        # 出错时也返回JSON，而非HTML错误页
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


@csrf_exempt
def area_add(request):
    """新增区域"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '区域已存在'}, content_type='application/json')
            # 新增区域并记录日志
            area = Area.objects.create(name=name, remark=remark)

            # ========== 新增：记录新增区域日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='area',
                object_id=area.id,
                object_name=area.name,
                operation_detail=f"新增区域：名称={area.name}，备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '添加成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
def area_edit(request, pk):
    """编辑区域"""
    try:
        area = get_object_or_404(Area, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            remark = request.POST.get('remark', '').strip()
            if not name:
                return JsonResponse({'code': 0, 'msg': '区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '区域名重复'}, content_type='application/json')

            # 保存修改前的信息（用于日志对比）
            old_name = area.name
            old_remark = area.remark if area.remark else '无'

            # 更新区域信息
            area.name = name
            area.remark = remark
            area.save()

            # ========== 新增：记录编辑区域日志 ==========
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='area',
                object_id=area.id,
                object_name=area.name,
                operation_detail=f"编辑区域：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
def area_delete(request, pk):
    """删除区域"""
    try:
        area = get_object_or_404(Area, pk=pk)
        # 保存删除前的信息（删除后无法获取）
        area_name = area.name
        area_remark = area.remark if area.remark else '无'

        # 删除区域
        area.delete()

        # ========== 新增：记录删除区域日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='area',
            object_id=pk,
            object_name=area_name,
            operation_detail=f"删除区域：ID={pk}，名称={area_name}，备注={area_remark}"
        )

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 区域组管理 CRUD =====================
@csrf_exempt
def group_list(request):
    """获取所有区域组列表"""
    try:
        groups = AreaGroup.objects.all().order_by('name')
        data = []
        for g in groups:
            data.append({
                'id': g.id,
                'name': g.name,
                'remark': g.remark if g.remark else '',
                'area_ids': [a.id for a in g.areas.all()],
                'area_names': [a.name for a in g.areas.all()]
            })
        return JsonResponse(data, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, safe=False, content_type='application/json')


@csrf_exempt
def group_add(request):
    """新增区域组"""
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

            # ========== 新增：记录新增区域组日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='area_group',
                object_id=g.id,
                object_name=g.name,
                operation_detail=f"新增区域组：名称={g.name}，备注={remark if remark else '无'}，包含区域={area_names_str}（ID：{','.join(map(str, valid_area_ids))}）"
            )

            return JsonResponse({'code': 1, 'msg': '创建成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'创建失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
def group_edit(request, pk):
    """编辑区域组"""
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

            # 保存修改前的信息（用于日志对比）
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

            # ========== 新增：记录编辑区域组日志 ==========
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='area_group',
                object_id=g.id,
                object_name=g.name,
                operation_detail=f"编辑区域组：原名称={old_name}→新名称={name}，原备注={old_remark}→新备注={remark if remark else '无'}，原包含区域={old_area_names_str}（ID：{','.join(map(str, old_area_ids))}）→新包含区域={new_area_names_str}（ID：{','.join(map(str, valid_area_ids))}）"
            )

            return JsonResponse({'code': 1, 'msg': '修改成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'修改失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
def group_delete(request, pk):
    """删除区域组"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        # 保存删除前的信息（删除后无法获取）
        group_name = g.name
        group_remark = g.remark if g.remark else '无'
        area_ids = [a.id for a in g.areas.all()]
        area_names = [a.name for a in g.areas.all()]
        area_names_str = ','.join(area_names) if area_names else '无'

        # 删除区域组
        g.delete()

        # ========== 新增：记录删除区域组日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='area_group',
            object_id=pk,
            object_name=group_name,
            operation_detail=f"删除区域组：ID={pk}，名称={group_name}，备注={group_remark}，包含区域={area_names_str}（ID：{','.join(map(str, area_ids))}）"
        )

        return JsonResponse({'code': 1, 'msg': '删除成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口 =====================
def area_page(request):
    """区域管理页面"""
    return render(request, 'area_manage/area.html')


def group_page(request):
    """区域组管理页面"""
    return render(request, 'area_manage/group.html')