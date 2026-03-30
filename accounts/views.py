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
# 新增：导入订单模型
from bill.models import Order, Area
from django.db.models import Sum, Count
from django.views.decorators.csrf import csrf_exempt

# 替换：导入低级别缓存API（安全缓存方案）
from django.core.cache import cache
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
# 导入模型和常量
from .models import (
    User, Role, Permission,
    ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR
)
from operation_log.models import OperationLog

from django.db.models import DecimalField
from decimal import Decimal

# 配置日志
logger = logging.getLogger(__name__)

# ========== 仅保留安全的缓存常量（角色权限数据缓存） ==========
CACHE_ROLE_PERMISSION = 1800      # 角色权限数据缓存：30分钟

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
# 🔥 已删除：错误的全局页面缓存，杜绝越权访问
def user_list(request):
    """用户列表（支持搜索/状态筛选 + 销售统计 + 分页）【性能优化版：批量注解，无N+1查询】"""
    # 筛选参数
    keyword = request.GET.get('keyword', '').strip()
    status = request.GET.get('status', 'all')
    # 分页参数
    page = request.GET.get('page', 1)
    page_size = 10

    # 预加载角色，解决N+1查询
    queryset = User.objects.select_related('role').all().order_by('-date_joined')

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

    # 核心优化：超级管理员 → 批量注解统计（无循环查询）
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN
    if is_super_admin:
        # 子查询1：当前用户的有效订单数
        order_count_sub = Order.objects.filter(
            creator=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened']
        ).values('creator').annotate(cnt=Count('id')).values('cnt')

        # 子查询2：当前用户的有效销售额
        sales_sum_sub = Order.objects.filter(
            creator=OuterRef('pk'),
            status__in=['pending', 'printed', 'reopened']
        ).values('creator').annotate(total=Sum('total_amount')).values('total')

        # 批量注解统计字段
        queryset = queryset.annotate(
            orders=Coalesce(Subquery(order_count_sub), Value(0), output_field=DecimalField(max_digits=12, decimal_places=2)),
            sales=Coalesce(Subquery(sales_sum_sub), Value(Decimal('0')), output_field=DecimalField(max_digits=12, decimal_places=2))
        )

    # 分页逻辑
    paginator = Paginator(queryset, page_size)
    try:
        users_page = paginator.page(page)
    except PageNotAnInteger:
        users_page = paginator.page(1)
    except EmptyPage:
        users_page = paginator.page(paginator.num_pages)

    # 店铺统计：1次SQL完成
    shop_stats = {
        'total_orders': 0,
        'total_sales': 0,
        'total_cancelled': 0
    }
    if is_super_admin:
        shop_stats = Order.objects.aggregate(
            total_orders=Count(Case(
                When(status__in=['pending', 'printed', 'reopened'], then='id')
            )),
            total_sales=Coalesce(
                Sum(
                    Case(
                        When(status__in=['pending', 'printed', 'reopened'], then='total_amount'),
                        output_field=DecimalField(max_digits=12, decimal_places=2)
                    )
                ),
                Value(Decimal('0')),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
            total_cancelled=Count(Case(
                When(status='cancelled', then='id')
            ))
        )

    # 计算销售占比
    if is_super_admin:
        total_sales = shop_stats['total_sales']
        for user in users_page:
            user.ratio = round((user.sales / total_sales) * 100) if total_sales > 0 else 0

    return render(request, 'accounts/user_list.html', {
        'users': users_page,
        'roles': Role.objects.all(),
        'keyword': keyword,
        'status': status,
        'current_user': request.user,
        'is_super_admin': is_super_admin,
        'shop_stats': shop_stats,
        'paginator': paginator,
        'page_obj': users_page,
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
    # 预加载角色
    user = get_object_or_404(User.objects.select_related('role'), id=user_id)

    if request.method == 'POST':
        # 获取表单数据
        username = request.POST.get('username', '').strip()
        user_code = request.POST.get('user_code', '').strip()
        phone = request.POST.get('phone', '').strip()
        role_id = request.POST.get('role_id')
        is_active = request.POST.get('is_active') == 'on'
        is_staff = request.POST.get('is_staff') == 'on'
        new_password = request.POST.get('new_password', '').strip()

        # 保存原始信息
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

            # 更新密码
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
@permission_required('user_view')
# 🔥 已删除：错误的全局页面缓存，杜绝数据泄露
def user_detail(request, user_id):
    """用户详情页（包含开单统计和最近订单）【优化版：合并聚合查询，减少DB请求】"""
    # 预加载角色
    user = get_object_or_404(User.objects.select_related('role'), id=user_id)
    # 仅超级管理员可见统计数据
    is_super_admin = request.user.role and request.user.role.code == ROLE_SUPER_ADMIN

    # 初始化统计数据
    user_stats = {
        'total_orders': 0,
        'total_sales': 0,
        'total_cancelled': 0,
        'sales_ratio': 0
    }

    if is_super_admin and user.is_active:
        from django.db.models import DecimalField, Count, Sum, Case, When
        # 1次聚合查询获取所有统计
        stats = Order.objects.filter(
            status__in=['pending', 'printed', 'reopened', 'cancelled']
        ).aggregate(
            shop_total=Coalesce(
                Sum(Case(When(status__in=['pending', 'printed', 'reopened'], then='total_amount'))),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
            user_orders=Count(Case(When(creator=user, status__in=['pending', 'printed', 'reopened'], then='id'))),
            user_sales=Coalesce(
                Sum(Case(When(creator=user, status__in=['pending', 'printed', 'reopened'], then='total_amount'))),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2)
            ),
            user_canceled=Count(Case(When(creator=user, status='cancelled', then='id')))
        )

        # 赋值统计结果
        user_stats['total_orders'] = stats['user_orders']
        user_stats['total_sales'] = stats['user_sales']
        user_stats['total_cancelled'] = stats['user_canceled']
        # 计算占比
        shop_total = stats['shop_total']
        user_stats['sales_ratio'] = round((user_stats['total_sales'] / shop_total) * 100) if shop_total > 0 else 0

    # 最近15条订单
    recent_orders = []
    if is_super_admin:
        recent_orders = Order.objects.filter(creator=user).order_by('-create_time')[:15]

    return render(request, 'accounts/user_detail.html', {
        'user': user,
        'is_super_admin': is_super_admin,
        'user_stats': user_stats,
        'recent_orders': recent_orders,
        'roles': Role.objects.all()
    })

@login_required
@permission_required('user_toggle_status')
def user_toggle_status(request, user_id):
    """切换用户状态（启用/禁用）"""
    if request.method != 'POST':
        return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

    try:
        user = get_object_or_404(User.objects.select_related('role'), id=user_id)
        # 切换状态
        user.is_active = not user.is_active
        user.save()

        # 日志
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
        user = get_object_or_404(User.objects.select_related('role'), id=user_id)
        temp_pwd = "123456"

        # 重置密码
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


@login_required
@permission_required('role_permission_config')
# 🔥 已删除：页面缓存，替换为安全的数据缓存
def role_permission_config(request, role_code):
    """角色权限配置（增强版）+ 安全数据缓存"""
    # 仅超级管理员可访问
    if not (request.user.role and request.user.role.code == ROLE_SUPER_ADMIN):
        return redirect('/accounts/no-permission/')

    # 获取角色
    role = get_object_or_404(Role, code=role_code)
    all_roles = Role.objects.filter(code__in=[ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]).order_by('id')
    cache_key = f"role_perms_{role_code}"

    # ========== 保存权限 ==========
    if request.method == 'POST' and 'save' in request.POST:
        try:
            selected_perm_ids = request.POST.getlist('permissions', [])

            # 校验
            if not selected_perm_ids and role.code != ROLE_SUPER_ADMIN:
                messages.error(request, '禁止配置空权限！至少保留基础查看权限')
                return redirect(f'/accounts/role-permission/{role_code}/')

            # 超级管理员强制全选
            if role.code == ROLE_SUPER_ADMIN:
                selected_perm_ids = Permission.objects.filter(is_active=True).values_list('id', flat=True)

            # 更新权限
            role.permissions.clear()
            if selected_perm_ids:
                selected_perms = Permission.objects.filter(id__in=selected_perm_ids)
                role.permissions.add(*selected_perms)

            # 🔥 关键：修改后主动清理缓存，杜绝脏数据
            cache.delete(cache_key)

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

            messages.success(request, f'角色 {role.name} 的权限配置已保存！')
            return redirect(f'/accounts/role-permission/{role_code}/')

        except Exception as e:
            messages.error(request, f'保存失败：{str(e)}')

    # ========== 安全数据缓存：仅缓存权限数据，按角色隔离 ==========
    # 先从缓存获取
    perm_groups = cache.get(cache_key)
    if not perm_groups:
        all_perms = Permission.objects.filter(is_active=True).order_by('category', 'code')
        selected_perm_ids = role.permissions.values_list('id', flat=True)

        # 超级管理员强制全选
        if role.code == ROLE_SUPER_ADMIN:
            selected_perm_ids = all_perms.values_list('id', flat=True)

        # 按分类分组权限
        perm_groups = {}
        for perm in all_perms:
            if perm.category not in perm_groups:
                perm_groups[perm.category] = {
                    'name': perm.get_category_display(),
                    'permissions': []
                }
            perm_groups[perm.category]['permissions'].append({
                'id': perm.id,
                'code': perm.code,
                'name': perm.name,
                'description': perm.description,
                'is_active': perm.is_active,
                'is_selected': perm.id in selected_perm_ids
            })
        # 写入缓存，30分钟过期
        cache.set(cache_key, perm_groups, CACHE_ROLE_PERMISSION)

    return render(request, 'accounts/role_permission_config.html', {
        'current_role': role,
        'all_roles': all_roles,
        'perm_groups': perm_groups,
        'is_super_admin_role': role.code == ROLE_SUPER_ADMIN,
        'ROLE_SUPER_ADMIN': ROLE_SUPER_ADMIN,
        'ROLE_ADMIN': ROLE_ADMIN,
        'ROLE_OPERATOR': ROLE_OPERATOR
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

        # 密码修改
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
# 🔥 已删除：无意义缓存
def no_permission(request):
    """无权限提示"""
    return render(request, 'accounts/no_permission.html')