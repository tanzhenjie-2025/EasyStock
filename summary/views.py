from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
# ========== 新增缓存导入 ==========
from django.views.decorators.cache import cache_page
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from io import BytesIO
import json

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Sum, F, DecimalField, Q, Prefetch
from django.db.models.functions import Coalesce

# ========== 缓存时长常量配置 ==========
# 高优先级：复杂聚合查询缓存 5分钟 (300秒)
CACHE_HIGH_PRIORITY = 300
# 中优先级：静态数据缓存 10分钟 (600秒)
CACHE_MID_PRIORITY = 600

# ========== 通用优化函数 ==========
def parse_datetime(date_str):
    """通用时间解析函数"""
    try:
        return datetime.strptime(date_str.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return None

def get_area_ids_by_group(group_id):
    """通用获取区域ID列表"""
    if group_id == '0':
        return Area.objects.all().values_list('id', flat=True)
    try:
        group = AreaGroup.objects.get(id=group_id)
        return group.areas.values_list('id', flat=True)
    except AreaGroup.DoesNotExist:
        return []

# ========== 核心导入：用户管理模块的RBAC体系 ==========
from accounts.models import (
    User, Role, Permission,
    ROLE_SUPER_ADMIN, PERM_ORDER_SUMMARY, PERM_PRODUCT_VIEW
)
from accounts.views import permission_required, create_operation_log, get_client_ip

# ========== 原有模型导入 ==========
from bill.models import Product, Order, OrderItem, AreaGroup, Area, Customer
from operation_log.models import OperationLog
from django.utils import timezone

# ========== 通用日志记录函数 ==========
def create_summary_operation_log(request, operation_type, object_type, object_id=None, object_name=None,
                                 operation_detail=None):
    create_operation_log(
        request=request,
        op_type=operation_type,
        obj_type=object_type,
        obj_id=object_id,
        obj_name=object_name,
        detail=operation_detail
    )

# ========== 核心业务视图 ==========
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def summary_page(request):
    """商品汇总页面"""
    return render(request, 'summary/summary.html')

@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
# 🔥 高优缓存：商品汇总复杂聚合接口 5分钟
@cache_page(CACHE_HIGH_PRIORITY)
def summary_by_group(request):
    """商品汇总接口 - 优化：数据库聚合 + 无N+1"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 处理区域
    area_ids = []
    if group_id == '0':
        area_ids = Area.objects.all().values_list('id', flat=True)
        group_name = '全部区域'
    else:
        try:
            group = AreaGroup.objects.get(id=group_id)
            group_name = group.name
            area_ids = group.areas.values_list('id', flat=True)
        except AreaGroup.DoesNotExist:
            return JsonResponse({'code': 0, 'msg': '分组不存在'})

    # 时间校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误'})

    # 🔥 修复语法：ORM链式调用连续写，不换行断裂
    items = OrderItem.objects.filter(
        order__area_id__in=area_ids,
        order__create_time__gte=start,
        order__create_time__lte=end
    ).exclude(
        order__status='cancelled'
    ).select_related('product').values(
        'product__id',
        'product__name',
        'product__unit',
        'product__price'
    ).annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('amount')
    ).order_by('-total_qty')

    # 🔥 数据库层面聚合总金额
    total_amount = items.aggregate(
        total=Coalesce(Sum('total_amt'), 0, output_field=DecimalField())
    )['total']

    # 数据组装
    data = []
    for idx, item in enumerate(items, 1):
        data.append({
            'serial': idx,
            'pid': item['product__id'],
            'name': item['product__name'],
            'unit': item['product__unit'],
            'price': float(item['product__price']),
            'total_qty': item['total_qty'] or 0,
            'total_amt': float(item['total_amt'] or 0),
            'remark': ''
        })

    # 日志
    create_summary_operation_log(
        request=request, operation_type='query', object_type='product_summary',
        object_name=f'商品汇总-{group_name}',
        operation_detail=f'查询{group_name} {start}至{end}，返回{len(data)}条数据'
    )

    return JsonResponse({
        'code': 1, 'data': data, 'total_amount': float(total_amount)
    })

@login_required
@permission_required(PERM_ORDER_SUMMARY)
# 📊 中优缓存：区域组列表静态数据 10分钟
@cache_page(CACHE_MID_PRIORITY)
def group_list(request):
    """区域组列表"""
    try:
        groups = AreaGroup.objects.all().order_by('name')
        group_list = [{'id': '0', 'name': '全部区域'}]
        group_list.extend([{'id': group.id, 'name': group.name} for group in groups])
        return JsonResponse(group_list, safe=False)
    except Exception as e:
        create_summary_operation_log(request=request, operation_type='error', object_type='group_list')
        return JsonResponse({'code': 0, 'msg': f'加载失败：{str(e)}'}, status=400)

@login_required
@permission_required(PERM_ORDER_SUMMARY)
def customer_summary_page(request):
    """客户汇总页面"""
    return render(request, 'summary/customer_summary.html')

@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
# 🔥 高优缓存：客户汇总复杂聚合接口 5分钟
@cache_page(CACHE_HIGH_PRIORITY)
def summary_customer_by_group(request):
    """客户汇总接口 - 优化：数据库聚合 + 无N+1"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 时间校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误'})

    # 处理区域
    area_ids = []
    if group_id == '0':
        area_ids = Area.objects.all().values_list('id', flat=True)
    else:
        try:
            group = AreaGroup.objects.get(id=group_id)
            area_ids = group.areas.values_list('id', flat=True)
            if not area_ids:
                return JsonResponse({'code': 0, 'msg': '该区域组未关联任何区域'})
        except AreaGroup.DoesNotExist:
            return JsonResponse({'code': 0, 'msg': '分组不存在'})

    # 🔥 修复语法：连续链式调用
    customer_summary = Order.objects.filter(
        area_id__in=area_ids, create_time__gte=start, create_time__lte=end, customer__isnull=False
    ).exclude(
        status='cancelled'
    ).select_related('customer', 'area').values(
        'customer__id', 'customer__name', 'customer__remark'
    ).annotate(
        total_amount=Sum('total_amount')
    ).order_by('-total_amount')

    # 🔥 数据库聚合总金额
    total_amount = customer_summary.aggregate(
        total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
    )['total']

    # 数据组装
    data = []
    for idx, item in enumerate(customer_summary, 1):
        data.append({
            'serial': idx, 'customer_id': item['customer__id'],
            'customer_name': item['customer__name'], 'total_amount': float(item['total_amount'] or 0),
            'remark': item['customer__remark'] or ''
        })

    create_summary_operation_log(request=request, operation_type='query', object_type='customer_summary')
    return JsonResponse({
        'code': 1, 'data': data, 'total_amount': float(total_amount),
        'msg': '查询成功' if data else '无消费数据'
    })

# ========== Excel导出函数（无修改） ==========
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
@csrf_exempt
def export_product_summary(request):
    """商品导出 - 优化：数据库聚合替代内存sum"""
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

            start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
            end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')

            # 处理区域
            if group_id == '0':
                area_ids = Area.objects.all().values_list('id', flat=True)
                group_name = '全部区域'
            else:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
                area_ids = group.areas.values_list('id', flat=True)

            today = date.today().strftime('%Y%m%d')
            file_name = f'{today}商品汇总_{group_name}'

            # 🔥 修复语法
            items = OrderItem.objects.filter(
                order__area_id__in=area_ids, order__create_time__gte=start, order__create_time__lte=end
            ).exclude(order__status='cancelled').select_related('product').values(
                'product__id', 'product__name', 'product__unit', 'product__price'
            ).annotate(total_qty=Sum('quantity'), total_amt=Sum('amount')).order_by('-total_qty')

            # 🔥 数据库聚合总金额
            total_amount = items.aggregate(
                total=Coalesce(Sum('total_amt'), 0, output_field=DecimalField())
            )['total']

            # 组装导出数据
            export_data = [{
                'serial': idx, 'name': item['product__name'], 'unit': item['product__unit'],
                'price': float(item['product__price']), 'total_qty': item['total_qty'] or 0,
                'total_amt': float(item['total_amt'] or 0), 'remark': ''
            } for idx, item in enumerate(items, 1)]

            total_row = {'total_amt': total_amount}
            create_summary_operation_log(request=request, operation_type='export', object_type='product_summary')
            return export_to_excel(
                data=export_data, title='商品汇总', headers={
                    'serial': '序号', 'name': '商品名称', 'unit': '单位', 'price': '单价',
                    'total_qty': '数量', 'total_amt': '总金额', 'remark': '备注'
                }, selected_fields=selected_fields, custom_fields=custom_fields,
                file_name=file_name, total_row=total_row
            )
        except Exception as e:
            create_summary_operation_log(request=request, operation_type='error', object_type='product_summary_export')
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)

