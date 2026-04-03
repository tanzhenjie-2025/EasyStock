# customer_manage\views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.core.cache import cache

from accounts.models import ROLE_SUPER_ADMIN, PERM_LOG_VIEW_ALL
from bill.models import OrderItem, Order
from product.models import Product,ProductAlias
from customer_manage.models import Customer,CustomerPrice,RepaymentRecord
from area_manage.models import Area


from django.db.models import Sum, F, Q, Max, Count
from django.db.models.functions import Coalesce
import datetime
import unicodedata  # 新增：处理全角半角转换
# ========== 新增：导入用户模块的权限装饰器和日志函数 ==========
from django.contrib.auth.decorators import login_required
from accounts.views import permission_required, create_operation_log  # 复用用户模块的日志和权限装饰器

from django.db.models import Sum, F, Q, OuterRef, Subquery, DecimalField
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage

# ========== 缓存时长常量配置 ==========
CACHE_HIGH_PRIORITY = 300  # 复杂聚合查询 5分钟
CACHE_MID_PRIORITY = 600  # 静态数据/搜索接口 10分钟

# 🔥 定义全局统一的缓存 Key
CACHE_KEY_AREA_LIST_FOR_CUSTOMER = "global:area_list_for_customer"

# 新增：全角转半角函数（处理输入容错）
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
@csrf_exempt
# 🔥 高优缓存：客户列表复杂聚合+分页
@cache_page(CACHE_HIGH_PRIORITY)
def customer_list(request):
    """优化版：无N+1、批量聚合、带分页"""
    try:
        keyword = request.GET.get('keyword', '').strip()
        # 分页参数（默认10条/页，可修改）
        page = request.GET.get('page', 1)
        page_size = 10

        # 1. 构建子查询：批量统计所有客户的 未结清金额、总消费、还款金额
        # 子查询1：未结清订单总额 ✅ 修复：添加status排除作废，命中索引
        unpaid_subquery = Order.objects.filter(
            customer=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened'],  # 索引必填+排除作废
            is_settled=False
        ).values('customer').annotate(
            total=Sum('total_amount')
        ).values('total')

        # 子查询2：总消费金额 ✅ 修复：添加status排除作废，命中索引
        consumption_subquery = Order.objects.filter(
            customer=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened']  # 索引必填+排除作废
        ).values('customer').annotate(
            total=Sum('total_amount')
        ).values('total')

        # 子查询3：还款总额
        paid_subquery = RepaymentRecord.objects.filter(
            customer=OuterRef('pk')
        ).values('customer').annotate(
            total=Sum('repayment_amount')
        ).values('total')

        # 2. 主查询：一次性注解所有统计字段（核心优化）
        customers = Customer.objects.all().select_related('area').annotate(
            # 用Coalesce将NULL转为0，避免强转报错
            unpaid_amount=Coalesce(Subquery(unpaid_subquery), 0, output_field=DecimalField()),
            total_consumption=Coalesce(Subquery(consumption_subquery), 0, output_field=DecimalField()),
            paid_amount=Coalesce(Subquery(paid_subquery), 0, output_field=DecimalField()),
        )

        # 搜索筛选 ✅ 修复：整型id不支持icontains，改为数字精准匹配
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

        # 3. 分页（必加，防止全表拉取）
        paginator = Paginator(customers, page_size)
        try:
            customer_page = paginator.page(page)
        except PageNotAnInteger:
            customer_page = paginator.page(1)
        except EmptyPage:
            customer_page = paginator.page(paginator.num_pages)

        # 4. 构造结果（无任何数据库查询，纯内存处理）
        result = []
        for c in customer_page:
            # 直接使用注解好的字段，无需查询
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
                # 可选：返回分页参数
                'page': int(page),
                'total': paginator.count
            })

        return JsonResponse(result, safe=False)
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'})


