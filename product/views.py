from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from bill.models import Product, ProductAlias
# 新增：导入日志模型和时间工具
from operation_log.models import OperationLog
from django.utils import timezone
# 新增：导入文件和Excel处理相关（原有）
import os
import io
from django.views.decorators.http import require_POST
from django.core.files.uploadedfile import InMemoryUploadedFile
import openpyxl
import xlrd


# ========== 新增：日志记录辅助函数（核心） ==========
def create_operation_log(request, operation_type, object_type, object_id=None, object_name=None, operation_detail=None):
    """
    创建操作日志的通用函数
    :param request: 请求对象（用于获取用户和IP）
    :param operation_type: 操作类型（对应OperationLog的OPERATION_TYPE_CHOICES）
    :param object_type: 操作对象类型（对应OperationLog的OBJECT_TYPE_CHOICES）
    :param object_id: 操作对象ID
    :param object_name: 操作对象名称
    :param operation_detail: 操作详情
    """
    # 获取客户端IP地址
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    ip_address = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')

    # 容错处理：避免日志记录失败影响主业务
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
        print(f"【日志记录失败】：{str(e)}")  # 仅打印错误，不中断主流程


# ====================== 商品管理主页面 ======================
def product_manage(request):
    """商品管理主页面（展示所有商品+别名）"""
    products = Product.objects.all().order_by('name')
    # 组装商品+别名数据
    product_list = []
    for product in products:
        aliases = product.aliases.all()  # 反向关联获取别名
        product_list.append({
            'id': product.id,
            'name': product.name,
            'price': product.price,
            'unit': product.unit,
            'stock': product.stock,
            'aliases': [{'id': a.id, 'alias_name': a.alias_name} for a in aliases]
        })
    return render(request, 'product/product_manage.html', {'products': product_list})


