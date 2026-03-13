# summary/views.py
from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from bill.models import Product, Order, OrderItem, AreaGroup

# 汇总页面
def summary_page(request):
    return render(request, 'summary/summary.html')

# 核心接口：按区域组 + 时间段汇总
@csrf_exempt
def summary_by_group(request):
    group_id = request.GET.get('group_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if not group_id or not start_date or not end_date:
        return JsonResponse({'code': 0, 'msg': '请选择组和日期范围'})

    try:
        group = AreaGroup.objects.get(id=group_id)
    except AreaGroup.DoesNotExist:
        return JsonResponse({'code': 0, 'msg': '分组不存在'})

    area_ids = group.areas.values_list('id', flat=True)

    # 按商品汇总销量/金额
    items = OrderItem.objects.filter(
        order__area_id__in=area_ids,
        order__create_time__date__gte=start_date,
        order__create_time__date__lte=end_date
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

# summary/views.py 补充以下代码
from django.http import JsonResponse
from bill.models import AreaGroup

# 新增：加载所有区域组列表（供前端下拉框）
def group_list(request):
    """获取所有区域组列表（前端下拉框数据源）"""
    try:
        # 查询所有区域组并按名称排序
        groups = AreaGroup.objects.all().order_by('name')
        # 构造前端需要的格式：[{id: xxx, name: xxx}, ...]
        group_list = [{'id': group.id, 'name': group.name} for group in groups]
        return JsonResponse(group_list, safe=False)  # safe=False 允许返回列表
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'加载组列表失败：{str(e)}'}, status=400)


# ========== 新增：客户金额汇总相关函数 ==========
from datetime import datetime  # 需新增导入


def customer_summary_page(request):
    """客户金额汇总页面（独立页面）"""
    return render(request, 'summary/customer_summary.html')


@csrf_exempt
def summary_customer_by_group(request):
    """按区域组+时间段汇总客户消费金额（自动累加同一客户多单）"""
    group_id = request.GET.get('group_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # 1. 参数校验
    if not group_id or not start_date or not end_date:
        return JsonResponse({'code': 0, 'msg': '请选择组和日期范围'})

    # 2. 日期格式校验
    try:
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '日期格式错误（需为YYYY-MM-DD）'})

    # 3. 校验区域组
    try:
        group = AreaGroup.objects.get(id=group_id)
    except AreaGroup.DoesNotExist:
        return JsonResponse({'code': 0, 'msg': '分组不存在'})

    # 4. 获取区域组对应的区域ID列表
    area_ids = group.areas.values_list('id', flat=True)
    if not area_ids:
        return JsonResponse({'code': 0, 'msg': '该区域组未关联任何区域'})

    # 5. 按客户汇总金额（过滤无客户的订单）
    customer_summary = Order.objects.filter(
        area_id__in=area_ids,  # 区域组内的订单
        create_time__date__gte=start_date,
        create_time__date__lte=end_date,
        customer__isnull=False  # 仅统计关联客户的订单
    ).values(
        'customer__id',  # 客户ID
        'customer__name',  # 客户名称
        'customer__remark'  # 客户备注
    ).annotate(
        total_amount=Sum('total_amount')  # 累加同一客户所有订单金额
    ).order_by('-total_amount')  # 按消费金额降序

    # 6. 构造返回数据
    data = []
    for item in customer_summary:
        data.append({
            'customer_id': item['customer__id'],
            'customer_name': item['customer__name'],
            'total_amount': float(item['total_amount'] or 0),  # 转浮点方便前端处理
            'remark': item['customer__remark'] or ''  # 备注为空则显示空字符串
        })

    return JsonResponse({
        'code': 1,
        'data': data,
        'msg': '查询成功' if data else '该时间段内无客户消费数据'
    })