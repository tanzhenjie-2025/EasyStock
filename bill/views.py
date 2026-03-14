from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from difflib import SequenceMatcher
from django.views.decorators.csrf import csrf_exempt
from .models import Product, Order, OrderItem, ProductAlias, DailySalesSummary, CustomerPrice, Customer, Area
from django.db.models import Q, Sum
import json
from datetime import date, datetime, timedelta
from .utils import generate_daily_summary, auto_summary_yesterday
# 新增：Django原生登录验证 + 权限装饰器
from django.contrib.auth.decorators import login_required, user_passes_test
from accounts.views import is_boss, is_operator


# ========== 开单核心功能 ==========
# 添加登录和权限装饰器 + 传递 is_boss 变量
@login_required
@user_passes_test(is_operator, login_url='/accounts/no-permission/', redirect_field_name=None)
def index(request):
    """开单主页面（三联单填写页）"""
    customers = Customer.objects.all().order_by('name')
    areas = Area.objects.all().order_by('name')
    return render(request, 'bill/index.html', {
        'customers': customers,
        'areas': areas,
        'is_boss': is_boss(request.user)  # 传递是否为老板的变量
    })


# 添加登录和权限装饰器
@login_required
@user_passes_test(is_operator, login_url='/accounts/no-permission/', redirect_field_name=None)
def search_product(request):
    """
    商品搜索：匹配 名称 / 别名 / 全拼 / 首字母
    新增：接收customer_id，返回客户专属价（如有）
    """
    keyword = request.GET.get('keyword', '').strip()
    customer_id = request.GET.get('customer_id', '').strip()  # 新增：接收客户ID
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 1. 从【商品表】匹配
    product_matches = Product.objects.filter(
        Q(name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword)
    )

    # 2. 从【别名表】匹配（修复字段名：pinyin_full → alias_pinyin_full，pinyin_abbr → alias_pinyin_abbr）
    alias_matches = ProductAlias.objects.filter(
        Q(alias_name__icontains=keyword) |
        Q(alias_pinyin_full__icontains=keyword) |  # 修复：原错误pinyin_full
        Q(alias_pinyin_abbr__icontains=keyword)   # 修复：原错误pinyin_abbr
    ).values_list('product_id', flat=True)
    alias_products = Product.objects.filter(id__in=alias_matches)

    # 3. 合并去重，最多返回8条（输入法式候选）
    all_products = (product_matches | alias_products).distinct()[:8]

    # 4. 匹配客户专属价（如有）
    data = []
    customer_prices = {}
    if customer_id:
        # 批量查询该客户的所有专属价，提升性能
        cp_list = CustomerPrice.objects.filter(
            customer_id=customer_id,
            product_id__in=[p.id for p in all_products]
        )
        customer_prices = {cp.product_id: float(cp.custom_price) for cp in cp_list}

    for p in all_products:
        # 优先用客户专属价，无则用标准价
        final_price = customer_prices.get(p.id, float(p.price))
        data.append({
            'id': p.id,
            'name': p.name,
            'price': final_price,
            'standard_price': float(p.price),
            'unit': p.unit,
            'stock': p.stock
        })

    return JsonResponse({'code': 1, 'data': data})


