# bill\views
# ========== 先导入所有必要模块（统一开头，避免重复） ==========
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from difflib import SequenceMatcher
from django.views.decorators.csrf import csrf_exempt
from .models import Product, Order, OrderItem, ProductAlias, DailySalesSummary, CustomerPrice, Customer, Area
from django.db.models import Q, Sum
import json
from datetime import date, datetime, timedelta
from .utils import generate_daily_summary, auto_summary_yesterday
from django.contrib.auth.decorators import login_required
from functools import wraps
import decimal  # 新增：导入decimal模块处理金额

# ========== 导入用户模块的RBAC核心组件 ==========
from accounts.models import ROLE_SUPER_ADMIN
from accounts.views import (
    permission_required,  # RBAC权限装饰器
    create_operation_log,  # 统一日志记录
    get_client_ip  # 获取客户端IP
)

# ========== 开单模块权限常量（和用户模块保持一致） ==========
PERM_ORDER_CREATE = 'order_create'
PERM_ORDER_VIEW = 'order_view'
PERM_ORDER_PRINT = 'order_print'
PERM_ORDER_CANCEL = 'order_cancel'
PERM_ORDER_REOPEN = 'order_reopen'
PERM_ORDER_SETTLE = 'order_settle'
PERM_ORDER_UNSETTLE = 'order_unsettle'
PERM_ORDER_SUMMARY = 'order_summary'
PERM_PRODUCT_SEARCH = 'product_search'


# ========== 重构：自定义AJAX装饰器（适配RBAC） ==========
def ajax_login_required(view_func):
    """AJAX登录验证装饰器：未登录返回JSON，而非重定向HTML"""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        # 识别AJAX请求，返回JSON错误
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get(
                'Content-Type', ''):
            return JsonResponse({'code': 0, 'msg': '请先登录系统'}, status=401)
        # 非AJAX请求仍重定向登录页
        return login_required(view_func)(request, *args, **kwargs)

    return wrapper


def ajax_permission_required(permission_code):
    """重构：AJAX RBAC权限验证装饰器"""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # 未登录→返回JSON
            if not request.user.is_authenticated:
                return JsonResponse({'code': 0, 'msg': '请先登录系统'}, status=401)

            # 超级管理员→直接放行
            if request.user.role and request.user.role.code == ROLE_SUPER_ADMIN:
                return view_func(request, *args, **kwargs)

            # 检查RBAC权限
            if not request.user.has_permission(permission_code):
                # AJAX请求返回JSON
                if request.headers.get(
                        'X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get(
                        'Content-Type', ''):
                    return JsonResponse({'code': 0, 'msg': '无操作权限，请联系管理员'}, status=403)
                # 非AJAX请求重定向无权限页
                return redirect('/accounts/no-permission/')

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


