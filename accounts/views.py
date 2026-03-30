from django.db.models import Q, OuterRef, Subquery, Value, Case, When
from django.db.models.functions import Coalesce
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
from bill.models import Order, Area
from django.db.models import Sum, Count
from django.views.decorators.csrf import csrf_exempt

from django.core.cache import cache
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from .models import (
    User, Role, Permission,
    ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR
)
from operation_log.models import OperationLog
from django.db.models import DecimalField
from decimal import Decimal

logger = logging.getLogger(__name__)

# ========== 缓存常量 ==========
CACHE_ROLE_PERMISSION = 1800

# ========== 操作常量 ==========
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

# ========== 工具函数 ==========
def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')

def create_operation_log(request, op_type, obj_type, obj_id=None, obj_name=None, detail=None):
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

# ========== 权限装饰器 ==========
def permission_required(permission_code):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f'/accounts/login/?next={request.path}')
            if request.user.role and request.user.role.code == ROLE_SUPER_ADMIN:
                return view_func(request, *args, **kwargs)
            if not request.user.has_permission(permission_code):
                return redirect('/accounts/no-permission/')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

# ========== 认证视图 ==========
def login_view(request):
    if request.user.is_authenticated:
        if request.user.force_password_change:
            return redirect('/accounts/force-change-password/')
        return redirect(request.GET.get('next', '/bill/'))

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)

        if user and user.is_active:
            login(request, user)
            request.session.update({
                'user_code': user.user_code,
                'user_name': user.name,
                'user_role_code': user.role.code if user.role else '',
                'user_role_name': user.role.name if user.role else '无'
            })
            create_operation_log(
                request=request, op_type=OP_TYPE_LOGIN, obj_type='user',
                obj_id=user.id, obj_name=f"{user.user_code}-{user.username}",
                detail=f"用户登录：编号={user.user_code}，用户名={user.username}"
            )
            if user.force_password_change:
                return redirect('/accounts/force-change-password/')
            return redirect(request.POST.get('next', request.GET.get('next', '/bill/')))
        else:
            messages.error(request, '用户名/密码错误或账户已禁用')
    return render(request, 'accounts/login.html', {'next': request.GET.get('next', '')})

@login_required
def force_change_password(request):
    if not request.user.force_password_change:
        return redirect('/bill/')
    if request.method == 'POST':
        old_pwd = request.POST.get('old_password', '').strip()
        new_pwd = request.POST.get('new_password', '').strip()
        confirm_pwd = request.POST.get('confirm_password', '').strip()
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
            request.user.set_password(new_pwd)
            request.user.force_password_change = False
            request.user.save()
            create_operation_log(
                request=request, op_type=OP_TYPE_CHANGE_PASSWORD, obj_type='user',
                obj_id=request.user.id, obj_name=f"{request.user.user_code}-{request.user.username}",
                detail=f"用户强制修改密码：编号={request.user.user_code}"
            )
            login(request, request.user)
            messages.success(request, '密码修改成功！请正常使用系统')
            return redirect('/bill/')
    return render(request, 'accounts/force_change_password.html', {'user': request.user})

def logout_view(request):
    if request.user.is_authenticated:
        create_operation_log(
            request=request, op_type=OP_TYPE_LOGOUT, obj_type='user',
            obj_id=request.user.id, obj_name=f"{request.user.user_code}-{request.user.username}",
            detail=f"用户登出：编号={request.user.user_code}"
        )
    logout(request)
    return redirect('/accounts/login/')

