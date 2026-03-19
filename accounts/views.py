from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.contrib import messages
from django.db import IntegrityError
from .models import User
from operation_log.models import OperationLog
from django.utils import timezone
import logging

# 配置日志
logger = logging.getLogger(__name__)


# ========== 通用函数 ==========
def create_operation_log(request, operation_type, object_type, object_id=None, object_name=None, operation_detail=None):
    """封装操作日志记录逻辑，容错处理"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    ip_address = x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR', '')
    try:
        OperationLog.objects.create(
            operator=request.user if request.user.is_authenticated else None,
            operation_time=timezone.now(),
            operation_type=operation_type,
            object_type=object_type,
            object_id=str(object_id) if object_id else None,
            object_name=object_name,
            operation_detail=operation_detail,
            ip_address=ip_address
        )
    except Exception as e:
        print(f"【日志记录失败】：{str(e)}")


def is_boss(user):
    """判断是否为老板（属于老板组）"""
    return user.groups.filter(name='老板').exists() or user.is_superuser


def is_operator(user):
    """判断是否为开单人"""
    logger.info(f"权限校验：用户名={user.username}，组={[g.name for g in user.groups.all()]}")
    return user.groups.filter(name='开单人').exists() or user.groups.filter(name='老板').exists() or user.is_superuser


# ========== 登录视图（新增强制改密码校验） ==========
def login_view(request):
    """登录页 - 登录后检查是否需要强制改密码"""
    if request.user.is_authenticated:
        # 已登录且需要强制改密码：直接跳改密码页
        if request.user.force_password_change:
            return redirect('/accounts/force-change-password/')
        next_url = request.GET.get('next', '/bill/')
        return redirect(next_url)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)

        if user is not None and user.is_active:
            login(request, user)
            request.session['user_code'] = user.user_code
            request.session['user_name'] = user.name

            # 记录登录日志
            create_operation_log(
                request=request,
                operation_type='login',
                object_type='user',
                object_id=user.id,
                object_name=f"{user.user_code}-{user.username}",
                operation_detail=f"用户登录：编号={user.user_code}，用户名={user.username}"
            )

            # 核心逻辑：检查是否需要强制改密码
            if user.force_password_change:
                return redirect('/accounts/force-change-password/')

            next_url = request.POST.get('next', request.GET.get('next', '/bill/'))
            return redirect(next_url)
        else:
            messages.error(request, '用户名/密码错误或账户已禁用')

    context = {'next': request.GET.get('next', '')}
    return render(request, 'accounts/login.html', context)


# ========== 老板重置密码视图（核心） ==========
@login_required
@user_passes_test(is_boss)
def reset_password(request, user_id):
    """老板重置员工密码为临时密码，标记强制改密码"""
    if request.method == 'POST':
        try:
            user = get_object_or_404(User, id=user_id)
            # 安全：老板永远看不到原密码（Django密码是哈希存储）
            temp_password = "123456"  # 临时密码（可配置）

            # 重置密码+标记强制改密码
            user.set_password(temp_password)
            user.force_password_change = True
            user.save()

            # 记录重置密码日志（不存储临时密码，仅记录操作）
            create_operation_log(
                request=request,
                operation_type='reset_password',
                object_type='user',
                object_id=user.id,
                object_name=f"{user.user_code}-{user.username}",
                operation_detail=f"重置用户密码：编号={user.user_code}，用户名={user.username}，已生成临时密码并强制改密码"
            )

            return JsonResponse({
                'code': 1,
                'msg': f'用户 {user.user_code} 密码已重置为临时密码：{temp_password}，用户登录后将强制修改密码'
            })
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'重置失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


# ========== 员工强制改密码视图 ==========
@login_required
def force_change_password(request):
    """员工登录后强制改密码页面，必须修改才能进入系统"""
    # 非强制改密码状态：直接跳首页
    if not request.user.force_password_change:
        return redirect('/bill/')

    if request.method == 'POST':
        old_password = request.POST.get('old_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        # 校验逻辑
        if not old_password or not new_password or not confirm_password:
            messages.error(request, '所有密码字段不能为空！')
        elif new_password != confirm_password:
            messages.error(request, '两次输入的新密码不一致！')
        elif len(new_password) < 8:
            messages.error(request, '新密码长度至少8位！')
        elif not request.user.check_password(old_password):
            messages.error(request, '原密码输入错误！')
        else:
            # 修改密码并取消强制改密码标记
            request.user.set_password(new_password)
            request.user.force_password_change = False
            request.user.save()

            # 记录改密码日志
            create_operation_log(
                request=request,
                operation_type='change_password',
                object_type='user',
                object_id=request.user.id,
                object_name=f"{request.user.user_code}-{request.user.username}",
                operation_detail=f"用户强制修改密码：编号={request.user.user_code}，用户名={request.user.username}"
            )

            # 重新登录（密码修改后）
            login(request, request.user)
            messages.success(request, '密码修改成功！请正常使用系统。')
            return redirect('/bill/')

    return render(request, 'accounts/force_change_password.html', {
        'is_boss': is_boss(request.user),
        'user': request.user
    })


# ========== 权限校验函数（用于装饰器） ==========
def is_boss(user):
    """判断是否为老板（属于老板组）"""
    return user.groups.filter(name='老板').exists() or user.is_superuser


def logout_view(request):
    """登出（清除session）"""
    # ========== 修改：日志操作类型从 query 改为 logout ==========
    if request.user.is_authenticated:
        create_operation_log(
            request=request,
            operation_type='logout',  # 关键修改：登出操作
            object_type='user',
            object_id=request.user.id,
            object_name=f"{request.user.user_code}-{request.user.username}",
            operation_detail=f"用户登出：编号={request.user.user_code}，用户名={request.user.username}，IP={request.META.get('REMOTE_ADDR', '')}"
        )

    logout(request)
    return redirect('/accounts/login/')


# ========== 以下代码无修改，保持原样 ==========
# 个人信息管理
@login_required
def profile(request):
    """个人信息修改（所有登录用户可访问）"""
    user = request.user
    if request.method == 'POST':
        try:
            # 保存修改前的信息（用于日志对比）
            old_info = {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'phone': user.phone,
                'address': user.address,
                'email': user.email
            }

            # 可修改的拓展字段（按需扩展）
            user.first_name = request.POST.get('first_name', user.first_name).strip()
            user.last_name = request.POST.get('last_name', user.last_name).strip()
            user.phone = request.POST.get('phone', user.phone).strip()
            user.address = request.POST.get('address', user.address).strip()
            user.email = request.POST.get('email', user.email).strip()

            # 密码修改（可选）
            new_password = request.POST.get('new_password', '').strip()
            password_changed = False
            if new_password:
                user.set_password(new_password)
                password_changed = True

            user.save()

            # ========== 新增：记录个人信息修改日志 ==========
            operation_detail = (
                f"修改个人信息：编号={user.user_code}，用户名={user.username}，"
                f"原姓名={old_info['first_name'] + old_info['last_name']}→新姓名={user.first_name + user.last_name}，"
                f"原电话={old_info['phone']}→新电话={user.phone}，"
                f"原邮箱={old_info['email']}→新邮箱={user.email}，"
                f"原地址={old_info['address']}→新地址={user.address}，"
                f"密码是否修改：{'是' if password_changed else '否'}"
            )
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='user',
                object_id=user.id,
                object_name=f"{user.user_code}-{user.username}",
                operation_detail=operation_detail
            )

            messages.success(request, '个人信息修改成功！')
            # 重新登录（密码修改后）
            if new_password:
                login(request, user)
        except Exception as e:
            messages.error(request, f'修改失败：{str(e)}')

    return render(request, 'accounts/profile.html', {
        'user': user,
        'is_boss': is_boss(request.user)  # 传递变量
    })


# 用户管理（仅老板可访问）
@login_required
@user_passes_test(is_boss)
def user_list(request):
    """用户列表（老板权限）- 支持搜索和状态筛选"""
    # 获取筛选参数
    keyword = request.GET.get('keyword', '').strip()
    status = request.GET.get('status', 'all')  # all/active/inactive

    # 初始化查询集
    users = User.objects.all().order_by('-date_joined')

    # 关键词筛选（编号/用户名/电话）
    if keyword:
        users = users.filter(
            Q(user_code__icontains=keyword) |
            Q(username__icontains=keyword) |
            Q(phone__icontains=keyword)
        )

    # 状态筛选
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)

    # 获取所有权限组
    groups = Group.objects.all()

    return render(request, 'accounts/user_list.html', {
        'users': users,
        'groups': groups,
        'is_boss': is_boss(request.user),
        'keyword': keyword,
        'status': status
    })


@login_required
@user_passes_test(is_boss)
def user_add(request):
    """添加新用户（老板权限）"""
    if request.method == 'POST':
        try:
            # 获取表单数据
            username = request.POST.get('username', '').strip()
            user_code = request.POST.get('user_code', '').strip()
            password = request.POST.get('password', '').strip()
            phone = request.POST.get('phone', '').strip()
            group_id = request.POST.get('group_id')
            is_active = request.POST.get('is_active') == 'on'
            is_staff = request.POST.get('is_staff') == 'on'

            # 必传字段校验
            if not username or not user_code or not password:
                messages.error(request, '用户名、用户编号、初始密码不能为空！')
                return render(request, 'accounts/user_form.html', {
                    'groups': Group.objects.all(),
                    'is_boss': is_boss(request.user),
                    'form_data': request.POST
                })

            # 创建用户
            user = User.objects.create_user(
                username=username,
                user_code=user_code,
                password=password,
                phone=phone,
                is_active=is_active,
                is_staff=is_staff
            )

            # 关联权限组
            group_name = '无'
            if group_id:
                group = Group.objects.get(id=group_id)
                user.groups.add(group)
                group_name = group.name

            # ========== 新增：记录新增用户日志 ==========
            operation_detail = (
                f"新增用户：编号={user_code}，用户名={username}，电话={phone if phone else '无'}，"
                f"权限组={group_name}，状态={'启用' if is_active else '禁用'}，是否后台管理员={'是' if is_staff else '否'}"
            )
            create_operation_log(
                request=request,
                operation_type='create',
                object_type='user',
                object_id=user.id,
                object_name=f"{user_code}-{username}",
                operation_detail=operation_detail
            )

            messages.success(request, f'用户 {user_code} - {username} 创建成功！')
            return redirect('/accounts/user-list/')

        except IntegrityError:
            messages.error(request, '用户编号已存在！请更换编号。')
            return render(request, 'accounts/user_form.html', {
                'groups': Group.objects.all(),
                'is_boss': is_boss(request.user),
                'form_data': request.POST
            })
        except Exception as e:
            messages.error(request, f'创建失败：{str(e)}')
            return render(request, 'accounts/user_form.html', {
                'groups': Group.objects.all(),
                'is_boss': is_boss(request.user),
                'form_data': request.POST
            })

    # GET请求：展示添加表单
    return render(request, 'accounts/user_form.html', {
        'groups': Group.objects.all(),
        'is_boss': is_boss(request.user),
        'is_add': True
    })


@login_required
@user_passes_test(is_boss)
def user_edit(request, user_id):
    """编辑用户（老板权限）- 修复组分配逻辑"""
    user = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        try:
            # 保存修改前的信息（用于日志对比）
            old_info = {
                'username': user.username,
                'user_code': user.user_code,
                'phone': user.phone,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'group': user.groups.first().name if user.groups.first() else '无'
            }

            # 基础信息
            user.username = request.POST.get('username', user.username).strip()
            user.user_code = request.POST.get('user_code', user.user_code).strip()
            user.phone = request.POST.get('phone', user.phone).strip()
            user.is_active = request.POST.get('is_active') == 'on'
            user.is_staff = request.POST.get('is_staff') == 'on'

            # 权限组 - 修复逻辑：无论是否选组，都显式处理
            group_id = request.POST.get('group_id', '').strip()
            # 先清空所有组
            user.groups.clear()
            # 如果选了组，就添加
            new_group_name = '无'
            if group_id and group_id.isdigit():
                try:
                    group = Group.objects.get(id=group_id)
                    user.groups.add(group)
                    new_group_name = group.name
                except Group.DoesNotExist:
                    messages.warning(request, '所选权限组不存在，已忽略')

            # 密码修改（可选）
            new_password = request.POST.get('new_password', '').strip()
            password_changed = False
            if new_password:
                user.set_password(new_password)
                password_changed = True

            user.save()

            # ========== 新增：记录编辑用户日志 ==========
            operation_detail = (
                f"编辑用户：原编号={old_info['user_code']}→新编号={user.user_code}，原用户名={old_info['username']}→新用户名={user.username}，"
                f"原电话={old_info['phone']}→新电话={user.phone if user.phone else '无'}，"
                f"原权限组={old_info['group']}→新权限组={new_group_name}，"
                f"原状态={'启用' if old_info['is_active'] else '禁用'}→新状态={'启用' if user.is_active else '禁用'}，"
                f"原后台管理员={'是' if old_info['is_staff'] else '否'}→新后台管理员={'是' if user.is_staff else '否'}，"
                f"密码是否修改：{'是' if password_changed else '否'}"
            )
            create_operation_log(
                request=request,
                operation_type='update',
                object_type='user',
                object_id=user.id,
                object_name=f"{user.user_code}-{user.username}",
                operation_detail=operation_detail
            )

            messages.success(request, f'用户 {user.user_code} - {user.username} 修改成功！')
            return redirect('/accounts/user-list/')
        except IntegrityError:
            messages.error(request, '用户编号已存在！请更换编号。')
        except Exception as e:
            messages.error(request, f'修改失败：{str(e)}')

    # 获取用户当前所属组
    user_group = user.groups.first()
    group_id = user_group.id if user_group else ''

    # GET请求：展示编辑表单
    return render(request, 'accounts/user_form.html', {
        'user': user,
        'groups': Group.objects.all(),
        'is_boss': is_boss(request.user),
        'group_id': group_id,
        'is_edit': True
    })


@login_required
@user_passes_test(is_boss)
def user_toggle_status(request, user_id):
    """切换用户状态（启用/禁用）- 替代原来的固定禁用"""
    if request.method == 'POST':
        try:
            user = get_object_or_404(User, id=user_id)
            # 保存修改前的信息
            old_info = {
                'user_code': user.user_code,
                'username': user.username,
                'is_active': user.is_active
            }

            # 切换状态：启用 ↔ 禁用
            user.is_active = not user.is_active
            user.save()

            # 确定操作类型和详情
            if user.is_active:
                operation_type = 'enable_user'
                operation_detail = (
                    f"启用用户：编号={old_info['user_code']}，用户名={old_info['username']}，"
                    f"原状态={'启用' if old_info['is_active'] else '禁用'}→新状态=启用"
                )
                msg = f'用户 {user.user_code} 已启用！'
            else:
                operation_type = 'disable_user'
                operation_detail = (
                    f"禁用用户：编号={old_info['user_code']}，用户名={old_info['username']}，"
                    f"原状态={'启用' if old_info['is_active'] else '禁用'}→新状态=禁用"
                )
                msg = f'用户 {user.user_code} 已禁用！'

            # 记录日志
            create_operation_log(
                request=request,
                operation_type=operation_type,
                object_type='user',
                object_id=user.id,
                object_name=f"{user.user_code}-{user.username}",
                operation_detail=operation_detail
            )

            return JsonResponse({'code': 1, 'msg': msg})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'操作失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})


# 新增：无权限提示页
@login_required
def no_permission(request):
    """权限不足提示页"""
    return render(request, 'accounts/no_permission.html', {
        'is_boss': is_boss(request.user)
    })