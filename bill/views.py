# ========== 先导入所有必要模块（统一开头，避免重复） ==========
from django.db import transaction
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse

from django.utils import timezone
from .models import Order, OrderItem
from product.models import Product, ProductAlias, ProductTag
from customer_manage.models import Customer, CustomerPrice
from area_manage.models import Area

from django.db.models import Q, Sum, Count, Case, When, DecimalField
import json
from datetime import datetime, timedelta
from django.contrib.auth.decorators import login_required
from functools import wraps
import decimal

from django.conf import settings
from django.core.cache import cache

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

# ========== 导入用户模块的RBAC核心组件 ==========
from accounts.models import ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR, PERM_ORDER_CANCEL_OWN
from accounts.views import (
    permission_required,  # RBAC权限装饰器
    create_operation_log,  # 统一日志记录
    get_client_ip  # 获取客户端IP
)
from product.models import Unit
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

def get_sort_context():
    """返回包含排序规则和商品标签映射的 context 字典，带缓存"""
    # 排序规则
    sort_stages_json = cache.get('sort_stages_json')
    if sort_stages_json is None:
        rules = SortRule.objects.select_related('tag').order_by('stage', 'priority')
        stages_dict = {}
        for r in rules:
            if r.stage not in stages_dict:
                stages_dict[r.stage] = []
            item = {
                'type': r.rule_type,
                'priority': r.priority,
            }
            if r.rule_type == 'tag':
                item['tag_id'] = r.tag_id
                item['tag_name'] = r.tag.name
            else:
                item['spec_condition'] = r.spec_condition
            stages_dict[r.stage].append(item)
        stages = [{'stage': s, 'rules': stages_dict[s]} for s in sorted(stages_dict.keys())]
        sort_stages_json = json.dumps(stages)
        cache.set('sort_stages_json', sort_stages_json, 3600)

    # 商品标签映射
    product_tags_map_json = cache.get('product_tags_map_json')
    if product_tags_map_json is None:
        products = Product.objects.filter(is_active=True).prefetch_related('tags')
        tags_map = {}
        for p in products:
            tag_ids = list(p.tags.filter(is_active=True).values_list('id', flat=True))
            if tag_ids:
                tags_map[str(p.id)] = tag_ids
        product_tags_map_json = json.dumps(tags_map)
        cache.set('product_tags_map_json', product_tags_map_json, 3600)

    return {
        'sort_stages_json': sort_stages_json,
        'product_tags_map_json': product_tags_map_json,
    }

@login_required
@permission_required(PERM_ORDER_CREATE)
def index(request):
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    context = {
        'is_super_admin': is_super_admin,
    }
    context.update(get_sort_context())   # 注入排序数据

    return render(request, 'bill/index.html', context)


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

        all_products = (product_matches | alias_products).distinct()[:200]

        cached_products = []
        for p in all_products:
            cached_products.append({
                'id': p.id,
                'name': p.name,
                'standard_price': float(p.price),
                'unit': p.unit,
                'stock_system': p.stock_system,
                # 👇 新增规格字段
                'specification': p.specification
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
            'stock_system': item['stock_system'],
            'specification': item['specification']
        })

    return JsonResponse({'code': 1, 'data': data})