# ========== 用户管理 ==========
@login_required
@permission_required('user_view')
def user_list(request):
    """用户列表 - 适配索引优化版"""
    keyword = request.GET.get('keyword', '').strip()
    status = request.GET.get('status', 'all')
    page = request.GET.get('page', 1)
    page_size = 10

    queryset = User.objects.select_related('role').all().order_by('-date_joined')

    # 关键词筛选
    if keyword:
        queryset = queryset.filter(
            Q(user_code__icontains=keyword) |
            Q(username__icontains=keyword) |
            Q(phone__icontains=keyword) |
            Q(first_name__icontains=keyword) |
            Q(last_name__icontains=keyword)
        )

    # 状态筛选
    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    if is_super_admin:
        # 🔥 适配索引：status + is_settled + creator 严格匹配联合索引
        order_count_sub = Order.objects.filter(
            creator=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened'],
            is_settled=False
        ).values('creator').annotate(cnt=Count('id')).values('cnt')

        sales_sum_sub = Order.objects.filter(
            creator=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened'],
            is_settled=False
        ).values('creator').annotate(total=Sum('total_amount')).values('total')

        # 批量注解统计字段
        queryset = queryset.annotate(
            orders=Coalesce(Subquery(order_count_sub), Value(0),
                            output_field=DecimalField(max_digits=12, decimal_places=2)),
            sales=Coalesce(Subquery(sales_sum_sub), Value(Decimal('0')),
                           output_field=DecimalField(max_digits=12, decimal_places=2))
        )

    # 分页逻辑
    paginator = Paginator(queryset, page_size)
    try:
        users_page = paginator.page(page)
    except PageNotAnInteger:
        users_page = paginator.page(1)
    except EmptyPage:
        users_page = paginator.page(paginator.num_pages)

    # 店铺统计 - 适配索引
    shop_stats = {'total_orders': 0, 'total_sales': 0, 'total_cancelled': 0}
    if is_super_admin:
        shop_stats = Order.objects.aggregate(
            total_orders=Count(Case(When(status__in=['pending', 'printed', 'reopened'], is_settled=False, then='id'))),
            total_sales=Coalesce(Sum(Case(
                When(status__in=['pending', 'printed', 'reopened'], is_settled=False, then='total_amount'),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )), Value(Decimal('0'))),
            total_cancelled=Count(Case(When(status='cancelled', then='id')))
        )

    # 计算销售占比
    if is_super_admin:
        total_sales = shop_stats['total_sales']
        for user in users_page:
            user.ratio = round((user.sales / total_sales) * 100) if total_sales > 0 else 0

    return render(request, 'accounts/user_list.html', {
        'users': users_page, 'roles': Role.objects.all(), 'keyword': keyword, 'status': status,
        'current_user': request.user, 'is_super_admin': is_super_admin, 'shop_stats': shop_stats,
        'paginator': paginator, 'page_obj': users_page,
    })

