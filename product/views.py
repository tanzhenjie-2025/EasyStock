from django.contrib.auth.decorators import login_required
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
from django.db.models import Sum, Count, Q, F, Prefetch, Case, When, DateTimeField
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils import timezone

# ========== 缓存核心导入 ==========
from django.core.cache import cache

# ========== RBAC权限组件 ==========
from accounts.views import permission_required, create_operation_log
from accounts.models import (
    PERM_PRODUCT_VIEW, PERM_PRODUCT_ADD, PERM_PRODUCT_EDIT,
    PERM_PRODUCT_DELETE, PERM_PRODUCT_ALIAS_ADD, PERM_PRODUCT_ALIAS_DELETE,
    PERM_PRODUCT_IMPORT, PERM_PRODUCT_STOCK_OP, PERM_PRODUCT_DETAIL
)

# 业务模型
from bill.models import Product, ProductAlias, Order, OrderItem, CustomerPrice, Area

# ====================== 缓存常量配置 ======================
CACHE_AREA = 3600
CACHE_COMMON = 60
CACHE_SALES_RANK = 10
# 新增：分页计数缓存时长（解决COUNT(*)瓶颈）
CACHE_PAGINATION_COUNT = 120

CACHE_PREFIX_PRODUCT_LIST = "product:list:"
CACHE_PREFIX_PRODUCT_DETAIL = "product:detail:"
CACHE_PREFIX_SALES_RANK = "product:sales_rank:"
# 新增：分页计数缓存键
CACHE_PREFIX_PRODUCT_COUNT = "product:count:"
KEY_AREA = "area:data"
KEY_PRODUCT_ALIAS = "product:alias"


# ====================== 缓存工具函数 ======================
def clear_product_all_cache():
    cache.delete_many([
        KEY_AREA,
        KEY_PRODUCT_ALIAS,
    ])
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_LIST}*"):
        cache.delete(key)
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_DETAIL}*"):
        cache.delete(key)
    for key in cache.keys(f"{CACHE_PREFIX_SALES_RANK}*"):
        cache.delete(key)
    # 新增：清理分页计数缓存
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_COUNT}*"):
        cache.delete(key)


# ====================== 商品管理主页面（修复：分页COUNT(*)性能瓶颈） ======================
@permission_required(PERM_PRODUCT_VIEW)
def product_manage(request):
    page = request.GET.get('page', 1)
    keyword = request.GET.get('keyword', '').strip()

    cache_key = f"{CACHE_PREFIX_PRODUCT_LIST}{keyword}:{page}"
    cached_data = cache.get(cache_key)

    if cached_data:
        product_list = cached_data['product_list']
        paginator = cached_data['paginator']
        page_products = cached_data['page_products']
    else:
        products_query = Product.objects.order_by('name').only(
            'id', 'name', 'price', 'unit', 'stock'
        )
        alias_query = ProductAlias.objects.only('id', 'alias_name')
        products_query = products_query.prefetch_related(
            Prefetch('aliases', queryset=alias_query)
        )

        # 搜索逻辑
        if keyword:
            alias_product_ids = ProductAlias.objects.filter(
                Q(alias_name__icontains=keyword)
            ).values_list('product_id', flat=True)
            products_query = products_query.filter(
                Q(name__icontains=keyword) | Q(id__in=alias_product_ids)
            )

        # ====================== 核心修复：分页COUNT(*)优化 ======================
        # 缓存总条数，避免每次执行全表COUNT(*)
        count_cache_key = f"{CACHE_PREFIX_PRODUCT_COUNT}{keyword}"
        total_count = cache.get(count_cache_key)
        if total_count is None:
            total_count = products_query.count()
            cache.set(count_cache_key, total_count, CACHE_PAGINATION_COUNT)

        # 手动分页：只查询当前页数据，不执行COUNT(*)
        page_size = 15
        offset = (int(page) - 1) * page_size
        page_products_qs = products_query[offset:offset + page_size]

        # 构建轻量分页对象（复用Paginator接口，无COUNT(*)）
        paginator = Paginator(products_query, page_size)
        paginator._count = total_count  # 直接赋值缓存的总数
        try:
            page_products = paginator.page(page)
            page_products.object_list = page_products_qs  # 覆盖查询集
        except PageNotAnInteger:
            page_products = paginator.page(1)
            page_products.object_list = products_query[:page_size]
        except EmptyPage:
            page_products = paginator.page(paginator.num_pages)
            page_products.object_list = products_query[(paginator.num_pages - 1) * page_size:]

        # 数据序列化
        product_list = []
        for product in page_products:
            product_list.append({
                'id': product.id,
                'name': product.name,
                'price': product.price,
                'unit': product.unit,
                'stock': product.stock,
                'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()],
                'status': 1
            })

        cache.set(cache_key, {
            'product_list': product_list,
            'paginator': paginator,
            'page_products': page_products
        }, CACHE_COMMON)

    # 区域缓存
    areas = cache.get(KEY_AREA)
    if not areas:
        areas = list(Area.objects.only('id', 'name'))
        cache.set(KEY_AREA, areas, CACHE_AREA)

    return render(request, 'product/product_manage.html', {
        'products': product_list,
        'paginator': paginator,
        'page_products': page_products,
        'keyword': keyword,
        'areas': areas,
        'can_add_product': request.user.has_permission(PERM_PRODUCT_ADD),
        'can_edit_product': request.user.has_permission(PERM_PRODUCT_EDIT),
        'can_delete_product': request.user.has_permission(PERM_PRODUCT_DELETE),
        'can_import_product': request.user.has_permission(PERM_PRODUCT_IMPORT),
        'can_stock_operation': request.user.has_permission(PERM_PRODUCT_STOCK_OP)
    })


