from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from bill.models import Customer, Area  # 导入客户和区域模型


# ===================== 客户管理CRUD =====================
@csrf_exempt
def customer_list(request):
    """获取客户列表接口"""
    try:
        customers = Customer.objects.all().select_related('area')  # 关联查询区域，提升性能
        result = []
        for c in customers:
            result.append({
                'id': c.id,
                'name': c.name,
                'area_id': c.area.id if c.area else '',
                'area_name': c.area.name if c.area else '',
                'phone': c.phone,
                'remark': c.remark or ''
            })
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


@csrf_exempt
def customer_add(request):
    """新增客户接口"""
    if request.method == 'POST':
        try:
            # 获取前端参数
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验必填项
            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            # 校验唯一性
            if Customer.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            # 校验区域是否存在
            area = get_object_or_404(Area, id=area_id)

            # 创建客户
            Customer.objects.create(
                name=name,
                area=area,
                phone=phone,
                remark=remark
            )
            return JsonResponse({'code': 1, 'msg': '新增客户成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


@csrf_exempt
def customer_edit(request, pk):
    """编辑客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        if request.method == 'POST':
            # 获取参数
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验必填项
            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            # 校验唯一性（排除自身）
            if Customer.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            # 校验区域
            area = get_object_or_404(Area, id=area_id)

            # 更新客户信息
            customer.name = name
            customer.area = area
            customer.phone = phone
            customer.remark = remark
            customer.save()

            return JsonResponse({'code': 1, 'msg': '编辑客户成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


@csrf_exempt
def customer_delete(request, pk):
    """删除客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        customer.delete()
        return JsonResponse({'code': 1, 'msg': '删除客户成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 辅助接口：获取区域列表 =====================
@csrf_exempt
def area_list_for_customer(request):
    """供客户管理页面获取区域下拉列表"""
    try:
        areas = Area.objects.all().order_by('name')
        result = [{'id': a.id, 'name': a.name} for a in areas]
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询区域失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


# ===================== 页面入口 =====================
def customer_page(request):
    """客户管理页面"""
    return render(request, 'customer_manage/customer.html')
