# ========== 先导入所有必要模块（统一开头，避免重复） ==========
from django.db import transaction
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse

from django.utils import timezone
from .models import Order, OrderItem
from product.models import Product, ProductAlias
from customer_manage.models import Customer, CustomerPrice
from area_manage.models import Area

from django.db.models import Q, Sum, Count, Case, When, DecimalField
import json
from datetime import datetime, timedelta
from django.contrib.auth.decorators import login_required
from functools import wraps
import decimal

from django.core.cache import cache

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

# ========== 导入用户模块的RBAC核心组件 ==========
from accounts.models import ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR, PERM_ORDER_CANCEL_OWN
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
PERM_ORDER_PRICE_CHECK = 'order_price_check'

# ========== 订单模块缓存时长常量（统一管理） ==========
CACHE_STOCK_LIST = 60  # 库存列表：60秒
CACHE_ORDER_LIST = 60  # 订单列表：60秒
CACHE_ORDER_DETAIL = 120  # 订单详情：2分钟
CACHE_PRINT_ORDER = 300  # 订单打印：5分钟
CACHE_CUSTOMER_RECENT_PRODUCT = 60  # 客户最近商品：60秒
CACHE_PRODUCT_SEARCH = 30  # 商品搜索：30秒
CACHE_CUSTOMER_SEARCH = 10  # 客户搜索：10秒

# ========== 订单模块缓存 Key 定义 ==========
CACHE_PREFIX_STOCK_LIST = "stock_list_"
CACHE_PREFIX_ORDER_LIST = "order_list_"
CACHE_PREFIX_ORDER_DETAIL = "order_detail_"
CACHE_PREFIX_PRINT_ORDER = "print_order_"
CACHE_PREFIX_PRODUCT_SEARCH = "product_search_"
CACHE_PREFIX_CUSTOMER_SEARCH = "customer_search_"
CACHE_PREFIX_CUSTOMER_RECENT_PRODUCT = "customer_recent_products_"

# ==========  订单有效状态常量（索引前缀核心字段） ==========
ORDER_STATUS_VALID = ['pending', 'printed', 'reopened']

import logging

logger = logging.getLogger(__name__)


# ========== 新增：统一缓存清理函数 ==========
def clear_order_cache(order_no: str = None):
    """
    清理订单相关缓存（含列表、详情、打印页）
    """
    # 1. 清理所有订单列表缓存
    cache.delete_pattern(f"{CACHE_PREFIX_ORDER_LIST}*")

    # 2. 清理指定订单的详情和打印缓存
    if order_no:
        cache.delete(f"{CACHE_PREFIX_ORDER_DETAIL}{order_no}")
        cache.delete(f"{CACHE_PREFIX_PRINT_ORDER}{order_no}")

    logger.info(f"已清理订单缓存: {order_no if order_no else '全列表'}")


def clear_stock_cache():
    """
    清理库存列表缓存
    """
    cache.delete_pattern(f"{CACHE_PREFIX_STOCK_LIST}*")
    logger.info("已清理库存列表缓存")


def clear_product_search_cache():
    """
    清理商品搜索缓存
    """
    cache.delete_pattern(f"{CACHE_PREFIX_PRODUCT_SEARCH}*")
    logger.info("已清理商品搜索缓存")


def clear_customer_related_cache(customer_id: int = None):
    """
    清理客户相关业务缓存（最近购买商品等）
    """
    if customer_id:
        cache.delete(f"{CACHE_PREFIX_CUSTOMER_RECENT_PRODUCT}{customer_id}")
    cache.delete_pattern(f"{CACHE_PREFIX_CUSTOMER_RECENT_PRODUCT}*")
    logger.info(f"已清理客户相关缓存: {customer_id if customer_id else '全量'}")


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
@permission_required(PERM_ORDER_CREATE)
def index(request):
    """开单主页面（三联单填写页）"""
    # 🔥 优化：删除无用的全表查询，仅保留必要逻辑
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    return render(request, 'bill/index.html', {
        'is_super_admin': is_super_admin
    })


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def search_product(request):
    """商品搜索（手动缓存版）"""
    keyword = request.GET.get('keyword', '').strip()
    customer_id = request.GET.get('customer_id', '').strip()

    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 🔥 手动缓存：基础商品信息缓存
    cache_key = f"{CACHE_PREFIX_PRODUCT_SEARCH}{keyword}"
    cached_products = cache.get(cache_key)

    if cached_products is None:
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

        cached_products = []
        for p in all_products:
            cached_products.append({
                'id': p.id,
                'name': p.name,
                'standard_price': float(p.price),
                'unit': p.unit,
                'stock_system': p.stock_system  # 修复：旧字段stock → stock_system
            })
        cache.set(cache_key, cached_products, timeout=CACHE_PRODUCT_SEARCH)
        logger.info(f"设置商品搜索缓存: {cache_key}")

    # 客户专属价查询（保留，无优化）
    customer_prices = {}
    if customer_id:
        product_ids = [item['id'] for item in cached_products]
        cp_list = CustomerPrice.objects.filter(customer_id=customer_id, product_id__in=product_ids)
        customer_prices = {cp.product_id: float(cp.custom_price) for cp in cp_list}

    data = []
    for item in cached_products:
        product_id = item['id']
        final_price = customer_prices.get(product_id, item['standard_price'])
        data.append({
            'id': product_id,
            'name': item['name'],
            'price': final_price,
            'standard_price': item['standard_price'],
            'unit': item['unit'],
            'stock_system': item['stock_system']
        })

    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CREATE)