# ====================== 商品CRUD（无修改，保留缓存清理） ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_ADD)
def product_add(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0').strip()
            unit = request.POST.get('unit', '件').strip()
            stock = request.POST.get('stock', '77').strip()

            if not name:
                return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
            if not price or float(price) < 0:
                return JsonResponse({'code': 0, 'msg': '请输入有效的单价'})

            product = Product.objects.create(
                name=name, price=float(price), unit=unit,
                stock=int(stock) if stock.isdigit() else 77
            )

            create_operation_log(
                request=request, op_type='create', obj_type='product',
                obj_id=product.id, obj_name=product.name,
                detail=f"新增商品：名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
            )

            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '商品新增成功', 'data': {
                'id': product.id, 'name': product.name, 'price': float(product.price),
                'unit': product.unit, 'stock': product.stock, 'aliases': []
            }})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '商品名称已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_EDIT)
def product_edit(request, pk):
    product = get_object_or_404(Product.objects.prefetch_related('aliases'), pk=pk)
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0').strip()
            unit = request.POST.get('unit', '件').strip()
            stock = request.POST.get('stock', '77').strip()

            if not name:
                return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
            if not price or float(price) < 0:
                return JsonResponse({'code': 0, 'msg': '请输入有效的单价'})
            if Product.objects.filter(name=name).exclude(id=pk).exists():
                return JsonResponse({'code': 0, 'msg': '商品名称已存在'})

            old_info = f"名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
            product.name = name
            product.price = float(price)
            product.unit = unit
            product.stock = int(stock) if stock.isdigit() else 77
            product.save()

            create_operation_log(
                request=request, op_type='update', obj_type='product',
                obj_id=product.id, obj_name=product.name,
                detail=f"编辑商品：原信息[{old_info}] → 新信息[名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}]"
            )

            clear_product_all_cache()
            aliases = [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]
            return JsonResponse({'code': 1, 'msg': '商品编辑成功', 'data': {
                'id': product.id, 'name': product.name, 'price': float(product.price),
                'unit': product.unit, 'stock': product.stock, 'aliases': aliases
            }})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_DELETE)
def product_delete(request, pk):
    try:
        product = get_object_or_404(Product, pk=pk)
        product_name = product.name
        product.delete()

        create_operation_log(
            request=request, op_type='delete', obj_type='product',
            obj_id=pk, obj_name=product_name, detail=f"删除商品：名称={product_name}，ID={pk}"
        )

        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '商品删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 别名CRUD（无修改） ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_ALIAS_ADD)