@login_required
@permission_required(PERM_ORDER_CREATE)
def save_order(request):
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

            # ---------- 1. 分离有效商品ID和临时商品 ----------
            valid_product_ids = []  # 需要校验、扣库存的商品ID
            item_data_list = []  # 存储每个明细的详细数据

            for item in items:
                pid = item.get('id', '').strip()  # 前端可能传空字符串
                name = item.get('name', '').strip()
                spec = item.get('spec', '').strip()
                unit = item.get('unit', '').strip()
                qty = item.get('qty', 0)
                price = item.get('price', 0)
                customer_name = data.get('customer_name', '').strip()

                if not name:
                    return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
                if not isinstance(qty, int) or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': f'商品{name}数量无效'})

                product = None
                product_id_int = None

                # 处理有效ID
                if pid:
                    try:
                        product_id_int = int(pid)
                        valid_product_ids.append(product_id_int)
                    except (ValueError, TypeError):
                        return JsonResponse({'code': 0, 'msg': f'商品{name}ID格式错误'})

                item_data_list.append({
                    'pid': product_id_int,
                    'name': name,
                    'spec': spec,
                    'unit': unit,
                    'qty': qty,
                    'price': decimal.Decimal(str(price)),
                })

            # ---------- 2. 批量查询有效商品 ----------
            products_map = {}
            if valid_product_ids:
                products_map = Product.objects.filter(id__in=valid_product_ids).in_bulk()
                for pid in valid_product_ids:
                    if pid not in products_map:
                        return JsonResponse({'code': 0, 'msg': f'商品ID {pid} 不存在'})

            # ---------- 3. 查询客户专属价（仅对有效商品） ----------
            customer_prices_dict = {}
            if customer_id and valid_product_ids:
                cp_list = CustomerPrice.objects.filter(
                    customer_id=customer_id, product_id__in=valid_product_ids
                )
                customer_prices_dict = {cp.product_id: cp.custom_price for cp in cp_list}

            # ---------- 4. 创建订单主表 ----------
            order = Order()
            order.creator = request.user
            order.customer_name_snapshot = customer_name
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
            # 需要更新库存的商品列表（仅有效商品）
            update_stock_products = []

            for item_data in item_data_list:
                pid = item_data['pid']
                name = item_data['name']
                spec = item_data['spec']
                qty = item_data['qty']
                input_price = item_data['price']
                amount = input_price * qty
                total_amount += amount

                if pid is not None:
                    # ---- 关联商品 ----
                    product = products_map[pid]
                    # 价格快照
                    snap_standard = product.price
                    snap_customer = customer_prices_dict.get(pid, None)
                    # 库存扣减准备
                    product.stock_system -= qty
                    update_stock_products.append(product)
                    # 创建明细
                    order_items.append(OrderItem(
                        order=order,
                        product=product,
                        product_name=product.name,  # 同步保存名称，方便统一处理
                        unit=item_data['unit'],
                        specification=spec,
                        quantity=qty,
                        amount=amount,
                        actual_unit_price=input_price,
                        snapshot_standard_price=snap_standard,
                        snapshot_customer_price=snap_customer,
                    ))
                else:
                    # ---- 临时商品（自由开单） ----
                    order_items.append(OrderItem(
                        order=order,
                        product=None,  # 不关联任何 Product
                        product_name=name,  # 保存手动输入的名称
                        unit=item_data['unit'],
                        specification=spec,
                        quantity=qty,
                        amount=amount,
                        actual_unit_price=input_price,
                        snapshot_standard_price=None,
                        snapshot_customer_price=None,
                    ))

            # ---------- 5. 保存订单和明细 ----------
            order.total_amount = total_amount
            order.save()

            OrderItem.objects.bulk_create(order_items)

            # ---------- 6. 批量更新库存（仅有效商品） ----------
            if update_stock_products:
                Product.objects.bulk_update(update_stock_products, ['stock_system'])

            # ---------- 7. 日志与缓存清理 ----------
            create_operation_log(request, 'create_order', 'order', str(order.id),
                                 f"订单-{order.order_no}", "创建订单")
            clear_stock_cache()
            clear_order_cache()
            if customer_id:
                clear_customer_related_cache(int(customer_id))

            return JsonResponse({'code': 1, 'msg': '开单成功', 'order_no': order.order_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'开单失败：{str(e)}'})

from django.db import transaction
from .models import SortRule, ProductTag
import json
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.shortcuts import render