# 添加登录和权限装饰器 + 关联开单人
@login_required
@user_passes_test(is_operator, login_url='/accounts/no-permission/', redirect_field_name=None)
def save_order(request):
    """保存订单（开单提交）- 修复字段名+完善异常处理 + 关联开单人"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_id = data.get('customer_id', '')  # 接收前端传递的客户ID

            if not items:
                return JsonResponse({'code': 0, 'msg': '无订单明细'})

            # 1. 创建订单（先不保存，待明细验证通过后再保存）
            order = Order()
            order.creator = request.user  # 关联当前登录的开单人
            # 关联客户（如有）
            if customer_id:
                try:
                    customer = Customer.objects.get(id=customer_id)
                    order.customer = customer
                    order.area = customer.area  # 同步客户所属区域到订单
                except Customer.DoesNotExist:
                    return JsonResponse({'code': 0, 'msg': '所选客户不存在'})

            total_amount = 0
            # 2. 先验证所有明细（避免部分创建后回滚）
            valid_items = []
            for item in items:
                # 修复：前端传的是qty，后端读取qty而非quantity
                product_id = item.get('id', '')
                qty = item.get('qty', 0)

                # 字段校验
                if not product_id or not isinstance(qty, int) or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': f'商品{item.get("name", "未知")}数量无效'})

                product = get_object_or_404(Product, id=product_id)
                # 库存校验
                if product.stock < qty:
                    return JsonResponse({'code': 0, 'msg': f'{product.name}库存不足（当前库存：{product.stock}）'})

                # 计算明细金额
                item_amount = product.price * qty
                valid_items.append({
                    'product': product,
                    'quantity': qty,
                    'amount': item_amount
                })
                total_amount += item_amount

            # 3. 验证通过后，保存订单主表
            order.total_amount = total_amount
            order.save()

            # 4. 创建订单明细
            for valid_item in valid_items:
                order_item = OrderItem(
                    order=order,
                    product=valid_item['product'],
                    quantity=valid_item['quantity'],
                    amount=valid_item['amount']
                )
                order_item.save()
                # 扣减库存
                valid_item['product'].stock -= valid_item['quantity']
                valid_item['product'].save()

            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})

        except KeyError as e:
            # 捕获字段缺失异常，回滚订单
            if 'order' in locals():
                order.delete()
            return JsonResponse({'code': 0, 'msg': f'开单失败：缺少字段 {str(e)}'})
        except Exception as e:
            # 通用异常，回滚订单
            if 'order' in locals():
                order.delete()
            return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


# 添加登录装饰器 + 传递 is_boss 变量
@login_required
def print_order(request, order_no):
    """订单打印页面（适配三联单）"""
    order = get_object_or_404(Order, order_no=order_no)
    items = order.items.all()
    return render(request, 'bill/print.html', {
        'order': order,
        'items': items,
        'is_boss': is_boss(request.user)  # 传递变量
    })


# 添加登录装饰器 + 传递 is_boss 变量
@login_required
def stock_list(request):
    """库存查询页面"""
    products = Product.objects.all()
    return render(request, 'bill/stock.html', {
        'products': products,
        'is_boss': is_boss(request.user)  # 传递变量
    })


# 添加登录装饰器 + 权限控制 + 传递 is_boss 变量
@login_required
def order_list(request):
    """订单列表页（支持日期、区域、商家名称叠加筛选）"""
    # 1. 获取所有筛选参数
    date_from = request.GET.get('date_from', '')  # 开始日期
    date_to = request.GET.get('date_to', '')  # 结束日期
    area_id = request.GET.get('area_id', '')  # 区域ID
    customer_name = request.GET.get('customer_name', '').strip()  # 商家名称

    # 2. 初始化查询集（按开单时间倒序）
    orders = Order.objects.select_related('area', 'customer', 'creator').order_by('-create_time')

    # 权限控制：老板看所有订单，开单人只看自己的
    if not is_boss(request.user):
        orders = orders.filter(creator=request.user)

    # 3. 叠加筛选逻辑（逐步过滤）
    # 日期筛选
    if date_from:
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            orders = orders.filter(create_time__date__gte=start_date)
        except:
            pass
    if date_to:
        try:
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            orders = orders.filter(create_time__date__lte=end_date)
        except:
            pass

    # 区域筛选
    if area_id and area_id.isdigit():
        orders = orders.filter(area_id=area_id)

    # 商家名称模糊筛选
    if customer_name:
        orders = orders.filter(customer__name__icontains=customer_name)

    # 4. 获取所有区域（用于下拉筛选）
    areas = Area.objects.all().order_by('name')

    # 5. 渲染模板
    context = {
        'orders': orders,
        'areas': areas,
        # 回显筛选条件
        'date_from': date_from,
        'date_to': date_to,
        'area_id': area_id,
        'customer_name': customer_name,
        'is_boss': is_boss(request.user)  # 传递变量
    }
    return render(request, 'bill/order_list.html', context)


# 添加登录装饰器 + 权限控制 + 传递 is_boss 变量
@login_required
def order_detail(request, order_no):
    """订单详情页"""
    # 获取订单及明细
    order = get_object_or_404(Order, order_no=order_no)

    # 权限控制：开单人只能看自己的订单
    if not is_boss(request.user) and order.creator != request.user:
        return redirect('/bill/orders/')

    items = OrderItem.objects.select_related('product').filter(order=order)

    context = {
        'order': order,
        'items': items,
        'is_boss': is_boss(request.user)  # 传递变量
    }
    return render(request, 'bill/order_detail.html', context)


# 添加登录和老板权限装饰器 + 传递 is_boss 变量
@login_required
@user_passes_test(is_boss)
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
        'total_quantity': total_quantity,
        'is_boss': is_boss(request.user)  # 传递变量
    })


# 添加老板权限装饰器（仅老板可手动汇总）
@login_required
@user_passes_test(is_boss)
def manual_summary(request):
    """手动生成/重置汇总接口（老板权限）"""
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


# 自动汇总接口（用于定时任务，保留原逻辑）
def auto_summary_task(request):
    """自动汇总昨天数据（定时任务调用）"""
    try:
        count = auto_summary_yesterday()
        return JsonResponse({'code': 1, 'msg': f'自动汇总完成：{count}个商品'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'自动汇总失败：{str(e)}'})


# 添加登录装饰器（仅登录用户可搜索客户）
@login_required
def search_customer(request):
    """
    客户搜索：匹配 区域名称 / 客户名称
    支持：1. 关键词匹配 2. 返回输入法式候选数据（格式：区域 | 客户名）
    """
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 基础查询：匹配区域名称 或 客户名称
    customer_matches = Customer.objects.select_related('area').filter(
        Q(name__icontains=keyword) |
        Q(area__name__icontains=keyword)
    ).distinct()[:8]

    # 构造返回数据（关键：去掉括号，格式为 区域 | 客户名）
    data = []
    for customer in customer_matches:
        area_name = customer.area.name if customer.area else '无区域'
        full_name = f"{area_name} | {customer.name}"  # 核心修改：去掉括号
        data.append({
            'id': customer.id,
            'name': customer.name,
            'area_id': customer.area.id if customer.area else '',
            'area_name': area_name,
            'full_name': full_name
        })

    return JsonResponse({'code': 1, 'data': data})


@login_required
@user_passes_test(is_boss, login_url='/accounts/no-permission/', redirect_field_name=None)
def cancel_order(request, order_no):
    """作废订单（仅老板/有权限操作员）"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, order_no=order_no)

            # 校验订单状态（已作废的不能重复作废）
            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '该订单已作废，无需重复操作'})

            # 获取作废原因
            reason = request.POST.get('reason', '').strip()
            if not reason:
                return JsonResponse({'code': 0, 'msg': '请填写作废原因'})

            # 更新订单作废信息
            order.status = 'cancelled'
            order.cancelled_by = request.user
            # 关键修复：去掉多余的 .datetime，直接使用 datetime.now()
            order.cancelled_time = datetime.now()
            order.cancelled_reason = reason
            order.save()

            # 恢复库存（作废订单后，库存加回来）
            for item in order.items.all():
                if item.product:
                    item.product.stock += item.quantity
                    item.product.save()

            return JsonResponse({'code': 1, 'msg': '订单作废成功', 'order_no': order_no})

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'作废失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


