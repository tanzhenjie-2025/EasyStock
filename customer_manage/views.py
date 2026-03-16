from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
# 新增：导入日志模型和时间工具
from operation_log.models import OperationLog
from django.utils import timezone

# 复用bill里的模型（表仍在bill，无需重复建）
from bill.models import Customer, Area, ProductAlias, CustomerPrice, Product


# ========== 新增：通用日志记录函数（核心） ==========
def create_operation_log(request, operation_type, object_type, object_id=None, object_name=None, operation_detail=None):
    """
    封装操作日志记录逻辑，容错处理（日志失败不影响主业务）
    :param request: 请求对象（获取用户/IP）
    :param operation_type: 操作类型（对应OperationLog的OPERATION_TYPE_CHOICES）
    :param object_type: 操作对象类型（对应OperationLog的OBJECT_TYPE_CHOICES）
    :param object_id: 操作对象ID
    :param object_name: 操作对象名称
    :param operation_detail: 操作详情（便于追溯）
    """
    # 获取客户端IP（兼容代理场景）
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    ip_address = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')

    # 容错处理：日志记录失败仅打印错误，不中断主流程
    try:
        OperationLog.objects.create(
            operator=request.user if request.user.is_authenticated else None,  # 当前登录用户
            operation_time=timezone.now(),
            operation_type=operation_type,
            object_type=object_type,
            object_id=str(object_id) if object_id else None,
            object_name=object_name,
            operation_detail=operation_detail,
            ip_address=ip_address
        )
    except Exception as e:
        print(f"【客户管理日志记录失败】：{str(e)}")


# ===================== 客户管理CRUD =====================
@csrf_exempt
def customer_list(request):
    """获取客户列表接口"""
    try:
        customers = Customer.objects.all().select_related('area')  # 关联查询区域，提升性能
        result = []
        for c in customers:
            result.append({
                'id': c.id,
                'name': c.name,
                'area_id': c.area.id if c.area else '',
                'area_name': c.area.name if c.area else '',
                'phone': c.phone,
                'remark': c.remark or ''
            })
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


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

            # ========== 新增：记录新增客户日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='customer',
                object_id=customer.id,
                object_name=customer.name,
                operation_detail=f"新增客户：名称={customer.name}，所属区域={area_name}，联系电话={phone}，备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '新增客户成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


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

            # ========== 新增：记录编辑客户日志 ==========
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='customer',
                object_id=customer.id,
                object_name=customer.name,
                operation_detail=f"编辑客户：原名称={old_name}→新名称={name}，原区域={old_area}→新区域={new_area_name}，原电话={old_phone}→新电话={phone}，原备注={old_remark}→新备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '编辑客户成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


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

        # ========== 新增：记录删除客户日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='customer',
            object_id=pk,
            object_name=customer_name,
            operation_detail=f"删除客户：ID={pk}，名称={customer_name}，所属区域={customer_area}，联系电话={customer_phone}，备注={customer_remark}"
        )

        return JsonResponse({'code': 1, 'msg': '删除客户成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 辅助接口：获取区域列表 =====================
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


# ===================== 页面入口 =====================
def customer_page(request):
    """客户管理页面"""
    return render(request, 'customer_manage/customer.html')


# ===================== 客户专属价格CRUD =====================
@csrf_exempt
def customer_price_list(request):
    """获取客户专属价格列表"""
    try:
        prices = CustomerPrice.objects.all().select_related('customer', 'product')
        result = []
        for cp in prices:
            result.append({
                'id': cp.id,
                'customer_id': cp.customer.id,
                'customer_name': cp.customer.name,
                'product_id': cp.product.id,
                'product_name': cp.product.name,
                'custom_price': float(cp.custom_price),
                'standard_price': float(cp.product.price),  # 商品标准价
                'remark': cp.remark or ''
            })
        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        return JsonResponse(
            {'code': 0, 'msg': f'查询失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )


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

            # ========== 新增：记录新增客户专属价日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='customer_price',
                object_id=cp.id,
                object_name=f"{customer.name}-{product.name}",
                operation_detail=f"新增客户专属价：客户={customer.name}，商品={product.name}，标准价={product_standard_price}元，专属价={custom_price}元，备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '新增专属价成功'}, content_type='application/json')
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'}, content_type='application/json')
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')


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

            # ========== 新增：记录编辑客户专属价日志 ==========
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='customer_price',
                object_id=cp.id,
                object_name=f"{customer_name}-{product_name}",
                operation_detail=f"编辑客户专属价：客户={customer_name}，商品={product_name}，标准价={product_standard_price}元，原专属价={old_price}元→新专属价={custom_price}元，原备注={old_remark}→新备注={remark if remark else '无'}"
            )

            return JsonResponse({'code': 1, 'msg': '编辑专属价成功'}, content_type='application/json')
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'}, content_type='application/json')


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

        # ========== 新增：记录删除客户专属价日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='customer_price',
            object_id=pk,
            object_name=f"{customer_name}-{product_name}",
            operation_detail=f"删除客户专属价：ID={pk}，客户={customer_name}，商品={product_name}，标准价={product_standard_price}元，专属价={custom_price}元，备注={remark}"
        )

        return JsonResponse({'code': 1, 'msg': '删除专属价成功'}, content_type='application/json')
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'}, content_type='application/json')


# ===================== 页面入口 =====================
def customer_price_page(request):
    """客户专属价格管理页面"""
    return render(request, 'customer_manage/customer_price.html')


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


from django.db.models import Q
from difflib import SequenceMatcher


# ===================== 客户搜索接口（输入法式选择） =====================
@csrf_exempt
def search_customer_for_price(request):
    """
    客户搜索：匹配名称/区域，返回输入法式候选数据
    供客户专属价页面使用
    """
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


# ===================== 商品搜索接口（输入法式选择） =====================
@csrf_exempt
def search_product_for_price(request):
    """
    商品搜索：匹配名称/拼音/别名，返回输入法式候选数据
    供客户专属价页面使用
    """
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