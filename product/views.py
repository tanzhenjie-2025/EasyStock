
from django.db import IntegrityError, transaction
from django.db.models.functions import Coalesce
from django.views.decorators.http import require_POST
import io
import openpyxl
import xlrd
from datetime import datetime, timedelta
from django.db.models import Sum, Count, Q, F, Prefetch, Case, When, DateTimeField, DecimalField
# ====================== 导出功能依赖导入 ======================
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse
from io import BytesIO
import logging
# ========== 缓存核心导入 ==========
from django.core.cache import cache
# ========== 导入依赖（和bill模块保持一致） ==========
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum, Count
from accounts.views import permission_required, create_operation_log
from accounts.models import ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR
from product.models import Product, StockIn, StockInItem, ProductPriceHistory
from bill.views import (
    # 复用缓存工具
    clear_stock_cache, clear_product_search_cache,
    # 复用AJAX装饰器
    ajax_login_required, ajax_permission_required
)
import json
import decimal
import logging

logger = logging.getLogger(__name__)

# ========== RBAC权限组件 ==========
from accounts.views import permission_required, create_operation_log
from accounts.models import (
    PERM_PRODUCT_VIEW, PERM_PRODUCT_ADD, PERM_PRODUCT_EDIT,
    PERM_PRODUCT_DELETE, PERM_PRODUCT_ALIAS_ADD, PERM_PRODUCT_ALIAS_DELETE,
    PERM_PRODUCT_IMPORT, PERM_PRODUCT_STOCK_OP, PERM_PRODUCT_DETAIL
)

# 业务模型
from bill.models import Order, OrderItem
from product.models import Product, ProductAlias
from area_manage.models import Area

# ====================== 缓存常量配置 ======================
CACHE_AREA = 3600
CACHE_COMMON = 60
CACHE_SALES_RANK = 10
CACHE_PAGINATION_COUNT = 120

CACHE_PREFIX_PRODUCT_LIST = "product:list:"
CACHE_PREFIX_PRODUCT_DETAIL = "product:detail:"
CACHE_PREFIX_SALES_RANK = "product:sales_rank:"
CACHE_PREFIX_PRODUCT_COUNT = "product:count:"
KEY_AREA = "area:data"
KEY_PRODUCT_ALIAS = "product:alias"

# ========== 入库模块权限常量 ==========
PERM_STOCK_IN_CREATE = 'stock_in_create'    # 新建入库
PERM_STOCK_IN_VIEW = 'stock_in_view'        # 查看入库
PERM_STOCK_IN_CANCEL = 'stock_in_cancel'    # 作废入库
# ====================== 缓存工具函数 ======================
def clear_product_all_cache():
    cache.delete_many([KEY_AREA, KEY_PRODUCT_ALIAS])
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_LIST}*"):
        cache.delete(key)
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_DETAIL}*"):
        cache.delete(key)
    for key in cache.keys(f"{CACHE_PREFIX_SALES_RANK}*"):
        cache.delete(key)
    for key in cache.keys(f"{CACHE_PREFIX_PRODUCT_COUNT}*"):
        cache.delete(key)

