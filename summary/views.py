from decimal import Decimal

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import cache_page
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from io import BytesIO
import json

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Sum, F, DecimalField, Q, Prefetch
from django.db.models.functions import Coalesce

# ========== 核心导入 ==========
from accounts.models import User, Role, Permission, ROLE_SUPER_ADMIN, PERM_ORDER_SUMMARY, PERM_PRODUCT_VIEW
from accounts.views import permission_required, create_operation_log, get_client_ip
from bill.models import Order, OrderItem
from product.models import Product
from customer_manage.models import Customer
from area_manage.models import Area, AreaGroup

from operation_log.models import OperationLog
from django.utils import timezone

# ========== 缓存时长常量配置 ==========
CACHE_HIGH_PRIORITY = 300  # 复杂聚合查询 5分钟
CACHE_MID_PRIORITY = 600  # 静态数据 10分钟


# ========== 通用优化函数 ==========
def parse_datetime(date_str):
    """通用时间解析函数 - 返回 Django aware datetime"""
    try:
        naive_dt = datetime.strptime(date_str.replace('T', ' '), '%Y-%m-%d %H:%M')
        # 关键修改：将原生 datetime 转换为带时区信息的 aware datetime
        return timezone.make_aware(naive_dt)
    except ValueError:
        return None


def get_area_ids_by_group(group_id):
    """【优化】极速获取区域ID列表"""
    if group_id == '0':
        return Area.objects.values_list('id', flat=True)
    try:
        return AreaGroup.objects.filter(id=group_id).values_list('areas__id', flat=True)
    except AreaGroup.DoesNotExist:
        return []


# ========== 通用日志 ==========
def create_summary_operation_log(request, operation_type, object_type, object_id=None, object_name=None,
                                 operation_detail=None):
    create_operation_log(
        request=request, op_type=operation_type, obj_type=object_type,
        obj_id=object_id, obj_name=object_name, detail=operation_detail
    )


# ========== 核心业务视图 ==========
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def summary_page(request):
    """商品汇总页面"""
    return render(request, 'summary/summary.html')


