# bill\views
# ========== 先导入所有必要模块（统一开头，避免重复） ==========
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from difflib import SequenceMatcher

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import Product, Order, OrderItem, ProductAlias, DailySalesSummary, CustomerPrice, Customer, Area
from django.db.models import Q, Sum
import json
from datetime import date, datetime, timedelta
from .utils import generate_daily_summary, auto_summary_yesterday
from django.contrib.auth.decorators import login_required
from functools import wraps
import decimal  # 新增：导入decimal模块处理金额

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
    """
    商品搜索：【拆分缓存优化版】
    1. 缓存：商品基础信息（所有客户共用，命中率极高）
    2. 实时查：客户专属价（仅当前8个商品，超快）
    """
    keyword = request.GET.get('keyword', '').strip()
    customer_id = request.GET.get('customer_id', '').strip()

    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # ===================== 1. 全局商品缓存（所有客户共用） =====================
    # 缓存键：仅关键词，无客户ID → 全系统共用
    cache_key = f"product_base_search_{keyword}"
    # 尝试读取缓存
    cached_products = cache.get(cache_key)

    if cached_products is None:
        # 缓存未命中 → 查询数据库（只查1次，全客户共用）
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

        # 合并、去重、取前8条
        all_products = (product_matches | alias_products).distinct()[:8]

        # 构建【商品基础数据】（仅固定信息，无价格）
        cached_products = []
        for p in all_products:
            cached_products.append({
                'id': p.id,
                'name': p.name,
                'standard_price': float(p.price),  # 原价
                'unit': p.unit,
                'stock': p.stock
            })

        # 存入缓存：30秒（商品基础信息不常变，可加长）
        cache.set(cache_key, cached_products, timeout=30)

    # ===================== 2. 实时查询客户专属价（轻量查询） =====================
    customer_prices = {}
    if customer_id:
        # 只查【当前8个商品】的专属价 → 极快，无性能压力
        product_ids = [item['id'] for item in cached_products]
        cp_list = CustomerPrice.objects.filter(
            customer_id=customer_id,
            product_id__in=product_ids
        )
        # 转成字典：{商品ID: 专属价}
        customer_prices = {cp.product_id: float(cp.custom_price) for cp in cp_list}

    # ===================== 3. 合并数据（返回前端） =====================
    data = []
    for item in cached_products:
        product_id = item['id']
        # 优先用客户专属价，没有就用原价
        final_price = customer_prices.get(product_id, item['standard_price'])
        data.append({
            'id': product_id,
            'name': item['name'],
            'price': final_price,  # 最终售价
            'standard_price': item['standard_price'],  # 原价
            'unit': item['unit'],
            'stock': item['stock']
        })

    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CREATE)
