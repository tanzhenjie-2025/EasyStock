# product\views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.core.files.uploadedfile import InMemoryUploadedFile
import os
import io
import openpyxl
import xlrd
import json
from datetime import datetime, timedelta
from django.db.models import Sum, Count, Q

# ========== 核心导入：RBAC权限组件 ==========
from accounts.views import permission_required, create_operation_log  # 复用用户模块的日志函数
from accounts.models import (
    PERM_PRODUCT_VIEW, PERM_PRODUCT_ADD, PERM_PRODUCT_EDIT,
    PERM_PRODUCT_DELETE, PERM_PRODUCT_ALIAS_ADD, PERM_PRODUCT_ALIAS_DELETE,
    PERM_PRODUCT_IMPORT, PERM_PRODUCT_STOCK_OP, PERM_PRODUCT_DETAIL
)

# 业务模型导入
from bill.models import Product, ProductAlias, Order, OrderItem, CustomerPrice


# ====================== 商品管理主页面 ======================
# product/views.py
# ====================== 商品管理主页面 ======================
# product/views.py
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q


@permission_required(PERM_PRODUCT_VIEW)
def product_manage(request):
    """商品管理主页面（分页优化版 - 每页20条 + 后端搜索）"""
    # 获取分页参数 + 搜索关键词
    page = request.GET.get('page', 1)
    keyword = request.GET.get('keyword', '').strip()

    # 优化查询：prefetch_related 解决N+1查询，一次性加载所有别名
    products_query = Product.objects.all().order_by('name').prefetch_related('aliases')

    # 后端搜索：匹配商品名称 或 商品别名
    if keyword:
        products_query = products_query.filter(
            Q(name__icontains=keyword) |
            Q(aliases__alias_name__icontains=keyword)
        ).distinct()  # 去重，避免别名重复导致商品重复

    # 核心分页：每页20条数据
    paginator = Paginator(products_query, 20)
    try:
        page_products = paginator.page(page)
    except PageNotAnInteger:
        page_products = paginator.page(1)
    except EmptyPage:
        page_products = paginator.page(paginator.num_pages)

    # 组装分页后的商品+别名数据
    product_list = []
    for product in page_products:
        aliases = product.aliases.all()
        product_list.append({
            'id': product.id,
            'name': product.name,
            'price': product.price,
            'unit': product.unit,
            'stock': product.stock,
            'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in aliases],
            'status': 1  # 兼容原有前端样式
        })

    # 新增：获取所有区域（用于销售排行筛选）
    areas = Area.objects.all()

    return render(request, 'product/product_manage.html', {
        'products': product_list,  # 分页后的商品数据
        'paginator': paginator,  # 分页器
        'page_products': page_products,  # 分页对象
        'keyword': keyword,  # 搜索关键词（回显）
        'areas': areas,
        # 权限标识
        'can_add_product': request.user.has_permission(PERM_PRODUCT_ADD),
        'can_edit_product': request.user.has_permission(PERM_PRODUCT_EDIT),
        'can_delete_product': request.user.has_permission(PERM_PRODUCT_DELETE),
        'can_import_product': request.user.has_permission(PERM_PRODUCT_IMPORT),
        'can_stock_operation': request.user.has_permission(PERM_PRODUCT_STOCK_OP)
    })


# ====================== 商品CRUD ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_ADD)  # 需"新增商品"权限
def product_add(request):
    """新增商品（AJAX接口）"""
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0').strip()
            unit = request.POST.get('unit', '件').strip()
            stock = request.POST.get('stock', '77').strip()

            # 表单验证
            if not name:
                return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
            if not price or float(price) < 0:
                return JsonResponse({'code': 0, 'msg': '请输入有效的单价'})

            # 创建商品
            product = Product.objects.create(
                name=name,
                price=float(price),
                unit=unit,
                stock=int(stock) if stock.isdigit() else 77
            )

            # 记录操作日志（复用用户模块的日志函数）
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='product',
                obj_id=product.id,
                obj_name=product.name,
                detail=f"新增商品：名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
            )

            return JsonResponse({'code': 1, 'msg': '商品新增成功', 'data': {
                'id': product.id,
                'name': product.name,
                'price': float(product.price),
                'unit': product.unit,
                'stock': product.stock,
                'aliases': []
            }})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '商品名称已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_EDIT)  # 需"编辑商品"权限
