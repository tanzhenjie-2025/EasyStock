from django.db.models import Q
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from django.utils import timezone
import logging

# 导入模型和常量
from .models import (
    User, Role, Permission,
    ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR
)
from operation_log.models import OperationLog

# 配置日志
logger = logging.getLogger(__name__)

# ========== 全局常量（去硬编码） ==========
# 操作类型常量（日志用）
OP_TYPE_LOGIN = 'login'
OP_TYPE_LOGOUT = 'logout'
OP_TYPE_CREATE = 'create'
OP_TYPE_UPDATE = 'update'
OP_TYPE_DELETE = 'delete'
OP_TYPE_RESET_PASSWORD = 'reset_password'
OP_TYPE_CHANGE_PASSWORD = 'change_password'
OP_TYPE_ENABLE_USER = 'enable_user'
OP_TYPE_DISABLE_USER = 'disable_user'
OP_TYPE_UPDATE_ROLE_PERM = 'update_role_permission'


# ========== 通用工具函数 ==========
def get_client_ip(request):
    """获取客户端IP"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')


def create_operation_log(request, op_type, obj_type, obj_id=None, obj_name=None, detail=None):
    """统一日志记录（容错+规范化）"""
    try:
        OperationLog.objects.create(
            operator=request.user if request.user.is_authenticated else None,
            operation_time=timezone.now(),
            operation_type=op_type,
            object_type=obj_type,
            object_id=str(obj_id) if obj_id else None,
            object_name=obj_name,
            operation_detail=detail,
            ip_address=get_client_ip(request)
        )
    except Exception as e:
        logger.error(f"日志记录失败：{str(e)}")


# ========== 核心权限装饰器（纯RBAC） ==========
def permission_required(permission_code):
    """
    自定义RBAC权限装饰器
    规则：超级管理员→放行；普通用户→检查权限；未登录→跳转登录
    """

    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            # 未登录→跳转登录
            if not request.user.is_authenticated:
                return redirect(f'/accounts/login/?next={request.path}')

            # 超级管理员→直接放行
            if request.user.role and request.user.role.code == ROLE_SUPER_ADMIN:
                return view_func(request, *args, **kwargs)

            # 普通用户→检查权限
            if not request.user.has_permission(permission_code):
                return redirect('/accounts/no-permission/')

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


# ========== 认证相关视图 ==========
def login_view(request):
    """登录视图（RBAC版）"""
    if request.user.is_authenticated:
        # 强制改密码检查
        if request.user.force_password_change:
            return redirect('/accounts/force-change-password/')
        return redirect(request.GET.get('next', '/bill/'))

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)

        if user and user.is_active:
            login(request, user)
            # 保存用户信息到Session（RBAC版）
            request.session.update({
                'user_code': user.user_code,
                'user_name': user.name,
                'user_role_code': user.role.code if user.role else '',
                'user_role_name': user.role.name if user.role else '无'
            })

            # 记录登录日志
            create_operation_log(
                request=request,
                op_type=OP_TYPE_LOGIN,
                obj_type='user',
                obj_id=user.id,
                obj_name=f"{user.user_code}-{user.username}",
                detail=f"用户登录：编号={user.user_code}，用户名={user.username}，角色={user.role.name if user.role else '无'}"
            )

            # 强制改密码跳转
            if user.force_password_change:
                return redirect('/accounts/force-change-password/')

            return redirect(request.POST.get('next', request.GET.get('next', '/bill/')))
        else:
            messages.error(request, '用户名/密码错误或账户已禁用')

    return render(request, 'accounts/login.html', {
        'next': request.GET.get('next', '')
    })


@login_required
def force_change_password(request):
    """强制改密码视图"""
    if not request.user.force_password_change:
        return redirect('/bill/')

    if request.method == 'POST':
        old_pwd = request.POST.get('old_password', '').strip()
        new_pwd = request.POST.get('new_password', '').strip()
        confirm_pwd = request.POST.get('confirm_password', '').strip()

        # 校验逻辑
        errors = []
        if not all([old_pwd, new_pwd, confirm_pwd]):
            errors.append('所有密码字段不能为空')
        if new_pwd != confirm_pwd:
            errors.append('两次新密码不一致')
        if len(new_pwd) < 8:
            errors.append('新密码长度至少8位')
        if not request.user.check_password(old_pwd):
            errors.append('原密码输入错误')

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            # 修改密码
            request.user.set_password(new_pwd)
            request.user.force_password_change = False
            request.user.save()

            # 记录日志
            create_operation_log(
                request=request,
                op_type=OP_TYPE_CHANGE_PASSWORD,
                obj_type='user',
                obj_id=request.user.id,
                obj_name=f"{request.user.user_code}-{request.user.username}",
                detail=f"用户强制修改密码：编号={request.user.user_code}"
            )

            # 重新登录
            login(request, request.user)
            messages.success(request, '密码修改成功！请正常使用系统')
            return redirect('/bill/')

    return render(request, 'accounts/force_change_password.html', {
        'user': request.user
    })


def logout_view(request):
    """登出视图"""
    if request.user.is_authenticated:
        # 记录登出日志
        create_operation_log(
            request=request,
            op_type=OP_TYPE_LOGOUT,
            obj_type='user',
            obj_id=request.user.id,
            obj_name=f"{request.user.user_code}-{request.user.username}",
            detail=f"用户登出：编号={request.user.user_code}，IP={get_client_ip(request)}"
        )
    logout(request)
    return redirect('/accounts/login/')


# ========== 用户管理视图（RBAC版） ==========
@login_required
@permission_required('user_view')
def user_list(request):
    """用户列表（支持搜索/状态筛选）"""
    # 筛选参数
    keyword = request.GET.get('keyword', '').strip()
    status = request.GET.get('status', 'all')

    # 基础查询
    queryset = User.objects.all().order_by('-date_joined')

    # 关键词筛选
    if keyword:
        queryset = queryset.filter(
            Q(user_code__icontains=keyword) |
            Q(username__icontains=keyword) |
            Q(phone__icontains=keyword) |
            Q(name__icontains=keyword)
        )

    # 状态筛选
    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    return render(request, 'accounts/user_list.html', {
        'users': queryset,
        'roles': Role.objects.all(),
        'keyword': keyword,
        'status': status,
        'current_user': request.user
    })


@login_required
@permission_required('user_add')
def user_add(request):
    """添加用户"""
    if request.method == 'POST':
        # 获取表单数据
        username = request.POST.get('username', '').strip()
        user_code = request.POST.get('user_code', '').strip()
        password = request.POST.get('password', '').strip()
        phone = request.POST.get('phone', '').strip()
        role_id = request.POST.get('role_id')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'

        # 基础校验
        if not all([username, user_code, password]):
            messages.error(request, '用户名、用户编号、初始密码不能为空！')
            return render(request, 'accounts/user_form.html', {
                'roles': Role.objects.all(),
                'form_data': request.POST,
                'is_add': True
            })

        try:
            # 创建用户
            user = User.objects.create_user(
                username=username,
                user_code=user_code,
                password=password,
                phone=phone,
                is_active=is_active,
                is_staff=is_staff
            )

            # 绑定角色
            role_name = '无'
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                user.role = role
                user.save()
                role_name = role.name

            # 记录日志
            create_operation_log(
                request=request,
                op_type=OP_TYPE_CREATE,
                obj_type='user',
                obj_id=user.id,
                obj_name=f"{user_code}-{username}",
                detail=(
                    f"新增用户：编号={user_code}，用户名={username}，电话={phone or '无'}，"
                    f"角色={role_name}，状态={'启用' if is_active else '禁用'}"
                )
            )

            messages.success(request, f'用户 {user_code} - {username} 创建成功！')
            return redirect('/accounts/user-list/')

        except IntegrityError:
            messages.error(request, '用户编号已存在！请更换编号。')
        except Exception as e:
            messages.error(request, f'创建失败：{str(e)}')

    # GET请求：展示表单
    return render(request, 'accounts/user_form.html', {
        'roles': Role.objects.all(),
        'is_add': True
    })


@login_required
@permission_required('user_edit')
def user_edit(request, user_id):
    """编辑用户"""
    user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        # 获取表单数据
        username = request.POST.get('username', '').strip()
        user_code = request.POST.get('user_code', '').strip()
        phone = request.POST.get('phone', '').strip()
        role_id = request.POST.get('role_id')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        new_password = request.POST.get('new_password', '').strip()

        # 保存原始信息（日志用）
        old_info = {
            'user_code': user.user_code,
            'username': user.username,
            'phone': user.phone,
            'role': user.role.name if user.role else '无',
            'is_active': user.is_active,
            'is_staff': user.is_staff
        }

        try:
            # 更新基础信息
            user.username = username
            user.user_code = user_code
            user.phone = phone
            user.is_active = is_active
            user.is_staff = is_staff

            # 更新密码（可选）
            pwd_changed = False
            if new_password:
                user.set_password(new_password)
                pwd_changed = True

            # 更新角色
            new_role_name = old_info['role']
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                user.role = role
                new_role_name = role.name

            user.save()

            # 记录日志
            create_operation_log(
                request=request,
                op_type=OP_TYPE_UPDATE,
                obj_type='user',
                obj_id=user.id,
                obj_name=f"{user_code}-{username}",
                detail=(
                    f"编辑用户：原编号={old_info['user_code']}→新编号={user_code}，"
                    f"原角色={old_info['role']}→新角色={new_role_name}，"
                    f"状态={'启用' if is_active else '禁用'}，密码是否修改：{'是' if pwd_changed else '否'}"
                )
            )

            messages.success(request, f'用户 {user_code} - {username} 修改成功！')
            return redirect('/accounts/user-list/')

        except IntegrityError:
            messages.error(request, '用户编号已存在！请更换编号。')
        except Exception as e:
            messages.error(request, f'修改失败：{str(e)}')

    # GET请求：展示表单
    return render(request, 'accounts/user_form.html', {
        'user': user,
        'roles': Role.objects.all(),
        'role_id': user.role.id if user.role else '',
        'is_edit': True
    })


@login_required
@permission_required('user_toggle_status')
def user_toggle_status(request, user_id):
    """切换用户状态（启用/禁用）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

    try:
        user = get_object_or_404(User, id=user_id)
        # 切换状态
        user.is_active = not user.is_active
        user.save()

        # 日志/返回信息
        op_type = OP_TYPE_ENABLE_USER if user.is_active else OP_TYPE_DISABLE_USER
        status_text = '启用' if user.is_active else '禁用'
        create_operation_log(
            request=request,
            op_type=op_type,
            obj_type='user',
            obj_id=user.id,
            obj_name=f"{user.user_code}-{user.username}",
            detail=f"{status_text}用户：编号={user.user_code}，原状态={'启用' if not user.is_active else '禁用'}→新状态={status_text}"
        )

        return JsonResponse({
            'code': 1,
            'msg': f'用户 {user.user_code} 已{status_text}！'
        })

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'操作失败：{str(e)}'})