def save_order(request):
    """保存订单（高性能优化版：批量操作 + 事务 + 无N+1）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '请求方式错误'})

    try:
        # 事务包裹：所有操作原子性，失败自动回滚
        with transaction.atomic():
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_id = data.get('customer_id', '')
            original_order_no = data.get('original_order_no', '')

            if not items:
                return JsonResponse({'code': 0, 'msg': '无订单明细'})

            # ===================== 1. 基础数据校验（一次性完成） =====================
            product_ids = []
            qty_map = {}
            for item in items:
                pid = item.get('id')
                qty = item.get('qty', 0)
                if not pid or not isinstance(qty, int) or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': f'商品{item.get("name", "未知")}数量无效'})
                product_ids.append(pid)
                qty_map[pid] = qty

            # ===================== 2. 批量查询商品：1次查询替代N次（解决N+1） =====================
            products = Product.objects.filter(id__in=product_ids).in_bulk()
            for pid in product_ids:
                if pid not in products:
                    return JsonResponse({'code': 0, 'msg': f'商品ID {pid} 不存在'})
                if products[pid].stock < qty_map[pid]:
                    return JsonResponse({'code': 0, 'msg': f'{products[pid].name}库存不足（当前：{products[pid].stock}）'})

            # ===================== 3. 创建订单主表 =====================
            order = Order()
            order.creator = request.user

            # 客户校验
            if customer_id:
                customer = get_object_or_404(Customer, id=customer_id)
                order.customer = customer
                order.area = customer.area

            # 重开订单校验
            if original_order_no:
                original_order = get_object_or_404(Order, order_no=original_order_no)
                if original_order.status != 'cancelled':
                    return JsonResponse({'code': 0, 'msg': '仅作废订单可重开'})
                order.original_order = original_order
                order.status = 'reopened'

            # 计算总金额
            total_amount = 0
            order_items = []
            for pid in product_ids:
                product = products[pid]
                qty = qty_map[pid]
                amount = product.price * qty
                total_amount += amount
                # 构建明细对象（不保存）
                order_items.append(OrderItem(
                    order=order,
                    product=product,
                    quantity=qty,
                    amount=amount
                ))

            order.total_amount = total_amount
            order.save()  # 仅1次保存

            # ===================== 4. 批量创建订单明细：1次写入替代N次 =====================
            OrderItem.objects.bulk_create(order_items)

            # ===================== 5. 批量更新库存：1次更新替代N次 =====================
            for pid in product_ids:
                products[pid].stock -= qty_map[pid]
            Product.objects.bulk_update(products.values(), ['stock'])

            # ===================== 6. 操作日志 =====================
            customer_name = order.customer.name if order.customer else '无'
            create_operation_log(
                request=request,
                op_type='create_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"创建订单{order.order_no}，客户：{customer_name}，总金额：{order.total_amount}元，商品数量：{len(items)}个"
            )

            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})

    except Exception as e:
        # 事务自动回滚，无需手动delete，数据绝对安全
        return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})

@login_required
@permission_required(PERM_ORDER_VIEW)
def stock_list(request):
    """库存查询页面（分页优化版 - 每页20条 + 后端搜索）"""
    # 获取搜索关键词 + 分页参数
    keyword = request.GET.get('keyword', '').strip()
    page = request.GET.get('page', 1)

    # 基础查询：按商品名称排序
    products = Product.objects.all().order_by('name')

    # 后端搜索筛选（匹配名称/拼音首字母）
    if keyword:
        products = products.filter(
            Q(name__icontains=keyword) |
            Q(pinyin_abbr__icontains=keyword)
        )

    # 核心：分页逻辑（固定每页20条）
    paginator = Paginator(products, 10)
    try:
        page_products = paginator.page(page)
    except PageNotAnInteger:
        page_products = paginator.page(1)
    except EmptyPage:
        page_products = paginator.page(paginator.num_pages)

    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    return render(request, 'bill/stock.html', {
        'products': page_products,  # 分页后的商品
        'paginator': paginator,  # 分页器
        'page_products': page_products,  # 分页对象
        'keyword': keyword,  # 搜索关键词（回显）
        'is_super_admin': is_super_admin
    })


@login_required
@permission_required(PERM_ORDER_VIEW)
def order_list(request):
    """订单列表页（分页优化版 - 每页20条）"""
    # 接收筛选参数
    order_no = request.GET.get('order_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    area_id = request.GET.get('area_id', '')
    customer_name = request.GET.get('customer_name', '').strip()
    settled_status = request.GET.get('settled_status', '')
    amount_operator = request.GET.get('amount_operator', '')
    amount_value = request.GET.get('amount_value', '').strip()
    # 分页参数：当前页码
    page = request.GET.get('page', 1)

    # 基础查询 + 关联预加载
    orders = Order.objects.select_related('area', 'customer', 'creator').order_by('-create_time')

    # 权限控制：仅看自己的订单
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    can_view_others = request.user.has_permission('order_view_others')
    if not is_super_admin and not can_view_others:
        orders = orders.filter(creator=request.user)

    # 角色标识
    is_admin = request.user.role and request.user.role.code == ROLE_ADMIN
    is_operator = request.user.role and request.user.role.code == ROLE_OPERATOR

    # 筛选逻辑（完全不变）
    if order_no:
        orders = orders.filter(order_no__contains=order_no)
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
    if area_id and area_id.isdigit():
        orders = orders.filter(area_id=area_id)
    if customer_name:
        orders = orders.filter(customer__name__icontains=customer_name)
    if settled_status == 'settled':
        orders = orders.filter(is_settled=True)
    elif settled_status == 'unsettled':
        orders = orders.filter(is_settled=False)
    if amount_operator in ['gt', 'lt'] and amount_value:
        try:
            amount = decimal.Decimal(amount_value)
            if amount_operator == 'gt':
                orders = orders.filter(total_amount__gt=amount)
            elif amount_operator == 'lt':
                orders = orders.filter(total_amount__lt=amount)
        except decimal.InvalidOperation:
            pass

    # ===================== 核心：分页逻辑（每页20条） =====================
    paginator = Paginator(orders, 15)  # 每页固定20条
    try:
        page_orders = paginator.page(page)
    except PageNotAnInteger:
        page_orders = paginator.page(1)  # 页码非数字，返回第一页
    except EmptyPage:
        page_orders = paginator.page(paginator.num_pages)  # 页码超出范围，返回最后一页

    # ===================== 统计数据（不变，基于筛选后总数据） =====================
    total_orders = orders.count()
    total_sales = orders.aggregate(total=Sum('total_amount'))['total'] or decimal.Decimal('0.00')
    settled_orders = orders.filter(is_settled=True).count()
    total_debt = orders.filter(is_settled=False).aggregate(total=Sum('total_amount'))['total'] or decimal.Decimal('0.00')

    # ===================== 作废权限计算（仅循环20条，性能拉满） =====================
    current_time = datetime.now()
    order_list = list(page_orders)  # 仅转20条数据为列表
    for order in order_list:
        time_diff = (current_time - order.create_time).total_seconds() / 60
        order.time_diff = time_diff
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

    # 区域数据
    areas = Area.objects.all().order_by('name')

    # 模板上下文（新增分页对象）
    context = {
        'orders': order_list,          # 分页后的20条订单
        'page_orders': page_orders,    # 分页对象（前端渲染页码用）
        'paginator': paginator,        # 分页器
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
        'total_debt': total_debt
    }
    return render(request, 'bill/order_list.html', context)


@login_required
@permission_required(PERM_ORDER_VIEW)
def order_detail(request, order_no):
    """订单详情页（性能优化版：无N+1、索引生效、缓存权限）"""
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
            if (order.creator == request.user and can_cancel_own) or (order.creator != request.user and can_cancel_others):
                show_cancel_btn = True
        elif is_operator:
            if order.creator == request.user and can_cancel_own and time_diff <= 5:
                show_cancel_btn = True

    # 🔥 优化4：使用已优化的明细数据（模板必须用这个，禁止用order.items.all）
    items = OrderItem.objects.select_related('product').filter(order=order)

    context = {
        'order': order,
        'items': items,  # 模板必须用这个变量
        'is_super_admin': is_super_admin,
        'time_diff': time_diff,
        'can_cancel_own': can_cancel_own,
        'can_cancel_others': can_cancel_others,
        'is_admin': is_admin,
        'is_operator': is_operator,
        'show_cancel_btn': show_cancel_btn
    }
    return render(request, 'bill/order_detail.html', context)

@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def search_customer(request):
    """客户搜索 + 缓存优化"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 🔥 缓存键：根据关键词生成
    cache_key = f"customer_search_{keyword}"
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

    # 🔥 缓存10秒，大幅减少数据库压力
    cache.set(cache_key, data, timeout=10)
    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CANCEL_OWN)