# ====================== 商品管理主页面 ======================
@permission_required(PERM_PRODUCT_VIEW)
def product_manage(request):
    page = request.GET.get('page', 1)
    keyword = request.GET.get('keyword', '').strip()
    status = request.GET.get('status', 'all')

    cache_key = f"{CACHE_PREFIX_PRODUCT_LIST}{keyword}:{page}:{status}"
    cached_data = cache.get(cache_key)

    if cached_data:
        product_list = cached_data['product_list']
        paginator = cached_data['paginator']
        page_products = cached_data['page_products']
        count_all = cached_data['count_all']
        count_active = cached_data['count_active']
        count_inactive = cached_data['count_inactive']
    else:
        products_query = Product.all_objects.order_by('name').only(
            'id', 'name', 'price', 'unit', 'stock_system', 'stock_actual', 'is_active'
        )
        alias_query = ProductAlias.all_objects.only('id', 'alias_name')
        products_query = products_query.prefetch_related(
            Prefetch('aliases', queryset=alias_query)
        )

        if status == 'active':
            products_query = products_query.filter(is_active=True)
        elif status == 'inactive':
            products_query = products_query.filter(is_active=False)

        if keyword:
            alias_product_ids = ProductAlias.all_objects.filter(
                Q(alias_name__icontains=keyword)
            ).values_list('product_id', flat=True)
            products_query = products_query.filter(
                Q(name__icontains=keyword) | Q(id__in=alias_product_ids)
            )

        count_all = Product.all_objects.count()
        count_active = Product.all_objects.filter(is_active=True).count()
        count_inactive = Product.all_objects.filter(is_active=False).count()

        count_cache_key = f"{CACHE_PREFIX_PRODUCT_COUNT}{keyword}:{status}"
        total_count = cache.get(count_cache_key)
        if total_count is None:
            total_count = products_query.count()
            cache.set(count_cache_key, total_count, CACHE_PAGINATION_COUNT)

        page_size = 15
        offset = (int(page) - 1) * page_size
        page_products_qs = products_query[offset:offset + page_size]

        paginator = Paginator(products_query, page_size)
        paginator._count = total_count
        try:
            page_products = paginator.page(page)
            page_products.object_list = page_products_qs
        except PageNotAnInteger:
            page_products = paginator.page(1)
            page_products.object_list = products_query[:page_size]
        except EmptyPage:
            page_products = paginator.page(paginator.num_pages)
            page_products.object_list = products_query[(paginator.num_pages - 1) * page_size:]

        product_list = []
        for product in page_products:
            product_list.append({
                'id': product.id,
                'name': product.name,
                'price': product.price,
                'unit': product.unit,
                'stock_system': product.stock_system,
                'stock_actual': product.stock_actual,
                'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()],
                'status': 1 if product.is_active else 0
            })

        cache.set(cache_key, {
            'product_list': product_list,
            'paginator': paginator,
            'page_products': page_products,
            'count_all': count_all,
            'count_active': count_active,
            'count_inactive': count_inactive
        }, CACHE_COMMON)

    areas = cache.get(KEY_AREA)
    if not areas:
        areas = list(Area.objects.only('id', 'name'))
        cache.set(KEY_AREA, areas, CACHE_AREA)

    return render(request, 'product/product_manage.html', {
        'products': product_list,
        'paginator': paginator,
        'page_products': page_products,
        'keyword': keyword,
        'status': status,
        'count_all': count_all,
        'count_active': count_active,
        'count_inactive': count_inactive,
        'areas': areas,
        'can_add_product': request.user.has_permission(PERM_PRODUCT_ADD),
        'can_edit_product': request.user.has_permission(PERM_PRODUCT_EDIT),
        'can_delete_product': request.user.has_permission(PERM_PRODUCT_DELETE),
        'can_import_product': request.user.has_permission(PERM_PRODUCT_IMPORT),
        'can_stock_operation': request.user.has_permission(PERM_PRODUCT_STOCK_OP)
    })

# ====================== 商品CRUD ======================
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
                stock_system=int(stock) if stock.isdigit() else 77,
                stock_actual=int(stock) if stock.isdigit() else 77
            )

            create_operation_log(
                request=request, op_type='create', obj_type='product',
                obj_id=product.id, obj_name=product.name,
                detail=f"新增商品：名称={product.name}，单价={product.price}，单位={product.unit}，系统库存={product.stock_system}，实际库存={product.stock_actual}"
            )

            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '商品新增成功'})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '商品名称已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'新增失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})


