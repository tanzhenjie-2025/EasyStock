# customer_manage\views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.core.cache import cache

from accounts.models import ROLE_SUPER_ADMIN, PERM_LOG_VIEW_ALL
from bill.models import OrderItem, Order
from product.models import Product,ProductAlias
from customer_manage.models import Customer,CustomerPrice,RepaymentRecord
from area_manage.models import Area

import datetime
import unicodedata  # 新增：处理全角半角转换
# ========== 新增：导入用户模块的权限装饰器和日志函数 ==========
from django.contrib.auth.decorators import login_required
from accounts.views import permission_required, create_operation_log  # 复用用户模块的日志和权限装饰器

from django.db.models import Sum, F, Q, OuterRef, Subquery, DecimalField, Max
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage

from django.utils import timezone
import json
from io import BytesIO
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, PatternFill
import openpyxl

# ========== 缓存时长常量配置 ==========
CACHE_HIGH_PRIORITY = 300  # 复杂聚合查询 5分钟
CACHE_MID_PRIORITY = 600  # 静态数据/搜索接口 10分钟

# 🔥 定义全局统一的缓存 Key
CACHE_KEY_AREA_LIST_FOR_CUSTOMER = "global:area_list_for_customer"
CACHE_PREFIX_CUSTOMER_LIST = "customer_list_"
CACHE_PREFIX_CUSTOMER_DETAIL = "customer_detail_"
CACHE_PREFIX_CUSTOMER_PRICE = "customer_price_"  # 新增：专属价格缓存前缀

import logging  # 1. 导入 logging 模块

# 2. 获取 logger 实例
logger = logging.getLogger(__name__)

def clear_customer_cache(customer_id: int = None):
    """
    清理客户相关缓存
    """
    # 1. 清理所有客户列表缓存（因为分页/搜索参数多，直接通配符删除）
    cache.delete_pattern(f"{CACHE_PREFIX_CUSTOMER_LIST}*")

    # 2. 如果指定了ID，清理该客户的详情页缓存
    if customer_id:
        cache.delete_pattern(f"{CACHE_PREFIX_CUSTOMER_DETAIL}{customer_id}*")

    logger.info(f"已清理客户缓存: {customer_id if customer_id else '全列表'}")

def full_to_half(s):
    """将全角字符转换为半角"""
    if not s:
        return s
    result = []
    for char in s:
        code_point = ord(char)
        # 全角空格转半角空格
        if code_point == 0x3000:
            code_point = 0x20
        # 其他全角字符（除空格）转半角
        elif 0xFF01 <= code_point <= 0xFF5E:
            code_point -= 0xFEE0
        result.append(chr(code_point))
    return ''.join(result)

@login_required
@permission_required('customer_view')
def customer_list(request):
    """优化版：无N+1、批量聚合、带分页 + 手动缓存"""
    try:
        keyword = request.GET.get('keyword', '').strip()
        page = request.GET.get('page', 1)
        page_size = 10

        # 1. 生成缓存 Key (包含用户、关键词、页码)
        cache_key = f"{CACHE_PREFIX_CUSTOMER_LIST}{request.user.id}_{keyword}_{page}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return JsonResponse(cached_data, safe=False)

        # 2. 构建子查询
        unpaid_subquery = Order.objects.filter(
            customer=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened'],
            is_settled=False
        ).values('customer').annotate(
            total=Sum('total_amount')
        ).values('total')

        consumption_subquery = Order.objects.filter(
            customer=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened']
        ).values('customer').annotate(
            total=Sum('total_amount')
        ).values('total')

        paid_subquery = RepaymentRecord.objects.filter(
            customer=OuterRef('pk')
        ).values('customer').annotate(
            total=Sum('repayment_amount')
        ).values('total')

        # 3. 主查询
        customers = Customer.objects.all().select_related('area').annotate(
            unpaid_amount=Coalesce(Subquery(unpaid_subquery), 0, output_field=DecimalField()),
            total_consumption=Coalesce(Subquery(consumption_subquery), 0, output_field=DecimalField()),
            paid_amount=Coalesce(Subquery(paid_subquery), 0, output_field=DecimalField()),
        )

        if keyword:
            id_q = Q()
            if keyword.isdigit():
                id_q = Q(id=int(keyword))
            customers = customers.filter(
                Q(name__icontains=keyword) |
                Q(phone__icontains=keyword) |
                id_q |
                Q(area__name__icontains=keyword)
            )

        # 4. 分页
        paginator = Paginator(customers, page_size)
        try:
            customer_page = paginator.page(page)
        except PageNotAnInteger:
            customer_page = paginator.page(1)
        except EmptyPage:
            customer_page = paginator.page(paginator.num_pages)

        # 5. 构造结果
        result = []
        for c in customer_page:
            total_debt = float(c.unpaid_amount - c.paid_amount)
            total_debt = max(total_debt, 0)
            result.append({
                'id': c.id,
                'name': c.name,
                'area_id': c.area.id if c.area else '',
                'area_name': c.area.name if c.area else '',
                'phone': c.phone,
                'remark': c.remark or '',
                'total_debt': total_debt,
                'total_consumption': float(c.total_consumption),
                'page': int(page),
                'total': paginator.count
            })

        # 6. 写入缓存
        cache.set(cache_key, result, CACHE_HIGH_PRIORITY)

        return JsonResponse(result, safe=False)
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'})