def save_order(request):
    """保存订单（含价格快照保存）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '请求方式错误'})

    try:
        with transaction.atomic():
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_id = data.get('customer_id', '')
            original_order_no = data.get('original_order_no', '')

            if not items:
                return JsonResponse({'code': 0, 'msg': '无订单明细'})

            # 1. 基础数据校验
            product_ids = []
            item_map = {}  # 存储前端传来的详细数据
            for item in items:
                pid = item.get('id')
                try:
                    pid = int(pid)
                except (ValueError, TypeError):
                    return JsonResponse({'code': 0, 'msg': f'商品{item.get("name", "未知")}ID格式错误'})

                qty = item.get('qty', 0)
                price = item.get('price', 0)  # 前端传来的单价

                if not pid or not isinstance(qty, int) or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': f'商品{item.get("name", "未知")}数量无效'})

                product_ids.append(pid)
                item_map[pid] = {'qty': qty, 'price': decimal.Decimal(str(price))}

            # 2. 批量查询商品
            products = Product.objects.filter(id__in=product_ids).in_bulk()
            for pid in product_ids:
                if pid not in products:
                    return JsonResponse({'code': 0, 'msg': f'商品ID {pid} 不存在'})

            # 3. 查询客户专属价 (用于快照)
            customer_prices_dict = {}
            if customer_id:
                cp_list = CustomerPrice.objects.filter(customer_id=customer_id, product_id__in=product_ids)
                customer_prices_dict = {cp.product_id: cp.custom_price for cp in cp_list}

            # 4. 创建订单主表
            order = Order()
            order.creator = request.user
            if customer_id:
                customer = get_object_or_404(Customer, id=customer_id)
                order.customer = customer
                order.area = customer.area

            if original_order_no:
                original_order = get_object_or_404(Order, order_no=original_order_no)
                if original_order.status != 'cancelled':
                    return JsonResponse({'code': 0, 'msg': '仅作废订单可重开'})
                order.original_order = original_order
                order.status = 'reopened'

            total_amount = 0
            order_items = []

            for pid in product_ids:
                product = products[pid]
                qty = item_map[pid]['qty']
                input_price = item_map[pid]['price']  # 开单员录入的单价
                amount = input_price * qty
                total_amount += amount

                # 获取快照价格
                snap_standard = product.price
                snap_customer = customer_prices_dict.get(pid, None)

                order_items.append(OrderItem(
                    order=order,
                    product=product,
                    quantity=qty,
                    amount=amount,
                    actual_unit_price=input_price,  # 【新增】保存实际单价
                    snapshot_standard_price=snap_standard,  # 【新增】保存标准价快照
                    snapshot_customer_price=snap_customer  # 【新增】保存客户价快照
                ))

            order.total_amount = total_amount
            order.save()

            # 5. 批量创建明细
            OrderItem.objects.bulk_create(order_items)

            # 6. 批量更新库存
            for pid in product_ids:
                products[pid].stock_system -= item_map[pid]['qty']
            Product.objects.bulk_update(products.values(), ['stock_system'])

            # 7. 日志与缓存清理
            create_operation_log(request, 'create_order', 'order', str(order.id), f"订单-{order.order_no}", f"创建订单")
            clear_stock_cache()
            clear_order_cache()
            if customer_id:
                clear_customer_related_cache(int(customer_id))

            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})


@login_required
@permission_required(PERM_ORDER_VIEW)
def order_list(request):
    """订单列表页（新增Tab状态筛选版 + 财务进度展示）"""
    order_no = request.GET.get('order_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    area_id = request.GET.get('area_id', '')
    customer_name = request.GET.get('customer_name', '').strip()
    settled_status = request.GET.get('settled_status', '')
    amount_operator = request.GET.get('amount_operator', '')
    amount_value = request.GET.get('amount_value', '').strip()
    status = request.GET.get('status', 'all')  # 🔥 新增：状态筛选参数
    page = request.GET.get('page', 1)

    # 🔥 手动缓存 Key：新增 status 参数
    cache_key = f"{CACHE_PREFIX_ORDER_LIST}{request.user.id}_{order_no}_{date_from}_{date_to}_{area_id}_{customer_name}_{settled_status}_{amount_operator}_{amount_value}_{status}_{page}"
    cached_data = cache.get(cache_key)

    if cached_data:
        return HttpResponse(cached_data)

    # 关联预加载（无N+1）
    orders = Order.objects.select_related('area', 'customer', 'creator').order_by('-create_time')

    # 权限控制
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    can_view_others = request.user.has_permission('order_view_others')
    if not is_super_admin and not can_view_others:
        orders = orders.filter(creator=request.user)

    is_admin = request.user.role and request.user.role.code == ROLE_ADMIN
    is_operator = request.user.role and request.user.role.code == ROLE_OPERATOR

    # 🔥 新增：状态筛选（核心Tab逻辑）
    base_orders = orders  # 保存基础查询集用于统计
    if status == 'normal':
        orders = orders.filter(status__in=ORDER_STATUS_VALID)
    elif status == 'cancelled':
        orders = orders.filter(status='cancelled')

    # 🔥 新增：Tab数量统计（基于权限控制后的基础数据）
    count_all = base_orders.count()
    count_normal = base_orders.filter(status__in=ORDER_STATUS_VALID).count()
    count_cancelled = base_orders.filter(status='cancelled').count()

    # 原有筛选逻辑（保留，注意移除了原来强制的 status__in=ORDER_STATUS_VALID）
    if order_no:
        orders = orders.filter(order_no__startswith=order_no)

    if settled_status == 'settled':
        orders = orders.filter(is_settled=True)
    elif settled_status == 'unsettled':
        orders = orders.filter(is_settled=False)

    if area_id and area_id.isdigit():
        orders = orders.filter(area_id=int(area_id))

    if customer_name:
        orders = orders.filter(customer__name__istartswith=customer_name)

    if date_from:
        try:
            start_datetime = timezone.make_aware(datetime.strptime(date_from, '%Y-%m-%d'))
            orders = orders.filter(create_time__gte=start_datetime)
        except:
            pass
    if date_to:
        try:
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            end_datetime = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
            orders = orders.filter(create_time__lt=end_datetime)
        except:
            pass

    if amount_operator in ['gt', 'lt'] and amount_value:
        try:
            amount = decimal.Decimal(amount_value)
            orders = orders.filter(total_amount__gt=amount) if amount_operator == 'gt' else orders.filter(
                total_amount__lt=amount)
        except decimal.InvalidOperation:
            pass

    # 分页
    paginator = Paginator(orders, 10)
    try:
        page_orders = paginator.page(page)
    except PageNotAnInteger:
        page_orders = paginator.page(1)
    except EmptyPage:
        page_orders = paginator.page(paginator.num_pages)

    # 统计数据（基于当前筛选结果）
    stats = orders.aggregate(
        total_orders=Count('id'),
        total_sales=Sum('total_amount', default=decimal.Decimal('0.00')),
        settled_orders=Count(Case(When(is_settled=True, then='id'))),
        total_debt=Sum(Case(
            When(is_settled=False, then='total_amount')
        ), default=decimal.Decimal('0.00'), output_field=DecimalField())
    )
    total_orders = stats['total_orders']
    total_sales = stats['total_sales']
    settled_orders = stats['settled_orders']
    total_debt = stats['total_debt']

    # 作废权限计算 & 🔥 新增：财务数据计算
    current_time = timezone.now()
    order_list = list(page_orders)
    for order in order_list:
        time_diff = (current_time - order.create_time).total_seconds() / 60
        order.time_diff = time_diff

        # 🔥 新增：计算财务进度 (统一用 Decimal 计算，防止浮点数误差)
        order.unpaid_amount = order.total_amount - order.received_amount
        # 计算已收比例 (用于前端进度条，可选)
        if order.total_amount > 0:
            order.paid_percent = (order.received_amount / order.total_amount) * 100
        else:
            order.paid_percent = 100

        can_cancel = False
        if order.status != 'cancelled' and not order.is_settled and order.status != 'printed':
            if is_super_admin:
                can_cancel = True
            elif is_admin:
                if (order.creator == request.user and request.user.has_permission('order_cancel_own')) or \
                        (order.creator != request.user and request.user.has_permission('order_cancel_others')):
                    can_cancel = True
            elif is_operator:
                if order.creator == request.user and time_diff <= 5 and request.user.has_permission('order_cancel_own'):
                    can_cancel = True
        order.can_cancel = can_cancel

    areas = Area.objects.all().order_by('name')
    context = {
        'orders': order_list,
        'page_orders': page_orders,
        'paginator': paginator,
        'areas': areas,
        'date_from': date_from,
        'date_to': date_to,
        'area_id': area_id,
        'customer_name': customer_name,
        'settled_status': settled_status,
        'amount_operator': amount_operator,
        'amount_value': amount_value,
        'is_super_admin': is_super_admin,
        'is_admin': is_admin,
        'is_operator': is_operator,
        'order_no': order_no,
        'total_orders': total_orders,
        'total_sales': total_sales,
        'settled_orders': settled_orders,
        'total_debt': total_debt,
        # 🔥 新增：Tab相关参数
        'status': status,
        'count_all': count_all,
        'count_normal': count_normal,
        'count_cancelled': count_cancelled,
    }

    response = render(request, 'bill/order_list.html', context)
    cache.set(cache_key, response.content, CACHE_ORDER_LIST)

    return response

@login_required
@permission_required(PERM_ORDER_VIEW)
def order_detail(request, order_no):
    """订单详情页（手动缓存版 + 财务数据展示）"""
    # 🔥 手动缓存 Key
    cache_key = f"{CACHE_PREFIX_ORDER_DETAIL}{order_no}"
    cached_data = cache.get(cache_key)

    if cached_data:
        return HttpResponse(cached_data)

    # 🔥 优化1：一次性预加载所有关联（customer/area/creator），彻底解决N+1
    order = get_object_or_404(
        Order.objects.select_related('customer', 'area', 'creator'),
        order_no=order_no
    )

    # 🔥 优化2：缓存用户角色/权限，仅查询1次
    user_role = request.user.role
    role_code = user_role.code if user_role else ''
    is_super_admin = role_code == ROLE_SUPER_ADMIN
    can_view_others = request.user.has_permission('order_view_others')

    # 权限控制
    if not is_super_admin and not can_view_others and order.creator != request.user:
        return redirect('/bill/orders/')

    # 🔥 优化3：使用Django时区时间
    current_time = timezone.now()
    time_diff = (current_time - order.create_time).total_seconds() / 60

    # 缓存权限，避免重复查询
    can_cancel_own = request.user.has_permission('order_cancel_own')
    can_cancel_others = request.user.has_permission('order_cancel_others')
    is_admin = role_code == ROLE_ADMIN
    is_operator = role_code == ROLE_OPERATOR

    # 作废按钮逻辑（不变）
    show_cancel_btn = False
    if order.status != 'cancelled' and not order.is_settled and order.status != 'printed':
        if is_super_admin:
            show_cancel_btn = True
        elif is_admin:
            if (order.creator == request.user and can_cancel_own) or (
                    order.creator != request.user and can_cancel_others):
                show_cancel_btn = True
        elif is_operator:
            if order.creator == request.user and can_cancel_own and time_diff <= 5:
                show_cancel_btn = True

    # 🔥 优化4：使用已优化的明细数据（模板必须用这个，禁止用order.items.all）
    items = OrderItem.objects.select_related('product').filter(order=order)

    # 🔥 新增：详情页财务数据计算
    unpaid_amount = order.total_amount - order.received_amount
    if order.total_amount > 0:
        paid_percent = (order.received_amount / order.total_amount) * 100
    else:
        paid_percent = 100

    context = {
        'order': order,
        'items': items,  # 模板必须用这个变量
        'is_super_admin': is_super_admin,
        'time_diff': time_diff,
        'can_cancel_own': can_cancel_own,
        'can_cancel_others': can_cancel_others,
        'is_admin': is_admin,
        'is_operator': is_operator,
        'show_cancel_btn': show_cancel_btn,
        # 🔥 新增：传递给模板
        'unpaid_amount': unpaid_amount,
        'paid_percent': paid_percent
    }

    response = render(request, 'bill/order_detail.html', context)
    cache.set(cache_key, response.content, CACHE_ORDER_DETAIL)

    return response


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def search_customer(request):
    """客户搜索 + 手动缓存优化"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 🔥 手动缓存键
    cache_key = f"{CACHE_PREFIX_CUSTOMER_SEARCH}{keyword}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse({'code': 1, 'data': cached_data})

    customer_matches = Customer.objects.select_related('area').filter(
        Q(name__icontains=keyword) | Q(area__name__icontains=keyword)
    ).distinct()[:8]

    data = []
    for customer in customer_matches:
        area_name = customer.area.name if customer.area else '无区域'
        data.append({
            'id': customer.id,
            'name': customer.name,
            'area_id': customer.area.id if customer.area else '',
            'area_name': area_name,
            'full_name': f"{area_name} | {customer.name}"
        })

    # 🔥 缓存10秒
    cache.set(cache_key, data, timeout=CACHE_CUSTOMER_SEARCH)
    logger.info(f"设置客户搜索缓存: {cache_key}")
    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CANCEL_OWN)
