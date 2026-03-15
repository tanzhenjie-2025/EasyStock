from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from bill.models import Product, ProductAlias

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
        product.delete()
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
        alias.delete()
        return JsonResponse({'code': 1, 'msg': '别名删除成功'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'删除失败：{str(e)}'})

# 在product/views.py末尾添加
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


import os
import io
from django.views.decorators.http import require_POST
from django.core.files.uploadedfile import InMemoryUploadedFile
import openpyxl
import xlrd


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