def product_edit(request, pk):
    """编辑商品（AJAX接口）"""
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0').strip()
            unit = request.POST.get('unit', '件').strip()
            stock = request.POST.get('stock', '77').strip()

            # 表单验证
            if not name:
                return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
            if not price or float(price) < 0:
                return JsonResponse({'code': 0, 'msg': '请输入有效的单价'})

            # 检查名称是否重复（排除自身）
            if Product.objects.filter(name=name).exclude(id=pk).exists():
                return JsonResponse({'code': 0, 'msg': '商品名称已存在'})

            # 保存原始信息（日志用）
            old_info = f"名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"

            # 更新商品
            product.name = name
            product.price = float(price)
            product.unit = unit
            product.stock = int(stock) if stock.isdigit() else 77
            product.save()

            # 记录操作日志
            create_operation_log(
                request=request,
                op_type='update',
                obj_type='product',
                obj_id=product.id,
                obj_name=product.name,
                detail=f"编辑商品：原信息[{old_info}] → 新信息[名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}]"
            )

            # 获取更新后的别名
            aliases = [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]

            return JsonResponse({'code': 1, 'msg': '商品编辑成功', 'data': {
                'id': product.id,
                'name': product.name,
                'price': float(product.price),
                'unit': product.unit,
                'stock': product.stock,
                'aliases': aliases
            }})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_DELETE)  # 需"删除商品"权限