@login_required
@permission_required(PERM_ORDER_CREATE)
def sort_rule_setting(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            stages = data.get('stages', [])   # [{stage:1, rules:[...]}, ...]
            with transaction.atomic():
                SortRule.objects.all().delete()
                for stage_info in stages:
                    stage_num = stage_info['stage']
                    for rule in stage_info.get('rules', []):
                        SortRule.objects.create(
                            stage=stage_num,
                            rule_type=rule['type'],
                            tag_id=rule.get('tag_id') if rule['type'] == 'tag' else None,
                            spec_condition=rule.get('spec_condition') if rule['type'] == 'spec' else None,
                            priority=rule['priority']
                        )
            return JsonResponse({'code': 1, 'msg': '规则保存成功'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'保存失败：{str(e)}'})

    # GET：返回按阶段分组的数据
    rules_qs = SortRule.objects.select_related('tag').order_by('stage', 'priority')
    stages_dict = {}
    for r in rules_qs:
        if r.stage not in stages_dict:
            stages_dict[r.stage] = []
        stages_dict[r.stage].append({
            'type': r.rule_type,
            'priority': r.priority,
            'tag_id': r.tag_id,
            'tag_name': r.tag.name if r.tag else '',
            'spec_condition': r.spec_condition,
        })

    stages_data = []
    for stage_num in sorted(stages_dict.keys()):
        stages_data.append({
            'stage': stage_num,
            'rules': stages_dict[stage_num]
        })

    # 如果没有阶段，给一个默认空阶段供界面展示
    if not stages_data:
        stages_data.append({'stage': 1, 'rules': []})

    tags_data = [{'id': t.id, 'name': t.name} for t in ProductTag.objects.filter(is_active=True)]
    return render(request, 'bill/sort_rule_setting.html', {
        'stages_json': json.dumps(stages_data),
        'tags_json': json.dumps(tags_data),
    })


@login_required
@permission_required(PERM_ORDER_CREATE)
def get_sort_rules(request):
    """供开单页调用的排序规则 API，返回阶段分组数组"""
    rules = SortRule.objects.select_related('tag').order_by('stage', 'priority')
    stages_dict = {}
    for r in rules:
        if r.stage not in stages_dict:
            stages_dict[r.stage] = []
        item = {
            'type': r.rule_type,
            'priority': r.priority,
        }
        if r.rule_type == 'tag':
            item['tag_id'] = r.tag_id
            item['tag_name'] = r.tag.name
        else:
            item['spec_condition'] = r.spec_condition
        stages_dict[r.stage].append(item)

    stages = []
    for stage_num in sorted(stages_dict.keys()):
        stages.append({
            'stage': stage_num,
            'rules': stages_dict[stage_num]
        })
    return JsonResponse({'code': 1, 'data': stages})

# views.py
@login_required
@permission_required(PERM_PRODUCT_SEARCH)  # 或合适的权限
def get_all_product_tags(request):
    # 只返回启用且有标签的商品
    products = Product.objects.filter(is_active=True).prefetch_related('tags')
    data = {}
    for p in products:
        tag_ids = list(p.tags.filter(is_active=True).values_list('id', flat=True))
        if tag_ids:
            data[str(p.id)] = tag_ids
    return JsonResponse({'code': 1, 'data': data})

@login_required
@permission_required(PERM_ORDER_VIEW)
def order_list(request):
    """订单列表页（新增Tab状态筛选版 + 财务进度展示）"""
    order_no = request.GET.get('order_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    area_id = request.GET.get('area_id', '')
    customer_name = request.GET.get('customer_name', '').strip()
    amount_operator = request.GET.get('amount_operator', '')
    amount_value = request.GET.get('amount_value', '').strip()
    status = request.GET.get('status', 'all')  # 🔥 新增：状态筛选参数
    page = request.GET.get('page', 1)

    # 🔥 手动缓存 Key：新增 status 参数
    cache_key = f"{CACHE_PREFIX_ORDER_LIST}{request.user.id}_{order_no}_{date_from}_{date_to}_{area_id}_{customer_name}_{amount_operator}_{amount_value}_{status}_{page}"
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

    # 🔥 新增：状态筛选（核心Tab逻辑，包含已结清/未结清）
    base_orders = orders  # 保存基础查询集用于统计
    if status == 'normal':
        orders = orders.filter(status__in=ORDER_STATUS_VALID)
    elif status == 'cancelled':
        orders = orders.filter(status='cancelled')
    elif status == 'settled':
        # 🔥 已结清：只看有效订单且 is_settled=True
        orders = orders.filter(is_settled=True, status__in=ORDER_STATUS_VALID)
    elif status == 'unsettled':
        # 🔥 未结清：只看有效订单且 is_settled=False
        orders = orders.filter(is_settled=False, status__in=ORDER_STATUS_VALID)

    # 🔥 优化：Tab数量统计（一次聚合查询替代三次count查询）
    counts = base_orders.aggregate(
        count_all=Count('id'),
        count_normal=Count(Case(When(status__in=ORDER_STATUS_VALID, then='id'))),
        count_cancelled=Count(Case(When(status='cancelled', then='id'))),
        # 🔥 新增：统计已结清/未结清数量（仅统计有效订单）
        count_settled=Count(Case(When(status__in=ORDER_STATUS_VALID, is_settled=True, then='id'))),
        count_unsettled=Count(Case(When(status__in=ORDER_STATUS_VALID, is_settled=False, then='id')))
    )
    count_all = counts['count_all']
    count_normal = counts['count_normal']
    count_cancelled = counts['count_cancelled']
    count_settled = counts['count_settled']  # 🔥 新增
    count_unsettled = counts['count_unsettled']  # 🔥 新增

    # 原有筛选逻辑
    if order_no:
        orders = orders.filter(order_no__startswith=order_no)

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
        if order.status != 'cancelled' and not order.is_settled:  # 移除 and order.status != 'printed'
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
        'count_settled': count_settled,  # 🔥 新增
        'count_unsettled': count_unsettled,  # 🔥 新增
    }
    # 构建基础查询字符串（去掉 page 参数，保留其他筛选条件）
    query_params = request.GET.copy()
    if 'page' in query_params:
        del query_params['page']
    base_query_string = query_params.urlencode()

    # 生成省略式页码范围
    def get_page_range(page, num_pages, surrounding=2):
        """返回包含省略号的页码列表"""
        if num_pages <= 7:
            return list(range(1, num_pages + 1))
        pages = [1]
        if page.number - surrounding > 2:
            pages.append('...')
        start = max(2, page.number - surrounding)
        end = min(num_pages - 1, page.number + surrounding)
        pages.extend(range(start, end + 1))
        if page.number + surrounding < num_pages - 1:
            pages.append('...')
        pages.append(num_pages)
        return pages

    page_range_display = get_page_range(page_orders, paginator.num_pages)

    context.update({
        'base_query_string': base_query_string,
        'page_range_display': page_range_display,
    })
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
    """
    客户搜索（支持名称/区域/拼音全拼/拼音首字母） + 手动缓存优化
    性能优化点：
    1. select_related('area') 避免N+1查询
    2. distinct() 避免关联区域可能产生的重复行
    3. 仅取前8条，降低传输和渲染开销
    4. 手动缓存10秒，缓解数据库压力
    """
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 缓存命中直接返回
    cache_key = f"{CACHE_PREFIX_CUSTOMER_SEARCH}{keyword}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse({'code': 1, 'data': cached_data})

    # 组合模糊查询：客户名、区域名、全拼、首字母
    customers = Customer.objects.select_related('area').filter(
        Q(name__icontains=keyword) |
        Q(area__name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword)
    ).distinct()[:50]   # 控制返回数量

    data = []
    for c in customers:
        area_name = c.area.name if c.area else '无区域'
        data.append({
            'id': c.id,
            'full_name': f"{area_name} | {c.name}",
            # 如需返回电话，可取消下面注释：
            # 'phone': c.primary_phone,
        })

    # 写入缓存
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

            # ===================== 1. 获取角色（提前） =====================
            user_role_code = request.user.role.code if request.user.role else None
            is_super_admin = user_role_code == ROLE_SUPER_ADMIN
            is_admin = user_role_code == ROLE_ADMIN
            is_operator = user_role_code == ROLE_OPERATOR

            # ===================== 2. 状态锁校验（超级管理员可越过部分限制） =====================
            if order.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '该订单已作废，无需重复操作'}, status=400)

            if not is_super_admin:
                if order.is_settled:
                    return JsonResponse({'code': 0, 'msg': '已收款的订单无法作废'}, status=400)
                if order.status == 'printed':
                    return JsonResponse({'code': 0, 'msg': '已出库的订单无法作废'}, status=400)

            # ===================== 3. 后续权限判断（保持不变） =====================
            if not is_super_admin:
                if is_admin:
                    if order.creator != request.user and not request.user.has_permission('order_cancel_others'):
                        return JsonResponse({'code': 0, 'msg': '无作废他人订单的权限'}, status=403)
                elif is_operator:
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
    cache_key = f"{CACHE_PREFIX_PRINT_ORDER}{order_no}"
    cached_data = cache.get(cache_key)

    if cached_data:
        return HttpResponse(cached_data)

    # 预加载订单及关联数据
    order = get_object_or_404(
        Order.objects.select_related('customer', 'area', 'creator'),
        order_no=order_no
    )
    items = order.items.select_related('product')

    # ===== 核心：构建固定15行的商品列表 =====
    # 最多取前15个真实商品，剩余用 None 补齐
    items_display = list(items[:18])
    items_display.extend([None] * (18 - len(items_display)))

    # RBAC权限判断
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    context = {
        'order': order,
        'items_display': items_display,   # 使用固定长度的列表
        'is_super_admin': is_super_admin,
        'phone_numbers': settings.PHONE_NUMBERS,
        'complaint_phone': settings.COMPLAINT_PHONE,
        'bill_title': settings.BILL_TITLE,
    }

    response = render(request, 'bill/print.html', context)
    cache.set(cache_key, response.content, CACHE_PRINT_ORDER)

    return response