def cancel_order(request, order_no):
    """
    作废订单（高性能优化版 + 手动缓存清理）
    """
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)

    # 事务包裹：订单作废 + 库存恢复 原子性操作
    with transaction.atomic():
        try:
            # ===================== 优化1：订单查询预加载关联字段，减少查询 =====================
            order = get_object_or_404(
                Order.objects.select_related('creator'),
                order_no=order_no
            )
            current_time = timezone.now()
            # 统一使用 Django 时区计算时间差
            time_diff = (current_time - order.create_time).total_seconds() / 60

            # ===================== 1. 状态锁校验 =====================
            if order.is_settled:
                return JsonResponse({'code': 0, 'msg': '已收款的订单无法作废'}, status=400)
            if order.status == 'printed':
                return JsonResponse({'code': 0, 'msg': '已出库的订单无法作废'}, status=400)
            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '该订单已作废，无需重复操作'}, status=400)

            # ===================== 2. 精简权限判断 =====================
            user_role_code = request.user.role.code if request.user.role else None
            is_super_admin = user_role_code == ROLE_SUPER_ADMIN
            is_admin = user_role_code == ROLE_ADMIN
            is_operator = user_role_code == ROLE_OPERATOR

            if not is_super_admin:
                if is_admin:
                    # 管理员：作废他人订单需要额外权限
                    if order.creator != request.user and not request.user.has_permission('order_cancel_others'):
                        return JsonResponse({'code': 0, 'msg': '无作废他人订单的权限'}, status=403)
                elif is_operator:
                    # 普通店员：仅自己 + 5分钟内
                    if order.creator != request.user:
                        return JsonResponse({'code': 0, 'msg': '普通店员仅能作废自己创建的订单'}, status=403)
                    if time_diff > 5:
                        return JsonResponse({'code': 0, 'msg': f'仅支持开单后5分钟内作废，当前已过{time_diff:.1f}分钟'},
                                            status=400)
                else:
                    return JsonResponse({'code': 0, 'msg': '无作废订单的权限'}, status=403)

            # ===================== 3. 参数校验 =====================
            data = json.loads(request.body)
            reason = data.get('reason', '').strip()
            if not reason:
                return JsonResponse({'code': 0, 'msg': '作废原因至少填写1个字'}, status=400)

            # ===================== 4. 执行作废操作 =====================
            order.status = 'cancelled'
            order.cancelled_by = request.user
            order.cancelled_time = current_time
            order.cancelled_reason = reason
            order.save(update_fields=['status', 'cancelled_by', 'cancelled_time', 'cancelled_reason'])

            # ===================== 优化2：解决N+1查询 + 批量恢复库存 =====================
            # 🔥 核心：一次查询获取所有订单项+关联商品，无N+1
            order_items = order.items.select_related('product')
            product_list = []
            item_count = 0

            for item in order_items:
                if item.product:
                    # 🔥 修复：旧字段stock → stock_system（恢复系统库存）
                    item.product.stock_system += item.quantity
                    product_list.append(item.product)
                    item_count += 1

            # 🔥 核心：批量更新库存，1次数据库操作（性能提升10~100倍）
            if product_list:
                Product.objects.bulk_update(product_list, fields=['stock_system'])

            # ===================== 5. 日志记录 =====================
            role_name = request.user.role.name if request.user.role else '未知'
            create_operation_log(
                request=request,
                op_type='cancel_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"作废订单{order.order_no}，操作人角色：{role_name}，原因：{reason}，恢复{item_count}个商品库存，开单后{time_diff:.1f}分钟作废"
            )

            # ===================== 6. 缓存清理（核心新增） =====================
            customer_id = order.customer_id if order.customer else None
            clear_order_cache(order_no)  # 清理当前订单详情/打印
            clear_stock_cache()  # 库存恢复，清理库存列表
            if customer_id:
                clear_customer_related_cache(customer_id)  # 清理客户最近购买

            return JsonResponse({'code': 1, 'msg': '订单作废成功', 'order_no': order_no})

        except json.JSONDecodeError:
            return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)
        except Exception as e:
            # 事务会自动回滚，安全返回错误
            return JsonResponse({'code': 0, 'msg': f'作废失败：{str(e)}'}, status=500)