def product_delete(request, pk):
    """删除商品（AJAX接口）"""
    try:
        product = get_object_or_404(Product, pk=pk)
        product_name = product.name  # 先保存名称（删除后无法获取）

        # 删除商品（级联删除别名）
        product.delete()

        # 记录操作日志
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='product',
            obj_id=pk,
            obj_name=product_name,
            detail=f"删除商品：名称={product_name}，ID={pk}"
        )

        return JsonResponse({'code': 1, 'msg': '商品删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 别名CRUD ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_ALIAS_ADD)  # 需"新增别名"权限
def alias_add(request):
    """新增商品别名（AJAX接口）"""
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product_id', '')
            alias_name = request.POST.get('alias_name', '').strip()

            # 表单验证
            if not product_id or not alias_name:
                return JsonResponse({'code': 0, 'msg': '商品ID和别名不能为空'})
            product = get_object_or_404(Product, pk=product_id)

            # 创建别名
            alias = ProductAlias.objects.create(
                product=product,
                alias_name=alias_name
            )

            # 记录操作日志
            create_operation_log(
                request=request,
                op_type='create',
                obj_type='product_alias',
                obj_id=alias.id,
                obj_name=f"{product.name}-{alias.alias_name}",
                detail=f"为商品【{product.name}】新增别名：{alias.alias_name}"
            )

            return JsonResponse({'code': 1, 'msg': '别名新增成功', 'data': {
                'id': alias.id,
                'alias_name': alias.alias_name
            }})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '该别名已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_ALIAS_DELETE)  # 需"删除别名"权限
def alias_delete(request, pk):
    """删除商品别名（AJAX接口）"""
    try:
        alias = get_object_or_404(ProductAlias, pk=pk)
        product_name = alias.product.name
        alias_name = alias.alias_name

        # 删除别名
        alias.delete()

        # 记录操作日志
        create_operation_log(
            request=request,
            op_type='delete',
            obj_type='product_alias',
            obj_id=pk,
            obj_name=f"{product_name}-{alias_name}",
            detail=f"删除商品【{product_name}】的别名：{alias_name}"
        )

        return JsonResponse({'code': 1, 'msg': '别名删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 商品数据接口 ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_VIEW)  # 需"查看商品"权限
def product_manage_data(request):
    """商品列表数据接口（AJAX）"""
    products = Product.objects.all().order_by('name')
    product_list = []
    for product in products:
        aliases = product.aliases.all()
        product_list.append({
            'id': product.id,
            'name': product.name,
            'price': float(product.price),
            'unit': product.unit,
            'stock': product.stock,
            'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in aliases]
        })
    return JsonResponse(product_list, safe=False)


@csrf_exempt
@permission_required(PERM_PRODUCT_EDIT)  # 需"编辑商品"权限
def product_edit_data(request, pk):
    """编辑商品数据接口（AJAX）"""
    product = get_object_or_404(Product, pk=pk)
    aliases = product.aliases.all()
    return JsonResponse({
        'id': product.id,
        'name': product.name,
        'price': float(product.price),
        'unit': product.unit,
        'stock': product.stock,
        'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in aliases]
    })


# ====================== 商品导入功能 ======================
@csrf_exempt
@require_POST
@permission_required(PERM_PRODUCT_IMPORT)  # 需"导入商品"权限
def product_import(request):
    """商品Excel导入接口"""
    try:
        # 1. 获取上传文件
        if 'file' not in request.FILES:
            return JsonResponse({'code': 0, 'msg': '请选择要上传的Excel文件'})

        file = request.FILES['file']
        # 验证文件格式
        file_name = file.name
        if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
            return JsonResponse({'code': 0, 'msg': '仅支持xlsx/xls格式的Excel文件'})

        # 2. 解析Excel文件
        success_count = 0
        fail_count = 0
        fail_reasons = []

        # 处理xlsx格式
        if file_name.endswith('.xlsx'):
            wb = openpyxl.load_workbook(io.BytesIO(file.read()))
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
        # 处理xls格式
        else:
            wb = xlrd.open_workbook(file_contents=file.read())
            ws = wb.sheet_by_index(0)
            rows = []
            for row_idx in range(ws.nrows):
                rows.append(ws.row_values(row_idx))

        # 3. 解析表头
        header_row = rows[0] if rows else []
        name_col_idx = -1
        price_col_idx = -1
        unit_col_idx = -1

        for idx, header in enumerate(header_row):
            header = str(header).strip()
            if '商品名称' in header:
                name_col_idx = idx
            elif '零售价' in header:
                price_col_idx = idx
            elif '辅助单位' in header:
                unit_col_idx = idx

        # 验证关键列
        if name_col_idx == -1:
            return JsonResponse({'code': 0, 'msg': 'Excel中未找到"商品名称"列'})

        # 4. 遍历数据行
        for row_num, row in enumerate(rows[1:], start=2):
            try:
                product_name = str(row[name_col_idx]).strip() if len(row) > name_col_idx else ''
                if not product_name:
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品名称为空')
                    continue

                # 获取零售价
                if price_col_idx != -1 and len(row) > price_col_idx and row[price_col_idx]:
                    try:
                        price = float(row[price_col_idx])
                    except:
                        price = 0.0
                else:
                    price = 0.0

                # 获取辅助单位
                if unit_col_idx != -1 and len(row) > unit_col_idx and row[unit_col_idx]:
                    unit = str(row[unit_col_idx]).strip()
                else:
                    unit = '件'

                # 去重检查
                if Product.objects.filter(name=product_name).exists():
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品"{product_name}"已存在')
                    continue

                # 创建商品
                Product.objects.create(
                    name=product_name,
                    price=price,
                    unit=unit,
                    stock=77
                )
                success_count += 1

            except Exception as e:
                fail_count += 1
                fail_reasons.append(f'第{row_num}行：导入失败 - {str(e)}')

        # 记录操作日志
        import_detail = f"批量导入商品：成功{success_count}条，失败{fail_count}条。"
        if fail_reasons:
            import_detail += f" 失败原因：{' | '.join(fail_reasons[:5])}{'...' if len(fail_reasons) > 5 else ''}"
        create_operation_log(
            request=request,
            op_type='import',
            obj_type='product',
            detail=import_detail
        )

        # 5. 返回结果
        msg = f'导入完成！成功{success_count}条，失败{fail_count}条'
        if fail_reasons:
            msg += f'。失败原因：{" | ".join(fail_reasons[:5])}{"..." if len(fail_reasons) > 5 else ""}'

        return JsonResponse({
            'code': 1,
            'msg': msg,
            'data': {
                'success_count': success_count,
                'fail_count': fail_count,
                'fail_reasons': fail_reasons
            }
        })

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'导入失败：{str(e)}'})


# ====================== 快速出入库操作 ======================
@csrf_exempt
@require_POST
@permission_required(PERM_PRODUCT_STOCK_OP)  # 需"商品出入库"权限
def quick_stock_operation(request):
    """快速出入库操作接口"""
    try:
        # 解析请求数据
        data = json.loads(request.body)
        items = data.get('items', [])

        if not items:
            return JsonResponse({'code': 0, 'msg': '无有效出入库数据'})

        # 事务处理
        with transaction.atomic():
            operation_details = []
            success_count = 0

            for item in items:
                product_id = item.get('product_id')
                in_quantity = int(item.get('in_quantity', 0))
                out_quantity = int(item.get('out_quantity', 0))

                # 验证参数
                if not product_id or (in_quantity <= 0 and out_quantity <= 0):
                    continue

                # 获取商品
                product = get_object_or_404(Product, pk=product_id)
                product_name = product.name

                # 验证出库数量
                if out_quantity > product.stock:
                    raise Exception(f'商品【{product_name}】出库数量{out_quantity}超过当前库存{product.stock}')

                # 更新库存
                old_stock = product.stock
                product.stock += in_quantity
                product.stock -= out_quantity
                product.save()

                # 记录操作详情
                operation_detail = f"商品【{product_name}】："
                if in_quantity > 0:
                    operation_detail += f"入库{in_quantity}{product.unit}，"
                if out_quantity > 0:
                    operation_detail += f"出库{out_quantity}{product.unit}，"
                operation_detail += f"库存从{old_stock}变更为{product.stock}"

                operation_details.append(operation_detail)
                success_count += 1

            # 记录操作日志
            if success_count > 0:
                create_operation_log(
                    request=request,
                    op_type='stock_operation',
                    obj_type='product',
                    detail=f"快速出入库操作：共处理{success_count}个商品。详情：{' | '.join(operation_details)}"
                )

                return JsonResponse({
                    'code': 1,
                    'msg': f'出入库操作成功！共处理{success_count}个商品',
                    'data': {'success_count': success_count}
                })
            else:
                return JsonResponse({'code': 0, 'msg': '无有效出入库数据'})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'出入库操作失败：{str(e)}'})