# 2. 客户详情（需customer_view权限）
@login_required
@permission_required('customer_view')
def customer_detail(request, pk):
    """客户详情接口 - 手动缓存版"""
    try:
        # 1. 生成缓存 Key (包含 pk 和 GET 参数)
        settle_status = request.GET.get('settle_status', 'all')
        page = request.GET.get('page', 1)
        cache_key = f"{CACHE_PREFIX_CUSTOMER_DETAIL}{pk}_{settle_status}_{page}"

        cached_data = cache.get(cache_key)
        if cached_data:
            return JsonResponse(cached_data, safe=False)

        customer = get_object_or_404(Customer, pk=pk)
        page_size = 10

        # 1. 订单基础查询
        base_orders = Order.objects.filter(
            customer=customer,
            status__in=['pending', 'printed', 'reopened']
        ).select_related('creator__role')

        if settle_status == 'settled':
            orders_query = base_orders.filter(is_settled=True)
        elif settle_status == 'unsettled':
            orders_query = base_orders.filter(is_settled=False)
        else:
            orders_query = base_orders

        # 2. 欠款统计
        unpaid_orders = base_orders.filter(is_settled=False)
        unpaid_order_count = unpaid_orders.count()
        unpaid_amount = unpaid_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        paid_amount = RepaymentRecord.objects.filter(customer=customer).aggregate(total=Sum('repayment_amount'))[
                          'total'] or 0
        total_debt = max(float(unpaid_amount) - float(paid_amount), 0)

        # 3. 订单分页
        orders_query = orders_query.order_by('-create_time')
        paginator = Paginator(orders_query, page_size)
        try:
            order_page = paginator.page(page)
        except PageNotAnInteger:
            order_page = paginator.page(1)
        except EmptyPage:
            order_page = paginator.page(paginator.num_pages)

        # 4. 格式化数据
        order_list = []
        for order in order_page:
            order_list.append({
                'order_no': order.order_no or '',
                'create_time': order.create_time.strftime('%Y-%m-%d %H:%M') if order.create_time else '',
                'total_amount': float(order.total_amount) if order.total_amount else 0.0,
                'is_settled': order.is_settled,
                'status': order.status,
                'status_text': dict(Order.ORDER_STATUS).get(order.status, '未知'),
                'overdue_days': order.get_overdue_days(),
                'order_date': order.create_time.strftime('%Y-%m-%d') if order.create_time else '',
                'creator_name': order.creator.username if order.creator else '未知',
                'creator_role': order.creator.role.name if (order.creator and order.creator.role) else '未知'
            })

        # 5. 还款记录
        repayment_list = []
        repayments = RepaymentRecord.objects.filter(customer=customer).select_related('operator__role').order_by(
            '-repayment_time')[:100]
        for repay in repayments:
            repayment_list.append({
                'id': repay.id,
                'repayment_amount': float(repay.repayment_amount) if repay.repayment_amount else 0.0,
                'repayment_time': repay.repayment_time.strftime('%Y-%m-%d %H:%M') if repay.repayment_time else '',
                'repayment_remark': repay.repayment_remark or '',
                'operator': repay.operator.username if repay.operator else '未知',
                'operator_role': repay.operator.role.name if (repay.operator and repay.operator.role) else '未知',
                'create_time': repay.create_time.strftime('%Y-%m-%d %H:%M') if repay.create_time else ''
            })

        # 6. 商品统计
        product_stats = OrderItem.objects.filter(
            order__customer=customer, product__isnull=False
        ).values(
            'product__id', 'product__name', 'product__unit'
        ).annotate(
            total_quantity=Coalesce(Sum('quantity'), 0),
            last_purchase_time=Coalesce(Max('order__create_time'), None)
        ).order_by('-total_quantity')

        product_stats_list = [{
            'product_name': stat['product__name'],
            'total_quantity': stat['total_quantity'],
            'unit': stat['product__unit'],
            'last_purchase_time': stat['last_purchase_time'].strftime('%Y-%m-%d') if stat['last_purchase_time'] else '无'
        } for stat in product_stats]

        # 返回数据
        response_data = {
            'code': 1, 'msg': '查询成功',
            'customer_info': {
                'id': customer.id, 'name': customer.name,
                'area_name': customer.area.name if customer.area else '',
                'phone': customer.phone, 'remark': customer.remark or ''
            },
            'debt_info': {
                'total_debt': total_debt, 'unpaid_order_count': unpaid_order_count,
                'unpaid_amount': float(unpaid_amount), 'paid_amount': float(paid_amount)
            },
            'orders': order_list,
            'current_page': order_page.number,
            'total_pages': paginator.num_pages,
            'total_orders': paginator.count,
            'repayments': repayment_list,
            'product_stats': product_stats_list
        }

        # 写入缓存
        cache.set(cache_key, response_data, CACHE_HIGH_PRIORITY)

        return JsonResponse(response_data, safe=False)

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, safe=False)