def cancel_order(request, order_no):
    """作废订单（新增时间锁/状态锁/角色权限控制）"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, order_no=order_no)
            current_time = datetime.now()
            time_diff = (current_time - order.create_time).total_seconds() / 60  # 分钟差

            # ========== 1. 状态锁：已收款/已出库/已作废 → 禁止作废 ==========
            if order.is_settled:
                return JsonResponse({'code': 0, 'msg': '已收款的订单无法作废'})
            if order.status == 'printed':
                return JsonResponse({'code': 0, 'msg': '已出库的订单无法作废'})
            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '该订单已作废，无需重复操作'})

            # ========== 2. 角色权限控制 ==========
            is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
            is_admin = request.user.role and request.user.role.code == ROLE_ADMIN
            is_operator = request.user.role and request.user.role.code == ROLE_OPERATOR

            if is_super_admin:
                pass  # 超级管理员无限制
            elif is_admin:
                # 管理员作废他人订单需权限
                if order.creator != request.user and not request.user.has_permission('order_cancel_others'):
                    return JsonResponse({'code': 0, 'msg': '无作废他人订单的权限'})
            elif is_operator:
                # 普通店员：仅作废自己的+3~5分钟内
                if order.creator != request.user:
                    return JsonResponse({'code': 0, 'msg': '普通店员仅能作废自己创建的订单'})
                if time_diff > 5:
                    return JsonResponse({'code': 0, 'msg': f'仅支持开单后5分钟内作废，当前已过{time_diff:.1f}分钟'})
            else:
                return JsonResponse({'code': 0, 'msg': '无作废订单的权限'})

            # ========== 3. 强制留痕：作废原因至少1个字 ==========
            data = json.loads(request.body)
            reason = data.get('reason', '').strip()
            if len(reason) < 1:
                return JsonResponse({'code': 0, 'msg': '作废原因至少填写1个字'})

            # ========== 4. 执行作废操作 ==========
            order.status = 'cancelled'
            order.cancelled_by = request.user
            order.cancelled_time = current_time
            order.cancelled_reason = reason
            order.save()

            # 恢复库存
            item_count = 0
            for item in order.items.all():
                if item.product:
                    item.product.stock += item.quantity
                    item.product.save()
                    item_count += 1

            # 日志留痕
            create_operation_log(
                request=request,
                op_type='cancel_order',
                obj_type='order',
                obj_id=str(order.id),
                obj_name=f"订单-{order.order_no}",
                detail=f"作废订单{order.order_no}，操作人角色：{request.user.role.name if request.user.role else '未知'}，原因：{reason}，恢复{item_count}个商品库存，开单后{time_diff:.1f}分钟作废"
            )

            return JsonResponse({'code': 1, 'msg': '订单作废成功', 'order_no': order_no})

        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'作废失败：{str(e)}'})

    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

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
@permission_required(PERM_ORDER_REOPEN)
def reopen_order_edit(request, order_no):
    """重开订单编辑页面【性能优化版】"""
    # 🔥 优化1：提前判断状态，减少无效代码执行
    # 🔥 优化2：预加载 customer/area，彻底消除 N+1 查询（1次查询搞定所有关联数据）
    original_order = get_object_or_404(
        Order.objects.select_related('customer', 'area'),  # 核心：预加载关联对象
        order_no=order_no
    )

    # 非作废订单直接重定向，终止后续逻辑
    if original_order.status != 'cancelled':
        return redirect('bill:order_detail', order_no=order_no)

    # 权限判断（仅有效场景执行）
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    # 🔥 原有优化保留：订单明细+商品 关联查询（无N+1）
    items = OrderItem.objects.select_related('product').filter(order=original_order)

    # 🔥 优化3：规范 Decimal 处理，避免精度丢失
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

    # 🔥 优化4：干掉全表查询！只查前端需要的字段（id+name）
    # 🔥 优化5：添加缓存（低频数据，缓存10分钟）
    CACHE_TIMEOUT = 600
    customers = cache.get('all_customers_simple')
    if not customers:
        customers = list(Customer.objects.values_list('id', 'name').order_by('name'))
        cache.set('all_customers_simple', customers, CACHE_TIMEOUT)

    areas = cache.get('all_areas_simple')
    if not areas:
        areas = list(Area.objects.values_list('id', 'name').order_by('name'))
        cache.set('all_areas_simple', areas, CACHE_TIMEOUT)

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
    """标记订单结清（性能优化版）"""
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

        return JsonResponse({'code': 1, 'msg': '订单标记结清成功', 'order_no': order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'标记结清失败：{str(e)}'}, status=500)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_UNSETTLE)
def unsettle_order(request, order_no):
    """撤销订单结清（性能优化版）"""
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

        return JsonResponse({'code': 1, 'msg': '撤销订单结清成功', 'order_no': order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'撤销结清失败：{str(e)}'}, status=500)


@ajax_login_required
@ajax_permission_required(PERM_ORDER_SETTLE)
def batch_settle_order(request):
    """批量标记订单结清（高性能优化版：无N+1 + 批量更新 + 事务）"""
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
        update_orders = []  # 待批量更新的订单
        current_time = timezone.now()

        # ===================== 优化1：提取所有订单号 + 基础校验 =====================
        order_no_map = {}  # {订单号: 备注}
        for item in order_list:
            # ✅ 修复核心：强制转字符串，再strip，解决int报错
            order_no = str(item.get('order_no', '')).strip()
            remark = str(item.get('remark', '')).strip()

            if not order_no or not remark:
                fail_list.append(f'{order_no or "未知订单"}：备注不能为空')
                continue
            order_no_map[order_no] = remark

        if not order_no_map:
            return JsonResponse({'code': 0, 'msg': '无有效订单数据'}, status=400)

        # ===================== 优化2：1次批量查询所有订单（彻底干掉N+1） =====================
        valid_orders = Order.objects.filter(order_no__in=order_no_map.keys())
        valid_order_nos = {o.order_no for o in valid_orders}

        # 遍历校验状态
        for order in valid_orders:
            remark = order_no_map[order.order_no]

            # 状态过滤
            if order.status == 'cancelled':
                fail_list.append(f'{order.order_no}：作废订单无法结清')
                continue
            if order.is_settled:
                fail_list.append(f'{order.order_no}：已结清，无需重复操作')
                continue

            # 赋值（不保存）
            order.is_settled = True
            order.settled_by = request.user
            order.settled_time = current_time
            order.settled_remark = remark
            update_orders.append(order)

        # 统计不存在的订单
        for order_no in order_no_map.keys():
            if order_no not in valid_order_nos:
                fail_list.append(f'{order_no}：订单不存在')

        # ===================== 优化3：批量更新（1次数据库操作） =====================
        if update_orders:
            with transaction.atomic():  # 事务保证原子性
                Order.objects.bulk_update(
                    update_orders,
                    fields=['is_settled', 'settled_by', 'settled_time', 'settled_remark']
                )
                # 批量记录日志（日志无法批量，只能循环，无性能影响）
                for order in update_orders:
                    create_operation_log(
                        request=request, op_type='batch_settle_order', obj_type='order',
                        obj_id=str(order.id), obj_name=f"订单-{order.order_no}",
                        detail=f"批量结清订单{order.order_no}，备注：{order.settled_remark}"
                    )
            success_count = len(update_orders)

        # 返回结果
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


@login_required
@permission_required(PERM_PRODUCT_SEARCH)
def get_customer_recent_products(request):
    """获取客户最近购买的商品（按购买时间倒序）【性能优化版】"""
    customer_id = request.GET.get('customer_id', '').strip()
    if not customer_id:
        return JsonResponse({'code': 0, 'msg': '请选择客户', 'data': []})

    try:
        # ============== 优化1：一次性查询订单 + 预加载所有明细（消除N+1） ==============
        customer_orders = Order.objects.filter(
            customer_id=customer_id,
            status__in=['pending', 'printed', 'reopened'],
        ).order_by('-create_time')[:50].prefetch_related('items__product')  # 预加载明细+商品

        # ============== 优化2：一次性查询该客户所有专属价格（消除循环查询） ==============
        customer_prices = {}
        price_qs = CustomerPrice.objects.filter(customer_id=customer_id).values('product_id', 'custom_price')
        for item in price_qs:
            customer_prices[item['product_id']] = float(item['custom_price'])

        product_dict = {}
        # 遍历预加载的数据，无数据库查询
        for order in customer_orders:
            # 直接获取预加载的明细，不查库
            for item in order.items.all():
                product = item.product
                if not product:
                    continue
                if product.id not in product_dict:
                    # 直接从缓存取价格
                    final_price = customer_prices.get(product.id, float(product.price))
                    product_dict[product.id] = {
                        'id': product.id,
                        'name': product.name,
                        'price': final_price,
                        'standard_price': float(product.price),
                        'unit': product.unit,
                        'last_purchase_time': order.create_time.strftime('%Y-%m-%d %H:%M'),
                        'last_quantity': item.quantity
                    }

        # 排序
        recent_products = sorted(
            product_dict.values(),
            key=lambda x: x['last_purchase_time'],
            reverse=True
        )

        return JsonResponse({'code': 1, 'data': recent_products})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'获取失败：{str(e)}', 'data': []})