# ====================== 商品详情页面 ======================
@permission_required(PERM_PRODUCT_DETAIL)  # 需"商品详情"权限
def product_detail(request, pk):
    """商品详情页面"""
    # 1. 获取商品基本信息
    product = get_object_or_404(Product, pk=pk)

    # 2. 获取客户专属价（取前5条展示）
    custom_prices = CustomerPrice.objects.filter(product=product).select_related('customer')[:5]

    # 3. 销量统计
    base_order_items = OrderItem.objects.filter(
        product=product,
        order__status__in=['pending', 'printed', 'reopened']
    ).select_related('order', 'order__customer')

    # 3.1 总销量
    total_sales = base_order_items.aggregate(total=Sum('quantity'))['total'] or 0

    # 3.2 近7天销量
    seven_days_ago = datetime.now() - timedelta(days=7)
    sales_7d = base_order_items.filter(
        order__create_time__gte=seven_days_ago
    ).aggregate(total=Sum('quantity'))['total'] or 0

    # 3.3 近30天销量
    thirty_days_ago = datetime.now() - timedelta(days=30)
    sales_30d = base_order_items.filter(
        order__create_time__gte=thirty_days_ago
    ).aggregate(total=Sum('quantity'))['total'] or 0

    # 4. 最近销售记录
    recent_sales_raw = base_order_items.filter(
        order__create_time__isnull=False
    ).order_by('-order__create_time')[:10]

    recent_sales = []
    for item in recent_sales_raw:
        unit_price = 0.0
        if item.quantity > 0 and item.amount:
            unit_price = float(item.amount) / item.quantity

        recent_sales.append({
            'order_no': item.order.order_no,
            'customer_name': item.order.customer.name if item.order.customer else '未知客户',
            'quantity': item.quantity,
            'unit_price': unit_price,
            'create_time': item.order.create_time,
            'is_settled': item.order.is_settled
        })

    # 5. 熟客统计
    customer_sales = base_order_items.values(
        'order__customer__id', 'order__customer__name'
    ).annotate(
        buy_count=Count('id'),
        buy_quantity=Sum('quantity')
    ).filter(
        order__customer__isnull=False
    ).order_by('-buy_quantity')[:10]

    # 组装模板数据
    context = {
        'product': product,
        'custom_prices': custom_prices,
        'total_sales': total_sales,
        'sales_7d': sales_7d,
        'sales_30d': sales_30d,
        'recent_sales': recent_sales,
        'customer_sales': customer_sales,
        'product_unit': product.unit or '件',
        # 权限标识
        'can_edit_product': request.user.has_permission(PERM_PRODUCT_EDIT),
        'can_stock_operation': request.user.has_permission(PERM_PRODUCT_STOCK_OP)
    }

    return render(request, 'product/product_detail.html', context)


