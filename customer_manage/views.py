# customer_manage\views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from bill.models import Customer, Area, ProductAlias, CustomerPrice, Product, OrderItem, Order, RepaymentRecord
from django.db.models import Sum, F, Q, Max, Count
from django.db.models.functions import Coalesce
import datetime
import unicodedata  # 新增：处理全角半角转换
# ========== 新增：导入用户模块的权限装饰器和日志函数 ==========
from django.contrib.auth.decorators import login_required
from accounts.views import permission_required, create_operation_log  # 复用用户模块的日志和权限装饰器


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




# ===================== 客户管理CRUD（添加权限装饰器） =====================
# 1. 客户列表（需customer_view权限）
@login_required
@permission_required('customer_view')
@csrf_exempt
def customer_list(request):
    """获取客户列表接口（新增总欠款字段 + 多维度搜索）"""
    try:
        # 新增：获取搜索关键词
        keyword = request.GET.get('keyword', '').strip()

        # 基础查询
        customers = Customer.objects.all().select_related('area')

        # 新增：多维度模糊匹配
        if keyword:
            customers = customers.filter(
                Q(name__icontains=keyword) |  # 客户名称
                Q(phone__icontains=keyword) |  # 手机号
                Q(id__icontains=keyword) |  # 客户编号（ID）
                Q(area__name__icontains=keyword)  # 所属区域
            )

        result = []
        for c in customers:
            # 计算客户总欠款：未结清订单的总金额之和
            unpaid_amount = Order.objects.filter(
                customer=c,
                is_settled=False
            ).aggregate(total=Sum('total_amount'))['total'] or 0

            # 计算客户已还款总额
            paid_amount = RepaymentRecord.objects.filter(
                customer=c
            ).aggregate(total=Sum('repayment_amount'))['total'] or 0

            # 实际欠款 = 未结清订单总额 - 已还款总额
            total_debt = float(unpaid_amount) - float(paid_amount)
            total_debt = max(total_debt, 0)  # 避免负数（还款超支）

            result.append({
                'id': c.id,
                'name': c.name,
                'area_id': c.area.id if c.area else '',
                'area_name': c.area.name if c.area else '',
                'phone': c.phone,
                'remark': c.remark or '',
                'total_debt': total_debt  # 新增：总欠款
            })
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )

