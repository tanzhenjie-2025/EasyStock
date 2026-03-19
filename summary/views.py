from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, Alignment
from io import BytesIO
import json

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
        order__status='cancelled'
    ).values(
        'product__id',
        'product__name',
        'product__unit',
        'product__price'  # 移除product__remark
    ).annotate(
        total_qty=Sum('quantity'),
        total_amt=Sum('amount')
    ).order_by('-total_qty')

    data = []
    for item in items:
        data.append({
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

    return JsonResponse({'code': 1, 'data': data})


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

    # 4. 查询客户数据
    customer_summary = Order.objects.filter(
        area_id__in=area_ids,
        create_time__gte=start,
        create_time__lte=end,
        customer__isnull=False
    ).exclude(
        status='cancelled'
    ).values(
        'customer__id',
        'customer__name',
        'customer__remark'
    ).annotate(
        total_amount=Sum('total_amount')
    ).order_by('-total_amount')

    # 5. 构造返回数据
    data = []
    for item in customer_summary:
        data.append({
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

    return JsonResponse({
        'code': 1,
        'data': data,
        'msg': '查询成功' if data else '该时间段内无客户消费数据'
    })


# ========== Excel导出核心函数（无权限，仅内部调用） ==========
def export_to_excel(data, title, headers, selected_fields, custom_fields, file_name):
    """
    通用Excel导出函数（内部工具函数，不直接对外暴露）
    :param data: 基础数据列表
    :param title: 工作表标题
    :param headers: 基础表头映射
    :param selected_fields: 选中的基础字段列表
    :param custom_fields: 自定义字段配置 [{name: '字段名', position: 'after/before', target: '目标字段'}]
    :param file_name: 导出文件名
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

            # 查询商品汇总数据
            items = OrderItem.objects.filter(
                order__area_id__in=area_ids,
                order__create_time__gte=start,
                order__create_time__lte=end
            ).exclude(
                order__status='cancelled'
            ).values(
                'product__id',
                'product__name',
                'product__unit',
                'product__price'
            ).annotate(
                total_qty=Sum('quantity'),
                total_amt=Sum('amount')
            ).order_by('-total_qty')

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

            # ========== 统一日志记录（对齐用户管理模块） ==========
            operation_detail = (
                f"导出商品汇总：区域组={group_name}，时间范围={start.strftime('%Y-%m-%d %H:%M')}至{end.strftime('%Y-%m-%d %H:%M')}，"
                f"选中字段={','.join(selected_fields)}，自定义字段={json.dumps(custom_fields, ensure_ascii=False)}，"
                f"导出数据行数={len(export_data)}，操作人={request.user.user_code}-{request.user.username}"
            )
            create_summary_operation_log(
                request=request,
                operation_type='export',
                object_type='product_summary',
                object_name='商品汇总',
                operation_detail=operation_detail
            )

            # 导出Excel
            return export_to_excel(
                data=export_data,
                title='商品汇总',
                headers=product_headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=file_name
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

            # 查询客户汇总数据
            customer_summary = Order.objects.filter(
                area_id__in=area_ids,
                create_time__gte=start,
                create_time__lte=end,
                customer__isnull=False
            ).exclude(
                status='cancelled'
            ).values(
                'customer__id',
                'customer__name',
                'customer__remark'
            ).annotate(
                total_amount=Sum('total_amount')
            ).order_by('-total_amount')

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

            # ========== 统一日志记录（对齐用户管理模块） ==========
            operation_detail = (
                f"导出客户汇总：区域组={group_name}，时间范围={start.strftime('%Y-%m-%d %H:%M')}至{end.strftime('%Y-%m-%d %H:%M')}，"
                f"选中字段={','.join(selected_fields)}，自定义字段={json.dumps(custom_fields, ensure_ascii=False)}，"
                f"导出数据行数={len(export_data)}，操作人={request.user.user_code}-{request.user.username}"
            )
            create_summary_operation_log(
                request=request,
                operation_type='export',
                object_type='customer_summary',
                object_name='客户汇总',
                operation_detail=operation_detail
            )

            # 导出Excel
            return export_to_excel(
                data=export_data,
                title='客户汇总',
                headers=customer_headers,
                selected_fields=selected_fields,
                custom_fields=custom_fields,
                file_name=file_name
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