from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F, Count
from django.utils import timezone
from datetime import datetime, timedelta
from bill.models import Order, OrderItem, Product, Area
from accounts.models import Permission, ROLE_SUPER_ADMIN
from accounts.views import permission_required


# 销售排行页面
@login_required
@permission_required('product_sales_rank')
def sales_rank(request):
    """商品销售排行页面"""
    # 获取所有区域
    areas = Area.objects.all()
    return render(request, 'product/sales_rank.html', {
        'areas': areas
    })


# 销售排行数据接口
@login_required
@permission_required('product_sales_rank')
def sales_rank_data(request):
    """商品销售排行数据接口"""
    try:
        # 获取筛选参数
        sort_type = request.GET.get('sort', 'sales_volume')  # sales_volume/sales_amount
        time_range = request.GET.get('time_range', 'today')  # today/week/month/year
        area_id = request.GET.get('area_id', 'all')  # all或区域ID

        # 1. 时间范围筛选
        now = timezone.now()
        if time_range == 'today':
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0)
        elif time_range == 'week':
            # 本周第一天（周一）
            week_day = now.weekday()  # 0=周一, 6=周日
            start_time = now - timedelta(days=week_day)
            start_time = datetime(start_time.year, start_time.month, start_time.day, 0, 0, 0)
        elif time_range == 'month':
            # 本月第一天
            start_time = datetime(now.year, now.month, 1, 0, 0, 0)
        elif time_range == 'year':
            # 本年第一天
            start_time = datetime(now.year, 1, 1, 0, 0, 0)
        else:
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0)

        # 2. 构建查询条件
        # 只统计正常生效订单（排除作废）
        order_filters = {
            'create_time__gte': start_time,
            'status__in': ['pending', 'printed', 'reopened']  # 排除cancelled
        }

        # 区域筛选
        if area_id != 'all' and area_id.isdigit():
            order_filters['area_id'] = int(area_id)

        # 3. 查询销售数据
        # 关联订单和订单项，聚合商品销售数据
        sales_data = OrderItem.objects.filter(
            order__in=Order.objects.filter(**order_filters)
        ).values(
            'product__id', 'product__name', 'product__unit'
        ).annotate(
            sales_volume=Sum('quantity'),  # 销量
            sales_amount=Sum('amount')  # 销售金额
        ).filter(
            product__isnull=False  # 排除无商品的订单项
        )

        # 4. 排序
        if sort_type == 'sales_amount':
            sales_data = sales_data.order_by('-sales_amount')
        else:
            sales_data = sales_data.order_by('-sales_volume')

        # 5. 取TOP30
        top30_data = sales_data[:30]

        # 6. 格式化返回数据
        result = []
        for item in top30_data:
            result.append({
                'product_id': item['product__id'],
                'product_name': item['product__name'],
                'unit': item['product__unit'],
                'sales_volume': item['sales_volume'],
                'sales_amount': float(item['sales_amount']) if item['sales_amount'] else 0.0
            })

        return JsonResponse({
            'code': 1,
            'msg': '获取成功',
            'data': result
        })

    except Exception as e:
        return JsonResponse({
            'code': 0,
            'msg': f'获取数据失败：{str(e)}',
            'data': []
        })