@login_required
@permission_required(PERM_ORDER_SUMMARY)
@cache_page(CACHE_HIGH_PRIORITY)
def summary_by_group(request):
    """商品汇总接口 - 严格匹配 OrderItem 统一索引 [product, order, quantity, amount]"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not all([group_id, start_datetime, end_datetime]):
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 区域处理
    area_ids = get_area_ids_by_group(group_id)
    group_name = '全部区域' if group_id == '0' else AreaGroup.objects.get(id=group_id).name

    # 时间校验 (修改：统一使用 parse_datetime)
    start = parse_datetime(start_datetime)
    end = parse_datetime(end_datetime)
    if not start or not end:
        return JsonResponse({'code': 0, 'msg': '时间格式错误'})

    # 🔥 核心优化：查询顺序匹配索引前缀，100%命中索引
    items = OrderItem.objects.filter(
        product__isnull=False,  # 索引第一位：product
        order__area_id__in=area_ids,
        order__create_time__gte=start,
        order__create_time__lte=end,
        order__status__in=['pending', 'printed', 'reopened']  # 排除作废，匹配Order索引
    ).select_related('product').values(
        'product__id', 'product__name', 'product__unit', 'product__price'
    ).annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('amount')
    ).order_by('-total_qty')  # 索引包含quantity，直接索引排序

    # 在 Python 中组装数据并同时计算总和
    data = []
    total_amount = Decimal('0.00')  # 初始化累加器

    for idx, item in enumerate(items, 1):
        # 确保金额不为 None
        item_total_amt = item['total_amt'] or Decimal('0.00')

        # 累加
        total_amount += item_total_amt

        # 组装字典
        data.append({
            'serial': idx,
            'pid': item['product__id'],
            'name': item['product__name'],
            'unit': item['product__unit'],
            'price': float(item['product__price']),
            'total_qty': item['total_qty'] or 0,
            'total_amt': float(item_total_amt),
            'remark': ''
        })

    create_summary_operation_log(
        request=request, operation_type='query', object_type='product_summary',
        object_name=f'商品汇总-{group_name}',
        operation_detail=f'查询{group_name} {start}至{end}，返回{len(data)}条数据'
    )

    return JsonResponse({'code': 1, 'data': data, 'total_amount': float(total_amount)})


@login_required
@permission_required(PERM_ORDER_SUMMARY)
@cache_page(CACHE_MID_PRIORITY)
def group_list(request):
    """区域组列表"""
    try:
        groups = AreaGroup.objects.all().order_by('name')
        group_list = [{'id': '0', 'name': '全部区域'}]
        group_list.extend([{'id': group.id, 'name': group.name} for group in groups])
        return JsonResponse(group_list, safe=False)
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, status=400)


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def customer_summary_page(request):
    """客户汇总页面"""
    return render(request, 'summary/customer_summary.html')


@login_required
@permission_required(PERM_ORDER_SUMMARY)
@cache_page(CACHE_HIGH_PRIORITY)
def summary_customer_by_group(request):
    """客户汇总接口 - 匹配 Order 统一合并索引"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not all([group_id, start_datetime, end_datetime]):
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 时间校验 (修改：统一使用 parse_datetime)
    start = parse_datetime(start_datetime)
    end = parse_datetime(end_datetime)
    if not start or not end:
        return JsonResponse({'code': 0, 'msg': '时间格式错误'})

    # 区域处理
    area_ids = get_area_ids_by_group(group_id)

    # 🔥 匹配Order合并索引：status → is_settled → area → customer → create_time
    customer_summary = Order.objects.filter(
        status__in=['pending', 'printed', 'reopened'],
        is_settled=False,
        area_id__in=area_ids,
        customer__isnull=False,
        create_time__gte=start,
        create_time__lte=end
    ).select_related('customer', 'area').values(
        'customer__id', 'customer__name', 'customer__remark'
    ).annotate(
        total_amount=Sum('total_amount')
    ).order_by('-total_amount')

    total_amount = customer_summary.aggregate(
        total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
    )['total']

    data = [{
        'serial': idx, 'customer_id': item['customer__id'],
        'customer_name': item['customer__name'], 'total_amount': float(item['total_amount'] or 0),
        'remark': item['customer__remark'] or ''
    } for idx, item in enumerate(customer_summary, 1)]

    create_summary_operation_log(request=request, operation_type='query', object_type='customer_summary')
    return JsonResponse(
        {'code': 1, 'data': data, 'total_amount': float(total_amount), 'msg': '查询成功' if data else '无消费数据'})