@login_required
@permission_required('user_reset_password')
def reset_password(request, user_id):
    """重置密码"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

    try:
        user = get_object_or_404(User, id=user_id)
        temp_pwd = "123456"

        # 重置密码+标记强制修改
        user.set_password(temp_pwd)
        user.force_password_change = True
        user.save()

        # 记录日志
        create_operation_log(
            request=request,
            op_type=OP_TYPE_RESET_PASSWORD,
            obj_type='user',
            obj_id=user.id,
            obj_name=f"{user.user_code}-{user.username}",
            detail=f"重置用户密码：编号={user.user_code}，临时密码={temp_pwd}，已标记强制改密码"
        )

        return JsonResponse({
            'code': 1,
            'msg': f'用户 {user.user_code} 密码已重置为：{temp_pwd}，登录后将强制修改密码！'
        })

    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'重置失败：{str(e)}'})


# ========== 权限管理视图 ==========
@login_required
@permission_required('permission_view')
def permission_list(request):
    """权限列表"""
    keyword = request.GET.get('keyword', '').strip()
    category = request.GET.get('category', 'all')

    # 基础查询
    queryset = Permission.objects.filter(is_active=True).order_by('category', 'code')

    # 筛选
    if keyword:
        queryset = queryset.filter(
            Q(code__icontains=keyword) |
            Q(name__icontains=keyword)
        )
    if category != 'all' and category in [c[0] for c in Permission.PERMISSION_CATEGORIES]:
        queryset = queryset.filter(category=category)

    return render(request, 'accounts/permission_list.html', {
        'permissions': queryset,
        'categories': Permission.PERMISSION_CATEGORIES,
        'keyword': keyword,
        'selected_category': category
    })


@login_required
@permission_required('permission_add')
def permission_add(request):
    """添加权限"""
    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', 'system')
        description = request.POST.get('description', '').strip()

        # 基础校验
        if not all([code, name]):
            messages.error(request, '权限编码和名称不能为空！')
            return render(request, 'accounts/permission_form.html', {
                'categories': Permission.PERMISSION_CATEGORIES,
                'form_data': request.POST,
                'is_add': True
            })

        # 检查编码唯一性
        if Permission.objects.filter(code=code).exists():
            messages.error(request, '权限编码已存在！')
            return render(request, 'accounts/permission_form.html', {
                'categories': Permission.PERMISSION_CATEGORIES,
                'form_data': request.POST,
                'is_add': True
            })

        try:
            # 创建权限
            perm = Permission.objects.create(
                code=code,
                name=name,
                category=category,
                description=description
            )

            # 记录日志
            create_operation_log(
                request=request,
                op_type=OP_TYPE_CREATE,
                obj_type='permission',
                obj_id=perm.id,
                obj_name=f"{perm.name} ({perm.code})",
                detail=f"新增权限：编码={code}，名称={name}，分类={perm.get_category_display()}"
            )

            messages.success(request, f'权限 {name} ({code}) 创建成功！')
            return redirect('/accounts/permission-list/')

        except Exception as e:
            messages.error(request, f'创建失败：{str(e)}')

    # GET请求：展示表单
    return render(request, 'accounts/permission_form.html', {
        'categories': Permission.PERMISSION_CATEGORIES,
        'is_add': True
    })


# ========== 角色管理视图 ==========
@login_required
@permission_required('role_view')
def role_list(request):
    """角色列表"""
    return render(request, 'accounts/role_list.html', {
        'roles': Role.objects.all()
    })


@login_required
@permission_required('role_permission_config')
def role_permission_config(request, role_code):
    """角色权限配置（核心）"""
    role = get_object_or_404(Role, code=role_code)
    all_perms = Permission.objects.filter(is_active=True).order_by('category', 'code')

    if request.method == 'POST':
        try:
            # 获取选中的权限ID
            selected_perm_ids = request.POST.getlist('permissions', [])

            # 清空原有权限，重新绑定
            role.permissions.clear()
            if selected_perm_ids:
                selected_perms = Permission.objects.filter(id__in=selected_perm_ids)
                role.permissions.add(*selected_perms)

            # 记录日志
            perm_names = [f"{p.name}({p.code})" for p in selected_perms] if selected_perm_ids else []
            create_operation_log(
                request=request,
                op_type=OP_TYPE_UPDATE_ROLE_PERM,
                obj_type='role',
                obj_id=role.id,
                obj_name=role.name,
                detail=f"配置角色权限：角色={role.name}，选中权限={','.join(perm_names) or '无'}"
            )

            messages.success(request, f'角色 {role.name} 的权限配置成功！')
            return redirect('/accounts/role-list/')

        except Exception as e:
            messages.error(request, f'配置失败：{str(e)}')

    # 已选中的权限ID
    selected_perm_ids = role.permissions.values_list('id', flat=True)

    # 按分类分组权限（方便前端展示）
    perm_groups = {}
    for perm in all_perms:
        if perm.category not in perm_groups:
            perm_groups[perm.category] = {
                'name': perm.get_category_display(),
                'permissions': []
            }
        perm_groups[perm.category]['permissions'].append(perm)

    return render(request, 'accounts/role_permission_config.html', {
        'role': role,
        'perm_groups': perm_groups,
        'selected_perm_ids': selected_perm_ids,
        'is_super_admin': role.is_super_admin
    })


# ========== 其他视图 ==========
@login_required
def profile(request):
    """个人信息"""
    user = request.user
    if request.method == 'POST':
        # 基础信息更新
        user.first_name = request.POST.get('first_name', user.first_name).strip()
        user.last_name = request.POST.get('last_name', user.last_name).strip()
        user.phone = request.POST.get('phone', user.phone).strip()
        user.address = request.POST.get('address', user.address).strip()
        user.email = request.POST.get('email', user.email).strip()

        # 密码修改（可选）
        new_pwd = request.POST.get('new_password', '').strip()
        pwd_changed = False
        if new_pwd:
            old_pwd = request.POST.get('old_password', '').strip()
            if not user.check_password(old_pwd):
                messages.error(request, '原密码输入错误！')
                return render(request, 'accounts/profile.html', {'user': user})
            user.set_password(new_pwd)
            pwd_changed = True

        user.save()

        # 记录日志
        create_operation_log(
            request=request,
            op_type=OP_TYPE_UPDATE,
            obj_type='user',
            obj_id=user.id,
            obj_name=f"{user.user_code}-{user.username}",
            detail=f"修改个人信息：姓名={user.name}，电话={user.phone}，密码是否修改：{'是' if pwd_changed else '否'}"
        )

        messages.success(request, '个人信息修改成功！')
        if pwd_changed:
            login(request, user)

    return render(request, 'accounts/profile.html', {'user': user})


@login_required
def no_permission(request):
    """无权限提示"""
    return render(request, 'accounts/no_permission.html')