@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
def export_customer_summary(request):
    """客户导出 - 优化：数据库聚合替代内存sum"""
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

            start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
            end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')

            if group_id == '0':
                area_ids = Area.objects.all().values_list('id', flat=True)
                group_name = '全部区域'
            else:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
                area_ids = group.areas.values_list('id', flat=True)

            today = date.today().strftime('%Y%m%d')
            file_name = f'{today}{group_name}'

            # 🔥 修复语法
            customer_summary = Order.objects.filter(
                area_id__in=area_ids, create_time__gte=start, create_time__lte=end, customer__isnull=False
            ).exclude(status='cancelled').select_related('customer').values(
                'customer__id', 'customer__name', 'customer__remark'
            ).annotate(total_amount=Sum('total_amount')).order_by('-total_amount')

            # 🔥 数据库聚合总金额
            total_amount = customer_summary.aggregate(
                total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
            )['total']

            export_data = [{
                'serial': idx, 'customer_name': item['customer__name'],
                'total_amount': float(item['total_amount'] or 0), 'remark': item['customer__remark'] or ''
            } for idx, item in enumerate(customer_summary, 1)]

            total_row = {'total_amount': total_amount}
            create_summary_operation_log(request=request, operation_type='export', object_type='customer_summary')
            return export_to_excel(
                data=export_data, title='客户汇总', headers={
                    'serial': '序号', 'customer_name': '客户名称',
                    'total_amount': '金额', 'remark': '备注'
                }, selected_fields=selected_fields, custom_fields=custom_fields,
                file_name=file_name, total_row=total_row
            )
        except Exception as e:
            create_summary_operation_log(request=request, operation_type='error', object_type='customer_summary_export')
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)

