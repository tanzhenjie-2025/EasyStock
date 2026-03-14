from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Sum  # 无需再导入Q
from django.views.decorators.csrf import csrf_exempt
from bill.models import Product, Order, OrderItem, AreaGroup
from datetime import datetime

# 汇总页面
def summary_page(request):
    return render(request, 'summary/summary.html')

# 核心接口：按区域组 + 精准时间段汇总（彻底修复语法错误）
@csrf_exempt
def summary_by_group(request):
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    try:
        group = AreaGroup.objects.get(id=group_id)
    except AreaGroup.DoesNotExist:
        return JsonResponse({'code': 0, 'msg': '分组不存在'})

    area_ids = group.areas.values_list('id', flat=True)

    # 时间格式校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误（需为YYYY-MM-DDTHH:MM）'})

    # 修复核心：先用filter过滤基础条件，再用exclude排除作废订单（全关键字参数，无位置参数问题）
    items = OrderItem.objects.filter(
        order__area_id__in=area_ids,
        order__create_time__gte=start,
        order__create_time__lte=end
    ).exclude(  # 新增：用exclude替代~Q()，彻底避免位置参数问题
        order__status='cancelled'
    ).values(
        'product__id',
        'product__name',
        'product__unit',
        'product__price'
    ).annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('amount')
    ).order_by('-total_qty')

    data = []
    for item in items:
        data.append({
            'pid': item['product__id'],
            'name': item['product__name'],
            'unit': item['product__unit'],
            'price': float(item['product__price']),
            'total_qty': item['total_qty'] or 0,
            'total_amt': float(item['total_amt'] or 0)
        })

    return JsonResponse({'code': 1, 'data': data})

# 加载所有区域组列表（无需修改）
def group_list(request):
    try:
        groups = AreaGroup.objects.all().order_by('name')
        group_list = [{'id': group.id, 'name': group.name} for group in groups]
        return JsonResponse(group_list, safe=False)
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'加载组列表失败：{str(e)}'}, status=400)

# 客户金额汇总页面
def customer_summary_page(request):
    return render(request, 'summary/customer_summary.html')

# 按区域组+精准时间段汇总客户消费金额（彻底修复语法错误）
@csrf_exempt
def summary_customer_by_group(request):
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    # 1. 参数校验
    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 2. 时间格式校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误（需为YYYY-MM-DDTHH:MM）'})

    # 3. 校验区域组
    try:
        group = AreaGroup.objects.get(id=group_id)
    except AreaGroup.DoesNotExist:
        return JsonResponse({'code': 0, 'msg': '分组不存在'})

    # 4. 获取区域组对应的区域ID列表
    area_ids = group.areas.values_list('id', flat=True)
    if not area_ids:
        return JsonResponse({'code': 0, 'msg': '该区域组未关联任何区域'})

    # 修复核心：filter + exclude 组合，全关键字参数
    customer_summary = Order.objects.filter(
        area_id__in=area_ids,
        create_time__gte=start,
        create_time__lte=end,
        customer__isnull=False
    ).exclude(  # 排除作废订单
        status='cancelled'
    ).values(
        'customer__id',
        'customer__name',
        'customer__remark'
    ).annotate(
        total_amount=Sum('total_amount')
    ).order_by('-total_amount')

    # 6. 构造返回数据
    data = []
    for item in customer_summary:
        data.append({
            'customer_id': item['customer__id'],
            'customer_name': item['customer__name'],
            'total_amount': float(item['total_amount'] or 0),
            'remark': item['customer__remark'] or ''
        })

    return JsonResponse({
        'code': 1,
        'data': data,
        'msg': '查询成功' if data else '该时间段内无客户消费数据'
    })