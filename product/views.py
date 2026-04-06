from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.db import IntegrityError, transaction
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

# ====================== 新增：导出功能依赖导入 ======================
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse
from io import BytesIO
import logging
# ========== 缓存核心导入 ==========
from django.core.cache import cache

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
from customer_manage.models import CustomerPrice, RepaymentRecord
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
            'id', 'name', 'price', 'unit', 'stock', 'is_active'
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
                'stock': product.stock,
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
                stock=int(stock) if stock.isdigit() else 77
            )

            create_operation_log(
                request=request, op_type='create', obj_type='product',
                obj_id=product.id, obj_name=product.name,
                detail=f"新增商品：名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
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


# ====================== 🔥 新增：行内编辑（价格/库存）接口 ======================
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
        elif field == 'stock':
            product.stock = int(value)
        else:
            return JsonResponse({'code': 0, 'msg': '无效字段'})

        product.save(update_fields=[field])
        clear_product_all_cache()
        return JsonResponse({'code': 1, 'msg': '更新成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})


# ====================== 🔥 新增：状态开关切换接口 ======================
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


# ====================== 🔥 新增：批量操作接口 ======================
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


# ====================== 别名CRUD ======================
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
        'unit': product.unit, 'stock': product.stock,
        'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]
    })


# ====================== 导入/导出/快速出入库/详情/排行（原有功能保留） ======================
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
                new_products.append(Product(name=name, price=price, unit=unit, stock=77))
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
                if out_q > p.stock:
                    raise Exception(f'{p.name} 库存不足')
                p.stock += in_q - out_q
                update_list.append(p)
            Product.objects.bulk_update(update_list, ['stock'])
            clear_product_all_cache()
            return JsonResponse({'code': 1, 'msg': '出入库成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': str(e)})


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
        # 获取筛选参数 (支持 POST)
        keyword = request.POST.get('keyword', request.GET.get('keyword', '')).strip()
        status = request.POST.get('status', request.GET.get('status', 'all'))

        # 获取导出字段和自定义字段配置
        selected_fields = request.POST.getlist('fields[]')
        custom_fields_json = request.POST.get('custom_fields', '[]')

        # 默认字段（如果前端没传，保持向后兼容）
        if not selected_fields:
            selected_fields = ['serial', 'id', 'name', 'price', 'unit', 'stock', 'aliases', 'status']

        try:
            custom_fields = json.loads(custom_fields_json)
        except Exception:
            custom_fields = []

        # 筛选商品数据
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

        # 定义字段映射与表头
        field_config = {
            'serial': {'header': '序号', 'width': 8},
            'id': {'header': 'ID', 'width': 8},
            'name': {'header': '商品名称', 'width': 20},
            'price': {'header': '单价（元）', 'width': 12},
            'unit': {'header': '单位', 'width': 8},
            'stock': {'header': '库存', 'width': 10},
            'aliases': {'header': '别名', 'width': 20},
            'status': {'header': '状态', 'width': 8}
        }

        # 1. 构建最终的字段列表（插入自定义字段）
        final_fields = selected_fields.copy()
        # 用于记录插入位置，防止重复插入导致索引错乱
        offset_map = {}

        for cf in custom_fields:
            target = cf['target']
            if target in final_fields:
                # 计算实际插入位置
                base_idx = final_fields.index(target)
                # 如果有在它之前插入的，索引要加上偏移量
                actual_idx = base_idx + offset_map.get(target, 0)

                custom_key = f'custom_{cf["name"]}'

                if cf['position'] == 'after':
                    final_fields.insert(actual_idx + 1, custom_key)
                    # 更新偏移量：在 target 之后插入，所有在 target 之后的基准索引都要+1
                    # 简单处理：只记录当前 target 的偏移
                    offset_map[target] = offset_map.get(target, 0) + 1
                else:
                    final_fields.insert(actual_idx, custom_key)
                    offset_map[target] = offset_map.get(target, 0) + 1

                # 添加到字段配置
                field_config[custom_key] = {'header': cf['name'], 'width': 15}

        # 生成 Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "商品列表"

        # 2. 写入表头
        for col_num, field in enumerate(final_fields, 1):
            cfg = field_config.get(field, {'header': field})
            cell = ws.cell(row=1, column=col_num, value=cfg['header'])
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
            # 设置列宽
            ws.column_dimensions[cell.column_letter].width = cfg.get('width', 12)

        # 3. 写入数据
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
                    # 设置数字格式
                    ws.cell(row=row_num, column=col_num).number_format = '0.00'
                elif field == 'unit':
                    value = product.unit
                elif field == 'stock':
                    value = product.stock
                elif field == 'aliases':
                    value = ','.join([a.alias_name for a in product.aliases.all()])
                elif field == 'status':
                    value = '启用' if product.is_active else '停用'
                elif field.startswith('custom_'):
                    # 自定义字段留空
                    value = ''

                ws.cell(row=row_num, column=col_num, value=value)
                col_num += 1

        # 保存到内存
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        # 返回响应
        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response[
            'Content-Disposition'] = f'attachment; filename=商品列表_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        return response

    except Exception as e:
        logger.error(f"导出失败: {str(e)}")
        return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'})


@permission_required(PERM_PRODUCT_DETAIL)
def product_detail(request, pk):
    p = get_object_or_404(Product, pk=pk)
    return render(request, 'product/product_detail.html', {'product': p})


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