def alias_add(request):
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product_id', '')
            alias_name = request.POST.get('alias_name', '').strip()

            if not product_id or not alias_name:
                return JsonResponse({'code': 0, 'msg': '商品ID和别名不能为空'})
            product = get_object_or_404(Product, pk=product_id)

            alias = ProductAlias.objects.create(product=product, alias_name=alias_name)
            create_operation_log(
                request=request, op_type='create', obj_type='product_alias',
                obj_id=alias.id, obj_name=f"{product.name}-{alias.alias_name}",
                detail=f"为商品【{product.name}】新增别名：{alias.alias_name}"
            )

            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '别名新增成功', 'data': {
                'id': alias.id, 'alias_name': alias.alias_name
            }})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '该别名已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@csrf_exempt
@permission_required(PERM_PRODUCT_ALIAS_DELETE)
def alias_delete(request, pk):
    try:
        alias = get_object_or_404(ProductAlias, pk=pk)
        product_name = alias.product.name
        alias_name = alias.alias_name
        alias.delete()

        create_operation_log(
            request=request, op_type='delete', obj_type='product_alias',
            obj_id=pk, obj_name=f"{product_name}-{alias_name}",
            detail=f"删除商品【{product_name}】的别名：{alias_name}"
        )

        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '别名删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 商品数据接口（无修改） ======================
@csrf_exempt
@permission_required(PERM_PRODUCT_EDIT)
def product_edit_data(request, pk):
    product = get_object_or_404(Product.objects.prefetch_related('aliases'), pk=pk)
    return JsonResponse({
        'id': product.id, 'name': product.name, 'price': float(product.price),
        'unit': product.unit, 'stock': product.stock,
        'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]
    })


# ====================== 商品导入功能（无修改） ======================
@csrf_exempt
@require_POST
@permission_required(PERM_PRODUCT_IMPORT)
def product_import(request):
    try:
        if 'file' not in request.FILES:
            return JsonResponse({'code': 0, 'msg': '请选择要上传的Excel文件'})

        file = request.FILES['file']
        file_name = file.name
        if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
            return JsonResponse({'code': 0, 'msg': '仅支持xlsx/xls格式的Excel文件'})

        success_count = 0
        fail_count = 0
        fail_reasons = []

        if file_name.endswith('.xlsx'):
            wb = openpyxl.load_workbook(io.BytesIO(file.read()))
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
        else:
            wb = xlrd.open_workbook(file_contents=file.read())
            ws = wb.sheet_by_index(0)
            rows = [ws.row_values(i) for i in range(ws.nrows)]

        header_row = rows[0] if rows else []
        name_col = price_col = unit_col = -1
        for idx, header in enumerate(header_row):
            h = str(header).strip()
            if '商品名称' in h:
                name_col = idx
            elif '零售价' in h:
                price_col = idx
            elif '辅助单位' in h:
                unit_col = idx

        if name_col == -1:
            return JsonResponse({'code': 0, 'msg': 'Excel中未找到"商品名称"列'})

        existing_product_names = set(Product.objects.values_list('name', flat=True))
        new_products = []

        for row_num, row in enumerate(rows[1:], start=2):
            try:
                product_name = str(row[name_col]).strip() if len(row) > name_col else ''
                if not product_name:
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品名称为空')
                    continue

                if product_name in existing_product_names:
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品"{product_name}"已存在')
                    continue

                price = float(row[price_col]) if (price_col != -1 and len(row) > price_col and row[price_col]) else 0.0
                unit = str(row[unit_col]).strip() if (unit_col != -1 and len(row) > unit_col and row[unit_col]) else '件'

                new_products.append(Product(name=product_name, price=price, unit=unit, stock=77))
                existing_product_names.add(product_name)
                success_count += 1

            except Exception as e:
                fail_count += 1
                fail_reasons.append(f'第{row_num}行：导入失败 - {str(e)}')

        if new_products:
            Product.objects.bulk_create(new_products)

        import_detail = f"批量导入商品：成功{success_count}条，失败{fail_count}条。"
        if fail_reasons:
            import_detail += f" 失败原因：{' | '.join(fail_reasons[:5])}..." if len(fail_reasons) > 5 else ""
        create_operation_log(request=request, op_type='import', obj_type='product', detail=import_detail)

        clear_product_all_cache()

        msg = f'导入完成！成功{success_count}条，失败{fail_count}条'
        if fail_reasons:
            msg += f'。失败原因：{" | ".join(fail_reasons[:5])}...' if len(fail_reasons) > 5 else ""
        return JsonResponse({'code': 1, 'msg': msg, 'data': {
            'success_count': success_count, 'fail_count': fail_count, 'fail_reasons': fail_reasons
        }})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'导入失败：{str(e)}'})