# ========== 重构：视图函数（替换所有权限装饰器） ==========
@login_required
@permission_required(PERM_ORDER_CREATE)  # 替换原user_passes_test(is_operator)
def index(request):
    """开单主页面（三联单填写页）"""
    customers = Customer.objects.all().order_by('name')
    areas = Area.objects.all().order_by('name')
    # 重构：RBAC判断是否为超级管理员
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    return render(request, 'bill/index.html', {
        'customers': customers,
        'areas': areas,
        'is_super_admin': is_super_admin  # 替换原is_boss
    })


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def search_product(request):
    """商品搜索：匹配 名称 / 别名 / 全拼 / 首字母"""
    keyword = request.GET.get('keyword', '').strip()
    customer_id = request.GET.get('customer_id', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    product_matches = Product.objects.filter(
        Q(name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword)
    )

    alias_matches = ProductAlias.objects.filter(
        Q(alias_name__icontains=keyword) |
        Q(alias_pinyin_full__icontains=keyword) |
        Q(alias_pinyin_abbr__icontains=keyword)
    ).values_list('product_id', flat=True)
    alias_products = Product.objects.filter(id__in=alias_matches)

    all_products = (product_matches | alias_products).distinct()[:8]

    data = []
    customer_prices = {}
    if customer_id:
        cp_list = CustomerPrice.objects.filter(
            customer_id=customer_id,
            product_id__in=[p.id for p in all_products]
        )
        customer_prices = {cp.product_id: float(cp.custom_price) for cp in cp_list}

    for p in all_products:
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


@login_required
@permission_required(PERM_ORDER_CREATE)
def save_order(request):
    """保存订单（开单提交）"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_id = data.get('customer_id', '')
            original_order_no = data.get('original_order_no', '')

            if not items:
                return JsonResponse({'code': 0, 'msg': '无订单明细'})

            order = Order()
            order.creator = request.user
            if customer_id:
                try:
                    customer = Customer.objects.get(id=customer_id)
                    order.customer = customer
                    order.area = customer.area
                except Customer.DoesNotExist:
                    return JsonResponse({'code': 0, 'msg': '所选客户不存在'})

            if original_order_no:
                try:
                    original_order = Order.objects.get(order_no=original_order_no)
                    if original_order.status != 'cancelled':
                        return JsonResponse({'code': 0, 'msg': '仅作废订单可重开'})
                    order.original_order = original_order
                    order.status = 'reopened'
                except Order.DoesNotExist:
                    return JsonResponse({'code': 0, 'msg': '原作废订单不存在'})

            total_amount = 0
            valid_items = []
            for item in items:
                product_id = item.get('id', '')
                qty = item.get('qty', 0)

                if not product_id or not isinstance(qty, int) or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': f'商品{item.get("name", "未知")}数量无效'})

                product = get_object_or_404(Product, id=product_id)
                if product.stock < qty:
                    return JsonResponse({'code': 0, 'msg': f'{product.name}库存不足（当前库存：{product.stock}）'})

                item_amount = product.price * qty
                valid_items.append({
                    'product': product,
                    'quantity': qty,
                    'amount': item_amount
                })
                total_amount += item_amount

            order.total_amount = total_amount
            order.save()

            for valid_item in valid_items:
                order_item = OrderItem(
                    order=order,
                    product=valid_item['product'],
                    quantity=valid_item['quantity'],
                    amount=valid_item['amount']
                )
                order_item.save()
                valid_item['product'].stock -= valid_item['quantity']
                valid_item['product'].save()

            customer_name = order.customer.name if order.customer else '无'
            # 重构：使用用户模块的统一日志记录
            create_operation_log(
                request=request,
                op_type='create_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"创建订单{order.order_no}，客户：{customer_name}，总金额：{order.total_amount}元，商品数量：{len(valid_items)}个"
            )

            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})

        except KeyError as e:
            if 'order' in locals():
                order.delete()
            return JsonResponse({'code': 0, 'msg': f'开单失败：缺少字段 {str(e)}'})
        except Exception as e:
            if 'order' in locals():
                order.delete()
            return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@login_required
@permission_required(PERM_ORDER_PRINT)
def print_order(request, order_no):
    """订单打印页面"""
    order = get_object_or_404(Order, order_no=order_no)
    items = order.items.all()
    # 重构：RBAC判断超级管理员
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    return render(request, 'bill/print.html', {
        'order': order,
        'items': items,
        'is_super_admin': is_super_admin
    })


@login_required
@permission_required(PERM_ORDER_VIEW)
def stock_list(request):
    """库存查询页面"""
    products = Product.objects.all()
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    return render(request, 'bill/stock.html', {
        'products': products,
        'is_super_admin': is_super_admin
    })


@login_required
@permission_required(PERM_ORDER_VIEW)
def order_list(request):
    """订单列表页（新增订单号搜索 + 修复金额筛选 + 新增数据统计）"""
    # 新增：获取订单号搜索参数
    order_no = request.GET.get('order_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    area_id = request.GET.get('area_id', '')
    customer_name = request.GET.get('customer_name', '').strip()
    settled_status = request.GET.get('settled_status', '')
    # 新增：获取金额筛选参数
    amount_operator = request.GET.get('amount_operator', '')
    amount_value = request.GET.get('amount_value', '').strip()

    orders = Order.objects.select_related('area', 'customer', 'creator').order_by('-create_time')

    # 重构：RBAC权限控制（非超级管理员只能看自己的订单）
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    if not is_super_admin:
        orders = orders.filter(creator=request.user)

    # 新增：订单号模糊筛选（核心逻辑）
    if order_no:
        orders = orders.filter(order_no__contains=order_no)

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

    # 客户名称筛选
    if customer_name:
        orders = orders.filter(customer__name__icontains=customer_name)

    # 结清状态筛选
    if settled_status == 'settled':
        orders = orders.filter(is_settled=True)
    elif settled_status == 'unsettled':
        orders = orders.filter(is_settled=False)

    # 金额筛选核心逻辑
    if amount_operator in ['gt', 'lt'] and amount_value:
        try:
            # 转换为Decimal类型（匹配Order.total_amount的字段类型）
            amount = decimal.Decimal(amount_value)
            if amount_operator == 'gt':
                # 大于指定金额
                orders = orders.filter(total_amount__gt=amount)
            elif amount_operator == 'lt':
                # 小于指定金额
                orders = orders.filter(total_amount__lt=amount)
        except decimal.InvalidOperation:
            # 金额格式错误时，跳过金额筛选（避免报错）
            pass

    # ========== 新增：数据统计计算 ==========
    # 1. 总订单数
    total_orders = orders.count()
    # 2. 总销售金额
    total_sales = orders.aggregate(total=Sum('total_amount'))['total'] or decimal.Decimal('0.00')
    # 3. 总结清订单数
    settled_orders = orders.filter(is_settled=True).count()
    # 4. 总欠款金额（未结清订单的金额总和）
    total_debt = orders.filter(is_settled=False).aggregate(total=Sum('total_amount'))['total'] or decimal.Decimal(
        '0.00')

    areas = Area.objects.all().order_by('name')

    context = {
        'orders': orders,
        'areas': areas,
        'date_from': date_from,
        'date_to': date_to,
        'area_id': area_id,
        'customer_name': customer_name,
        'settled_status': settled_status,
        'amount_operator': amount_operator,
        'amount_value': amount_value,
        'is_super_admin': is_super_admin,
        # 新增：传递订单号参数到模板（实现搜索框回显）
        'order_no': order_no,
        # ========== 新增：统计数据传入模板 ==========
        'total_orders': total_orders,
        'total_sales': total_sales,
        'settled_orders': settled_orders,
        'total_debt': total_debt
    }
    return render(request, 'bill/order_list.html', context)


@login_required
@permission_required(PERM_ORDER_VIEW)
def order_detail(request, order_no):
    """订单详情页"""
    order = get_object_or_404(Order, order_no=order_no)
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    # 重构：RBAC权限控制（非超级管理员只能看自己的订单）
    if not is_super_admin and order.creator != request.user:
        return redirect('/bill/orders/')

    items = OrderItem.objects.select_related('product').filter(order=order)

    context = {
        'order': order,
        'items': items,
        'is_super_admin': is_super_admin
    }
    return render(request, 'bill/order_detail.html', context)


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def summary_list(request):
    """销售汇总列表页"""
    target_date_str = request.GET.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
        except:
            target_date = date.today() - timedelta(days=1)
    else:
        target_date = date.today() - timedelta(days=1)

    summary_data = DailySalesSummary.objects.filter(
        summary_date=target_date
    ).select_related('product').order_by('-sale_quantity')

    total_product = summary_data.count()
    total_quantity = summary_data.aggregate(total=Sum('sale_quantity'))['total'] or 0
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    return render(request, 'bill/summary_list.html', {
        'summary_data': summary_data,
        'target_date': target_date,
        'total_product': total_product,
        'total_quantity': total_quantity,
        'is_super_admin': is_super_admin
    })


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def manual_summary(request):
    """手动生成/重置汇总接口"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            target_date_str = data.get('date')
            if not target_date_str:
                return JsonResponse({'code': 0, 'msg': '请选择汇总日期'})

            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            count = generate_daily_summary(target_date=target_date, is_manual=True)

            # 重构：统一日志记录
            create_operation_log(
                request=request,
                op_type='manual_summary',
                obj_type='daily_sales_summary',
                obj_name=f"销售汇总-{target_date}",
                detail=f"手动生成{target_date}销售汇总，共统计{count}个商品"
            )

            return JsonResponse({'code': 1, 'msg': f'汇总完成！共统计{count}个商品'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'汇总失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


def auto_summary_task(request):
    """自动汇总昨天数据"""
    try:
        count = auto_summary_yesterday()
        return JsonResponse({'code': 1, 'msg': f'自动汇总完成：{count}个商品'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'自动汇总失败：{str(e)}'})


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def search_customer(request):
    """客户搜索"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    customer_matches = Customer.objects.select_related('area').filter(
        Q(name__icontains=keyword) |
        Q(area__name__icontains=keyword)
    ).distinct()[:8]

    data = []
    for customer in customer_matches:
        area_name = customer.area.name if customer.area else '无区域'
        full_name = f"{area_name} | {customer.name}"
        data.append({
            'id': customer.id,
            'name': customer.name,
            'area_id': customer.area.id if customer.area else '',
            'area_name': area_name,
            'full_name': full_name
        })

    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CANCEL)
def cancel_order(request, order_no):
    """作废订单"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, order_no=order_no)

            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '该订单已作废，无需重复操作'})

            data = json.loads(request.body)
            reason = data.get('reason', '').strip()

            if not reason:
                return JsonResponse({'code': 0, 'msg': '请填写作废原因'})

            order.status = 'cancelled'
            order.cancelled_by = request.user
            order.cancelled_time = datetime.now()
            order.cancelled_reason = reason
            order.save()

            item_count = 0
            for item in order.items.all():
                if item.product:
                    item.product.stock += item.quantity
                    item.product.save()
                    item_count += 1

            # 重构：统一日志记录
            create_operation_log(
                request=request,
                op_type='cancel_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"作废订单{order.order_no}，原因：{reason}，恢复{item_count}个商品库存"
            )

            return JsonResponse({'code': 1, 'msg': '订单作废成功', 'order_no': order_no})

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'作废失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


@login_required
@permission_required(PERM_ORDER_REOPEN)
def reopen_order(request, order_no):
    """重开订单"""
    if request.method == 'POST':
        try:
            original_order = get_object_or_404(Order, order_no=order_no)

            if original_order.status != 'cancelled':
                return JsonResponse({'code': 0, 'msg': '仅作废订单可重开'})

            new_order = Order()
            new_order.creator = request.user
            new_order.customer = original_order.customer
            new_order.area = original_order.area
            new_order.status = 'reopened'
            new_order.original_order = original_order
            new_order.save()

            total_amount = 0
            item_count = 0
            for original_item in original_order.items.all():
                if original_item.product:
                    if original_item.product.stock < original_item.quantity:
                        new_order.delete()
                        return JsonResponse({
                            'code': 0,
                            'msg': f'{original_item.product.name}库存不足（当前库存：{original_item.product.stock}）'
                        })

                    new_item = OrderItem(
                        order=new_order,
                        product=original_item.product,
                        quantity=original_item.quantity,
                        amount=original_item.amount
                    )
                    new_item.save()

                    original_item.product.stock -= original_item.quantity
                    original_item.product.save()

                    total_amount += float(new_item.amount or 0)
                    item_count += 1

            new_order.total_amount = total_amount
            new_order.save()

            customer_name = new_order.customer.name if new_order.customer else '无'
            # 重构：统一日志记录
            create_operation_log(
                request=request,
                op_type='reopen_order',
                obj_type='order',
                obj_id=str(new_order.id),
                obj_name=f"订单-{new_order.order_no}",
                detail=f"重开订单{new_order.order_no}，原作废订单：{original_order.order_no}，客户：{customer_name}，总金额：{new_order.total_amount}元，商品数量：{item_count}个"
            )

            return JsonResponse({
                'code': 1,
                'msg': '订单重开成功',
                'new_order_no': new_order.order_no,
                'original_order_no': original_order.order_no
            })

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'重开失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


@login_required
@permission_required(PERM_ORDER_REOPEN)
def reopen_order_edit(request, order_no):
    """重开订单编辑页面"""
    original_order = get_object_or_404(Order, order_no=order_no)
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    if original_order.status != 'cancelled':
        return redirect('bill:order_detail', order_no=order_no)

    items = OrderItem.objects.select_related('product').filter(order=original_order)

    order_data = {
        'order_no': original_order.order_no,
        'customer_id': original_order.customer.id if original_order.customer else '',
        'customer_name': f"{original_order.area.name} | {original_order.customer.name}" if (
                original_order.customer and original_order.area) else '',
        'items': [
            {
                'id': item.product.id if item.product else '',
                'name': item.product.name if item.product else '',
                'qty': item.quantity,
                'unit': item.product.unit if item.product else '',
                'price': float(item.product.price) if item.product else 0,
                'amt': float(item.amount) if item.amount else 0
            }
            for item in items
        ]
    }

    customers = Customer.objects.all().order_by('name')
    areas = Area.objects.all().order_by('name')

    return render(request, 'bill/index.html', {
        'customers': customers,
        'areas': areas,
        'is_super_admin': is_super_admin,
        'reopen_order_data': order_data
    })


# ========== 重构：结清相关视图（适配RBAC） ==========
@ajax_login_required
@ajax_permission_required(PERM_ORDER_SETTLE)
def settle_order(request, order_no):
    """标记订单结清"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, order_no=order_no)

            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '作废订单无法标记结清'}, status=400)
            if order.is_settled:
                return JsonResponse({'code': 0, 'msg': '该订单已结清，无需重复操作'}, status=400)

            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)
            remark = data.get('remark', '').strip()
            if not remark:
                return JsonResponse({'code': 0, 'msg': '请填写结清备注'}, status=400)

            order.is_settled = True
            order.settled_by = request.user
            order.settled_time = datetime.now()
            order.settled_remark = remark
            order.save()

            # 重构：统一日志记录
            create_operation_log(
                request=request,
                op_type='settle_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"标记订单{order.order_no}结清，备注：{remark}"
            )

            return JsonResponse({'code': 1, 'msg': '订单标记结清成功', 'order_no': order_no})

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'标记结清失败：{str(e)}'}, status=500)
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_UNSETTLE)
def unsettle_order(request, order_no):
    """撤销订单结清"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, order_no=order_no)

            if not order.is_settled:
                return JsonResponse({'code': 0, 'msg': '该订单未结清，无需撤销'}, status=400)

            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)
            remark = data.get('remark', '').strip()
            if not remark:
                return JsonResponse({'code': 0, 'msg': '请填写撤销结清备注'}, status=400)

            order.is_settled = False
            order.unsettled_by = request.user
            order.unsettled_time = datetime.now()
            order.unsettled_remark = remark
            order.save()

            # 重构：统一日志记录
            create_operation_log(
                request=request,
                op_type='unsettle_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"撤销订单{order.order_no}结清状态，备注：{remark}"
            )

            return JsonResponse({'code': 1, 'msg': '撤销订单结清成功', 'order_no': order_no})

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'撤销结清失败：{str(e)}'}, status=500)
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_SETTLE)
def batch_settle_order(request):
    """批量标记订单结清"""
    if request.method == 'POST':
        try:
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)

            order_list = data.get('orders', [])
            if not order_list:
                return JsonResponse({'code': 0, 'msg': '请选择要结清的订单'}, status=400)

            success_count = 0
            fail_list = []

            for item in order_list:
                order_no = item.get('order_no')
                remark = item.get('remark', '').strip()

                if not order_no or not remark:
                    fail_list.append(f'{order_no or "未知订单"}：备注不能为空')
                    continue

                try:
                    order = get_object_or_404(Order, order_no=order_no)

                    if order.status == 'cancelled':
                        fail_list.append(f'{order_no}：作废订单无法结清')
                        continue
                    if order.is_settled:
                        fail_list.append(f'{order_no}：已结清，无需重复操作')
                        continue

                    order.is_settled = True
                    order.settled_by = request.user
                    order.settled_time = datetime.now()
                    order.settled_remark = remark
                    order.save()

                    # 重构：统一日志记录
                    create_operation_log(
                        request=request,
                        op_type='batch_settle_order',
                        obj_type='order',
                        obj_id=str(order.id),
                        obj_name=f"订单-{order.order_no}",
                        detail=f"批量结清订单{order.order_no}，备注：{remark}"
                    )

                    success_count += 1
                except Exception as e:
                    fail_list.append(f'{order_no}：{str(e)}')

            msg = f'批量处理完成！成功{success_count}个，失败{len(fail_list)}个'
            if fail_list:
                msg += f'；失败原因：{"; ".join(fail_list)}'

            return JsonResponse({
                'code': 1 if success_count > 0 else 0,
                'msg': msg,
                'success_count': success_count,
                'fail_list': fail_list
            })

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'批量结清失败：{str(e)}'}, status=500)
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)