@login_required
@permission_required(PERM_ORDER_REOPEN)
def reopen_order_edit(request, order_no):
    original_order = get_object_or_404(
        Order.objects.select_related('customer', 'area'),
        order_no=order_no
    )
    if original_order.status != 'cancelled':
        return redirect('bill:order_detail', order_no=order_no)

    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    items = OrderItem.objects.select_related('product').filter(order=original_order)

    order_data = {
        'order_no': original_order.order_no,
        'customer_id': original_order.customer_id if original_order.customer else '',
        'customer_name': original_order.customer_name_snapshot or (
            f"{original_order.area.name} | {original_order.customer.name}"
            if original_order.customer and original_order.area else ''
        ),
        'items': [
            {
                'id': item.product_id if item.product else '',
                'name': item.product_name or (item.product.name if item.product else ''),
                'qty': item.quantity,
                'unit': item.unit,
                'price': float(item.actual_unit_price) if item.actual_unit_price else 0,
                'amt': float(item.amount) if item.amount else 0,
                'spec': item.specification
            }
            for item in items
        ]
    }

    context = {
        'is_super_admin': is_super_admin,
        'reopen_order_data': order_data,
    }
    context.update(get_sort_context())   # 注入排序数据

    return render(request, 'bill/index.html', context)


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
    customer_id = request.GET.get('customer_id', '').strip()
    if not customer_id:
        return JsonResponse({'code': 0, 'msg': '请选择客户', 'data': []})

    cache_key = f"{CACHE_PREFIX_CUSTOMER_RECENT_PRODUCT}{customer_id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse({'code': 1, 'data': cached_data})

    try:
        # 获取最近有效订单ID（取10个，覆盖足够的最近购买记录）
        recent_order_ids = list(
            Order.objects.filter(
                customer_id=customer_id,
                status__in=ORDER_STATUS_VALID,
                is_settled=False
            )
            .order_by('-create_time')
            .values_list('id', flat=True)[:10]
        )

        if not recent_order_ids:
            cache.set(cache_key, [], timeout=CACHE_CUSTOMER_RECENT_PRODUCT)
            return JsonResponse({'code': 1, 'data': []})

        order_items = OrderItem.objects.filter(
            order_id__in=recent_order_ids
        ).select_related('product', 'order').order_by('-order__create_time')

        # 分别处理有product的商品与自由开单商品
        product_dict = {}          # key: product.id
        free_product_dict = {}     # key: "free_产品名|规格|单位|价格"

        for item in order_items:
            if item.product:
                product = item.product
                if product.id in product_dict:
                    continue

                # ----- 价格快照逻辑 -----
                # 1. 优先使用开单时的实际单价（成交价快照）
                if item.actual_unit_price is not None:
                    final_price = float(item.actual_unit_price)
                # 2. 若无实际单价，尝试客户价快照
                elif item.snapshot_customer_price is not None:
                    final_price = float(item.snapshot_customer_price)
                # 3. 再尝试标准价快照
                elif item.snapshot_standard_price is not None:
                    final_price = float(item.snapshot_standard_price)
                # 4. 兜底：使用商品当前标准价
                else:
                    final_price = float(product.price)

                # 规格：优先使用订单明细中的规格快照
                specification = item.specification or product.specification or ''

                product_dict[product.id] = {
                    'id': product.id,
                    'name': product.name,
                    'price': final_price,                    # 成交价快照
                    'standard_price': float(product.price),  # 当前标准价（用于对比）
                    'unit': product.unit,
                    'last_purchase_time': item.order.create_time.strftime('%Y-%m-%d %H:%M'),
                    'last_quantity': item.quantity,
                    'specification': specification,
                }
            else:
                # 自由开单商品：使用当时的成交价（原有逻辑不变）
                name = item.product_name or ''
                spec = item.specification or ''
                unit = item.unit or ''
                price = float(item.actual_unit_price) if item.actual_unit_price else 0
                free_key = f"free_{name}|{spec}|{unit}|{price}"
                if free_key in free_product_dict:
                    continue
                free_product_dict[free_key] = {
                    'id': None,
                    'name': name,
                    'price': price,
                    'standard_price': price,
                    'unit': unit,
                    'last_purchase_time': item.order.create_time.strftime('%Y-%m-%d %H:%M'),
                    'last_quantity': item.quantity,
                    'specification': spec,
                }

        # 组装结果
        recent_products = list(product_dict.values())
        free_offset = 0
        for free_data in free_product_dict.values():
            free_offset += 1
            free_data['id'] = -100000 - free_offset
            recent_products.append(free_data)

        # 缓存
        cache.set(cache_key, recent_products, timeout=CACHE_CUSTOMER_RECENT_PRODUCT)
        logger.info(
            f"设置客户最近商品缓存: {cache_key} "
            f"(含{len(product_dict)}个系统商品, {len(free_product_dict)}个自由商品)"
        )

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

