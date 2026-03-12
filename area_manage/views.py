# area_manage/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# 复用bill里的模型（表仍在bill，无需重复建）
from bill.models import Area, AreaGroup

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
                return JsonResponse({'code':0,'msg':'区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exists():
                return JsonResponse({'code':0,'msg':'区域已存在'}, content_type='application/json')
            Area.objects.create(name=name, remark=remark)
            return JsonResponse({'code':1,'msg':'添加成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code':0,'msg':f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code':0,'msg':'仅支持POST请求'}, content_type='application/json')

@csrf_exempt
def area_edit(request, pk):
    """编辑区域"""
    try:
        area = get_object_or_404(Area, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name','').strip()
            remark = request.POST.get('remark','').strip()
            if not name:
                return JsonResponse({'code':0,'msg':'区域名不能为空'}, content_type='application/json')
            if Area.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code':0,'msg':'区域名重复'}, content_type='application/json')
            area.name = name
            area.remark = remark
            area.save()
            return JsonResponse({'code':1,'msg':'修改成功'}, content_type='application/json')
        return JsonResponse({'code':0,'msg':'仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code':0,'msg':f'编辑失败：{str(e)}'}, content_type='application/json')

@csrf_exempt
def area_delete(request, pk):
    """删除区域"""
    try:
        area = get_object_or_404(Area, pk=pk)
        area.delete()
        return JsonResponse({'code':1,'msg':'删除成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code':0,'msg':f'删除失败：{str(e)}'}, content_type='application/json')

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
            name = request.POST.get('name','').strip()
            remark = request.POST.get('remark','').strip()
            area_ids = request.POST.getlist('area_ids[]')
            if not name:
                return JsonResponse({'code':0,'msg':'组名不能为空'}, content_type='application/json')
            if AreaGroup.objects.filter(name=name).exists():
                return JsonResponse({'code':0,'msg':'组名已存在'}, content_type='application/json')
            # 验证区域ID是否有效
            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            g = AreaGroup.objects.create(name=name, remark=remark)
            g.areas.set(valid_area_ids)
            return JsonResponse({'code':1,'msg':'创建成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code':0,'msg':f'创建失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code':0,'msg':'仅支持POST请求'}, content_type='application/json')

@csrf_exempt
def group_edit(request, pk):
    """编辑区域组"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name','').strip()
            remark = request.POST.get('remark','').strip()
            area_ids = request.POST.getlist('area_ids[]')
            if not name:
                return JsonResponse({'code':0,'msg':'组名不能为空'}, content_type='application/json')
            if AreaGroup.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code':0,'msg':'组名重复'}, content_type='application/json')
            # 验证区域ID是否有效
            valid_area_ids = Area.objects.filter(id__in=area_ids).values_list('id', flat=True)
            g.name = name
            g.remark = remark
            g.save()
            g.areas.set(valid_area_ids)
            return JsonResponse({'code':1,'msg':'修改成功'}, content_type='application/json')
        return JsonResponse({'code':0,'msg':'仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code':0,'msg':f'修改失败：{str(e)}'}, content_type='application/json')

@csrf_exempt
def group_delete(request, pk):
    """删除区域组"""
    try:
        g = get_object_or_404(AreaGroup, pk=pk)
        g.delete()
        return JsonResponse({'code':1,'msg':'删除成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code':0,'msg':f'删除失败：{str(e)}'}, content_type='application/json')

# ===================== 页面入口 =====================
def area_page(request):
    """区域管理页面"""
    return render(request, 'area_manage/area.html')

def group_page(request):
    """区域组管理页面"""
    return render(request, 'area_manage/group.html')