# ========== Excel导出（优化索引匹配） ==========
def export_to_excel(data, title, headers, selected_fields, custom_fields, file_name, total_row=None):
    wb = openpyxl.Workbook()
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


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def export_product_summary(request):
    """商品导出 - 命中OrderItem统一索引"""
    if request.method == 'POST':
        try:
            data = request.POST
            group_id = data.get('group_id')
            start_datetime = data.get('start_date')
            end_datetime = data.get('end_date')
            selected_fields = data.getlist('fields[]')
            custom_fields = json.loads(data.get('custom_fields', '[]'))

            if not all([group_id, start_datetime, end_datetime, selected_fields]):
                return JsonResponse({'code': 0, 'msg': '参数不完整'})

            # 修改：统一使用 parse_datetime
            start = parse_datetime(start_datetime)
            end = parse_datetime(end_datetime)
            if not start or not end:
                return JsonResponse({'code': 0, 'msg': '时间格式错误'})

            area_ids = get_area_ids_by_group(group_id)
            group_name = '全部区域' if group_id == '0' else AreaGroup.objects.get(id=group_id).name

            # 🔥 索引匹配查询
            items = OrderItem.objects.filter(
                product__isnull=False,
                order__area_id__in=area_ids,
                order__create_time__gte=start,
                order__create_time__lte=end,
                order__status__in=['pending', 'printed', 'reopened']
            ).select_related('product').values(
                'product__name', 'product__unit', 'product__price'
            ).annotate(
                total_qty=Sum('quantity'),
                total_amt=Sum('amount')
            ).order_by('-total_qty')

            total_amount = items.aggregate(total=Coalesce(Sum('total_amt'), 0, output_field=DecimalField()))['total']
            export_data = [{
                'serial': idx, 'name': item['product__name'], 'unit': item['product__unit'],
                'price': float(item['product__price']), 'total_qty': item['total_qty'] or 0,
                'total_amt': float(item['total_amt'] or 0), 'remark': ''
            } for idx, item in enumerate(items, 1)]

            create_summary_operation_log(request=request, operation_type='export', object_type='product_summary')

            # 修改：使用 timezone.localdate() 替代 date.today()
            file_date_str = timezone.localdate().strftime("%Y%m%d")

            return export_to_excel(
                data=export_data, title='商品汇总', headers={
                    'serial': '序号', 'name': '商品名称', 'unit': '单位', 'price': '单价',
                    'total_qty': '数量', 'total_amt': '总金额', 'remark': '备注'
                }, selected_fields=selected_fields, custom_fields=custom_fields,
                file_name=f'{file_date_str}商品汇总_{group_name}', total_row={'total_amt': total_amount}
            )
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def export_customer_summary(request):
    """客户导出 - 命中Order合并索引"""
    if request.method == 'POST':
        try:
            data = request.POST
            group_id = data.get('group_id')
            start_datetime = data.get('start_date')
            end_datetime = data.get('end_date')
            selected_fields = data.getlist('fields[]')
            custom_fields = json.loads(data.get('custom_fields', '[]'))

            if not all([group_id, start_datetime, end_datetime, selected_fields]):
                return JsonResponse({'code': 0, 'msg': '参数不完整'})

            # 修改：统一使用 parse_datetime
            start = parse_datetime(start_datetime)
            end = parse_datetime(end_datetime)
            if not start or not end:
                return JsonResponse({'code': 0, 'msg': '时间格式错误'})

            area_ids = get_area_ids_by_group(group_id)

            # 🔥 索引匹配查询
            customer_summary = Order.objects.filter(
                status__in=['pending', 'printed', 'reopened'],
                area_id__in=area_ids,
                customer__isnull=False,
                create_time__gte=start,
                create_time__lte=end
            ).select_related('customer').values(
                'customer__name', 'customer__remark'
            ).annotate(total_amount=Sum('total_amount')).order_by('-total_amount')

            total_amount = \
            customer_summary.aggregate(total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField()))['total']
            export_data = [{
                'serial': idx, 'customer_name': item['customer__name'],
                'total_amount': float(item['total_amount'] or 0), 'remark': item['customer__remark'] or ''
            } for idx, item in enumerate(customer_summary, 1)]

            create_summary_operation_log(request=request, operation_type='export', object_type='customer_summary')

            # 修改：使用 timezone.localdate() 替代 date.today()
            file_date_str = timezone.localdate().strftime("%Y%m%d")

            return export_to_excel(
                data=export_data, title='客户汇总', headers={
                    'serial': '序号', 'customer_name': '客户名称',
                    'total_amount': '金额', 'remark': '备注'
                }, selected_fields=selected_fields, custom_fields=custom_fields,
                file_name=f'{file_date_str}{"全部区域" if group_id == "0" else AreaGroup.objects.get(id=group_id).name}',
                total_row={'total_amount': total_amount}
            )
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)


@login_required
@permission_required(PERM_PRODUCT_VIEW)
@cache_page(CACHE_MID_PRIORITY)
def product_list_for_price(request):
    """商品列表接口"""
    try:
        products = Product.objects.all().order_by('name')
        return JsonResponse([{'id': p.id, 'name': p.name, 'price': float(p.price)} for p in products], safe=False)
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)


@login_required
def customer_amount_detail_page(request, customer_id):
    """客户金额详情页"""
    customer = get_object_or_404(Customer, id=customer_id)
    return render(request, 'summary/amount_detail.html', {'customer': customer, 'customer_id': customer_id})