@login_required
@permission_required('user_add')
def user_add(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        user_code = request.POST.get('user_code', '').strip()
        password = request.POST.get('password', '').strip()
        phone = request.POST.get('phone', '').strip()
        role_id = request.POST.get('role_id')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'

        if not all([username, user_code, password]):
            messages.error(request, '用户名、用户编号、初始密码不能为空！')
            return render(request, 'accounts/user_form.html', {
                'roles': Role.objects.all(), 'form_data': request.POST, 'is_add': True
            })
        try:
            user = User.objects.create_user(
                username=username, user_code=user_code, password=password,
                phone=phone, is_active=is_active, is_staff=is_staff
            )
            role_name = '无'
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                user.role = role
                user.save()
                role_name = role.name
            create_operation_log(
                request=request, op_type=OP_TYPE_CREATE, obj_type='user',
                obj_id=user.id, obj_name=f"{user_code}-{username}",
                detail=f"新增用户：编号={user_code}，角色={role_name}"
            )
            messages.success(request, f'用户 {user_code} - {username} 创建成功！')
            return redirect('/accounts/user-list/')
        except IntegrityError:
            messages.error(request, '用户编号已存在！')
        except Exception as e:
            messages.error(request, f'创建失败：{str(e)}')
    return render(request, 'accounts/user_form.html', {'roles': Role.objects.all(), 'is_add': True})

@login_required
@permission_required('user_edit')
def user_edit(request, user_id):
    user = get_object_or_404(User.objects.select_related('role'), id=user_id)
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        user_code = request.POST.get('user_code', '').strip()
        phone = request.POST.get('phone', '').strip()
        role_id = request.POST.get('role_id')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        new_password = request.POST.get('new_password', '').strip()

        old_info = {
            'user_code': user.user_code, 'username': user.username, 'phone': user.phone,
            'role': user.role.name if user.role else '无', 'is_active': user.is_active
        }
        try:
            user.username = username
            user.user_code = user_code
            user.phone = phone
            user.is_active = is_active
            user.is_staff = is_staff
            pwd_changed = False
            if new_password:
                user.set_password(new_password)
                pwd_changed = True
            new_role_name = old_info['role']
            if role_id:
                role = get_object_or_404(Role, id=role_id)
                user.role = role
                new_role_name = role.name
            user.save()
            create_operation_log(
                request=request, op_type=OP_TYPE_UPDATE, obj_type='user',
                obj_id=user.id, obj_name=f"{user_code}-{username}",
                detail=f"编辑用户：原编号={old_info['user_code']}→新编号={user_code}"
            )
            messages.success(request, f'用户 {user_code} - {username} 修改成功！')
            return redirect('/accounts/user-list/')
        except IntegrityError:
            messages.error(request, '用户编号已存在！')
        except Exception as e:
            messages.error(request, f'修改失败：{str(e)}')
    return render(request, 'accounts/user_form.html', {
        'user': user, 'roles': Role.objects.all(), 'role_id': user.role.id if user.role else '', 'is_edit': True
    })

@login_required
@permission_required('user_view')
def user_detail(request, user_id):
    """用户详情 - 适配索引优化版"""
    user = get_object_or_404(User.objects.select_related('role'), id=user_id)
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    user_stats = {'total_orders': 0, 'total_sales': 0, 'total_cancelled': 0, 'sales_ratio': 0}

    if is_super_admin and user.is_active:
        # 🔥 适配索引：严格匹配 status + is_settled + creator
        stats = Order.objects.filter(
            status__in=['pending', 'printed', 'reopened', 'cancelled']
        ).aggregate(
            shop_total=Coalesce(Sum(Case(
                When(status__in=['pending', 'printed', 'reopened'], is_settled=False, then='total_amount')
            )), 0, output_field=DecimalField(max_digits=12, decimal_places=2)),
            user_orders=Count(Case(When(creator=user, status__in=['pending', 'printed', 'reopened'], is_settled=False, then='id'))),
            user_sales=Coalesce(Sum(Case(
                When(creator=user, status__in=['pending', 'printed', 'reopened'], is_settled=False, then='total_amount')
            )), 0, output_field=DecimalField(max_digits=12, decimal_places=2)),
            user_canceled=Count(Case(When(creator=user, status='cancelled', then='id')))
        )
        user_stats['total_orders'] = stats['user_orders']
        user_stats['total_sales'] = stats['user_sales']
        user_stats['total_cancelled'] = stats['user_canceled']
        shop_total = stats['shop_total']
        user_stats['sales_ratio'] = round((user_stats['total_sales'] / shop_total) * 100) if shop_total > 0 else 0

    recent_orders = []
    if is_super_admin:
        # 🔥 适配索引：查询+排序完全命中索引
        recent_orders = Order.objects.filter(
            creator=user,
            status__in=['pending', 'printed', 'reopened', 'cancelled']
        ).order_by('-create_time')[:15]

    return render(request, 'accounts/user_detail.html', {
        'user': user, 'is_super_admin': is_super_admin, 'user_stats': user_stats,
        'recent_orders': recent_orders, 'roles': Role.objects.all()
    })

@login_required
@permission_required('user_toggle_status')
def user_toggle_status(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})
    try:
        user = get_object_or_404(User.objects.select_related('role'), id=user_id)
        user.is_active = not user.is_active
        user.save()
        op_type = OP_TYPE_ENABLE_USER if user.is_active else OP_TYPE_DISABLE_USER
        status_text = '启用' if user.is_active else '禁用'
        create_operation_log(
            request=request, op_type=op_type, obj_type='user',
            obj_id=user.id, obj_name=f"{user.user_code}-{user.username}",
            detail=f"{status_text}用户：编号={user.user_code}"
        )
        return JsonResponse({'code': 1, 'msg': f'用户 {user.user_code} 已{status_text}！'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'操作失败：{str(e)}'})

@login_required
@permission_required('user_reset_password')
def reset_password(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})
    try:
        user = get_object_or_404(User.objects.select_related('role'), id=user_id)
        temp_pwd = "123456"
        user.set_password(temp_pwd)
        user.force_password_change = True
        user.save()
        create_operation_log(
            request=request, op_type=OP_TYPE_RESET_PASSWORD, obj_type='user',
            obj_id=user.id, obj_name=f"{user.user_code}-{user.username}",
            detail=f"重置用户密码：编号={user.user_code}"
        )
        return JsonResponse({'code': 1, 'msg': f'密码已重置为：{temp_pwd}，登录后强制修改！'})
    except Exception as e:
        return JsonResponse({'code': 0, 'msg': f'重置失败：{str(e)}'})

