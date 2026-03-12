from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from difflib import SequenceMatcher
from .models import Product, Order, OrderItem, ProductAlias, DailySalesSummary
from django.db.models import Q, Sum
import json
from datetime import date, datetime, timedelta
from .utils import generate_daily_summary, auto_summary_yesterday

# ========== 开单核心功能 ==========
def index(request):
    """开单主页面（三联单填写页）"""
    return render(request, 'bill/index.html')

# 保留完善的商品搜索（去重后的版本）
def search_product(request):
    """
    商品搜索：匹配 名称 / 别名 / 全拼 / 首字母
    返回：去重后的商品列表 [id, name, price, unit, stock]
    """
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 1. 从【商品表】匹配
    product_matches = Product.objects.filter(
        Q(name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword)
    )

    # 2. 从【别名表】匹配，并拿到对应的商品
    alias_matches = ProductAlias.objects.filter(
        Q(alias_name__icontains=keyword) |
        Q(alias_pinyin_full__icontains=keyword) |
        Q(alias_pinyin_abbr__icontains=keyword)
    ).values_list('product_id', flat=True)
    alias_products = Product.objects.filter(id__in=alias_matches)

    # 3. 合并去重，最多返回8条（输入法式候选）
    all_products = (product_matches | alias_products).distinct()[:8]

    data = [{
        'id': p.id,
        'name': p.name,
        'price': float(p.price),
        'unit': p.unit,
        'stock': p.stock
    } for p in all_products]

    return JsonResponse({'code': 1, 'data': data})

def save_order(request):
    """保存订单（开单提交）"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            items = data.get('items', [])
            if not items:
                return JsonResponse({'code': 0, 'msg': '无订单明细'})

            # 创建订单
            order = Order()
            order.save()
            total_amount = 0
            # 创建订单明细
            for item in items:
                product = get_object_or_404(Product, id=item['id'])
                quantity = int(item['quantity'])
                # 检查库存
                if product.stock < quantity:
                    order.delete()  # 回滚订单
                    return JsonResponse({'code': 0, 'msg': f'{product.name}库存不足'})
                order_item = OrderItem(
                    order=order,
                    product=product,
                    quantity=quantity,
                    amount=product.price * quantity
                )
                order_item.save()
                total_amount += order_item.amount

            # 更新订单总金额
            order.total_amount = total_amount
            order.save()
            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})

def print_order(request, order_no):
    """订单打印页面（适配三联单）"""
    order = get_object_or_404(Order, order_no=order_no)
    items = order.items.all()
    return render(request, 'bill/print.html', {'order': order, 'items': items})

def stock_list(request):
    """库存查询页面"""
    products = Product.objects.all()
    return render(request, 'bill/stock.html', {'products': products})

def order_list(request):
    """订单记录页面"""
    orders = Order.objects.all().order_by('-create_time')
    return render(request, 'bill/order_list.html', {'orders': orders})

# ========== 基础销售汇总（按日期） ==========
def summary_list(request):
    """销售汇总列表页（核心展示页）"""
    # 默认查询昨天的汇总，支持日期筛选
    target_date_str = request.GET.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except:
            target_date = date.today() - timedelta(days=1)
    else:
        target_date = date.today() - timedelta(days=1)

    # 查询指定日期的汇总数据
    summary_data = DailySalesSummary.objects.filter(
        summary_date=target_date
    ).select_related('product').order_by('-sale_quantity')  # 按销量降序

    # 统计总计
    total_product = summary_data.count()
    total_quantity = summary_data.aggregate(total=Sum('sale_quantity'))['total'] or 0

    return render(request, 'bill/summary_list.html', {
        'summary_data': summary_data,
        'target_date': target_date,
        'total_product': total_product,
        'total_quantity': total_quantity
    })

def manual_summary(request):
    """手动生成/重置汇总接口（无登录限制，简化操作）"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            target_date_str = data.get('date')
            if not target_date_str:
                return JsonResponse({'code': 0, 'msg': '请选择汇总日期'})

            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            # 生成/重置汇总
            count = generate_daily_summary(target_date=target_date, is_manual=True)
            return JsonResponse({'code': 1, 'msg': f'汇总完成！共统计{count}个商品'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'汇总失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

# 自动汇总接口（用于定时任务）
def auto_summary_task(request):
    """自动汇总昨天数据（定时任务调用）"""
    try:
        count = auto_summary_yesterday()
        return JsonResponse({'code': 1, 'msg': f'自动汇总完成：{count}个商品'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'自动汇总失败：{str(e)}'})