import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from django.http import HttpResponse
from django.db.models import Prefetch
from urllib.parse import quote
@login_required
@permission_required(PERM_ORDER_VIEW)
def export_orders(request):
    """导出当前筛选条件下的订单 Excel（模板可复用）"""
    # 复用 order_list 的查询逻辑，但跳过缓存和分页
    # 下面代码提取与 order_list 相同的过滤条件（可根据实际封装公共函数）
    order_no = request.GET.get('order_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    area_id = request.GET.get('area_id', '')
    customer_name = request.GET.get('customer_name', '').strip()
    amount_operator = request.GET.get('amount_operator', '')
    amount_value = request.GET.get('amount_value', '').strip()
    status = request.GET.get('status', 'all')

    # 权限控制（与 order_list 一致）
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    can_view_others = request.user.has_permission('order_view_others')
    orders = Order.objects.select_related('area').order_by('-create_time')
    if not is_super_admin and not can_view_others:
        orders = orders.filter(creator=request.user)

    # 应用状态筛选
    if status == 'normal':
        orders = orders.filter(status__in=ORDER_STATUS_VALID)
    elif status == 'cancelled':
        orders = orders.filter(status='cancelled')
    elif status == 'settled':
        orders = orders.filter(is_settled=True, status__in=ORDER_STATUS_VALID)
    elif status == 'unsettled':
        orders = orders.filter(is_settled=False, status__in=ORDER_STATUS_VALID)

    # 应用其他筛选
    if order_no:
        orders = orders.filter(order_no__startswith=order_no)
    if area_id and area_id.isdigit():
        orders = orders.filter(area_id=int(area_id))
    if customer_name:
        orders = orders.filter(customer__name__istartswith=customer_name)
    if date_from:
        try:
            start = timezone.make_aware(datetime.strptime(date_from, '%Y-%m-%d'))
            orders = orders.filter(create_time__gte=start)
        except:
            pass
    if date_to:
        try:
            end = datetime.strptime(date_to, '%Y-%m-%d').date()
            end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), datetime.min.time()))
            orders = orders.filter(create_time__lt=end_dt)
        except:
            pass
    if amount_operator in ['gt', 'lt'] and amount_value:
        try:
            amount = decimal.Decimal(amount_value)
            if amount_operator == 'gt':
                orders = orders.filter(total_amount__gt=amount)
            else:
                orders = orders.filter(total_amount__lt=amount)
        except:
            pass

    # 预加载订单明细及关联商品，防止N+1
    orders = orders.prefetch_related(
        Prefetch('items', queryset=OrderItem.objects.select_related('product'))
    )

    # 创建 Excel 工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "订单数据"

    # 表头
    headers = ['订单编号', '客户名称', '区域', '商品名称', '规格', '单位', '数量', '单价', '小计金额', '订单状态']
    header_font = Font(bold=True)
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num, value=header)
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    row = 2
    for order in orders:
        area_name = order.area.name if order.area else ''
        customer_name_snap = order.customer_name_snapshot or ''
        # 避免 Excel 将长数字转为科学计数法，订单编号强制存为文本
        order_no_text = order.order_no
        for item in order.items.all():
            ws.cell(row=row, column=1, value=order_no_text)
            ws.cell(row=row, column=2, value=customer_name_snap)
            ws.cell(row=row, column=3, value=area_name)
            ws.cell(row=row, column=4, value=item.product_name)
            ws.cell(row=row, column=5, value=item.specification)
            ws.cell(row=row, column=6, value=item.unit)
            ws.cell(row=row, column=7, value=item.quantity)
            # 单价取实际单价，若无则取0
            price = float(item.actual_unit_price) if item.actual_unit_price else 0.0
            ws.cell(row=row, column=8, value=price)
            # 小计金额使用快照金额
            amt = float(item.amount) if item.amount else 0.0
            ws.cell(row=row, column=9, value=amt)
            ws.cell(row=row, column=10, value=order.status)
            row += 1

    # 设置响应为 Excel 下载
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    # 生成带日期的中文文件名，例如：订单导出20260713.xlsx
    now_str = timezone.now().strftime('%Y%m%d')
    filename = f'订单导出{now_str}.xlsx'
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename)}"
    wb.save(response)
    return response