@permission_required(PERM_PRODUCT_EDIT)
def product_edit(request, pk):
    product = get_object_or_404(Product.objects.prefetch_related('aliases'), pk=pk)
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            price = request.POST.get('price', '0').strip()
            unit = request.POST.get('unit', '件').strip()
            stock = request.POST.get('stock', '77').strip()
            # 可选：接收备注
            remark = request.POST.get('remark', '后台编辑').strip()

            if not name:
                return JsonResponse({'code': 0, 'msg': '商品名称不能为空'})
            if not price or float(price) < 0:
                return JsonResponse({'code': 0, 'msg': '请输入有效的单价'})
            if Product.objects.filter(name=name).exclude(id=pk).exists():
                return JsonResponse({'code': 0, 'msg': '商品名称已存在'})

            old_info = f"名称={product.name}，单价={product.price}，单位={product.unit}，系统库存={product.stock_system}"

            # 🔥 核心：检测价格变动
            old_price_val = product.price
            new_price_val = decimal.Decimal(price)

            product.name = name
            product.price = new_price_val
            product.unit = unit
            product.stock_system = int(stock) if stock.isdigit() else 77
            product.save()

            # 🔥 如果价格变了，写入历史表
            if old_price_val != new_price_val:
                ProductPriceHistory.objects.create(
                    product=product,
                    old_price=old_price_val,
                    new_price=new_price_val,
                    operator=request.user,
                    remark=remark
                )

            create_operation_log(
                request=request, op_type='update', obj_type='product',
                obj_id=product.id, obj_name=product.name,
                detail=f"编辑商品：原信息[{old_info}] → 新信息[名称={product.name}，单价={product.price}]"
            )

            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '商品编辑成功'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'编辑失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})
@permission_required(PERM_PRODUCT_DELETE)
def product_delete(request, pk):
    try:
        product = get_object_or_404(Product.all_objects, pk=pk)
        product.delete()
        create_operation_log(request=request, op_type='delete', obj_type='product', obj_id=pk, obj_name=product.name,
                             detail=f"禁用商品")
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '商品禁用成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'禁用失败：{str(e)}'})

@permission_required(PERM_PRODUCT_EDIT)
def product_restore(request, pk):
    try:
        product = get_object_or_404(Product.all_objects, pk=pk)
        product.is_active = True
        product.save(update_fields=['is_active'])
        create_operation_log(request=request, op_type='update', obj_type='product', obj_id=pk, obj_name=product.name,
                             detail=f"启用商品")
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '商品启用成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'启用失败：{str(e)}'})

# ====================== 🔥 行内编辑（仅修改系统库存） ======================
@require_POST
@permission_required(PERM_PRODUCT_EDIT)
def product_inline_update(request):
    try:
        pk = request.POST.get('id')
        field = request.POST.get('field')
        value = request.POST.get('value')
        product = get_object_or_404(Product, pk=pk)

        if field == 'price':
            product.price = float(value)
        elif field == 'stock_system':
            product.stock_system = int(value)
        else:
            return JsonResponse({'code': 0, 'msg': '无效字段'})

        product.save(update_fields=[field])
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '更新成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

# ====================== 🔥 状态开关 ======================
@require_POST
@permission_required(PERM_PRODUCT_EDIT)
def product_toggle_status(request):
    try:
        pk = request.POST.get('id')
        product = get_object_or_404(Product.all_objects, pk=pk)
        product.is_active = not product.is_active
        product.save(update_fields=['is_active'])
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'status': 1 if product.is_active else 0, 'msg': '状态已更新'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

# ====================== 🔥 批量操作 ======================
@require_POST
@permission_required(PERM_PRODUCT_DELETE)
def product_batch_operation(request):
    try:
        ids = json.loads(request.POST.get('ids', '[]'))
        action = request.POST.get('action')
        if not ids:
            return JsonResponse({'code': 0, 'msg': '请选择商品'})

        products = Product.all_objects.filter(id__in=ids)
        if action == 'enable':
            products.update(is_active=True)
            msg = '批量启用成功'
        elif action == 'disable':
            products.update(is_active=False)
            msg = '批量停用成功'
        else:
            return JsonResponse({'code': 0, 'msg': '无效操作'})

        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': msg})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