@login_required
@permission_required(PERM_ORDER_PRINT)
def print_order(request, order_no):
    """订单打印页面（手动缓存版）"""
    # 🔥 手动缓存 Key
    cache_key = f"{CACHE_PREFIX_PRINT_ORDER}{order_no}"
    cached_data = cache.get(cache_key)

    if cached_data:
        return HttpResponse(cached_data)

    # ===================== 核心优化1：预加载订单关联的所有外键 =====================
    # 一次性加载 客户、区域、开单人，无额外查询
    order = get_object_or_404(
        Order.objects.select_related('customer', 'area', 'creator'),
        order_no=order_no
    )

    # ===================== 核心优化2：预加载订单项的商品，彻底消除N+1 =====================
    # 1次查询获取所有订单项 + 关联商品，模板渲染无额外DB请求
    items = order.items.select_related('product')

    # RBAC权限判断（保持原有逻辑不变）
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    context = {
        'order': order,
        'items': items,
        'is_super_admin': is_super_admin
    }

    response = render(request, 'bill/print.html', context)
    cache.set(cache_key, response.content, CACHE_PRINT_ORDER)

    return response


@login_required
@permission_required(PERM_ORDER_REOPEN)
def reopen_order_edit(request, order_no):
    """重开订单编辑页面【终极性能版：删除无用全量查询】"""
    # 预加载关联，消除N+1查询
    original_order = get_object_or_404(
        Order.objects.select_related('customer', 'area'),
        order_no=order_no
    )

    # 非作废订单直接重定向
    if original_order.status != 'cancelled':
        return redirect('bill:order_detail', order_no=order_no)

    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    # 订单商品明细（无N+1）
    items = OrderItem.objects.select_related('product').filter(order=original_order)

    # 订单回显数据（核心业务，保留）
    order_data = {
        'order_no': original_order.order_no,
        'customer_id': original_order.customer_id if original_order.customer else '',
        'customer_name': f"{original_order.area.name} | {original_order.customer.name}" if (
                original_order.customer and original_order.area) else '',
        'items': [
            {
                'id': item.product_id if item.product else '',
                'name': item.product.name if item.product else '',
                'qty': item.quantity,
                'unit': item.product.unit if item.product else '',
                'price': float(item.product.price) if item.product else 0,
                'amt': float(item.amount) if item.amount else 0
            }
            for item in items
        ]
    }

    # 仅传递前端必需参数：客户信息前端自动回显+搜索，无需全量列表
    return render(request, 'bill/index.html', {
        'is_super_admin': is_super_admin,
        'reopen_order_data': order_data  # 仅保留订单回显数据
    })