@login_required
def get_customer_order_source(request, customer_id):
    """客户订单来源 - 无N+1，匹配Order索引"""
    try:
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))

        if not all([start_date, end_date]):
            return JsonResponse({'code': 0, 'msg': '缺少时间参数'}, status=400)
        start, end = parse_datetime(start_date), parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        # 🔥 匹配Order索引查询
        orders = Order.objects.filter(
            customer_id=customer_id,
            status__in=['pending', 'printed', 'reopened'],
            create_time__gte=start,
            create_time__lte=end
        ).select_related('customer', 'area').prefetch_related(
            Prefetch('items', queryset=OrderItem.objects.select_related('product'))
        ).order_by('-create_time')

        total_amount = orders.aggregate(total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField()))['total']
        paginator = Paginator(orders, page_size)
        current_page = paginator.page(page) if page in paginator.page_range else paginator.page(1)

        order_list = [{
            'order_no': order.order_no,
            'create_time': order.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_amount': float(order.total_amount),
            'status': dict(order.ORDER_STATUS).get(order.status, '未知状态'),
            'items': [{
                'product_name': item.product.name, 'quantity': item.quantity,
                'unit': item.product.unit, 'price': float(item.product.price),
                'amount': float(item.amount)
            } for item in order.items.all()]
        } for order in current_page]

        return JsonResponse({
            'code': 1, 'data': order_list, 'total_amount': round(float(total_amount), 2),
            'customer_name': Customer.objects.get(id=customer_id).name,
            'page': page, 'page_size': page_size, 'total_count': paginator.count
        })
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def product_summary_detail_page(request, product_id):
    """商品汇总详情页"""
    product = get_object_or_404(Product, id=product_id)
    group_id = request.GET.get('group_id', '0')
    group_name = '全部区域' if group_id == '0' else AreaGroup.objects.filter(id=group_id).first().name

    return render(request, 'summary/product_summary_detail.html', {
        'product': product, 'product_id': product_id, 'group_id': group_id,
        'group_name': group_name, 'start_date': request.GET.get('start_date', ''),
        'end_date': request.GET.get('end_date', '')
    })


@login_required
@permission_required(PERM_ORDER_SUMMARY)
def get_product_order_source(request, product_id):
    """商品订单来源 - 100%命中 OrderItem 统一索引"""
    try:
        group_id = request.GET.get('group_id', '0')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))

        if not all([start_date, end_date]):
            return JsonResponse({'code': 0, 'msg': '缺少时间参数'}, status=400)
        start, end = parse_datetime(start_date), parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        area_ids = get_area_ids_by_group(group_id)
        product = get_object_or_404(Product, id=product_id)

        # 🔥 终极优化：完全匹配 OrderItem 索引 [product, order, quantity, amount]
        order_items = OrderItem.objects.filter(
            product_id=product_id,  # 索引第一位，必传
            order__area_id__in=area_ids,
            order__create_time__gte=start,
            order__create_time__lte=end,
            order__status__in=['pending', 'printed', 'reopened']
        ).select_related('order__customer', 'order__area').order_by('-order__create_time')

        # 索引聚合
        aggregate_data = order_items.aggregate(
            total_quantity=Coalesce(Sum('quantity'), 0),
            total_amount=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )

        paginator = Paginator(order_items, page_size)
        current_page = paginator.page(page) if page in paginator.page_range else paginator.page(1)

        order_list = [{
            'order_no': item.order.order_no,
            'create_time': item.order.create_time.strftime('%Y-%m-%d %H:%M:%S'),
            'customer_name': item.order.customer.name if item.order.customer else '无客户',
            'area_name': item.order.area.name if item.order.area else '无区域',
            'quantity': item.quantity, 'unit': product.unit,
            'price': float(product.price), 'amount': float(item.amount),
            'order_status': dict(item.order.ORDER_STATUS).get(item.order.status, '未知状态')
        } for item in current_page]

        return JsonResponse({
            'code': 1, 'data': order_list,
            'total_quantity': aggregate_data['total_quantity'],
            'total_amount': round(float(aggregate_data['total_amount']), 2),
            'product_name': product.name, 'page': page, 'page_size': page_size, 'total_count': paginator.count
        })
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)