# ====================== 商品CRUD ======================
@csrf_exempt
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

            # 创建商品（拼音字段自动生成）
            product = Product.objects.create(
                name=name,
                price=float(price),
                unit=unit,
                stock=int(stock) if stock.isdigit() else 77
            )

            # ========== 新增：记录新增商品日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='product',
                object_id=product.id,
                object_name=product.name,
                operation_detail=f"新增商品：名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
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

            # 更新商品
            product.name = name
            product.price = float(price)
            product.unit = unit
            product.stock = int(stock) if stock.isdigit() else 77
            product.save()  # 自动更新拼音字段

            # 获取更新后的别名
            aliases = [{'id': a.id, 'alias_name': a.alias_name} for a in product.aliases.all()]

            # ========== 新增：记录编辑商品日志 ==========
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='product',
                object_id=product.id,
                object_name=product.name,
                operation_detail=f"编辑商品：名称={product.name}，单价={product.price}，单位={product.unit}，库存={product.stock}"
            )

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
def product_delete(request, pk):
    """删除商品（AJAX接口）"""
    try:
        product = get_object_or_404(Product, pk=pk)
        product_name = product.name  # 先保存名称（删除后无法获取）
        product.delete()

        # ========== 新增：记录删除商品日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='product',
            object_id=pk,
            object_name=product_name,
            operation_detail=f"删除商品：名称={product_name}，ID={pk}"
        )

        return JsonResponse({'code': 1, 'msg': '商品删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 别名CRUD ======================
@csrf_exempt
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

            # 创建别名（拼音字段自动生成）
            alias = ProductAlias.objects.create(
                product=product,
                alias_name=alias_name
            )

            # ========== 新增：记录新增别名日志 ==========
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='product_alias',
                object_id=alias.id,
                object_name=f"{product.name}-{alias.alias_name}",
                operation_detail=f"为商品【{product.name}】新增别名：{alias.alias_name}"
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
def alias_delete(request, pk):
    """删除商品别名（AJAX接口）"""
    try:
        alias = get_object_or_404(ProductAlias, pk=pk)
        product_name = alias.product.name  # 保存关联商品名称
        alias_name = alias.alias_name  # 保存别名
        alias.delete()

        # ========== 新增：记录删除别名日志 ==========
        create_operation_log(
            request=request,
            operation_type='delete',
            object_type='product_alias',
            object_id=pk,
            object_name=f"{product_name}-{alias_name}",
            operation_detail=f"删除商品【{product_name}】的别名：{alias_name}"
        )

        return JsonResponse({'code': 1, 'msg': '别名删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})


# ====================== 商品数据接口（原有） ======================
@csrf_exempt
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
def product_import(request):
    """
    商品Excel导入接口
    支持格式：xlsx、xls
    解析字段：商品名称（必填）、零售价（price）、辅助单位（unit），其他字段忽略
    """
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
        success_count = 0  # 导入成功数量
        fail_count = 0  # 导入失败数量
        fail_reasons = []  # 失败原因

        # 处理xlsx格式
        if file_name.endswith('.xlsx'):
            wb = openpyxl.load_workbook(io.BytesIO(file.read()))
            ws = wb.active  # 获取第一个工作表
            rows = list(ws.iter_rows(values_only=True))
        # 处理xls格式
        else:
            wb = xlrd.open_workbook(file_contents=file.read())
            ws = wb.sheet_by_index(0)
            rows = []
            for row_idx in range(ws.nrows):
                rows.append(ws.row_values(row_idx))

        # 3. 解析表头，找到对应列索引
        header_row = rows[0] if rows else []
        name_col_idx = -1  # 商品名称列
        price_col_idx = -1  # 零售价列
        unit_col_idx = -1  # 辅助单位列

        for idx, header in enumerate(header_row):
            header = str(header).strip()
            if '商品名称' in header:
                name_col_idx = idx
            elif '零售价' in header:
                price_col_idx = idx
            elif '辅助单位' in header:
                unit_col_idx = idx

        # 验证关键列是否存在
        if name_col_idx == -1:
            return JsonResponse({'code': 0, 'msg': 'Excel中未找到"商品名称"列'})

        # 4. 遍历数据行（跳过表头）
        for row_num, row in enumerate(rows[1:], start=2):  # 行号从2开始（表头是1）
            try:
                # 获取商品名称（必填）
                product_name = str(row[name_col_idx]).strip() if len(row) > name_col_idx else ''
                if not product_name:
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品名称为空')
                    continue

                # 获取零售价（可选，默认0）
                if price_col_idx != -1 and len(row) > price_col_idx and row[price_col_idx]:
                    try:
                        price = float(row[price_col_idx])
                    except:
                        price = 0.0
                else:
                    price = 0.0

                # 获取辅助单位（可选，默认件）
                if unit_col_idx != -1 and len(row) > unit_col_idx and row[unit_col_idx]:
                    unit = str(row[unit_col_idx]).strip()
                else:
                    unit = '件'

                # 5. 保存商品（去重：名称重复则跳过）
                if Product.objects.filter(name=product_name).exists():
                    fail_count += 1
                    fail_reasons.append(f'第{row_num}行：商品"{product_name}"已存在')
                    continue

                # 创建商品
                Product.objects.create(
                    name=product_name,
                    price=price,
                    unit=unit,
                    stock=77  # 库存默认77
                )
                success_count += 1

            except Exception as e:
                fail_count += 1
                fail_reasons.append(f'第{row_num}行：导入失败 - {str(e)}')

        # ========== 新增：记录导入商品日志 ==========
        import_detail = f"批量导入商品：成功{success_count}条，失败{fail_count}条。"
        if fail_reasons:
            import_detail += f" 失败原因：{' | '.join(fail_reasons[:5])}{'...' if len(fail_reasons) > 5 else ''}"
        create_operation_log(
            request=request,
            operation_type='import',
            object_type='product',
            operation_detail=import_detail
        )

        # 6. 返回导入结果
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


from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from bill.models import Product, ProductAlias
from operation_log.models import OperationLog
from django.utils import timezone
import os
import io
from django.views.decorators.http import require_POST
from django.core.files.uploadedfile import InMemoryUploadedFile
import openpyxl
import xlrd
import json


# ========== 原有函数保留，新增以下函数 ==========
@csrf_exempt
@require_POST
def quick_stock_operation(request):
    """
    快速出入库操作接口
    接收参数：items = [{"product_id": "", "in_quantity": 0, "out_quantity": 0}, ...]
    """
    try:
        # 解析请求数据
        data = json.loads(request.body)
        items = data.get('items', [])

        if not items:
            return JsonResponse({'code': 0, 'msg': '无有效出入库数据'})

        # 事务处理：确保所有库存更新要么都成功，要么都失败
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
                    operation_type='stock_operation',
                    object_type='product',
                    operation_detail=f"快速出入库操作：共处理{success_count}个商品。详情：{' | '.join(operation_details)}"
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