# 2. 客户详情（需customer_view权限）
@login_required
@permission_required('customer_view')
@csrf_exempt
def customer_detail(request, pk):
    """客户详情接口：支持订单筛选 + 商品统计"""
    try:
        customer = get_object_or_404(Customer, pk=pk)

        # 获取订单筛选参数（all/settled/unsettled）
        settle_status = request.GET.get('settle_status', 'all')

        # 1. 基础订单查询（所有该客户的订单）
        base_orders = Order.objects.filter(customer=customer).select_related('creator')

        # 按结清状态筛选订单
        if settle_status == 'settled':
            orders_query = base_orders.filter(is_settled=True)
        elif settle_status == 'unsettled':
            orders_query = base_orders.filter(is_settled=False)
        else:
            orders_query = base_orders

        # 2. 欠款计算（保留原有逻辑）
        unpaid_orders = base_orders.filter(is_settled=False)
        unpaid_order_count = unpaid_orders.count()
        unpaid_amount = unpaid_orders.aggregate(total=Sum('total_amount'))['total'] or 0
        paid_amount = RepaymentRecord.objects.filter(customer=customer).aggregate(total=Sum('repayment_amount'))[
                          'total'] or 0
        total_debt = float(unpaid_amount) - float(paid_amount)
        total_debt = max(total_debt, 0)

        # 3. 筛选后的订单列表（新增：包含所有状态）
        order_list = []
        orders = orders_query.order_by('-create_time')
        for order in orders:
            order_list.append({
                'order_no': order.order_no or '',
                'create_time': order.create_time.strftime('%Y-%m-%d %H:%M') if order.create_time else '',
                'total_amount': float(order.total_amount) if order.total_amount else 0.0,
                'is_settled': order.is_settled,
                'status': order.status,
                'status_text': dict(Order.ORDER_STATUS).get(order.status, '未知'),
                'overdue_days': order.get_overdue_days(),
                'order_date': order.create_time.strftime('%Y-%m-%d') if order.create_time else ''
            })

        # 4. 还款记录（保留原有）
        repayment_list = []
        repayments = RepaymentRecord.objects.filter(customer=customer).select_related('operator')
        for repay in repayments:
            repayment_list.append({
                'id': repay.id,
                'repayment_amount': float(repay.repayment_amount) if repay.repayment_amount else 0.0,
                'repayment_time': repay.repayment_time.strftime('%Y-%m-%d %H:%M') if repay.repayment_time else '',
                'repayment_remark': repay.repayment_remark or '',
                'operator': repay.operator.username if (repay.operator and repay.operator.username) else '未知',
                'create_time': repay.create_time.strftime('%Y-%m-%d %H:%M') if repay.create_time else ''
            })

        # 5. 新增：客户购买商品统计（去重、总数量、最近购买时间）
        product_stats = OrderItem.objects.filter(
            order__customer=customer,
            product__isnull=False
        ).values(
            'product__id', 'product__name', 'product__unit'
        ).annotate(
            total_quantity=Coalesce(Sum('quantity'), 0),
            last_purchase_time=Coalesce(Max('order__create_time'), None)
        ).order_by('-total_quantity')

        product_stats_list = []
        for stat in product_stats:
            last_time = stat['last_purchase_time'].strftime('%Y-%m-%d') if stat['last_purchase_time'] else '无'
            product_stats_list.append({
                'product_name': stat['product__name'],
                'total_quantity': stat['total_quantity'],
                'unit': stat['product__unit'],
                'last_purchase_time': last_time
            })

        # 组装返回数据
        result = {
            'code': 1,
            'msg': '查询成功',
            'customer_info': {
                'id': customer.id,
                'name': customer.name,
                'area_name': customer.area.name if customer.area else '',
                'phone': customer.phone,
                'remark': customer.remark or ''
            },
            'debt_info': {
                'total_debt': total_debt,
                'unpaid_order_count': unpaid_order_count,
                'unpaid_amount': float(unpaid_amount),
                'paid_amount': float(paid_amount)
            },
            'orders': order_list,  # 替换原有unpaid_orders
            'repayments': repayment_list,
            'product_stats': product_stats_list  # 新增商品统计
        }

        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )

# 3. 还款登记（需customer_repayment权限）
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

# 4. 客户详情页面入口（需customer_view权限）
@login_required
@permission_required('customer_view')
def customer_detail_page(request, pk):
    """客户详情页面"""
    return render(request, 'customer_manage/customer_detail.html', {'customer_id': pk})

# 5. 还款登记页面入口（需customer_repayment权限）
@login_required
@permission_required('customer_repayment')
def repayment_page(request):
    """还款登记页面"""
    return render(request, 'customer_manage/repayment.html')

# 6. 新增客户（需customer_add权限）
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

# 7. 编辑客户（需customer_edit权限）
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