@login_required
@permission_required(PERM_PRODUCT_VIEW)
@csrf_exempt
# 📊 中优缓存：商品基础信息静态数据 10分钟
@cache_page(CACHE_MID_PRIORITY)
def product_list_for_price(request):
    """商品列表接口"""
    try:
        products = Product.objects.all().order_by('name')
        result = [{'id': p.id, 'name': p.name, 'price': float(p.price)} for p in products]
        create_summary_operation_log(request=request, operation_type='query', object_type='product_list')
        return JsonResponse(result, safe=False)
    except Exception as e:
        create_summary_operation_log(request=request, operation_type='error', object_type='product_list')
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)

@login_required
def customer_amount_detail_page(request, customer_id):
    """客户金额详情页"""
    try:
        customer = get_object_or_404(Customer, id=customer_id)
        return render(request, 'summary/amount_detail.html', {'customer': customer, 'customer_id': customer_id})
    except Exception as e:
        return render(request, 'error.html', {'error_msg': f'获取客户信息失败：{str(e)}'}, status=400)

@login_required
def get_customer_order_source(request, customer_id):
    """客户订单来源 - 优化：极致预加载，彻底消灭N+1"""
    try:
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))

        if not start_date or not end_date:
            return JsonResponse({'code': 0, 'msg': '缺少时间参数'}, status=400)
        start, end = parse_datetime(start_date), parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        customer = get_object_or_404(Customer, id=customer_id)

        # 🔥 修复语法 + 预加载
        orders = Order.objects.filter(
            customer_id=customer_id, create_time__gte=start, create_time__lte=end
        ).exclude(status='cancelled').select_related('customer', 'area').prefetch_related(
            Prefetch('items', queryset=OrderItem.objects.select_related('product'))
        ).order_by('-create_time')

        # 数据库聚合总金额
        total_amount = orders.aggregate(
            total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
        )['total']

        # 分页
        paginator = Paginator(orders, page_size)
        current_page = paginator.page(page) if page in paginator.page_range else paginator.page(1)

        # 数据组装
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
            'customer_name': customer.name, 'page': page, 'page_size': page_size, 'total_count': paginator.count
        })
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)

@login_required
@permission_required(PERM_ORDER_SUMMARY)
def product_summary_detail_page(request, product_id):
    """商品汇总详情页"""
    try:
        product = get_object_or_404(Product, id=product_id)
        group_id = request.GET.get('group_id', '0')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')

        group_name = '全部区域'
        if group_id != '0' and group_id.isdigit():
            try:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
            except AreaGroup.DoesNotExist:
                pass

        return render(request, 'summary/product_summary_detail.html', {
            'product': product, 'product_id': product_id, 'group_id': group_id,
            'group_name': group_name, 'start_date': start_date, 'end_date': end_date
        })
    except Exception as e:
        return render(request, 'error.html', {'error_msg': f'获取商品信息失败：{str(e)}'}, status=400)

@login_required
@permission_required(PERM_ORDER_SUMMARY)
def get_product_order_source(request, product_id):
    """商品订单来源 - 优化：无N+1 + 数据库聚合"""
    try:
        group_id = request.GET.get('group_id', '0')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))

        if not start_date or not end_date:
            return JsonResponse({'code': 0, 'msg': '缺少时间参数'}, status=400)
        start, end = parse_datetime(start_date), parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        area_ids = get_area_ids_by_group(group_id)
        product = get_object_or_404(Product, id=product_id)

        # 🔥 修复：添加排序，解决分页警告（唯一修改点）
        order_items = OrderItem.objects.filter(
            product_id=product_id, order__area_id__in=area_ids,
            order__create_time__gte=start, order__create_time__lte=end
        ).exclude(order__status='cancelled').select_related('order__customer', 'order__area')
        # 新增排序：按订单创建时间 倒序
        order_items = order_items.order_by('-order__create_time')

        # 数据库聚合
        aggregate_data = order_items.aggregate(
            total_quantity=Coalesce(Sum('quantity'), 0),
            total_amount=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )

        # 分页
        paginator = Paginator(order_items, page_size)
        current_page = paginator.page(page) if page in paginator.page_range else paginator.page(1)

        # 数据组装
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