# ====================== 快速出入库（核心修复：循环save → bulk_update 批量优化） ======================
@csrf_exempt
@require_POST
@permission_required(PERM_PRODUCT_STOCK_OP)
def quick_stock_operation(request):
    try:
        data = json.loads(request.body)
        items = data.get('items', [])
        if not items:
            return JsonResponse({'code': 0, 'msg': '无有效出入库数据'})

        with transaction.atomic():
            product_ids = [int(item.get('product_id')) for item in items if item.get('product_id')]
            if not product_ids:
                return JsonResponse({'code': 0, 'msg': '无有效商品ID'})

            product_map = {
                p.id: p for p in Product.objects.filter(id__in=product_ids).select_for_update()
            }

            operation_details = []
            success_count = 0
            update_products = []  # 收集待更新的商品对象

            for item in items:
                product_id = int(item.get('product_id', 0))
                in_qty = int(item.get('in_quantity', 0))
                out_qty = int(item.get('out_quantity', 0))

                if product_id not in product_map or (in_qty <= 0 and out_qty <= 0):
                    continue

                product = product_map[product_id]
                if out_qty > product.stock:
                    raise Exception(f'商品【{product.name}】出库数量{out_qty}超过库存{product.stock}')

                # 仅修改内存对象，不执行数据库更新
                old_stock = product.stock
                product.stock += in_qty - out_qty
                update_products.append(product)  # 加入批量更新列表

                detail = f"商品【{product.name}】："
                if in_qty > 0: detail += f"入库{in_qty}{product.unit}，"
                if out_qty > 0: detail += f"出库{out_qty}{product.unit}，"
                detail += f"库存{old_stock}→{product.stock}"
                operation_details.append(detail)
                success_count += 1

            if success_count > 0:
                # ====================== 核心修复：批量更新，仅执行1次DB操作 ======================
                Product.objects.bulk_update(update_products, ['stock'])

                create_operation_log(
                    request=request, op_type='stock_operation', obj_type='product',
                    detail=f"快速出入库：处理{success_count}个商品 | {' | '.join(operation_details)}"
                )

                clear_product_all_cache()
                return JsonResponse(
                    {'code': 1, 'msg': f'操作成功！处理{success_count}个商品', 'data': {'success_count': success_count}})
            return JsonResponse({'code': 0, 'msg': '无有效操作数据'})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'操作失败：{str(e)}'})