# 8. 删除客户（需customer_delete权限）
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
    """供客户管理页面获取区域下拉列表"""
    try:
        areas = Area.objects.all().order_by('name')
        result = [{'id': a.id, 'name': a.name} for a in areas]
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
def customer_price_list(request):
    """获取客户专属价格列表 - 新增多维度搜索+高级筛选"""
    try:
        # 1. 获取所有筛选参数
        keyword = request.GET.get('keyword', '').strip()
        min_price = request.GET.get('min_price', '').strip()  # 价格区间最小值
        max_price = request.GET.get('max_price', '').strip()  # 价格区间最大值
        area_id = request.GET.get('area_id', '').strip()  # 所属区域ID

        # 2. 基础查询（关联客户、商品、商品别名、客户区域）
        prices = CustomerPrice.objects.all().select_related('customer__area', 'product')

        # 3. 处理搜索关键词（容错+多维度匹配）
        if keyword:
            # 容错处理：全角转半角、去多余空格（移除中文无意义的lower()）
            keyword = full_to_half(keyword).strip()
            # 拆分多关键词（空格分隔）
            keywords = [k for k in keyword.split() if k]

            # 构建基础Q对象（所有关键词需同时匹配）
            base_q = Q()
            for kw in keywords:
                # 客户维度：名称（模糊）、编号（精准）
                customer_q = Q(customer__name__icontains=kw)
                # 如果是数字，转int后精准匹配客户编号
                if kw.isdigit():
                    customer_q |= Q(customer__id=int(kw))

                # 商品维度：名称、别名、编号（修复核心错误）
                product_q = Q(product__name__icontains=kw)
                # 如果是数字，转int后精准匹配商品编号
                if kw.isdigit():
                    product_q |= Q(product__id=int(kw))
                # 匹配商品别名
                alias_ids = ProductAlias.objects.filter(
                    Q(alias_name__icontains=kw) |
                    Q(alias_pinyin_full__icontains=kw) |
                    Q(alias_pinyin_abbr__icontains=kw)
                ).values_list('product_id', flat=True)
                if alias_ids:  # 只有有别名匹配时才添加
                    product_q |= Q(product__id__in=alias_ids)

                # 合并当前关键词的匹配条件（客户或商品维度）
                kw_q = customer_q | product_q
                # 所有关键词需同时满足（AND关系）
                base_q &= kw_q

            # 应用关键词筛选
            prices = prices.filter(base_q)

        # 4. 高级筛选：价格区间
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

        # 5. 高级筛选：所属区域
        if area_id and area_id.isdigit():
            prices = prices.filter(customer__area_id=int(area_id))

        # 6. 构造返回数据
        result = []
        for cp in prices:
            result.append({
                'id': cp.id,
                'customer_id': cp.customer.id,
                'customer_name': cp.customer.name,
                'customer_area_id': cp.customer.area.id if cp.customer.area else '',
                'customer_area_name': cp.customer.area.name if cp.customer.area else '',
                'product_id': cp.product.id,
                'product_name': cp.product.name,
                # 新增：商品别名（用于前端匹配标红）
                'product_aliases': [alias.alias_name for alias in cp.product.aliases.all()],
                'custom_price': float(cp.custom_price),
                'standard_price': float(cp.product.price),  # 商品标准价
                'remark': cp.remark or ''
            })

        # 返回搜索结果+匹配的关键词（用于前端标红）
        return JsonResponse({
            'code': 1,
            'msg': '查询成功',
            'data': result,
            'keyword': keyword  # 返回处理后的关键词，用于前端标红
        }, safe=False, content_type='application/json')

    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}', 'data': []},
            safe=False,
            content_type='application/json'
        )

# 2. 新增客户价格（需customer_price_add权限）
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

# 3. 编辑客户价格（需customer_price_edit权限）
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

# 4. 删除客户价格（需customer_price_delete权限）
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
def search_product_for_price(request):
    """商品搜索：匹配名称/拼音/别名，返回输入法式候选数据"""
    keyword = request.GET.get('keyword', '').strip()
    if not keyword:
        return JsonResponse({'code': 0, 'data': []})

    # 1. 匹配商品名称/拼音
    product_matches = Product.objects.filter(
        Q(name__icontains=keyword) |
        Q(pinyin_full__icontains=keyword) |
        Q(pinyin_abbr__icontains=keyword)
    )

    # 2. 匹配商品别名
    alias_matches = ProductAlias.objects.filter(
        Q(alias_name__icontains=keyword) |
        Q(alias_pinyin_full__icontains=keyword) |
        Q(alias_pinyin_abbr__icontains=keyword)
    ).values_list('product_id', flat=True)
    alias_products = Product.objects.filter(id__in=alias_matches)

    # 3. 合并去重，取前8条
    all_products = (product_matches | alias_products).distinct()[:8]

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