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