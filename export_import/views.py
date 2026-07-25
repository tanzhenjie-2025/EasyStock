import tempfile, zipfile, os, json


from django.http import FileResponse, JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone
from django.core.exceptions import ImproperlyConfigured
from importlib import import_module
import io


from .registry import MODULE_HANDLERS

def import_string(dotted_path):
    """动态导入函数"""
    try:
        module_path, class_name = dotted_path.rsplit('.', 1)
        module = import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        raise ImproperlyConfigured(f'无法导入 {dotted_path}: {e}')

import shutil  # 新增导入
import tempfile, zipfile, os, json
from django.http import FileResponse, JsonResponse
# ... 其他导入

@staff_member_required
def full_export(request):
    # 手动创建临时目录（不会自动清理）
    tmpdir = tempfile.mkdtemp()
    zip_path = os.path.join(tmpdir, f'backup_{timezone.now().strftime("%Y%m%d_%H%M%S")}.zip')
    manifest = {'exported_at': timezone.now().isoformat(), 'modules': []}

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for module_key, handler in MODULE_HANDLERS.items():
                export_func = import_string(handler['export'])
                buffer = export_func()  # 返回 BytesIO
                filename = f'{module_key}.xlsx'
                zf.writestr(filename, buffer.getvalue())
                manifest['modules'].append({
                    'name': module_key,
                    'file': filename,
                })
            zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception:
        # 如果生成失败，清理临时目录并重新抛出
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    # 打开文件用于响应
    file_handle = open(zip_path, 'rb')
    response = FileResponse(
        file_handle,
        content_type='application/zip',
        as_attachment=True,
        filename=os.path.basename(zip_path)
    )

    # 在响应结束后清理临时目录
    def cleanup():
        try:
            file_handle.close()   # 确保关闭
        except:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    # Django 的 FileResponse 在 close() 时会调用 _resource_closers
    response._resource_closers = [cleanup]
    return response


@staff_member_required
def full_import(request):
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持 POST 请求'})

    zip_file = request.FILES.get('file')
    if not zip_file:
        return JsonResponse({'code': 0, 'msg': '请上传 ZIP 备份文件'})

    with tempfile.TemporaryDirectory() as tmpdir:
        # 解压
        with zipfile.ZipFile(zip_file, 'r') as zf:
            zf.extractall(tmpdir)

        # 读取 manifest
        manifest_path = os.path.join(tmpdir, 'manifest.json')
        if not os.path.exists(manifest_path):
            return JsonResponse({'code': 0, 'msg': '无效的备份包，缺少 manifest.json'})
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        # 按依赖顺序（硬编码或拓扑排序）
        # 简单起见，使用注册表的顺序（已按依赖排序）
        ordered_keys = list(MODULE_HANDLERS.keys())

        results = {}
        errors_occurred = False
        # 使用事务（目前仅对支持事务的数据库有效）
        from django.db import transaction
        try:
            with transaction.atomic():
                for module_key in ordered_keys:
                    if module_key not in MODULE_HANDLERS:
                        continue
                    handler = MODULE_HANDLERS[module_key]
                    file_path = os.path.join(tmpdir, f'{module_key}.xlsx')
                    if not os.path.exists(file_path):
                        continue  # 允许缺失
                    import_func = import_string(handler['import'])
                    with open(file_path, 'rb') as f:
                        file_obj = io.BytesIO(f.read())
                        result = import_func(file_obj, strategy='append')
                    results[module_key] = result
                    if result.get('errors'):
                        # 有错误则抛出异常回滚
                        raise Exception(f"模块 {module_key} 导入错误: {result['errors']}")
                # 所有模块成功
                return JsonResponse({
                    'code': 1,
                    'msg': '全部导入成功',
                    'details': results
                })
        except Exception as e:
            return JsonResponse({
                'code': 0,
                'msg': f'导入失败，已回滚: {str(e)}',
                'details': results
            })

from django.shortcuts import render

def data_migration_page(request):
    return render(request, 'export_import/data_migration.html')