@login_required
@permission_required('role_permission_config')
def role_permission_config(request, role_code):
    if not (request.user.role and request.user.role.code == ROLE_SUPER_ADMIN):
        return redirect('/accounts/no-permission/')
    role = get_object_or_404(Role, code=role_code)
    all_roles = Role.objects.filter(code__in=[ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]).order_by('id')
    cache_key = f"role_perms_{role_code}"

    if request.method == 'POST' and 'save' in request.POST:
        try:
            selected_perm_ids = request.POST.getlist('permissions', [])
            if not selected_perm_ids and role.code != ROLE_SUPER_ADMIN:
                messages.error(request, '禁止空权限！')
                return redirect(f'/accounts/role-permission/{role_code}/')
            if role.code == ROLE_SUPER_ADMIN:
                selected_perm_ids = Permission.objects.filter(is_active=True).values_list('id', flat=True)
            role.permissions.clear()
            if selected_perm_ids:
                selected_perms = Permission.objects.filter(id__in=selected_perm_ids)
                role.permissions.add(*selected_perms)
            cache.delete(cache_key)
            create_operation_log(
                request=request, op_type=OP_TYPE_UPDATE_ROLE_PERM, obj_type='role',
                obj_id=role.id, obj_name=role.name, detail=f"配置角色权限：{role.name}"
            )
            messages.success(request, f'角色 {role.name} 权限已保存！')
            return redirect(f'/accounts/role-permission/{role_code}/')
        except Exception as e:
            messages.error(request, f'保存失败：{str(e)}')

    perm_groups = cache.get(cache_key)
    if not perm_groups:
        all_perms = Permission.objects.filter(is_active=True).order_by('category', 'code')
        selected_perm_ids = role.permissions.values_list('id', flat=True)
        if role.code == ROLE_SUPER_ADMIN:
            selected_perm_ids = all_perms.values_list('id', flat=True)
        perm_groups = {}
        for perm in all_perms:
            if perm.category not in perm_groups:
                perm_groups[perm.category] = {'name': perm.get_category_display(), 'permissions': []}
            perm_groups[perm.category]['permissions'].append({
                'id': perm.id, 'code': perm.code, 'name': perm.name, 'description': perm.description,
                'is_active': perm.is_active, 'is_selected': perm.id in selected_perm_ids
            })
        cache.set(cache_key, perm_groups, CACHE_ROLE_PERMISSION)

    return render(request, 'accounts/role_permission_config.html', {
        'current_role': role, 'all_roles': all_roles, 'perm_groups': perm_groups,
        'is_super_admin_role': role.code == ROLE_SUPER_ADMIN,
        'ROLE_SUPER_ADMIN': ROLE_SUPER_ADMIN, 'ROLE_ADMIN': ROLE_ADMIN, 'ROLE_OPERATOR': ROLE_OPERATOR
    })

# ========== 其他视图 ==========
@login_required
def profile(request):
    user = request.user
    if request.method == 'POST':
        user.first_name = request.POST.get('first_name', user.first_name).strip()
        user.last_name = request.POST.get('last_name', user.last_name).strip()
        user.phone = request.POST.get('phone', user.phone).strip()
        user.address = request.POST.get('address', user.address).strip()
        user.email = request.POST.get('email', user.email).strip()
        new_pwd = request.POST.get('new_password', '').strip()
        pwd_changed = False
        if new_pwd:
            old_pwd = request.POST.get('old_password', '').strip()
            if not user.check_password(old_pwd):
                messages.error(request, '原密码错误！')
                return render(request, 'accounts/profile.html', {'user': user})
            user.set_password(new_pwd)
            pwd_changed = True
        user.save()
        create_operation_log(
            request=request, op_type=OP_TYPE_UPDATE, obj_type='user',
            obj_id=user.id, obj_name=f"{user.user_code}-{user.username}",
            detail=f"修改个人信息"
        )
        messages.success(request, '个人信息修改成功！')
        if pwd_changed:
            login(request, user)
    return render(request, 'accounts/profile.html', {'user': user})

@login_required
def no_permission(request):
    return render(request, 'accounts/no_permission.html')