# ====================== 🔥 新增：实际库存校准接口 ======================
@require_POST
@permission_required(PERM_PRODUCT_STOCK_OP)
def product_stock_calibrate(request):
    """实际库存校准（核心功能）"""
    try:
        pk = request.POST.get('id')
        actual_stock = request.POST.get('actual_stock')
        product = get_object_or_404(Product, pk=pk)

        if not actual_stock or not actual_stock.isdigit() or int(actual_stock) < 0:
            return JsonResponse({'code': 0, 'msg': '请输入有效的实际库存'})

        old_actual = product.stock_actual
        product.stock_actual = int(actual_stock)
        product.save(update_fields=['stock_actual'])

        # 校准日志
        create_operation_log(
            request=request, op_type='calibrate_stock', obj_type='product',
            obj_id=product.id, obj_name=product.name,
            detail=f"库存校准：原实际库存={old_actual} → 新实际库存={product.stock_actual}，系统库存={product.stock_system}"
        )

        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '库存校准成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

# ====================== 别名CRUD（无修改） ======================
@permission_required(PERM_PRODUCT_ALIAS_ADD)
def alias_add(request):
    if request.method == 'POST':
        try:
            product_id = request.POST.get('product_id')
            alias_name = request.POST.get('alias_name').strip()
            product = get_object_or_404(Product, pk=product_id)
            alias = ProductAlias.objects.create(product=product, alias_name=alias_name)
            create_operation_log(request=request, op_type='create', obj_type='product_alias', obj_id=alias.id,
                                 obj_name=f"{product.name}-{alias_name}")
            clear_product_all_cache()
            return JsonResponse(
                {'code': 1, 'msg': '别名添加成功', 'data': {'id': alias.id, 'alias_name': alias.alias_name}})
        except IntegrityError:
            return JsonResponse({'code': 0, 'msg': '别名已存在'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': str(e)})
    return JsonResponse({'code': 0, 'msg': '请求方式错误'})

@permission_required(PERM_PRODUCT_ALIAS_DELETE)
def alias_delete(request, pk):
    try:
        alias = get_object_or_404(ProductAlias.all_objects, pk=pk)
        alias.delete()
        create_operation_log(request=request, op_type='delete', obj_type='product_alias', obj_id=pk,
                             obj_name=f"{alias.product.name}-{alias.alias_name}")
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '别名禁用成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

@permission_required(PERM_PRODUCT_EDIT)
def product_edit_data(request, pk):
    product = get_object_or_404(Product.objects.prefetch_related('aliases'), pk=pk)
    return JsonResponse({
        'id': product.id, 'name': product.name, 'price': float(product.price),
        'unit': product.unit, 'stock': product.stock_system,
        'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]
    })

# ====================== 导入/导出/快速出入库（仅修改系统库存） ======================
@require_POST
@permission_required(PERM_PRODUCT_IMPORT)
def product_import(request):
    try:
        if 'file' not in request.FILES:
            return JsonResponse({'code': 0, 'msg': '请选择Excel文件'})
        file = request.FILES['file']
        success_count = fail_count = 0
        fail_reasons = []

        if file.name.endswith('.xlsx'):
            wb = openpyxl.load_workbook(io.BytesIO(file.read()))
            rows = list(wb.active.iter_rows(values_only=True))
        else:
            wb = xlrd.open_workbook(file_contents=file.read())
            rows = [wb.sheet_by_index(0).row_values(i) for i in range(wb.sheet_by_index(0).nrows)]

        name_col = price_col = unit_col = -1
        for idx, h in enumerate(rows[0] if rows else []):
            h = str(h).strip()
            if '商品名称' in h:
                name_col = idx
            elif '零售价' in h:
                price_col = idx
            elif '辅助单位' in h:
                unit_col = idx

        existing = set(Product.objects.values_list('name', flat=True))
        new_products = []
        for i, row in enumerate(rows[1:], 2):
            try:
                name = str(row[name_col]).strip() if len(row) > name_col else ''
                if not name or name in existing:
                    fail_count += 1
                    continue
                price = float(row[price_col]) if (price_col != -1 and len(row) > price_col) else 0.0
                unit = str(row[unit_col]).strip() if (unit_col != -1 and len(row) > unit_col) else '件'
                new_products.append(Product(name=name, price=price, unit=unit, stock_system=77, stock_actual=77))
                existing.add(name)
                success_count += 1
            except:
                fail_count += 1

        if new_products:
            Product.objects.bulk_create(new_products)
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': f'成功{success_count}，失败{fail_count}'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

@require_POST
@permission_required(PERM_PRODUCT_STOCK_OP)
def quick_stock_operation(request):
    """快速出入库：仅操作系统库存"""
    try:
        data = json.loads(request.body)
        items = data.get('items', [])
        with transaction.atomic():
            pids = [int(i['product_id']) for i in items if i.get('product_id')]
            products = {p.id: p for p in Product.objects.filter(id__in=pids).select_for_update()}
            update_list = []
            for i in items:
                pid = int(i['product_id'])
                if pid not in products: continue
                p = products[pid]
                in_q = int(i.get('in_quantity', 0))
                out_q = int(i.get('out_quantity', 0))
                if out_q > p.stock_system:
                    raise Exception(f'{p.name} 系统库存不足')
                p.stock_system += in_q - out_q
                update_list.append(p)
            Product.objects.bulk_update(update_list, ['stock_system'])
            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '出入库成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})

# 导出/详情/排行/库存列表（无修改，仅适配字段）
def export_to_excel(data, title, headers, selected_fields, custom_fields, file_name):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    final = selected_fields.copy()
    for cf in custom_fields:
        final.insert(final.index(cf['target']) + 1, f'custom_{cf["name"]}')
    for i, h in enumerate([headers[f] for f in selected_fields], 1):
        ws.cell(1, i, h)
    for r, d in enumerate(data, 2):
        for c, f in enumerate(selected_fields, 1):
            ws.cell(r, c, d.get(f, ''))
    buffer = BytesIO()
    wb.save(buffer)
    return HttpResponse(buffer.getvalue(), content_type='application/vnd.ms-excel')

@login_required
@permission_required(PERM_PRODUCT_IMPORT)
def product_export(request):
    try:
        keyword = request.POST.get('keyword', request.GET.get('keyword', '')).strip()
        status = request.POST.get('status', request.GET.get('status', 'all'))
        selected_fields = request.POST.getlist('fields[]')
        custom_fields_json = request.POST.get('custom_fields', '[]')

        if not selected_fields:
            selected_fields = ['serial', 'id', 'name', 'price', 'unit', 'stock_system', 'stock_actual', 'aliases', 'status']

        try:
            custom_fields = json.loads(custom_fields_json)
        except Exception:
            custom_fields = []

        products_query = Product.objects.all()
        if status == 'active':
            products_query = products_query.filter(is_active=True)
        elif status == 'inactive':
            products_query = products_query.filter(is_active=False)

        if keyword:
            alias_product_ids = ProductAlias.objects.filter(
                Q(alias_name__icontains=keyword)
            ).values_list('product_id', flat=True)
            products_query = products_query.filter(
                Q(name__icontains=keyword) | Q(id__in=alias_product_ids)
            )

        products = products_query.prefetch_related('aliases').order_by('name')
        field_config = {
            'serial': {'header': '序号', 'width': 8},
            'id': {'header': 'ID', 'width': 8},
            'name': {'header': '商品名称', 'width': 20},
            'price': {'header': '单价（元）', 'width': 12},
            'unit': {'header': '单位', 'width': 8},
            'stock_system': {'header': '系统库存', 'width': 10},
            'stock_actual': {'header': '实际库存', 'width': 10},
            'aliases': {'header': '别名', 'width': 20},
            'status': {'header': '状态', 'width': 8}
        }

        final_fields = selected_fields.copy()
        offset_map = {}
        for cf in custom_fields:
            target = cf['target']
            if target in final_fields:
                base_idx = final_fields.index(target)
                actual_idx = base_idx + offset_map.get(target, 0)
                custom_key = f'custom_{cf["name"]}'
                if cf['position'] == 'after':
                    final_fields.insert(actual_idx + 1, custom_key)
                else:
                    final_fields.insert(actual_idx, custom_key)
                offset_map[target] = offset_map.get(target, 0) + 1
                field_config[custom_key] = {'header': cf['name'], 'width': 15}

        wb = Workbook()
        ws = wb.active
        ws.title = "商品列表"

        for col_num, field in enumerate(final_fields, 1):
            cfg = field_config.get(field, {'header': field})
            cell = ws.cell(row=1, column=col_num, value=cfg['header'])
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
            ws.column_dimensions[cell.column_letter].width = cfg.get('width', 12)

        for row_num, product in enumerate(products, 2):
            col_num = 1
            for field in final_fields:
                value = ''
                if field == 'serial':
                    value = row_num - 1
                elif field == 'id':
                    value = product.id
                elif field == 'name':
                    value = product.name
                elif field == 'price':
                    value = float(product.price)
                    ws.cell(row=row_num, column=col_num).number_format = '0.00'
                elif field == 'unit':
                    value = product.unit
                elif field == 'stock_system':
                    value = product.stock_system
                elif field == 'stock_actual':
                    value = product.stock_actual
                elif field == 'aliases':
                    value = ','.join([a.alias_name for a in product.aliases.all()])
                elif field == 'status':
                    value = '启用' if product.is_active else '停用'
                elif field.startswith('custom_'):
                    value = ''
                ws.cell(row=row_num, column=col_num, value=value)
                col_num += 1

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename=商品列表_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        return response

    except Exception as e:
        logger.error(f"导出失败: {str(e)}")
        return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'})


# ===================== 修改：商品详情主视图 =====================
@permission_required(PERM_PRODUCT_DETAIL)
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    # 1. 最近售卖订单列表 (分页20条，展示当时售价)
    # 排除作废订单
    valid_status = ['pending', 'printed', 'reopened']
    recent_sales_qs = OrderItem.objects.filter(
        product_id=pk,
        order__status__in=valid_status
    ).select_related('order', 'order__customer').order_by('-order__create_time')

    paginator = Paginator(recent_sales_qs, 20)
    page = request.GET.get('page', 1)
    try:
        recent_sales = paginator.page(page)
    except PageNotAnInteger:
        recent_sales = paginator.page(1)
    except EmptyPage:
        recent_sales = paginator.page(paginator.num_pages)

    # 2. 价格变更历史
    price_history = ProductPriceHistory.objects.filter(product=product).select_related('operator')[:50]  # 限制展示条数

    return render(request, 'product/product_detail.html', {
        'product': product,
        'recent_sales': recent_sales,
        'price_history': price_history,
    })

@login_required
def sales_rank(request):
    return render(request, 'product/sales_rank.html')

@login_required
def sales_rank_data(request):
    data = OrderItem.objects.values('product__name').annotate(total=Sum('quantity')).order_by('-total')[:30]
    return JsonResponse({'data': [{'name': i['product__name'], 'num': i['total']} for i in data]})

@login_required
def stock_list(request):
    kw = request.GET.get('keyword', '')
    qs = Product.objects.filter(name__icontains=kw)
    page = Paginator(qs, 10).get_page(request.GET.get('page', 1))
    return render(request, 'product/stock.html', {'products': page})

# ========== 1. 入库首页（替换开单首页，无客户搜索） ==========
@login_required
@permission_required(PERM_STOCK_IN_CREATE)
def stock_in_index(request):
    """快速入库首页"""
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    return render(request, 'product/stock_in_index.html', {
        'is_super_admin': is_super_admin
    })

# ========== 2. 保存入库单（核心：增加库存） ==========
@login_required
@permission_required(PERM_STOCK_IN_CREATE)
def save_stock_in(request):
    """保存入库单 + 增加商品库存"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '请求方式错误'})

    try:
        with transaction.atomic():
            data = json.loads(request.body)
            items = data.get('items', [])

            if not items:
                return JsonResponse({'code': 0, 'msg': '请填写入库商品'})

            # 校验商品数据
            product_ids = []
            item_map = {}
            for item in items:
                pid = item.get('id')
                try:
                    pid = int(pid)
                except:
                    return JsonResponse({'code': 0, 'msg': '商品ID格式错误'})

                qty = item.get('qty', 0)
                price = item.get('price', 0)
                if not pid or qty <= 0:
                    return JsonResponse({'code': 0, 'msg': '商品数量必须大于0'})

                product_ids.append(pid)
                item_map[pid] = {'qty': qty, 'price': decimal.Decimal(str(price))}

            # 批量查询商品
            products = Product.objects.filter(id__in=product_ids).in_bulk()
            for pid in product_ids:
                if pid not in products:
                    return JsonResponse({'code': 0, 'msg': f'商品ID {pid} 不存在'})

            # 创建入库单
            stock_in = StockIn()
            stock_in.creator = request.user
            total_amount = 0
            stock_in_items = []

            for pid in product_ids:
                product = products[pid]
                qty = item_map[pid]['qty']
                price = item_map[pid]['price']
                amount = price * qty
                total_amount += amount

                stock_in_items.append(StockInItem(
                    stock_in=stock_in,
                    product=product,
                    quantity=qty,
                    amount=amount,
                    actual_unit_price=price
                ))

            stock_in.total_amount = total_amount
            stock_in.save()
            StockInItem.objects.bulk_create(stock_in_items)

            # ✅ 核心：入库 = 增加库存
            for pid in product_ids:
                products[pid].stock_system += item_map[pid]['qty']
            Product.objects.bulk_update(products.values(), ['stock_system'])

            # 日志+清理缓存
            create_operation_log(request, 'create_stock_in', 'stock_in', str(stock_in.id),
                                f"入库单-{stock_in.stock_in_no}", "创建入库单")
            clear_stock_cache()
            clear_product_search_cache()

            return JsonResponse({'code': 1, 'msg': '入库成功', 'stock_in_no': stock_in.stock_in_no})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'入库失败：{str(e)}'})

# ========== 3. 入库单列表（替换订单列表） ==========
@login_required
@permission_required(PERM_STOCK_IN_VIEW)
def stock_in_list(request):
    """入库单列表页"""
    stock_in_no = request.GET.get('stock_in_no', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    status = request.GET.get('status', 'all')
    page = request.GET.get('page', 1)

    # 查询集
    stock_ins = StockIn.objects.select_related('creator').order_by('-create_time')

    # 权限控制
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    if not is_super_admin:
        stock_ins = stock_ins.filter(creator=request.user)

    # 状态筛选
    if status == 'normal':
        stock_ins = stock_ins.filter(status__in=['pending', 'completed'])
    elif status == 'cancelled':
        stock_ins = stock_ins.filter(status='cancelled')

    # 搜索筛选
    if stock_in_no:
        stock_ins = stock_ins.filter(stock_in_no__startswith=stock_in_no)
    if date_from:
        try:
            start = timezone.make_aware(datetime.datetime.strptime(date_from, '%Y-%m-%d'))
            stock_ins = stock_ins.filter(create_time__gte=start)
        except:
            pass
    if date_to:
        try:
            end_date = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
            end = timezone.make_aware(datetime.datetime.combine(end_date + datetime.timedelta(days=1), datetime.datetime.min.time()))
            stock_ins = stock_ins.filter(create_time__lt=end)
        except:
            pass

    # 分页
    paginator = Paginator(stock_ins, 10)
    try:
        page_data = paginator.page(page)
    except:
        page_data = paginator.page(1)

    # 统计
    stats = stock_ins.aggregate(
        total=Count('id'),
        total_amount=Sum('total_amount', default=decimal.Decimal('0.00'))
    )

    # 作废权限
    current_time = timezone.now()
    data_list = list(page_data)
    for item in data_list:
        time_diff = (current_time - item.create_time).total_seconds() / 60
        item.can_cancel = (
            item.status != 'cancelled'
            and is_super_admin
            or (item.creator == request.user and time_diff <= 5)
        )

    context = {
        'stock_ins': data_list,
        'page_data': page_data,
        'paginator': paginator,
        'stock_in_no': stock_in_no,
        'date_from': date_from,
        'date_to': date_to,
        'status': status,
        'total': stats['total'],
        'total_amount': stats['total_amount'],
        'is_super_admin': is_super_admin,
    }
    return render(request, 'product/stock_in_list.html', context)

# ========== 4. 入库单详情（替换订单详情） ==========
@login_required
@permission_required(PERM_STOCK_IN_VIEW)
def stock_in_detail(request, stock_in_no):
    """入库单详情页"""
    stock_in = get_object_or_404(
        StockIn.objects.select_related('creator'),
        stock_in_no=stock_in_no
    )

    # 权限控制
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    if not is_super_admin and stock_in.creator != request.user:
        return redirect('product:stock_in_list')

    # 作废按钮判断
    current_time = timezone.now()
    time_diff = (current_time - stock_in.create_time).total_seconds() / 60
    show_cancel_btn = (
        stock_in.status != 'cancelled'
        and is_super_admin
        or (stock_in.creator == request.user and time_diff <= 5)
    )

    # 明细
    items = StockInItem.objects.select_related('product').filter(stock_in=stock_in)

    context = {
        'stock_in': stock_in,
        'items': items,
        'is_super_admin': is_super_admin,
        'show_cancel_btn': show_cancel_btn,
    }
    return render(request, 'product/stock_in_detail.html', context)

# ========== 5. 作废入库单（核心：回滚库存） ==========
@login_required
@permission_required(PERM_STOCK_IN_CANCEL)
def cancel_stock_in(request, stock_in_no):
    """作废入库单 + 减少库存"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST'})

    try:
        with transaction.atomic():
            stock_in = get_object_or_404(StockIn, stock_in_no=stock_in_no)

            # 校验
            if stock_in.status == 'cancelled':
                return JsonResponse({'code': 0, 'msg': '已作废，无需重复操作'})

            # 权限
            is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
            if not is_super_admin and stock_in.creator != request.user:
                return JsonResponse({'code': 0, 'msg': '仅能作废自己的入库单'})

            # 参数
            data = json.loads(request.body)
            reason = data.get('reason', '').strip()
            if not reason:
                return JsonResponse({'code': 0, 'msg': '请填写作废原因'})

            # 更新状态
            stock_in.status = 'cancelled'
            stock_in.cancelled_by = request.user
            stock_in.cancelled_time = timezone.now()
            stock_in.cancelled_reason = reason
            stock_in.save()

            # ✅ 核心：作废入库 = 减少库存
            items = stock_in.items.select_related('product')
            product_list = []
            for item in items:
                if item.product:
                    item.product.stock_system -= item.quantity
                    product_list.append(item.product)
            if product_list:
                Product.objects.bulk_update(product_list, ['stock_system'])

            # 日志+缓存
            create_operation_log(request, 'cancel_stock_in', 'stock_in', str(stock_in.id),
                                f"入库单-{stock_in.stock_in_no}", f"作废：{reason}")
            clear_stock_cache()

            return JsonResponse({'code': 1, 'msg': '作废成功'})

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'作废失败：{str(e)}'})


# ===================== 新增：商品详情统计 API =====================
@login_required
@permission_required(PERM_PRODUCT_DETAIL)
def product_statistics_api(request, pk):
    """
    异步统计接口：点击按钮后才计算
    优化：使用 select_related 关联 order，利用索引
    """
    product = get_object_or_404(Product, pk=pk)

    # 排除作废订单，只统计有效单据
    valid_status = ['pending', 'printed', 'reopened']

    # 利用索引 (product, order) 进行过滤
    items_qs = OrderItem.objects.filter(
        product_id=pk,
        order__status__in=valid_status
    ).select_related('order')  # 减少回表

    stats = items_qs.aggregate(
        total_qty=Coalesce(Sum('quantity'), 0),
        total_amount=Coalesce(Sum('amount'), 0, output_field=DecimalField()),
        count_orders=Count('order', distinct=True),
    )

    return JsonResponse({
        'code': 1,
        'data': {
            'total_qty': stats['total_qty'],
            'total_amount': float(stats['total_amount']),
            'count_orders': stats['count_orders'],
        }
    })