# ====================== 商品详情（核心修复：内存聚合→DB聚合 + 消除重复查询） ======================
@permission_required(PERM_PRODUCT_DETAIL)
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    cache_key = f"{CACHE_PREFIX_PRODUCT_DETAIL}{pk}"
    now = timezone.now()
    start_7d = now - timedelta(days=7)
    start_30d = now - timedelta(days=30)

    # 客户专属价格
    custom_prices = CustomerPrice.objects.filter(product=product) \
                        .select_related('customer').only('customer__name', 'custom_price')[:5]

    # ====================== 核心修复：复用1个基础查询集，消除重复查询 ======================
    base_items = OrderItem.objects.filter(
        product=product, order__status__in=['pending', 'printed', 'reopened']
    ).select_related('order')

    cache_data = cache.get(cache_key)
    if cache_data:
        total_sales = cache_data['total_sales']
        sales_7d = cache_data['sales_7d']
        sales_30d = cache_data['sales_30d']
        recent_sales = cache_data['recent_sales']
        customer_sales = cache_data['customer_sales']
    else:
        # ====================== 核心修复：数据库聚合，不加载数据到内存 ======================
        # 总销量（DB SUM）
        total_sales = base_items.aggregate(total=Sum('quantity'))['total'] or 0
        # 7天销量（DB SUM）
        sales_7d = base_items.filter(order__create_time__gte=start_7d).aggregate(total=Sum('quantity'))['total'] or 0
        # 30天销量（DB SUM）
        sales_30d = base_items.filter(order__create_time__gte=start_30d).aggregate(total=Sum('quantity'))['total'] or 0

        # 最近销售记录（仅查10条，无全量加载）
        recent_sales = []
        for item in base_items.annotate(unit_price=F('amount') / F('quantity')).order_by('-order__create_time')[:10]:
            recent_sales.append({
                'order_no': item.order.order_no,
                'customer_name': item.order.customer.name if item.order.customer else '未知客户',
                'quantity': item.quantity,
                'unit_price': float(item.unit_price or 0),
                'create_time': item.order.create_time,
                'is_settled': item.order.is_settled
            })

        # 客户排行（复用查询集）
        customer_sales = base_items.values(
            'order__customer__id', 'order__customer__name'
        ).annotate(buy_count=Count('id'), buy_quantity=Sum('quantity')).filter(
            order__customer__isnull=False
        ).order_by('-buy_quantity')[:10]

        cache.set(cache_key, {
            'total_sales': total_sales,
            'sales_7d': sales_7d,
            'sales_30d': sales_30d,
            'recent_sales': recent_sales,
            'customer_sales': customer_sales
        }, CACHE_COMMON)

    return render(request, 'product/product_detail.html', {
        'product': product,
        'custom_prices': custom_prices,
        'total_sales': total_sales,
        'sales_7d': sales_7d,
        'sales_30d': sales_30d,
        'recent_sales': recent_sales,
        'customer_sales': customer_sales,
        'product_unit': product.unit or '件',
        'can_edit_product': request.user.has_permission(PERM_PRODUCT_EDIT),
        'can_stock_operation': request.user.has_permission(PERM_PRODUCT_STOCK_OP)
    })


# ====================== 销售排行（无修改） ======================
@login_required
@permission_required('product_sales_rank')
def sales_rank(request):
    areas = cache.get(KEY_AREA)
    if not areas:
        areas = list(Area.objects.only('id', 'name'))
        cache.set(KEY_AREA, areas, CACHE_AREA)
    return render(request, 'product/sales_rank.html', {'areas': areas})


@login_required
@permission_required('product_sales_rank')
def sales_rank_data(request):
    try:
        sort_type = request.GET.get('sort', 'sales_volume')
        time_range = request.GET.get('time_range', 'today')
        area_id = request.GET.get('area_id', 'all')
        now = timezone.now()

        cache_key = f"{CACHE_PREFIX_SALES_RANK}{sort_type}:{time_range}:{area_id}"
        cached_data = cache.get(cache_key)

        if cached_data:
            return JsonResponse({'code': 1, 'msg': '获取成功', 'data': cached_data})

        if time_range == 'today':
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0)
        elif time_range == 'week':
            week_day = now.weekday()
            start_time = now - timedelta(days=week_day)
            start_time = datetime(start_time.year, start_time.month, start_time.day, 0, 0, 0)
        elif time_range == 'month':
            start_time = datetime(now.year, now.month, 1, 0, 0, 0)
        elif time_range == 'year':
            start_time = datetime(now.year, 1, 1, 0, 0, 0)
        else:
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0)

        query_filters = {
            'order__status__in': ['pending', 'printed', 'reopened'],
            'order__create_time__gte': start_time,
            'product__isnull': False
        }

        if area_id != 'all' and area_id.isdigit():
            query_filters['order__area_id'] = int(area_id)

        sales_data = OrderItem.objects.filter(**query_filters
                                              ).values(
            'product__id', 'product__name', 'product__unit'
        ).annotate(
            sales_volume=Sum('quantity'),
            sales_amount=Sum('amount')
        ).order_by(f'-{sort_type}')[:30]

        result = [{
            'product_id': item['product__id'],
            'product_name': item['product__name'],
            'unit': item['product__unit'],
            'sales_volume': item['sales_volume'],
            'sales_amount': float(item['sales_amount'] or 0.0)
        } for item in sales_data]

        cache.set(cache_key, result, CACHE_SALES_RANK)
        return JsonResponse({'code': 1, 'msg': '获取成功', 'data': result})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'获取数据失败：{str(e)}', 'data': []})