# ========== 重构：结清相关视图（适配RBAC + 缓存清理） ==========
@ajax_login_required
@ajax_permission_required(PERM_ORDER_SETTLE)
def settle_order(request, order_no):
    """标记订单结清（性能优化版 + 缓存清理）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)

    try:
        # 优化：select_related 预加载（规范，无N+1）
        order = get_object_or_404(Order.objects.select_related('creator'), order_no=order_no)

        # 状态校验
        if order.status == 'cancelled':
            return JsonResponse({'code': 0, 'msg': '作废订单无法标记结清'}, status=400)
        if order.is_settled:
            return JsonResponse({'code': 0, 'msg': '该订单已结清，无需重复操作'}, status=400)

        # 参数校验
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)

        remark = data.get('remark', '').strip()
        if not remark:
            return JsonResponse({'code': 0, 'msg': '请填写结清备注'}, status=400)

        # 优化：统一使用 Django 时区时间
        order.is_settled = True
        order.settled_by = request.user
        order.settled_time = timezone.now()
        order.settled_remark = remark
        order.save(update_fields=['is_settled', 'settled_by', 'settled_time', 'settled_remark'])

        # 操作日志
        create_operation_log(
            request=request, op_type='settle_order', obj_type='order',
            obj_id=str(order.id), obj_name=f"订单-{order.order_no}",
            detail=f"标记订单{order.order_no}结清，备注：{remark}"
        )

        # 🔥 缓存清理
        clear_order_cache(order_no)

        return JsonResponse({'code': 1, 'msg': '订单标记结清成功', 'order_no': order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'标记结清失败：{str(e)}'}, status=500)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_UNSETTLE)
def unsettle_order(request, order_no):
    """撤销订单结清（性能优化版 + 缓存清理）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)

    try:
        order = get_object_or_404(Order.objects.select_related('creator'), order_no=order_no)

        if not order.is_settled:
            return JsonResponse({'code': 0, 'msg': '该订单未结清，无需撤销'}, status=400)

        # 参数校验
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)

        remark = data.get('remark', '').strip()
        if not remark:
            return JsonResponse({'code': 0, 'msg': '请填写撤销结清备注'}, status=400)

        # 优化：时区统一 + 仅更新必要字段
        order.is_settled = False
        order.unsettled_by = request.user
        order.unsettled_time = timezone.now()
        order.unsettled_remark = remark
        order.save(update_fields=['is_settled', 'unsettled_by', 'unsettled_time', 'unsettled_remark'])

        # 操作日志
        create_operation_log(
            request=request, op_type='unsettle_order', obj_type='order',
            obj_id=str(order.id), obj_name=f"订单-{order.order_no}",
            detail=f"撤销订单{order.order_no}结清状态，备注：{remark}"
        )

        # 🔥 缓存清理
        clear_order_cache(order_no)

        return JsonResponse({'code': 1, 'msg': '撤销订单结清成功', 'order_no': order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'撤销结清失败：{str(e)}'}, status=500)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_SETTLE)