# ========== 新增：订单重开功能 ==========
@login_required
@user_passes_test(is_operator, login_url='/accounts/no-permission/', redirect_field_name=None)
def reopen_order(request, order_no):
    """重开订单（一键复用原单信息）"""
    if request.method == 'POST':
        try:
            # 获取原作废订单
            original_order = get_object_or_404(Order, order_no=order_no)

            # 校验原订单状态（必须是作废状态才能重开）
            if original_order.status != 'cancelled':
                return JsonResponse({'code': 0, 'msg': '仅作废订单可重开'})

            # 1. 创建新订单
            new_order = Order()
            new_order.creator = request.user  # 新订单开单人是当前用户
            new_order.customer = original_order.customer
            new_order.area = original_order.area
            new_order.status = 'reopened'  # 新订单状态为"重开"
            new_order.original_order = original_order  # 关联原订单
            new_order.save()  # 先保存生成新订单号

            # 2. 复制原订单明细
            total_amount = 0
            for original_item in original_order.items.all():
                if original_item.product:
                    # 校验库存
                    if original_item.product.stock < original_item.quantity:
                        # 回滚：删除已创建的新订单
                        new_order.delete()
                        return JsonResponse({
                            'code': 0,
                            'msg': f'{original_item.product.name}库存不足（当前库存：{original_item.product.stock}）'
                        })

                    # 创建新订单明细
                    new_item = OrderItem(
                        order=new_order,
                        product=original_item.product,
                        quantity=original_item.quantity,
                        amount=original_item.amount
                    )
                    new_item.save()

                    # 扣减库存
                    original_item.product.stock -= original_item.quantity
                    original_item.product.save()

                    total_amount += float(new_item.amount or 0)

            # 3. 更新新订单总金额
            new_order.total_amount = total_amount
            new_order.save()

            return JsonResponse({
                'code': 1,
                'msg': '订单重开成功',
                'new_order_no': new_order.order_no,
                'original_order_no': original_order.order_no
            })

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'重开失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})