# 2. 客户详情（需customer_view权限）
# 2. 客户详情（需customer_view权限）
@login_required
@permission_required('customer_view')
@csrf_exempt
# 🔥 高优缓存：客户详情多表关联+统计计算
@cache_page(CACHE_HIGH_PRIORITY)
def customer_detail(request, pk):
    """客户详情接口：支持订单筛选 + 分页【性能优化版】"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        settle_status = request.GET.get('settle_status', 'all')
        # 分页参数
        page = request.GET.get('page', 1)
        page_size = 10  # 每页10条

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
        paid_amount = RepaymentRecord.objects.filter(customer=customer).aggregate(total=Sum('repayment_amount'))['total'] or 0
        total_debt = max(float(unpaid_amount) - float(paid_amount), 0)

        # 3. 🔥 订单分页核心逻辑
        orders_query = orders_query.order_by('-create_time')
        paginator = Paginator(orders_query, page_size)
        try:
            order_page = paginator.page(page)
        except PageNotAnInteger:
            order_page = paginator.page(1)
        except EmptyPage:
            order_page = paginator.page(paginator.num_pages)

        # 4. 分页订单数据格式化
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

        # 5. 还款记录（无分页，限制100条防超时）
        repayment_list = []
        repayments = RepaymentRecord.objects.filter(customer=customer).select_related('operator__role').order_by('-repayment_time')[:100]
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

        # 返回数据（新增分页字段）
        return JsonResponse({
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
            # 分页数据
            'orders': order_list,
            'current_page': order_page.number,
            'total_pages': paginator.num_pages,
            'total_orders': paginator.count,
            'repayments': repayment_list,
            'product_stats': product_stats_list
        }, safe=False)

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, safe=False)


# 3. 还款登记（需customer_repayment权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_repayment')
@csrf_exempt
def repayment_register(request):
    """还款登记接口"""
    if request.method == 'POST':
        try:
            # 获取参数
            customer_id = request.POST.get('customer_id', '').strip()
            repayment_amount = request.POST.get('repayment_amount', '').strip()
            repayment_time = request.POST.get('repayment_time', '').strip()
            repayment_remark = request.POST.get('repayment_remark', '').strip()

            # 校验必填项
            if not customer_id or not repayment_amount:
                return JsonResponse({'code': 0, 'msg': '客户和还款金额不能为空'}, content_type='application/json')

            # 校验金额
            try:
                repayment_amount = float(repayment_amount)
                if repayment_amount <= 0:
                    return JsonResponse({'code': 0, 'msg': '还款金额必须大于0'}, content_type='application/json')
            except:
                return JsonResponse({'code': 0, 'msg': '还款金额必须是数字'}, content_type='application/json')

            # 校验客户
            customer = get_object_or_404(Customer, id=customer_id)

            # 处理还款时间
            if repayment_time:
                try:
                    repayment_time = timezone.make_aware(datetime.datetime.strptime(repayment_time, '%Y-%m-%d %H:%M'))
                except:
                    return JsonResponse({'code': 0, 'msg': '还款时间格式错误（正确格式：YYYY-MM-DD HH:MM）'},
                                        content_type='application/json')
            else:
                repayment_time = timezone.now()

            # 创建还款记录
            repayment = RepaymentRecord.objects.create(
                customer=customer,
                repayment_amount=repayment_amount,
                repayment_time=repayment_time,
                repayment_remark=repayment_remark,
                operator=request.user if request.user.is_authenticated else None
            )

            # 记录操作日志（复用accounts的函数）
            create_operation_log(
                request=request,
                op_type='repayment_register',
                obj_type='repayment',
                obj_id=repayment.id,
                obj_name=f'{customer.name} - 还款¥{repayment_amount}',
                detail=f"为客户{customer.name}登记还款：金额¥{repayment_amount}，时间{repayment_time.strftime('%Y-%m-%d %H:%M')}，备注：{repayment_remark if repayment_remark else '无'}"
            )

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
@csrf_exempt
def customer_add(request):
    """新增客户接口"""
    if request.method == 'POST':
        try:
            # 获取前端参数
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验必填项
            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            # 校验唯一性
            if Customer.objects.filter(name=name).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            # 校验区域是否存在
            area = get_object_or_404(Area, id=area_id)
            area_name = area.name

            # 创建客户
            customer = Customer.objects.create(
                name=name,
                area=area,
                phone=phone,
                remark=remark
            )

            # 记录操作日志（复用accounts的函数）
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='customer',
                obj_id=customer.id,
                obj_name=customer.name,
                detail=f"新增客户：名称={customer.name}，所属区域={area_name}，联系电话={phone}，备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '新增客户成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


# 7. 编辑客户（需customer_edit权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_edit')
@csrf_exempt
def customer_edit(request, pk):
    """编辑客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        if request.method == 'POST':
            # 获取参数
            name = request.POST.get('name', '').strip()
            area_id = request.POST.get('area_id', '').strip()
            phone = request.POST.get('phone', '').strip()
            remark = request.POST.get('remark', '').strip()

            # 校验必填项
            if not name:
                return JsonResponse({'code': 0, 'msg': '客户名称不能为空'}, content_type='application/json')
            if not area_id:
                return JsonResponse({'code': 0, 'msg': '所属区域不能为空'}, content_type='application/json')
            if not phone:
                return JsonResponse({'code': 0, 'msg': '联系电话不能为空'}, content_type='application/json')

            # 校验唯一性（排除自身）
            if Customer.objects.filter(name=name).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '客户名称已存在'}, content_type='application/json')
            if Customer.objects.filter(phone=phone).exclude(pk=pk).exists():
                return JsonResponse({'code': 0, 'msg': '联系电话已存在'}, content_type='application/json')

            # 校验区域
            area = get_object_or_404(Area, id=area_id)
            new_area_name = area.name

            # 保存修改前的信息（用于日志对比）
            old_name = customer.name
            old_area = customer.area.name if customer.area else '无'
            old_phone = customer.phone
            old_remark = customer.remark if customer.remark else '无'

            # 更新客户信息
            customer.name = name
            customer.area = area
            customer.phone = phone
            customer.remark = remark
            customer.save()

            # 记录操作日志（复用accounts的函数）
            create_operation_log(
                request=request,
                op_type='update',
                obj_type='customer',
                obj_id=customer.id,
                obj_name=customer.name,
                detail=f"编辑客户：原名称={old_name}→新名称={name}，原区域={old_area}→新区域={new_area_name}，原电话={old_phone}→新电话={phone}，原备注={old_remark}→新备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '编辑客户成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


# 8. 删除客户（需customer_delete权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_delete')
@csrf_exempt
def customer_delete(request, pk):
    """删除客户接口"""
    try:
        customer = get_object_or_404(Customer, pk=pk)
        # 保存删除前的信息（删除后无法获取）
        customer_name = customer.name
        customer_area = customer.area.name if customer.area else '无'
        customer_phone = customer.phone
        customer_remark = customer.remark if customer.remark else '无'

        # 删除客户
        customer.delete()

        # 记录操作日志（复用accounts的函数）
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='customer',
            obj_id=pk,
            obj_name=customer_name,
            detail=f"删除客户：ID={pk}，名称={customer_name}，所属区域={customer_area}，联系电话={customer_phone}，备注={customer_remark}"
        )

        return JsonResponse({'code': 1, 'msg': '删除客户成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 辅助接口：获取区域列表（需customer_view权限） =====================
@login_required
@permission_required('customer_view')
@csrf_exempt
def area_list_for_customer(request):
    """供客户管理页面获取区域下拉列表 - 手动缓存版"""
    try:
        # 1. 尝试读取缓存
        cached_data = cache.get(CACHE_KEY_AREA_LIST_FOR_CUSTOMER)
        if cached_data:
            return JsonResponse(cached_data, safe=False, content_type='application/json')

        # 2. 缓存未命中，查询数据库
        areas = Area.objects.all().order_by('name')
        result = [{'id': a.id, 'name': a.name} for a in areas]

        # 3. 写入缓存
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
@csrf_exempt
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
@csrf_exempt
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

            return JsonResponse({'code': 1, 'msg': '新增专属价成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


# 3. 编辑客户价格（需customer_price_edit权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_price_edit')
@csrf_exempt
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

            return JsonResponse({'code': 1, 'msg': '编辑专属价成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


# 4. 删除客户价格（需customer_price_delete权限）✅ 写操作，不缓存
@login_required
@permission_required('customer_price_delete')
@csrf_exempt
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
@csrf_exempt
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