def batch_settle_order(request):
    """批量标记订单结清（高性能优化版 + 缓存清理）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)

    try:
        # 参数解析
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'code': 0, 'msg': '请求数据格式错误，必须是JSON'}, status=400)

        order_list = data.get('orders', [])
        if not order_list:
            return JsonResponse({'code': 0, 'msg': '请选择要结清的订单'}, status=400)

        success_count = 0
        fail_list = []
        update_orders = []
        current_time = timezone.now()

        order_no_map = {}
        for item in order_list:
            order_no = str(item.get('order_no', '')).strip()
            remark = str(item.get('remark', '')).strip()

            if not order_no or not remark:
                fail_list.append(f'{order_no or "未知订单"}：备注不能为空')
                continue
            order_no_map[order_no] = remark

        if not order_no_map:
            return JsonResponse({'code': 0, 'msg': '无有效订单数据'}, status=400)

        valid_orders = Order.objects.filter(order_no__in=order_no_map.keys())
        valid_order_nos = {o.order_no for o in valid_orders}

        for order in valid_orders:
            remark = order_no_map[order.order_no]

            if order.status == 'cancelled':
                fail_list.append(f'{order.order_no}：作废订单无法结清')
                continue
            if order.is_settled:
                fail_list.append(f'{order.order_no}：已结清，无需重复操作')
                continue

            order.is_settled = True
            order.settled_by = request.user
            order.settled_time = current_time
            order.settled_remark = remark
            update_orders.append(order)

        for order_no in order_no_map.keys():
            if order_no not in valid_order_nos:
                fail_list.append(f'{order_no}：订单不存在')

        if update_orders:
            with transaction.atomic():
                Order.objects.bulk_update(
                    update_orders,
                    fields=['is_settled', 'settled_by', 'settled_time', 'settled_remark']
                )
                for order in update_orders:
                    create_operation_log(
                        request=request, op_type='batch_settle_order', obj_type='order',
                        obj_id=str(order.id), obj_name=f"订单-{order.order_no}",
                        detail=f"批量结清订单{order.order_no}，备注：{order.settled_remark}"
                    )
            success_count = len(update_orders)

            # 🔥 缓存清理：清理所有受影响的订单缓存
            for order in update_orders:
                clear_order_cache(order.order_no)

        msg = f'批量处理完成！成功{success_count}个，失败{len(fail_list)}个'
        if fail_list:
            msg += f'；失败原因：{"; ".join(fail_list)}'

        return JsonResponse({'code': 1 if success_count > 0 else 0,
                             'msg': msg,
                             'success_count': success_count,
                             'fail_list': fail_list})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'批量结清失败：{str(e)}'}, status=500)


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def get_customer_recent_products(request):
    """获取客户最近购买的商品（最近5单汇总版）"""
    customer_id = request.GET.get('customer_id', '').strip()
    if not customer_id:
        return JsonResponse({'code': 0, 'msg': '请选择客户', 'data': []})

    # 🔥 手动缓存 Key
    cache_key = f"{CACHE_PREFIX_CUSTOMER_RECENT_PRODUCT}{customer_id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse({'code': 1, 'data': cached_data})

    try:
        # ============== 🔥 第一步：获取最近 5 单的 ID ==============
        # 利用索引 (status, is_settled, customer, create_time) 高效查询
        recent_order_ids = Order.objects.filter(
            customer_id=customer_id,
            status__in=ORDER_STATUS_VALID,  # 只看有效订单
            is_settled=False
        ).order_by('-create_time').values_list('id', flat=True)[:5]  # 核心修改：只取最近 5 单

        if not recent_order_ids:
            # 没有历史订单，直接缓存空结果
            cache.set(cache_key, [], timeout=CACHE_CUSTOMER_RECENT_PRODUCT)
            return JsonResponse({'code': 1, 'data': []})

        # ============== 🔥 第二步：获取这 5 单的所有商品明细 ==============
        # select_related 优化查询，一次性把 Product 和 Order 信息查出来
        order_items = OrderItem.objects.filter(
            order_id__in=recent_order_ids
        ).select_related('product', 'order').order_by('-order__create_time')  # 倒序排列：保证最后买的在最前面

        # ============== 🔥 第三步：查询客户专属价格 ==============
        customer_prices = {}
        price_qs = CustomerPrice.objects.filter(customer_id=customer_id).values('product_id', 'custom_price')
        for item in price_qs:
            customer_prices[item['product_id']] = float(item['custom_price'])

        # ============== 🔥 第四步：汇总去重商品 ==============
        product_dict = {}
        for item in order_items:
            product = item.product
            if not product:
                continue

            # 核心去重逻辑：如果商品已在字典中，说明已经记录过更近的一次购买了，直接跳过
            if product.id in product_dict:
                continue

            final_price = customer_prices.get(product.id, float(product.price))
            product_dict[product.id] = {
                'id': product.id,
                'name': product.name,
                'price': final_price,
                'standard_price': float(product.price),
                'unit': product.unit,
                'last_purchase_time': item.order.create_time.strftime('%Y-%m-%d %H:%M'),
                'last_quantity': item.quantity
            }

        # 转换为列表（Python 3.7+ 字典保持插入顺序，即最后购买时间倒序）
        recent_products = list(product_dict.values())

        # 设置缓存
        cache.set(cache_key, recent_products, timeout=CACHE_CUSTOMER_RECENT_PRODUCT)
        logger.info(
            f"设置客户最近商品缓存: {cache_key} (基于最近{len(recent_order_ids)}单, 共{len(recent_products)}个商品)")

        return JsonResponse({'code': 1, 'data': recent_products})
    except Exception as e:
        logger.error(f"获取客户最近商品失败: {str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': f'获取失败：{str(e)}', 'data': []})


# ===================== 2. 新增：价格核算视图 =====================

@login_required
@permission_required(PERM_ORDER_PRICE_CHECK)  # 复用查看权限，或者你可以新建一个 PERM_ORDER_PRICE_CHECK
def price_check_view(request):
    """价格核算页面入口"""
    date_from = request.GET.get('date_from', (timezone.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
    date_to = request.GET.get('date_to', timezone.now().strftime('%Y-%m-%d'))

    # 传递空的结果集，只显示筛选框
    return render(request, 'bill/price_check.html', {
        'date_from': date_from,
        'date_to': date_to,
        'results': None,
        'stats': None
    })


@login_required
@permission_required(PERM_ORDER_PRICE_CHECK)
def price_check_ajax(request):
    """执行价格核算的AJAX接口"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '请求错误'})

    date_from = request.POST.get('date_from')
    date_to = request.POST.get('date_to')

    if not date_from or not date_to:
        return JsonResponse({'code': 0, 'msg': '请选择日期范围'})

    # 构建时间范围
    start_datetime = timezone.make_aware(datetime.strptime(date_from, '%Y-%m-%d'))
    end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
    end_datetime = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    # 查询订单及明细 (select_related 优化性能)
    orders = Order.objects.filter(
        create_time__gte=start_datetime,
        create_time__lt=end_datetime,
        status__in=['pending', 'printed', 'reopened']  # 只核查有效订单
    ).select_related('customer', 'creator').prefetch_related('items__product').order_by('-create_time')

    # 权限控制 (如果非管理员，只能看自己的)
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    can_view_others = request.user.has_permission('order_view_others')
    if not is_super_admin and not can_view_others:
        orders = orders.filter(creator=request.user)

    results = []
    total_checked = 0
    total_abnormal = 0
    total_loss_risk = decimal.Decimal('0.00')

    for order in orders:
        total_checked += 1
        order_has_issue = False
        issue_items = []

        for item in order.items.all():
            # 确定基准价
            base_price = item.snapshot_standard_price
            price_type = "标准价"

            # 如果有客户价快照，基准价应为客户价
            if item.snapshot_customer_price is not None:
                base_price = item.snapshot_customer_price
                price_type = "客户价"

            # 如果没有快照数据（历史旧数据），跳过或标记
            if base_price is None or item.actual_unit_price is None:
                continue

            diff = item.actual_unit_price - base_price
            issue_type = None
            issue_label = ""

            # 逻辑判断
            if item.snapshot_customer_price is not None:
                # 情况A：有熟客价
                if item.actual_unit_price != item.snapshot_customer_price:
                    # 虽然有熟客价，但没用对
                    if item.actual_unit_price == item.snapshot_standard_price:
                        issue_type = 'mismatch'
                        issue_label = "错配：未用熟客价"
                    elif item.actual_unit_price < item.snapshot_customer_price:
                        issue_type = 'short'
                        issue_label = "低报：低于熟客价"
                        total_loss_risk += (abs(diff) * item.quantity)
                    else:
                        issue_type = 'over'
                        issue_label = "高报：高于熟客价"

            else:
                # 情况B：无熟客价
                if item.actual_unit_price < item.snapshot_standard_price:
                    issue_type = 'short'
                    issue_label = "低报"
                    total_loss_risk += (abs(diff) * item.quantity)
                elif item.actual_unit_price > item.snapshot_standard_price:
                    issue_type = 'over'
                    issue_label = "高报"

            if issue_type:
                order_has_issue = True
                issue_items.append({
                    'product_name': item.product.name if item.product else '未知',
                    'qty': item.quantity,
                    'snapshot_std': item.snapshot_standard_price,
                    'snapshot_cust': item.snapshot_customer_price,
                    'actual': item.actual_unit_price,
                    'diff': diff,
                    'type': issue_type,
                    'label': issue_label
                })

        if order_has_issue:
            total_abnormal += 1
            results.append({
                'order_no': order.order_no,
                'customer_name': order.customer.name if order.customer else '散客',
                'creator_name': order.creator.name if order.creator else '未知',
                'create_time': order.create_time,
                'items': issue_items
            })

    stats = {
        'checked': total_checked,
        'abnormal': total_abnormal,
        'loss': total_loss_risk
    }

    return JsonResponse({'code': 1, 'data': results, 'stats': stats})