from openpyxl import load_workbook
from collections import defaultdict
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pypinyin import lazy_pinyin

@login_required
@permission_required(PERM_ORDER_CREATE)
def import_orders(request):
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST'})

    excel_file = request.FILES.get('file')
    if not excel_file:
        return JsonResponse({'code': 0, 'msg': '请上传文件'})

    try:
        wb = load_workbook(excel_file, read_only=True)
        ws = wb.active
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'文件解析失败：{str(e)}'})

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    if not rows:
        return JsonResponse({'code': 0, 'msg': 'Excel 无数据'})

    order_groups = defaultdict(list)
    area_names = set()
    product_names = set()
    pure_customer_names = set()
    pure_name_area = {}
    product_create_info = {}

    def parse_customer_name(raw_name, given_area):
        if given_area:
            prefix = given_area + " | "
            if raw_name.startswith(prefix):
                pure = raw_name[len(prefix):].strip()
            else:
                pure = raw_name
            return given_area, pure
        else:
            if " | " in raw_name:
                parts = raw_name.split(" | ", 1)
                extracted_area = parts[0].strip()
                pure = parts[1].strip()
                return extracted_area, pure
            else:
                return "", raw_name

    for row in rows:
        if len(row) < 9:
            continue
        order_no = str(row[0]).strip() if row[0] else ''
        raw_customer_name = str(row[1]).strip() if row[1] else ''
        area_name = str(row[2]).strip() if row[2] else ''
        prod_name = str(row[3]).strip() if row[3] else ''
        spec = str(row[4]).strip() if row[4] else ''
        unit = str(row[5]).strip() if row[5] else ''
        try:
            qty = int(row[6])
        except:
            continue
        try:
            price = Decimal(str(row[7]))
        except:
            price = Decimal('0')
        status = str(row[9]).strip() if len(row) > 9 and row[9] else 'pending'

        final_area, pure_customer_name = parse_customer_name(raw_customer_name, area_name)

        order_key = (order_no, raw_customer_name, area_name)
        order_groups[order_key].append({
            'product_name': prod_name,
            'spec': spec,
            'unit': unit,
            'qty': qty,
            'price': price,
            'status': status,
            'pure_customer_name': pure_customer_name,
            'area_name': final_area,
        })

        # 仅有效订单才收集需要创建的区域、客户、商品信息
        if status != 'cancelled':
            if final_area:
                area_names.add(final_area)
            if pure_customer_name:
                pure_customer_names.add(pure_customer_name)
                if pure_customer_name not in pure_name_area:
                    pure_name_area[pure_customer_name] = final_area
            product_names.add(prod_name)
            product_key = (prod_name, unit)
            if product_key not in product_create_info:
                product_create_info[product_key] = {
                    'spec': spec,
                    'price': price,
                }

    # ========== 1. 批量查询/创建区域 ==========
    area_map = {}
    if area_names:
        existing_areas = Area.objects.filter(name__in=area_names)
        area_map = {a.name: a for a in existing_areas}
        missing_areas = area_names - set(area_map.keys())
        if missing_areas:
            new_areas = [Area(name=name) for name in missing_areas if name]
            if new_areas:
                Area.objects.bulk_create(new_areas)
                fresh_areas = Area.objects.filter(name__in=[a.name for a in new_areas])
                for area in fresh_areas:
                    area_map[area.name] = area

    # ========== 2. 批量查询/创建商品（带拼音）==========
    existing_products = Product.objects.filter(name__in=product_names) if product_names else []
    product_map = {(p.name, p.unit): p for p in existing_products}

    missing_product_keys = set(product_create_info.keys()) - set(product_map.keys())
    if missing_product_keys:
        new_products = []
        for pname, punit in missing_product_keys:
            info = product_create_info[(pname, punit)]
            pinyin_full = ''.join(lazy_pinyin(pname, style=0))
            pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(pname, style=0)])
            new_products.append(Product(
                name=pname,
                unit=punit,
                specification=info['spec'],
                price=info['price'],
                stock_system=0,
                stock_actual=0,
                pinyin_full=pinyin_full,
                pinyin_abbr=pinyin_abbr,
            ))
        if new_products:
            created = Product.objects.bulk_create(new_products)
            q_filter = Q()
            for p in created:
                q_filter |= Q(name=p.name, unit=p.unit)
            if q_filter:
                fresh_products = Product.objects.filter(q_filter)
                for p in fresh_products:
                    product_map[(p.name, p.unit)] = p

    # ========== 3. 批量查询/创建客户（带拼音）==========
    customer_map = {}
    if pure_customer_names:
        existing_customers = Customer.objects.filter(name__in=pure_customer_names)
        customer_map = {c.name: c for c in existing_customers}
        missing_names = pure_customer_names - set(customer_map.keys())

        if missing_names:
            new_customers = []
            for pure_name in missing_names:
                area_name_for_customer = pure_name_area.get(pure_name)
                area = area_map.get(area_name_for_customer) if area_name_for_customer else None
                pinyin_full = ''.join(lazy_pinyin(pure_name, style=0))
                pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(pure_name, style=0)])
                new_customers.append(Customer(
                    name=pure_name,
                    area=area,
                    pinyin_full=pinyin_full,
                    pinyin_abbr=pinyin_abbr,
                ))
            try:
                Customer.objects.bulk_create(new_customers, ignore_conflicts=True)
                for c in Customer.objects.filter(name__in=missing_names):
                    customer_map[c.name] = c
            except Exception:
                for c_obj in new_customers:
                    obj, created = Customer.objects.get_or_create(
                        name=c_obj.name,
                        defaults={
                            'area': c_obj.area,
                            'pinyin_full': c_obj.pinyin_full,
                            'pinyin_abbr': c_obj.pinyin_abbr,
                        }
                    )
                    customer_map[obj.name] = obj

    # ========== 4. 已存在订单编号检查 ==========
    existing_orders = set(
        Order.objects.filter(order_no__in=[k[0] for k in order_groups if k[0]])
        .values_list('order_no', flat=True)
    )

    success_count = 0
    skip_count = 0

    # ========== 5. 事务中创建订单和订单明细 ==========
    with transaction.atomic():
        for (order_no, raw_customer_name, area_name), items in order_groups.items():
            if order_no and order_no in existing_orders:
                skip_count += 1
                continue

            status = items[0]['status']

            # 作废订单：强制不关联任何区域、客户、商品
            if status == 'cancelled':
                area = None
                customer = None
            else:
                final_area_name = items[0]['area_name']
                area = area_map.get(final_area_name) if final_area_name else None
                pure_customer_name = items[0]['pure_customer_name']
                customer = customer_map.get(pure_customer_name) if pure_customer_name else None

            order = Order(
                order_no=order_no if order_no else '',
                customer_name_snapshot=raw_customer_name,
                area=area,
                customer=customer,
                creator=request.user,
                total_amount=0,
                status=status,
            )
            order.save()

            if status == 'cancelled':
                order.cancelled_by = request.user
                order.cancelled_time = timezone.now()
                order.cancelled_reason = '从 Excel 导入（原作废订单）'
                order.save(update_fields=['cancelled_by', 'cancelled_time', 'cancelled_reason'])

            total = Decimal('0')
            order_items = []
            for item_data in items:
                prod_name = item_data['product_name']
                spec = item_data['spec']
                unit = item_data['unit']
                price = item_data['price']
                qty = item_data['qty']
                amount = price * qty
                total += amount

                # 作废订单明细不关联商品
                product = None if status == 'cancelled' else product_map.get((prod_name, unit))

                order_items.append(OrderItem(
                    order=order,
                    product=product,
                    product_name=prod_name if not product else product.name,
                    specification=spec,
                    unit=unit,
                    quantity=qty,
                    amount=amount,
                    actual_unit_price=price,
                    snapshot_standard_price=product.price if product else None,
                    snapshot_customer_price=None,
                ))

            OrderItem.objects.bulk_create(order_items)
            order.total_amount = total
            order.save(update_fields=['total_amount'])
            success_count += 1

    clear_order_cache()

    msg = f'导入完成：成功 {success_count} 个订单'
    if skip_count:
        msg += f'，跳过 {skip_count} 个重复订单'
    return JsonResponse({'code': 1, 'msg': msg})

@login_required
@permission_required(PERM_ORDER_PRINT)
def mark_order_printed(request, order_no):
    """标记订单为已打印（仅在窗口打印后由前端调用）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, status=405)

    order = get_object_or_404(Order, order_no=order_no)

    if order.status == 'pending':
        order.status = 'printed'
        order.save(update_fields=['status'])

        # 清理相关缓存
        clear_order_cache(order_no)

        # 记录操作日志
        create_operation_log(
            request,
            'mark_printed', 'order', str(order.id),
            f"订单-{order_no}", "打印后标记为已打印"
        )
        return JsonResponse({'code': 1, 'msg': '订单已标记为已打印'})

    elif order.status == 'printed':
        return JsonResponse({'code': 1, 'msg': '订单已是已打印状态'})

    else:
        # 作废、重开等状态不允许标记
        return JsonResponse({'code': 0, 'msg': f'订单状态为{order.status}，无法标记已打印'})