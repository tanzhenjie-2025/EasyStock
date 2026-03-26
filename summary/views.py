from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from io import BytesIO
import json

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Sum, F, DecimalField
from django.db.models.functions import Coalesce

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
from bill.models import Product, Order, OrderItem, AreaGroup, Area
from operation_log.models import OperationLog
from django.utils import timezone

# ========== 通用日志记录函数（对齐用户管理模块规范） ==========
# 注：复用accounts的create_operation_log，确保日志格式统一
def create_summary_operation_log(request, operation_type, object_type, object_id=None, object_name=None,
                                 operation_detail=None):
    """汇总模块专用日志（复用用户管理模块的日志逻辑）"""
    create_operation_log(
        request=request,
        op_type=operation_type,
        obj_type=object_type,
        obj_id=object_id,
        obj_name=object_name,
        detail=operation_detail
    )

# ========== 核心业务视图（添加RBAC权限控制） ==========
# 1. 汇总页面（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def summary_page(request):
    """商品汇总页面 - 需【销售汇总】权限"""
    return render(request, 'summary/summary.html')

# 2. 核心接口：按区域组 + 精准时间段汇总（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
def summary_by_group(request):
    """商品汇总接口 - 需【销售汇总】权限"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 处理全区域查询
    area_ids = []
    if group_id == '0':  # 0代表全部区域
        area_ids = Area.objects.all().values_list('id', flat=True)
        group_name = '全部区域'
    else:
        try:
            group = AreaGroup.objects.get(id=group_id)
            group_name = group.name
            area_ids = group.areas.values_list('id', flat=True)
        except AreaGroup.DoesNotExist:
            return JsonResponse({'code': 0, 'msg': '分组不存在'})

    # 时间格式校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误（需为YYYY-MM-DDTHH:MM）'})

    # 查询商品数据（移除product__remark）
    items = OrderItem.objects.filter(
        order__area_id__in=area_ids,
        order__create_time__gte=start,
        order__create_time__lte=end
    ).exclude(
        order__status='cancelled'  # 仅排除作废订单，重开/未结清都计入
    ).values(
        'product__id',
        'product__name',
        'product__unit',
        'product__price'  # 移除product__remark
    ).annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('amount')
    ).order_by('-total_qty')

    # 1. 计算商品汇总总金额
    total_amount = sum(item['total_amt'] or 0 for item in items)
    # 2. 为每条数据添加序号
    data = []
    for idx, item in enumerate(items, 1):
        data.append({
            'serial': idx,  # 添加序号字段
            'pid': item['product__id'],
            'name': item['product__name'],
            'unit': item['product__unit'],
            'price': float(item['product__price']),
            'total_qty': item['total_qty'] or 0,
            'total_amt': float(item['total_amt'] or 0),
            'remark': ''  # 手动添加空的备注字段
        })

    # 记录查询日志
    create_summary_operation_log(
        request=request,
        operation_type='query',
        object_type='product_summary',
        object_name=f'商品汇总-{group_name}',
        operation_detail=f'查询区域组{group_name} {start.strftime("%Y-%m-%d %H:%M")}至{end.strftime("%Y-%m-%d %H:%M")}的商品汇总，返回{len(data)}条数据'
    )

    # 3. 返回数据包含总金额
    return JsonResponse({
        'code': 1,
        'data': data,
        'total_amount': float(total_amount)  # 新增总金额字段
    })

# 3. 加载所有区域组列表（内部接口，随主接口权限控制）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def group_list(request):
    """区域组列表接口 - 依赖销售汇总权限"""
    try:
        groups = AreaGroup.objects.all().order_by('name')
        group_list = [{'id': '0', 'name': '全部区域'}]  # 新增全部区域选项
        group_list.extend([{'id': group.id, 'name': group.name} for group in groups])
        return JsonResponse(group_list, safe=False)
    except Exception as e:
        # 记录异常日志
        create_summary_operation_log(
            request=request,
            operation_type='error',
            object_type='group_list',
            operation_detail=f'加载区域组列表失败：{str(e)}'
        )
        return JsonResponse({'code': 0, 'msg': f'加载组列表失败：{str(e)}'}, status=400)

# 4. 客户金额汇总页面（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def customer_summary_page(request):
    """客户汇总页面 - 需【销售汇总】权限"""
    return render(request, 'summary/customer_summary.html')

# 5. 按区域组+精准时间段汇总客户消费金额（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
def summary_customer_by_group(request):
    """客户汇总接口 - 需【销售汇总】权限"""
    group_id = request.GET.get('group_id')
    start_datetime = request.GET.get('start_date')
    end_datetime = request.GET.get('end_date')

    # 1. 参数校验
    if not group_id or not start_datetime or not end_datetime:
        return JsonResponse({'code': 0, 'msg': '请选择组和时间范围'})

    # 2. 时间格式校验
    try:
        start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
        end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
    except ValueError:
        return JsonResponse({'code': 0, 'msg': '时间格式错误（需为YYYY-MM-DDTHH:MM）'})

    # 3. 处理全区域查询
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

    # 4. 查询客户数据（仅排除作废订单，其余状态都计入，不限制结清状态）
    customer_summary = Order.objects.filter(
        area_id__in=area_ids,
        create_time__gte=start,
        create_time__lte=end,
        customer__isnull=False
    ).exclude(
        status='cancelled'  # 仅排除作废订单，重开/未结清都计入
    ).values(
        'customer__id',
        'customer__name',
        'customer__remark'
    ).annotate(
        total_amount=Sum('total_amount')
    ).order_by('-total_amount')

    # 5. 计算客户汇总总金额
    total_amount = sum(item['total_amount'] or 0 for item in customer_summary)
    # 6. 为每条数据添加序号
    data = []
    for idx, item in enumerate(customer_summary, 1):
        data.append({
            'serial': idx,  # 添加序号字段
            'customer_id': item['customer__id'],
            'customer_name': item['customer__name'],
            'total_amount': float(item['total_amount'] or 0),
            'remark': item['customer__remark'] or ''
        })

    # 记录查询日志
    create_summary_operation_log(
        request=request,
        operation_type='query',
        object_type='customer_summary',
        object_name=f'客户汇总-{group_id}',
        operation_detail=f'查询区域组{group_id} {start.strftime("%Y-%m-%d %H:%M")}至{end.strftime("%Y-%m-%d %H:%M")}的客户汇总，返回{len(data)}条数据'
    )

    # 7. 返回数据包含总金额
    return JsonResponse({
        'code': 1,
        'data': data,
        'total_amount': float(total_amount),  # 新增总金额字段
        'msg': '查询成功' if data else '该时间段内无客户消费数据'
    })

# ========== Excel导出核心函数（无权限，仅内部调用） ==========
def export_to_excel(data, title, headers, selected_fields, custom_fields, file_name, total_row=None):
    """
    通用Excel导出函数（内部工具函数，不直接对外暴露）
    :param data: 基础数据列表
    :param title: 工作表标题
    :param headers: 基础表头映射
    :param selected_fields: 选中的基础字段列表
    :param custom_fields: 自定义字段配置 [{name: '字段名', position: 'after/before', target: '目标字段'}]
    :param file_name: 导出文件名
    :param total_row: 总计行数据 {字段名: 总计值}
    :return: HttpResponse
    """
    # 创建工作簿
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title

    # 构建最终的字段列表（包含自定义字段）
    final_fields = selected_fields.copy()
    final_headers = {field: headers[field] for field in selected_fields}

    # 处理自定义字段
    if custom_fields:
        for cf in custom_fields:
            cf_name = cf.get('name', '')
            cf_position = cf.get('position', 'after')
            cf_target = cf.get('target', '')

            if not cf_name or not cf_target:
                continue

            # 生成唯一的字段标识
            custom_field_key = f'custom_{cf_name.replace(" ", "_")}_{len(final_fields)}'
            final_headers[custom_field_key] = cf_name

            # 找到插入位置
            try:
                target_index = final_fields.index(cf_target)
                if cf_position == 'after':
                    insert_index = target_index + 1
                else:  # before
                    insert_index = target_index
                final_fields.insert(insert_index, custom_field_key)
            except ValueError:
                # 目标字段不存在，追加到末尾
                final_fields.append(custom_field_key)

    # 准备表头
    selected_headers = [final_headers[field] for field in final_fields]

    # 设置标题行样式
    title_font = Font(bold=True, size=12)
    alignment = Alignment(horizontal='center')

    # 写入表头
    for col, header in enumerate(selected_headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = title_font
        cell.alignment = alignment

    # 写入数据行
    for row, item in enumerate(data, 2):
        for col, field in enumerate(final_fields, 1):
            # 判断是否是自定义字段（值为空）
            if field.startswith('custom_'):
                value = ''
            else:
                # 处理数值格式化
                value = item.get(field, '')
                if isinstance(value, float):
                    value = round(value, 2)
            ws.cell(row=row, column=col, value=value)

    # 8. 添加总计行（核心修改）
    if total_row:
        total_row_num = len(data) + 2
        # 设置总计行样式
        total_font = Font(bold=True, color="FFFFFF")
        total_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

        # 写入总计行标题
        ws.cell(row=total_row_num, column=1, value="总计")
        ws.cell(row=total_row_num, column=1).font = total_font
        ws.cell(row=total_row_num, column=1).fill = total_fill

        # 写入总计金额
        for col, field in enumerate(final_fields, 1):
            if field in total_row:
                cell = ws.cell(row=total_row_num, column=col, value=round(total_row[field], 2))
                cell.font = total_font
                cell.fill = total_fill
                cell.alignment = Alignment(horizontal='center')

    # 调整列宽
    for col in range(1, len(selected_headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 15

    # 保存到内存
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # 构建响应
    response = HttpResponse(
        buffer,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{file_name}.xlsx"'
    return response

# 6. 商品汇总导出接口（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
def export_product_summary(request):
    """商品汇总导出 - 需【销售汇总】权限"""
    if request.method == 'POST':
        try:
            # 获取请求参数
            data = request.POST
            group_id = data.get('group_id')
            start_datetime = data.get('start_date')
            end_datetime = data.get('end_date')
            selected_fields = data.getlist('fields[]')
            custom_fields = data.get('custom_fields', '[]')

            # 解析自定义字段
            try:
                custom_fields = json.loads(custom_fields)
            except:
                custom_fields = []

            # 基础参数校验
            if not group_id or not start_datetime or not end_datetime or not selected_fields:
                return JsonResponse({'code': 0, 'msg': '参数不完整'})

            # 时间格式处理
            start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
            end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')

            # 处理全区域查询
            area_ids = []
            group_name = ''
            if group_id == '0':
                area_ids = Area.objects.all().values_list('id', flat=True)
                group_name = '全部区域'
            else:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
                area_ids = group.areas.values_list('id', flat=True)

            # 组装文件名
            today = date.today().strftime('%Y%m%d')
            file_name = f'{today}商品汇总_{group_name}'

            # 查询商品汇总数据（仅排除作废订单）
            items = OrderItem.objects.filter(
                order__area_id__in=area_ids,
                order__create_time__gte=start,
                order__create_time__lte=end
            ).exclude(
                order__status='cancelled'  # 仅排除作废订单
            ).values(
                'product__id',
                'product__name',
                'product__unit',
                'product__price'
            ).annotate(
                total_qty=Sum('quantity'),
                total_amt=Sum('amount')
            ).order_by('-total_qty')

            # 计算商品汇总总金额
            total_amount = sum(item['total_amt'] or 0 for item in items)

            # 构建导出数据
            export_data = []
            product_headers = {
                'serial': '序号',
                'name': '商品名称',
                'unit': '单位',
                'price': '单价',
                'total_qty': '数量',
                'total_amt': '总金额',
                'remark': '备注'
            }

            for idx, item in enumerate(items, 1):
                export_data.append({
                    'serial': idx,
                    'name': item['product__name'],
                    'unit': item['product__unit'],
                    'price': float(item['product__price']),
                    'total_qty': item['total_qty'] or 0,
                    'total_amt': float(item['total_amt'] or 0),
                    'remark': ''  # 空的备注字段
                })

            # 构建总计行数据
            total_row = {
                'total_amt': total_amount
            }

            # ========== 统一日志记录（对齐用户管理模块） ==========
            operation_detail = (
                f"导出商品汇总：区域组={group_name}，时间范围={start.strftime('%Y-%m-%d %H:%M')}至{end.strftime('%Y-%m-%d %H:%M')}，"
                f"选中字段={','.join(selected_fields)}，自定义字段={json.dumps(custom_fields, ensure_ascii=False)}，"
                f"导出数据行数={len(export_data)}，总金额={total_amount}，操作人={request.user.user_code}-{request.user.username}"
            )
            create_summary_operation_log(
                request=request,
                operation_type='export',
                object_type='product_summary',
                object_name='商品汇总',
                operation_detail=operation_detail
            )

            # 导出Excel（传入总计行）
            return export_to_excel(
                data=export_data,
                title='商品汇总',
                headers=product_headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=file_name,
                total_row=total_row  # 传入总计行
            )

        except Exception as e:
            # 记录异常日志
            create_summary_operation_log(
                request=request,
                operation_type='error',
                object_type='product_summary_export',
                operation_detail=f'商品汇总导出失败：{str(e)}，操作人={request.user.user_code}-{request.user.username}'
            )
            # 异常时返回JSON错误信息
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)

# 7. 客户汇总导出接口（需销售汇总权限）
@login_required
@permission_required(PERM_ORDER_SUMMARY)
@csrf_exempt
def export_customer_summary(request):
    """客户汇总导出 - 需【销售汇总】权限"""
    if request.method == 'POST':
        try:
            # 获取请求参数
            data = request.POST
            group_id = data.get('group_id')
            start_datetime = data.get('start_date')
            end_datetime = data.get('end_date')
            selected_fields = data.getlist('fields[]')
            custom_fields = data.get('custom_fields', '[]')

            # 解析自定义字段
            try:
                custom_fields = json.loads(custom_fields)
            except:
                custom_fields = []

            # 基础参数校验
            if not group_id or not start_datetime or not end_datetime or not selected_fields:
                return JsonResponse({'code': 0, 'msg': '参数不完整'})

            # 时间格式处理
            start = datetime.strptime(start_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')
            end = datetime.strptime(end_datetime.replace('T', ' '), '%Y-%m-%d %H:%M')

            # 处理全区域查询
            area_ids = []
            group_name = ''
            if group_id == '0':
                area_ids = Area.objects.all().values_list('id', flat=True)
                group_name = '全部区域'
            else:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
                area_ids = group.areas.values_list('id', flat=True)

            # 组装文件名
            today = date.today().strftime('%Y%m%d')
            file_name = f'{today}{group_name}'

            # 查询客户汇总数据（仅排除作废订单）
            customer_summary = Order.objects.filter(
                area_id__in=area_ids,
                create_time__gte=start,
                create_time__lte=end,
                customer__isnull=False
            ).exclude(
                status='cancelled'  # 仅排除作废订单
            ).values(
                'customer__id',
                'customer__name',
                'customer__remark'
            ).annotate(
                total_amount=Sum('total_amount')
            ).order_by('-total_amount')

            # 计算客户汇总总金额
            total_amount = sum(item['total_amount'] or 0 for item in customer_summary)

            # 构建导出数据
            export_data = []
            customer_headers = {
                'serial': '序号',
                'customer_name': '客户名称',
                'total_amount': '金额',
                'remark': '备注'
            }

            for idx, item in enumerate(customer_summary, 1):
                export_data.append({
                    'serial': idx,
                    'customer_name': item['customer__name'],
                    'total_amount': float(item['total_amount'] or 0),
                    'remark': item['customer__remark'] or ''
                })

            # 构建总计行数据
            total_row = {
                'total_amount': total_amount
            }

            # ========== 统一日志记录（对齐用户管理模块） ==========
            operation_detail = (
                f"导出客户汇总：区域组={group_name}，时间范围={start.strftime('%Y-%m-%d %H:%M')}至{end.strftime('%Y-%m-%d %H:%M')}，"
                f"选中字段={','.join(selected_fields)}，自定义字段={json.dumps(custom_fields, ensure_ascii=False)}，"
                f"导出数据行数={len(export_data)}，总金额={total_amount}，操作人={request.user.user_code}-{request.user.username}"
            )
            create_summary_operation_log(
                request=request,
                operation_type='export',
                object_type='customer_summary',
                object_name='客户汇总',
                operation_detail=operation_detail
            )

            # 导出Excel（传入总计行）
            return export_to_excel(
                data=export_data,
                title='客户汇总',
                headers=customer_headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=file_name,
                total_row=total_row  # 传入总计行
            )

        except Exception as e:
            # 记录异常日志
            create_summary_operation_log(
                request=request,
                operation_type='error',
                object_type='customer_summary_export',
                operation_detail=f'客户汇总导出失败：{str(e)}，操作人={request.user.user_code}-{request.user.username}'
            )
            return JsonResponse({'code': 0, 'msg': f'导出失败：{str(e)}'}, status=500)

# 8. 商品列表接口（供客户价格管理，需商品查看权限）
@login_required
@permission_required(PERM_PRODUCT_VIEW)
@csrf_exempt
def product_list_for_price(request):
    """供客户价格管理页面获取商品列表 - 需【查看商品】权限"""
    try:
        products = Product.objects.all().order_by('name')
        result = [{'id': p.id, 'name': p.name, 'price': float(p.price)} for p in products]

        # 记录查询日志
        create_summary_operation_log(
            request=request,
            operation_type='query',
            object_type='product_list',
            object_name='商品列表',
            operation_detail=f'查询商品列表，返回{len(result)}条数据，用于客户价格管理'
        )

        return JsonResponse(result, safe=False, content_type='application/json')
    except Exception as e:
        # 记录异常日志
        create_summary_operation_log(
            request=request,
            operation_type='error',
            object_type='product_list',
            operation_detail=f'查询商品列表失败：{str(e)}，操作人={request.user.user_code}-{request.user.username}'
        )
        return JsonResponse(
            {'code': 0, 'msg': f'查询商品失败：{str(e)}'},
            safe=False,
            content_type='application/json'
        )

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db import models
from bill.models import Customer, Order, OrderItem, AreaGroup

# 原有视图保持不变，新增以下内容

@login_required
def customer_amount_detail_page(request, customer_id):
    """金额汇总详情页 - 页面渲染"""
    try:
        customer = get_object_or_404(Customer, id=customer_id)
        return render(request, 'summary/amount_detail.html', {
            'customer': customer,
            'customer_id': customer_id
        })
    except Exception as e:
        # 页面渲染失败时返回错误页面，而非JSON
        return render(request, 'error.html', {
            'error_msg': f'获取客户信息失败：{str(e)}'
        }, status=400)

# ========== 修复：客户订单来源数据接口 ==========
@login_required
def get_customer_order_source(request, customer_id):
    """优化后：客户订单来源数据接口"""
    try:
        # 1. 获取参数（分页）
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        # 分页参数转整数
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))

        # 2. 参数校验
        if not start_date or not end_date:
            return JsonResponse({'code': 0, 'msg': '缺少时间范围参数'}, status=400)
        start = parse_datetime(start_date)
        end = parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        # 3. 提前查询客户
        customer = get_object_or_404(Customer, id=customer_id)

        # 4. 🔥 修复：移除__ne，改用exclude过滤作废订单
        orders = Order.objects.filter(
            customer_id=customer_id,
            create_time__gte=start,
            create_time__lte=end,
        ).exclude(
            status='cancelled'  # 正确写法
        ).order_by('-create_time').prefetch_related('items__product')

        # 5. 聚合总金额
        total_amount = orders.aggregate(
            total=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
        )['total']

        # 6. 分页
        paginator = Paginator(orders, page_size)
        try:
            current_page = paginator.page(page)
        except (PageNotAnInteger, EmptyPage):
            current_page = paginator.page(1)

        # 7. 组装数据
        order_list = []
        for order in current_page:
            item_list = [{
                'product_name': item.product.name if item.product else '未知商品',
                'quantity': item.quantity,
                'unit': item.product.unit if item.product else '',
                'price': float(item.product.price) if item.product else 0,
                'amount': float(item.amount) if item.amount else 0
            } for item in order.items.all()]

            order_list.append({
                'order_no': order.order_no,
                'create_time': order.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_amount': float(order.total_amount),
                'status': dict(order.ORDER_STATUS).get(order.status, '未知状态'),
                'items': item_list
            })

        return JsonResponse({
            'code': 1,
            'data': order_list,
            'total_amount': round(float(total_amount), 2),
            'customer_name': customer.name,
            'page': page,
            'page_size': page_size,
            'total_count': paginator.count
        })
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)





# ========== 新增：商品汇总详情页相关 ==========
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def product_summary_detail_page(request, product_id):
    """商品汇总详情页 - 页面渲染"""
    try:
        # 获取商品信息
        product = get_object_or_404(Product, id=product_id)
        # 获取汇总页传递的查询参数（区域组、时间范围）
        group_id = request.GET.get('group_id', '0')
        start_date = request.GET.get('start_date', '')
        end_date = request.GET.get('end_date', '')

        # 获取区域组名称（用于页面展示）
        group_name = '全部区域'
        if group_id != '0' and group_id.isdigit():
            try:
                group = AreaGroup.objects.get(id=group_id)
                group_name = group.name
            except AreaGroup.DoesNotExist:
                pass

        return render(request, 'summary/product_summary_detail.html', {
            'product': product,
            'product_id': product_id,
            'group_id': group_id,
            'group_name': group_name,
            'start_date': start_date,
            'end_date': end_date
        })
    except Exception as e:
        # 页面渲染失败返回错误页
        return render(request, 'error.html', {
            'error_msg': f'获取商品信息失败：{str(e)}'
        }, status=400)


# ========== 修复：商品订单来源数据接口 ==========
@login_required
@permission_required(PERM_ORDER_SUMMARY)
def get_product_order_source(request, product_id):
    """优化后：商品订单来源数据接口"""
    try:
        # 1. 获取参数 + 转整数
        group_id = request.GET.get('group_id', '0')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))

        # 2. 参数校验
        if not start_date or not end_date:
            return JsonResponse({'code': 0, 'msg': '缺少时间范围参数'}, status=400)
        start = parse_datetime(start_date)
        end = parse_datetime(end_date)
        if not start or not end:
            return JsonResponse({'code': 0, 'msg': '时间格式错误'}, status=400)

        # 3. 获取区域ID + 商品信息
        area_ids = get_area_ids_by_group(group_id)
        product = get_object_or_404(Product, id=product_id)

        # 4. 🔥 修复：移除__ne，改用exclude过滤作废订单
        order_items = OrderItem.objects.filter(
            product_id=product_id,
            order__area_id__in=area_ids,
            order__create_time__gte=start,
            order__create_time__lte=end,
        ).exclude(
            order__status='cancelled'  # 正确写法
        ).select_related('order__customer', 'order__area')

        # 5. 聚合数据
        aggregate_data = order_items.aggregate(
            total_quantity=Coalesce(Sum('quantity'), 0),
            total_amount=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )

        # 6. 分页
        paginator = Paginator(order_items, page_size)
        try:
            current_page = paginator.page(page)
        except (PageNotAnInteger, EmptyPage):
            current_page = paginator.page(1)

        # 7. 组装数据
        order_list = []
        for item in current_page:
            order = item.order
            order_list.append({
                'order_no': order.order_no,
                'create_time': order.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                'customer_name': order.customer.name if order.customer else '无客户',
                'area_name': order.area.name if order.area else '无区域',
                'quantity': item.quantity,
                'unit': product.unit,
                'price': float(product.price),
                'amount': float(item.amount),
                'order_status': dict(order.ORDER_STATUS).get(order.status, '未知状态')
            })

        return JsonResponse({
            'code': 1,
            'data': order_list,
            'total_quantity': aggregate_data['total_quantity'],
            'total_amount': round(float(aggregate_data['total_amount']), 2),
            'product_name': product.name,
            'page': page,
            'page_size': page_size,
            'total_count': paginator.count
        })
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'查询失败：{str(e)}'}, status=500)