# ===================== 新增：订单统计相关视图 =====================

# 工具函数：解析时间范围（复用你区域组统计的逻辑）
def parse_order_time_range(time_range, start_date_str, end_date_str):
    from datetime import datetime, timedelta
    today = timezone.now().date()

    if time_range == 'today':
        return today, today
    elif time_range == '7days':
        return today - timedelta(days=7), today
    elif time_range == 'month':
        return today.replace(day=1), today
    elif time_range == 'custom' and start_date_str and end_date_str:
        try:
            start = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            return start, end
        except:
            pass
    # 默认：最近30天
    return today - timedelta(days=30), today


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def order_stats_page(request):
    """订单统计页面入口（零计算，仅渲染HTML）"""
    return render(request, 'bill/order_stats.html')


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def calculate_order_stats(request):
    """
    核心：订单统计计算接口（懒加载专用）
    只有点击按钮才调用，利用现有索引优化性能
    """
    try:
        # 1. 获取参数
        time_range = request.GET.get('time_range', '30days')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')

        # 2. 解析时间
        start_dt, end_dt = parse_order_time_range(time_range, start_date, end_date)

        # 3. 构建基础QuerySet（利用索引：status, is_settled, create_time）
        # 注意：这里不做权限过滤，统计全公司数据（如果需要按人过滤请自行添加）
        base_orders = Order.objects.filter(
            create_time__date__gte=start_dt,
            create_time__date__lte=end_dt
        )

        # 4. 核心指标聚合（一次数据库查询搞定所有聚合）
        # 利用索引：status, is_settled, create_time, total_amount
        agg_result = base_orders.aggregate(
            # 经营核心
            total_sales=Coalesce(Sum('total_amount', filter=Q(status__in=ORDER_STATUS_VALID)), 0,
                                 output_field=DecimalField(max_digits=12, decimal_places=2)),
            total_orders=Count('id', filter=Q(status__in=ORDER_STATUS_VALID)),

            # 回款监控
            settled_amount=Coalesce(Sum('total_amount', filter=Q(status__in=ORDER_STATUS_VALID, is_settled=True)), 0,
                                    output_field=DecimalField(max_digits=12, decimal_places=2)),
            total_debt=Coalesce(Sum('total_amount', filter=Q(status__in=ORDER_STATUS_VALID, is_settled=False)), 0,
                                output_field=DecimalField(max_digits=12, decimal_places=2)),
            debt_order_count=Count('id', filter=Q(status__in=ORDER_STATUS_VALID, is_settled=False)),

            # 风险预警
            cancelled_count=Count('id', filter=Q(status='cancelled')),
            reopened_count=Count('id', filter=Q(status='reopened')),

            # 活跃客户
            active_customers=Count('customer', distinct=True,
                                   filter=Q(status__in=ORDER_STATUS_VALID, customer__isnull=False))
        )

        # 5. 计算衍生指标
        total_sales_val = float(agg_result['total_sales'])
        total_orders_val = agg_result['total_orders']
        settled_amount_val = float(agg_result['settled_amount'])

        avg_order_value = round(total_sales_val / total_orders_val, 2) if total_orders_val > 0 else 0.0
        repayment_rate = round((settled_amount_val / total_sales_val) * 100, 2) if total_sales_val > 0 else 0.0

        # 6. 组装返回数据
        data = {
            # 经营核心
            'total_sales': total_sales_val,
            'total_orders': total_orders_val,
            'avg_order_value': avg_order_value,

            # 回款监控
            'settled_amount': settled_amount_val,
            'total_debt': float(agg_result['total_debt']),
            'repayment_rate': repayment_rate,
            'debt_order_count': agg_result['debt_order_count'],

            # 风险预警
            'cancelled_count': agg_result['cancelled_count'],
            'reopened_count': agg_result['reopened_count'],

            # 活跃客户
            'active_customers': agg_result['active_customers'],

            # 统计信息
            'date_range': {
                'start': start_dt.strftime('%Y-%m-%d'),
                'end': end_dt.strftime('%Y-%m-%d')
            },
            'calculated_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        return JsonResponse({'code': 1, 'data': data})

    except Exception as e:
        logger.error(f"订单统计计算失败：{str(e)}", exc_info=True)
        return JsonResponse({'code': 0, 'msg': f'统计失败：{str(e)}'})