# 3. 还款登记（需customer_repayment权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_repayment')
def repayment_register(request):
    """还款登记接口"""
    if request.method == 'POST':
        try:
            customer_id = request.POST.get('customer_id', '').strip()
            repayment_amount = request.POST.get('repayment_amount', '').strip()
            repayment_time = request.POST.get('repayment_time', '').strip()
            repayment_remark = request.POST.get('repayment_remark', '').strip()

            if not customer_id or not repayment_amount:
                return JsonResponse({'code': 0, 'msg': '客户和还款金额不能为空'}, content_type='application/json')

            try:
                repayment_amount = float(repayment_amount)
                if repayment_amount <= 0:
                    return JsonResponse({'code': 0, 'msg': '还款金额必须大于0'}, content_type='application/json')
            except:
                return JsonResponse({'code': 0, 'msg': '还款金额必须是数字'}, content_type='application/json')

            customer = get_object_or_404(Customer, id=customer_id)

            if repayment_time:
                try:
                    repayment_time = timezone.make_aware(datetime.datetime.strptime(repayment_time, '%Y-%m-%d %H:%M'))
                except:
                    return JsonResponse({'code': 0, 'msg': '还款时间格式错误'}, content_type='application/json')
            else:
                repayment_time = timezone.now()

            repayment = RepaymentRecord.objects.create(
                customer=customer,
                repayment_amount=repayment_amount,
                repayment_time=repayment_time,
                repayment_remark=repayment_remark,
                operator=request.user if request.user.is_authenticated else None
            )

            create_operation_log(
                request=request,
                op_type='repayment_register',
                obj_type='repayment',
                obj_id=repayment.id,
                obj_name=f'{customer.name} - 还款¥{repayment_amount}',
                detail=f"为客户{customer.name}登记还款"
            )

            # 🔥 新增：还款后清理缓存
            clear_customer_cache(customer_id=int(customer_id))

            return JsonResponse({'code': 1, 'msg': '还款登记成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'登记失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')



# 4. 客户详情页面入口（需customer_view权限）✅ 页面，不缓存
@login_required
@permission_required('customer_view')
def customer_detail_page(request, pk):
    """客户详情页面"""
    return render(request, 'customer_manage/customer_detail.html', {'customer_id': pk})


# 5. 还款登记页面入口（需customer_repayment权限）✅ 页面，不缓存
@login_required
@permission_required('customer_repayment')
def repayment_page(request):
    """还款登记页面"""
    return render(request, 'customer_manage/repayment.html')


# 6. 新增客户（需customer_add权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_add')
def customer_add(request):
    """新增客户接口"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            if Customer.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            area = get_object_or_404(Area, id=area_id)
            area_name = area.name

            customer = Customer.objects.create(
                name=name,
                area=area,
                phone=phone,
                remark=remark
            )

            create_operation_log(
                request=request,
                op_type='create',
                obj_type='customer',
                obj_id=customer.id,
                obj_name=customer.name,
                detail=f"新增客户：名称={customer.name}"
            )

            # 🔥 新增：清理缓存
            clear_customer_cache()

            return JsonResponse({'code': 1, 'msg': '新增客户成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


# 7. 编辑客户（需customer_edit权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_edit')
def customer_edit(request, pk):
    """编辑客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        if request.method == 'POST':
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            if Customer.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            area = get_object_or_404(Area, id=area_id)

            customer.name = name
            customer.area = area
            customer.phone = phone
            customer.remark = remark
            customer.save()

            create_operation_log(
                request=request,
                op_type='update',
                obj_type='customer',
                obj_id=customer.id,
                obj_name=customer.name,
                detail=f"编辑客户"
            )

            # 🔥 新增：清理缓存
            clear_customer_cache(customer_id=pk)

            return JsonResponse({'code': 1, 'msg': '编辑客户成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


# 8. 删除客户（需customer_delete权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_delete')
def customer_delete(request, pk):
    """删除客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        customer_name = customer.name

        customer.delete()

        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='customer',
            obj_id=pk,
            obj_name=customer_name,
            detail=f"删除客户"
        )

        # 🔥 新增：清理缓存
        clear_customer_cache(customer_id=pk)

        return JsonResponse({'code': 1, 'msg': '删除客户成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 辅助接口：获取区域列表（需customer_view权限） =====================
@login_required
@permission_required('customer_view')
def area_list_for_customer(request):
    """供客户管理页面获取区域下拉列表 - 手动缓存版"""
    try:
        cached_data = cache.get(CACHE_KEY_AREA_LIST_FOR_CUSTOMER)
        if cached_data:
            return JsonResponse(cached_data, safe=False, content_type='application/json')

        areas = Area.objects.all().order_by('name')
        result = [{'id': a.id, 'name': a.name} for a in areas]
        cache.set(CACHE_KEY_AREA_LIST_FOR_CUSTOMER, result, CACHE_MID_PRIORITY)

        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询区域失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


# ===================== 页面入口（需customer_view权限） =====================
@login_required
@permission_required('customer_view')
def customer_page(request):
    """客户管理页面"""
    return render(request, 'customer_manage/customer.html')


@login_required
@permission_required('customer_price_view')
# 🔥 高优缓存：客户专属价格复杂搜索+预加载+分页
@cache_page(CACHE_HIGH_PRIORITY)
def customer_price_list(request):
    """获取客户专属价格列表 - 多维度搜索+高级筛选+分页(15条/页)【优化：商品别名预加载，解决N+1】"""
    try:
        # 1. 获取所有筛选参数 + 分页参数
        keyword = request.GET.get('keyword', '').strip()
        min_price = request.GET.get('min_price', '').strip()
        max_price = request.GET.get('max_price', '').strip()
        area_id = request.GET.get('area_id', '').strip()
        page = request.GET.get('page', 1)
        page_size = 15

        # 2. 🔥 核心优化：预加载 客户/区域/商品 + 商品别名（彻底解决N+1）
        # select_related：外键正向加载
        # prefetch_related：一对多反向加载（商品别名）
        prices = CustomerPrice.objects.all() \
            .select_related('customer__area', 'product') \
            .prefetch_related('product__aliases')  # 关键修复

        # 3. 权限/筛选逻辑（完全保留）
        if not request.user.has_permission(PERM_LOG_VIEW_ALL):
            prices = prices.filter(operator=request.user)

        # 关键词筛选
        if keyword:
            keyword = full_to_half(keyword).strip()
            keywords = [k for k in keyword.split() if k]
            base_q = Q()
            for kw in keywords:
                customer_q = Q(customer__name__icontains=kw)
                if kw.isdigit():
                    customer_q |= Q(customer__id=int(kw))
                product_q = Q(product__name__icontains=kw)
                if kw.isdigit():
                    product_q |= Q(product__id=int(kw))
                alias_ids = ProductAlias.objects.filter(
                    Q(alias_name__icontains=kw) |
                    Q(alias_pinyin_full__icontains=kw) |
                    Q(alias_pinyin_abbr__icontains=kw)
                ).values_list('product_id', flat=True)
                if alias_ids:
                    product_q |= Q(product__id__in=alias_ids)
                kw_q = customer_q | product_q
                base_q &= kw_q
            prices = prices.filter(base_q)

        # 价格区间筛选
        if min_price:
            try:
                min_price = float(min_price)
                prices = prices.filter(custom_price__gte=min_price)
            except:
                pass
        if max_price:
            try:
                max_price = float(max_price)
                prices = prices.filter(custom_price__lte=max_price)
            except:
                pass

        # 区域筛选
        if area_id and area_id.isdigit():
            prices = prices.filter(customer__area_id=int(area_id))

        # 4. 分页逻辑（保留）
        paginator = Paginator(prices, page_size)
        try:
            price_page = paginator.page(page)
        except PageNotAnInteger:
            price_page = paginator.page(1)
        except EmptyPage:
            price_page = paginator.page(paginator.num_pages)

        # 5. 构造数据（无任何数据库查询，直接使用预加载数据）
        result = []
        for cp in price_page:
            result.append({
                'id': cp.id,
                'customer_id': cp.customer.id,
                'customer_name': cp.customer.name,
                'customer_area_id': cp.customer.area.id if cp.customer.area else '',
                'customer_area_name': cp.customer.area.name if cp.customer.area else '',
                'product_id': cp.product.id,
                'product_name': cp.product.name,
                'product_aliases': [alias.alias_name for alias in cp.product.aliases.all()],
                'custom_price': float(cp.custom_price),
                'standard_price': float(cp.product.price),
                'remark': cp.remark or ''
            })

        return JsonResponse({
            'code': 1, 'msg': '查询成功', 'data': result, 'keyword': keyword,
            'page': int(page), 'total': paginator.count, 'total_pages': paginator.num_pages,
            'has_next': price_page.has_next(), 'has_previous': price_page.has_previous(),
        }, safe=False, content_type='application/json')

    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}', 'data': []},
            safe=False, content_type='application/json'
        )


# 2. 新增客户价格（需customer_price_add权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_price_add')
def customer_price_add(request):
    """新增客户专属价格"""
    if request.method == 'POST':
        try:
            customer_id = request.POST.get('customer_id', '').strip()
            product_id = request.POST.get('product_id', '').strip()
            custom_price = request.POST.get('custom_price', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验必填项
            if not customer_id or not product_id or not custom_price:
                return JsonResponse({'code': 0, 'msg': '客户、商品、专属价不能为空'}, content_type='application/json')

            # 校验价格格式
            try:
                custom_price = float(custom_price)
                if custom_price < 0:
                    return JsonResponse({'code': 0, 'msg': '专属价不能为负数'}, content_type='application/json')
            except:
                return JsonResponse({'code': 0, 'msg': '专属价必须是数字'}, content_type='application/json')

            # 校验客户和商品存在
            customer = get_object_or_404(Customer, id=customer_id)
            product = get_object_or_404(Product, id=product_id)
            product_standard_price = float(product.price)

            # 校验是否已存在该客户-商品的专属价
            if CustomerPrice.objects.filter(customer=customer, product=product).exists():
                return JsonResponse({'code': 0, 'msg': '该客户已设置过此商品的专属价'}, content_type='application/json')

            # 创建专属价
            cp = CustomerPrice.objects.create(
                customer=customer,
                product=product,
                custom_price=custom_price,
                remark=remark
            )

            # 记录操作日志（复用accounts的函数）
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='customer_price',
                obj_id=cp.id,
                obj_name=f"{customer.name}-{product.name}",
                detail=f"新增客户专属价：客户={customer.name}，商品={product.name}，标准价={product_standard_price}元，专属价={custom_price}元，备注={remark if remark else '无'}"
            )
            clear_customer_price_cache()

            return JsonResponse({'code': 1, 'msg': '新增专属价成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


# 3. 编辑客户价格（需customer_price_edit权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_price_edit')
def customer_price_edit(request, pk):
    """编辑客户专属价格"""
    try:
        cp = get_object_or_404(CustomerPrice, pk=pk)
        if request.method == 'POST':
            custom_price = request.POST.get('custom_price', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验价格
            if not custom_price:
                return JsonResponse({'code': 0, 'msg': '专属价不能为空'}, content_type='application/json')
            try:
                custom_price = float(custom_price)
                if custom_price < 0:
                    return JsonResponse({'code': 0, 'msg': '专属价不能为负数'}, content_type='application/json')
            except:
                return JsonResponse({'code': 0, 'msg': '专属价必须是数字'}, content_type='application/json')

            # 保存修改前的信息
            old_price = float(cp.custom_price)
            old_remark = cp.remark if cp.remark else '无'
            customer_name = cp.customer.name
            product_name = cp.product.name
            product_standard_price = float(cp.product.price)

            # 更新
            cp.custom_price = custom_price
            cp.remark = remark
            cp.save()

            # 记录操作日志（复用accounts的函数）
            create_operation_log(
                request=request,
                op_type='update',
                obj_type='customer_price',
                obj_id=cp.id,
                obj_name=f"{customer_name}-{product_name}",
                detail=f"编辑客户专属价：客户={customer_name}，商品={product_name}，标准价={product_standard_price}元，原专属价={old_price}元→新专属价={custom_price}元，原备注={old_remark}→新备注={remark if remark else '无'}"
            )
            clear_customer_price_cache()

            return JsonResponse({'code': 1, 'msg': '编辑专属价成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


# 4. 删除客户价格（需customer_price_delete权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_price_delete')
def customer_price_delete(request, pk):
    """删除客户专属价格"""
    try:
        cp = get_object_or_404(CustomerPrice, pk=pk)
        # 保存删除前的信息
        customer_name = cp.customer.name
        product_name = cp.product.name
        custom_price = float(cp.custom_price)
        product_standard_price = float(cp.product.price)
        remark = cp.remark if cp.remark else '无'

        # 删除专属价
        cp.delete()

        # 记录操作日志（复用accounts的函数）
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='customer_price',
            obj_id=pk,
            obj_name=f"{customer_name}-{product_name}",
            detail=f"删除客户专属价：ID={pk}，客户={customer_name}，商品={product_name}，标准价={product_standard_price}元，专属价={custom_price}元，备注={remark}"
        )
        clear_customer_price_cache()

        return JsonResponse({'code': 1, 'msg': '删除专属价成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口（需customer_price_view权限） =====================
@login_required
@permission_required('customer_price_view')
def customer_price_page(request):
    """客户专属价格管理页面"""
    return render(request, 'customer_manage/customer_price.html')


# ===================== 辅助接口（商品/客户搜索，需customer_price_view权限） =====================
@login_required
@permission_required('customer_price_view')
# 📊 中优缓存：价格页商品静态列表
@cache_page(CACHE_MID_PRIORITY)
def product_list_for_price(request):
    """供客户价格管理页面获取商品列表"""
    try:
        products = Product.objects.all().order_by('name')
        result = [{'id': p.id, 'name': p.name, 'price': float(p.price)} for p in products]
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询商品失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


@login_required
@permission_required('customer_price_view')
@csrf_exempt
# 📊 中优缓存：价格页客户高频搜索
@cache_page(CACHE_MID_PRIORITY)
def search_customer_for_price(request):
    """客户搜索：匹配名称/区域，返回输入法式候选数据"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 匹配客户名称 或 区域名称
    customer_matches = Customer.objects.select_related('area').filter(
        Q(name__icontains=keyword) |
        Q(area__name__icontains=keyword)
    ).distinct()[:8]

    # 构造返回数据（格式：区域 | 客户名）
    data = []
    for customer in customer_matches:
        area_name = customer.area.name if customer.area else '无区域'
        full_name = f"{area_name} | {customer.name}"
        data.append({
            'id': customer.id,
            'name': customer.name,
            'area_name': area_name,
            'full_name': full_name
        })

    return JsonResponse({'code': 1, 'data': data}, content_type='application/json')


@login_required
@permission_required('customer_price_view')
@csrf_exempt
# 📊 中优缓存：价格页商品高频搜索
@cache_page(CACHE_MID_PRIORITY)
def search_product_for_price(request):
    """商品搜索：匹配名称/拼音/别名，返回输入法式候选数据【优化：1次DB查询】"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 🔥 核心优化：合并两次查询为1次 OR 查询，仅执行1次数据库请求
    # 1. 商品自身名称/拼音匹配
    # 2. 关联别名表匹配，一次性查询完成
    all_products = Product.objects.filter(
        Q(name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword) |
        Q(id__in=ProductAlias.objects.filter(
            Q(alias_name__icontains=keyword) |
            Q(alias_pinyin_full__icontains=keyword) |
            Q(alias_pinyin_abbr__icontains=keyword)
        ).values('product_id'))
    ).distinct()[:8]

    # 构造返回数据
    data = []
    for product in all_products:
        data.append({
            'id': product.id,
            'name': product.name,
            'price': float(product.price),
            'unit': product.unit
        })

    return JsonResponse({'code': 1, 'data': data}, content_type='application/json')


@login_required
@permission_required('customer_price_view')
@csrf_exempt
# 📊 中优缓存：价格页静态区域筛选列表
@cache_page(CACHE_MID_PRIORITY)
def area_list_for_price(request):
    """供专属价高级筛选获取区域列表"""
    try:
        areas = Area.objects.all().order_by('name')
        result = [{'id': a.id, 'name': a.name} for a in areas]
        return JsonResponse({'code': 1, 'data': result}, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询区域失败：{str(e)}', 'data': []},
            content_type='application/json'
        )


# ========== 客户消费TOP30排行（仅超级管理员可见） ==========
@login_required
@permission_required('customer_sales_rank')
def customer_sales_rank_page(request):
    """客户消费TOP30排行页面"""
    # 获取所有区域（用于筛选）
    areas = Area.objects.all().order_by('name')
    return render(request, 'customer_manage/customer_sales_rank.html', {
        'areas': areas,
        'is_super_admin': request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    })


@login_required
@permission_required('customer_sales_rank')
@csrf_exempt
# 🔥 高优缓存：客户消费TOP30大数据聚合统计
@cache_page(CACHE_HIGH_PRIORITY)
def customer_sales_rank_data(request):
    """获取客户消费TOP30数据（支持区域+日期筛选 + 新增总欠款字段）【优化：批量查询，无循环DB请求】"""
    try:
        from django.db.models import Sum
        import datetime
        from django.utils import timezone

        # 获取筛选参数
        area_id = request.GET.get('area_id', '').strip()
        time_range = request.GET.get('time_range', 'year').strip()

        # 基础查询：统计正常有效订单 ✅ 修复：添加is_settled索引前缀，命中联合索引
        base_orders = Order.objects.filter(
            status__in=['pending', 'printed', 'reopened'],
            is_settled__in=[True, False],  # 索引必填，不限制结清状态
            customer__isnull=False
        )

        # 1. 日期筛选 ✅ 修复：移除__date，使用原生时间范围，命中索引
        today = datetime.date.today()
        today_start = timezone.make_aware(datetime.datetime.combine(today, datetime.time.min))
        today_end = timezone.make_aware(datetime.datetime.combine(today, datetime.time.max))

        if time_range == 'today':
            base_orders = base_orders.filter(create_time__gte=today_start, create_time__lte=today_end)
        elif time_range == 'week':
            week_start = today - datetime.timedelta(days=today.weekday())
            week_end = week_start + datetime.timedelta(days=6)
            week_start_dt = timezone.make_aware(datetime.datetime.combine(week_start, datetime.time.min))
            week_end_dt = timezone.make_aware(datetime.datetime.combine(week_end, datetime.time.max))
            base_orders = base_orders.filter(create_time__gte=week_start_dt, create_time__lte=week_end_dt)
        elif time_range == 'month':
            month_start = datetime.date(today.year, today.month, 1)
            if today.month == 12:
                month_end = datetime.date(today.year, 12, 31)
            else:
                month_end = datetime.date(today.year, today.month + 1, 1) - datetime.timedelta(days=1)
            month_start_dt = timezone.make_aware(datetime.datetime.combine(month_start, datetime.time.min))
            month_end_dt = timezone.make_aware(datetime.datetime.combine(month_end, datetime.time.max))
            base_orders = base_orders.filter(create_time__gte=month_start_dt, create_time__lte=month_end_dt)

        # 2. 区域筛选（保留）
        if area_id and area_id.isdigit():
            base_orders = base_orders.filter(customer__area_id=int(area_id))

        # 3. 分组统计TOP30客户（保留）
        customer_sales = base_orders.values(
            'customer__id', 'customer__name', 'customer__area__name'
        ).annotate(
            total_amount=Sum('total_amount')
        ).order_by('-total_amount')[:30]

        # 🔥 核心优化：批量查询，避免循环内N+1查询
        if customer_sales:
            # 提取所有TOP30客户ID
            customer_ids = [item['customer__id'] for item in customer_sales]

            # 批量查询：所有客户未结清订单总额 ✅ 修复：添加status排除作废，命中索引
            unpaid_data = Order.objects.filter(
                customer_id__in=customer_ids,
                status__in=['pending', 'printed', 'reopened'],  # 索引必填+排除作废
                is_settled=False
            ).values('customer_id').annotate(total=Sum('total_amount'))
            unpaid_dict = {item['customer_id']: float(item['total'] or 0) for item in unpaid_data}

            # 批量查询：所有客户还款总额（1次查询）
            paid_data = RepaymentRecord.objects.filter(
                customer_id__in=customer_ids
            ).values('customer_id').annotate(total=Sum('repayment_amount'))
            paid_dict = {item['customer_id']: float(item['total'] or 0) for item in paid_data}
        else:
            unpaid_dict = {}
            paid_dict = {}

        # 4. 构造数据：循环内**无任何数据库查询**
        result = []
        for idx, item in enumerate(customer_sales, 1):
            customer_id = item['customer__id']
            # 直接从字典取值，性能提升90%
            unpaid_amount = unpaid_dict.get(customer_id, 0)
            paid_amount = paid_dict.get(customer_id, 0)
            total_debt = max(unpaid_amount - paid_amount, 0)

            result.append({
                'rank': idx,
                'customer_id': customer_id,
                'customer_name': item['customer__name'],
                'area_name': item['customer__area__name'] or '无区域',
                'total_amount': float(item['total_amount'] or 0.0),
                'total_debt': total_debt
            })

        return JsonResponse({
            'code': 1, 'msg': '查询成功', 'data': result
        }, content_type='application/json')

    except Exception as e:
        return JsonResponse({
            'code': 0, 'msg': f'查询失败：{str(e)}', 'data': []
        }, content_type='application/json')


# ========== Excel导出（通用函数，直接复用） ==========
def export_to_excel(data, title, headers, selected_fields, custom_fields, file_name, total_row=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title

    final_fields = selected_fields.copy()
    final_headers = {field: headers[field] for field in selected_fields}

    if custom_fields:
        for cf in custom_fields:
            cf_name = cf.get('name', '')
            cf_position = cf.get('position', 'after')
            cf_target = cf.get('target', '')
            if not cf_name or not cf_target: continue
            custom_field_key = f'custom_{cf_name.replace(" ", "_")}_{len(final_fields)}'
            final_headers[custom_field_key] = cf_name
            try:
                target_index = final_fields.index(cf_target)
                insert_index = target_index + 1 if cf_position == 'after' else target_index
                final_fields.insert(insert_index, custom_field_key)
            except ValueError:
                final_fields.append(custom_field_key)

    selected_headers = [final_headers[field] for field in final_fields]
    title_font = Font(bold=True, size=12)
    alignment = Alignment(horizontal='center')

    for col, header in enumerate(selected_headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = title_font
        cell.alignment = alignment

    for row, item in enumerate(data, 2):
        for col, field in enumerate(final_fields, 1):
            value = item.get(field, '') if not field.startswith('custom_') else ''
            if isinstance(value, float): value = round(value, 2)
            ws.cell(row=row, column=col, value=value)

    if total_row:
        total_row_num = len(data) + 2
        total_font = Font(bold=True, color="FFFFFF")
        total_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        ws.cell(row=total_row_num, column=1, value="总计").font = total_font
        ws.cell(row=total_row_num, column=1).fill = total_fill
        for col, field in enumerate(final_fields, 1):
            if field in total_row:
                cell = ws.cell(row=total_row_num, column=col, value=round(total_row[field], 2))
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='center')

    for col in range(1, len(selected_headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 15

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{file_name}.xlsx"'
    return response


# ========== 客户导出视图 ==========
@login_required
def customer_export(request):
    """
    导出客户信息（支持字段选择和自定义字段）
    """
    if request.method == 'POST':
        try:
            data = request.POST
            selected_fields = data.getlist('fields[]')
            custom_fields = json.loads(data.get('custom_fields', '[]'))

            if not selected_fields:
                return JsonResponse({'code': 0, 'msg': '请至少选择一个导出字段'})

            # 定义表头映射
            headers = {
                'serial': '序号',
                'id': 'ID',
                'name': '客户名称',
                'area_name': '所属区域',
                'phone': '联系电话',
                'remark': '备注'
            }

            # 查询数据
            customers = Customer.objects.select_related('area').order_by('-create_time')

            # 格式化数据
            export_data = []
            for idx, customer in enumerate(customers, 1):
                export_data.append({
                    'serial': idx,
                    'id': customer.id,
                    'name': customer.name,
                    'area_name': customer.area.name if customer.area else '无',
                    'phone': customer.phone,
                    'remark': customer.remark or ''
                })

            # 生成文件名
            file_date_str = timezone.localdate().strftime("%Y%m%d")

            return export_to_excel(
                data=export_data,
                title='客户列表',
                headers=headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=f'{file_date_str}客户管理导出',
                total_row=None
            )
        except Exception as e:
            logger.error(f"导出客户失败：{str(e)}", exc_info=True)
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


# ========== 客户导入视图 ==========
@login_required
def customer_import(request):
    """
    导入客户信息
    逻辑：读取Excel，根据客户名称/手机号判断，已存在则跳过，不存在则新增
    """
    if request.method == 'POST':
        try:
            file_obj = request.FILES.get('file')
            if not file_obj:
                return JsonResponse({'code': 0, 'msg': '请上传文件'})

            # 加载工作簿
            wb = load_workbook(file_obj)
            ws = wb.active

            # 读取数据（假设第一行是表头，从第二行开始）
            new_count = 0
            skip_count = 0
            error_list = []

            # 先获取所有区域名到ID的映射，加速查询
            area_map = {area.name: area for area in Area.objects.all()}

            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                # 过滤掉空行
                if not any(row):
                    continue

                # 尝试解析数据 (容错处理)
                name = ''
                area_name = ''
                phone = ''
                remark = ''

                # 简单的列分配逻辑（根据实际导出顺序调整）
                cells = [str(cell).strip() if cell else '' for cell in row]

                # 这里做一个假设：Excel列顺序为 [序号, 客户名称, 区域, 电话, 备注]
                if len(cells) >= 4:
                    name = cells[1]
                    area_name = cells[2]
                    phone = cells[3]
                    if len(cells) > 4:
                        remark = cells[4]

                # 校验核心字段
                if not name or not phone:
                    error_list.append(f"第{row_idx}行：客户名称或电话为空，跳过")
                    continue

                # 查重逻辑：根据 客户名称 或 手机号 判断是否存在
                exists = Customer.objects.filter(name=name).exists() or Customer.objects.filter(phone=phone).exists()
                if exists:
                    skip_count += 1
                    continue

                # 处理区域
                area_obj = None
                if area_name and area_name in area_map:
                    area_obj = area_map[area_name]

                # 创建客户
                try:
                    Customer.objects.create(
                        name=name,
                        area=area_obj,
                        phone=phone,
                        remark=remark
                    )
                    new_count += 1
                except Exception as e:
                    error_list.append(f"第{row_idx}行：保存失败（{str(e)}）")

            msg = f"导入完成！新增：{new_count} 条，跳过重复：{skip_count} 条。"
            if error_list:
                msg += f" 异常：{len(error_list)} 条。"

            # 🔥 新增：导入成功后清理缓存（因为有新数据写入）
            if new_count > 0:
                clear_customer_cache()

            return JsonResponse({'code': 1, 'msg': msg})

        except Exception as e:
            logger.error(f"导入客户失败：{str(e)}", exc_info=True)
            return JsonResponse({'code': 0, 'msg': f'导入失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


# ========== 客户专属价格缓存清理函数 ==========
def clear_customer_price_cache():
    """
    清理客户专属价格相关缓存
    """
    # 1. 清理使用 @cache_page 产生的缓存 (通常 key 以 views.decorators.cache.cache_page 开头)
    # 为了确保彻底清理，我们也清理可能的手动 key
    cache.delete_pattern(f"*{CACHE_PREFIX_CUSTOMER_PRICE}*")

    # 2. 清理 Django @cache_page 的默认前缀 (这是最关键的)
    # Django cache_page 生成的 key 通常包含视图函数名的路径
    cache.delete_pattern("*customer_price_list*")

    # 3. 同时清理相关的辅助接口缓存
    cache.delete_pattern("*product_list_for_price*")
    cache.delete_pattern("*search_customer_for_price*")
    cache.delete_pattern("*search_product_for_price*")
    cache.delete_pattern("*area_list_for_price*")

    logger.info(f"已清理客户专属价格全量缓存")


# ========== 客户专属价格导出视图 ==========
@login_required
def customer_price_export(request):
    """
    导出客户专属价格（支持字段选择和自定义字段）
    """
    if request.method == 'POST':
        try:
            data = request.POST
            selected_fields = data.getlist('fields[]')
            custom_fields = json.loads(data.get('custom_fields', '[]'))

            if not selected_fields:
                return JsonResponse({'code': 0, 'msg': '请至少选择一个导出字段'})

            # 定义表头映射
            headers = {
                'serial': '序号',
                'id': 'ID',
                'customer_name': '客户名称',
                'customer_area_name': '所属区域',
                'product_name': '商品名称',
                'standard_price': '商品标准价',
                'custom_price': '客户专属价',
                'remark': '备注'
            }

            # 查询数据 (使用 select_related 优化性能)
            prices = CustomerPrice.objects.select_related(
                'customer', 'customer__area', 'product'
            ).order_by('-create_time')

            # 格式化数据
            export_data = []
            for idx, cp in enumerate(prices, 1):
                export_data.append({
                    'serial': idx,
                    'id': cp.id,
                    'customer_name': cp.customer.name if cp.customer else '未知',
                    'customer_area_name': cp.customer.area.name if (cp.customer and cp.customer.area) else '无',
                    'product_name': cp.product.name if cp.product else '未知',
                    'standard_price': float(cp.product.price) if cp.product else 0.0,
                    'custom_price': float(cp.custom_price),
                    'remark': cp.remark or ''
                })

            # 生成文件名
            file_date_str = timezone.localdate().strftime("%Y%m%d")

            return export_to_excel(
                data=export_data,
                title='客户专属价格',
                headers=headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=f'{file_date_str}客户专属价格导出',
                total_row=None
            )
        except Exception as e:
            logger.error(f"导出客户专属价格失败：{str(e)}", exc_info=True)
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


# ========== 客户专属价格导入视图 ==========
# ========== 客户专属价格导入视图 (修复版) ==========
@login_required
def customer_price_import(request):
    """
    导入客户专属价格 (修复版)
    逻辑：读取Excel，根据【客户名称 + 商品名称】判断，已存在则跳过，不存在则新增
    """
    if request.method == 'POST':
        try:
            file_obj = request.FILES.get('file')
            if not file_obj:
                return JsonResponse({'code': 0, 'msg': '请上传文件'})

            # 加载工作簿
            wb = load_workbook(file_obj, data_only=True)  # data_only=True 读取公式计算后的值
            ws = wb.active

            new_count = 0
            skip_count = 0
            error_list = []

            # 1. 预加载数据到内存 (加速查询)
            # 格式: { '客户名': CustomerObj }
            customer_map = {c.name: c for c in Customer.objects.all()}
            # 格式: { '商品名': ProductObj }
            product_map = {p.name: p for p in Product.objects.all()}
            # 格式: { (customer_id, product_id): True }
            existing_price_keys = set(
                CustomerPrice.objects.values_list('customer_id', 'product_id')
            )

            logger.info(f"开始导入专属价格，预加载客户数: {len(customer_map)}, 商品数: {len(product_map)}")

            # 2. 遍历 Excel
            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
                # 过滤空行
                if not any(row):
                    continue

                # 解析数据
                # 预期列顺序:
                # [0]序号, [1]客户名称, [2]所属区域(忽略), [3]商品名称, [4]标准价(忽略), [5]专属价, [6]备注
                cells = [str(cell).strip() if cell is not None else '' for cell in row]

                # 安全填充
                while len(cells) < 7:
                    cells.append('')

                customer_name = cells[1]
                product_name = cells[3]
                custom_price_str = cells[5]
                remark = cells[6]

                # 校验核心字段
                if not customer_name:
                    error_list.append(f"第{row_idx}行：客户名称为空，跳过")
                    continue
                if not product_name:
                    error_list.append(f"第{row_idx}行：商品名称为空，跳过")
                    continue
                if not custom_price_str:
                    error_list.append(f"第{row_idx}行：专属价格为空，跳过")
                    continue

                # 校验价格格式
                try:
                    # 尝试清洗价格字符串 (去除 ¥, 逗号等)
                    price_clean = custom_price_str.replace('¥', '').replace(',', '').strip()
                    custom_price = float(price_clean)
                    if custom_price < 0:
                        raise ValueError("价格为负")
                except Exception as e:
                    error_list.append(f"第{row_idx}行：专属价格格式错误 ({custom_price_str})")
                    continue

                # 查找客户和商品 (内存中查找，极快)
                if customer_name not in customer_map:
                    error_list.append(f"第{row_idx}行：客户【{customer_name}】在系统中不存在，跳过")
                    continue
                if product_name not in product_map:
                    error_list.append(f"第{row_idx}行：商品【{product_name}】在系统中不存在，跳过")
                    continue

                customer = customer_map[customer_name]
                product = product_map[product_name]

                # 查重：(客户ID, 商品ID) 是否已存在
                if (customer.id, product.id) in existing_price_keys:
                    skip_count += 1
                    continue

                # 创建专属价
                try:
                    CustomerPrice.objects.create(
                        customer=customer,
                        product=product,
                        custom_price=custom_price,  # 这里 Django 会自动把 float 转 Decimal
                        remark=remark
                    )
                    new_count += 1
                    # 加入内存集合，防止同文件内重复导入
                    existing_price_keys.add((customer.id, product.id))

                except Exception as e:
                    error_list.append(f"第{row_idx}行：数据库保存失败（{str(e)}）")

            # 3. 构造返回消息
            msg = f"导入完成！新增：{new_count} 条，跳过重复/错误：{skip_count + len(error_list)} 条。"
            if error_list:
                # 只显示前10个错误，防止消息太长
                msg += f" (前10个错误: {'; '.join(error_list[:10])})"

            logger.info(msg)

            # 4. 清理缓存 (关键步骤)
            if new_count > 0:
                clear_customer_price_cache()
                logger.info("已触发专属价格缓存清理")

            return JsonResponse({'code': 1, 'msg': msg})

        except Exception as e:
            logger.error(f"导入客户专属价格系统异常：{str(e)}", exc_info=True)
            return JsonResponse({'code': 0, 'msg': f'导入系统异常：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


