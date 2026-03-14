from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.contrib import messages
from django.db import IntegrityError
from .models import User


# ========== 权限校验函数（用于装饰器） ==========
def is_boss(user):
    """判断是否为老板（属于老板组）"""
    return user.groups.filter(name='老板').exists() or user.is_superuser


import logging  # 顶部导入日志模块

# 配置日志
logger = logging.getLogger(__name__)


def is_operator(user):
    """判断是否为开单人（属于开单人组）- 增加调试日志"""
    # 打印用户信息和组信息
    logger.info(f"=== 权限校验 ===")
    logger.info(f"用户名：{user.username} | ID：{user.id} | 超级管理员：{user.is_superuser}")
    logger.info(f"用户所属组：{[g.name for g in user.groups.all()]}")

    # 核心逻辑
    is_in_operator_group = user.groups.filter(name='开单人').exists()
    logger.info(f"是否在「开单人」组：{is_in_operator_group}")

    result = is_in_operator_group or user.is_superuser
    logger.info(f"最终校验结果：{result}")
    return (
            user.groups.filter(name='开单人').exists() or
            user.groups.filter(name='老板').exists() or
            user.is_superuser
    )


# ========== 登录/登出 ==========
def login_view(request):
    """登录页（复用Django auth认证）- 修复重定向循环"""
    # 1. 已登录用户：优先跳转到next参数，无则跳转到/bill/
    if request.user.is_authenticated:
        next_url = request.GET.get('next', '/bill/')
        return redirect(next_url)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()

        # 验证用户
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_active:
            login(request, user)
            # 记录登录态
            request.session['user_code'] = user.user_code
            request.session['user_name'] = user.name
            # 2. 登录成功：跳转到next参数（优先），无则跳转到/bill/
            next_url = request.POST.get('next', request.GET.get('next', '/bill/'))
            return redirect(next_url)
        else:
            messages.error(request, '用户名/密码错误或账户已禁用')

    # 3. 把next参数传递给前端模板
    context = {
        'next': request.GET.get('next', '')
    }
    return render(request, 'accounts/login.html', context)


def logout_view(request):
    """登出（清除session）"""
    logout(request)
    return redirect('/accounts/login/')


# ========== 个人信息管理 ==========
@login_required
def profile(request):
    """个人信息修改（所有登录用户可访问）"""
    user = request.user
    if request.method == 'POST':
        try:
            # 可修改的拓展字段（按需扩展）
            user.first_name = request.POST.get('first_name', user.first_name).strip()
            user.last_name = request.POST.get('last_name', user.last_name).strip()
            user.phone = request.POST.get('phone', user.phone).strip()
            user.address = request.POST.get('address', user.address).strip()
            user.email = request.POST.get('email', user.email).strip()

            # 密码修改（可选）
            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                user.set_password(new_password)  # Django自动加密

            user.save()
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


# ========== 用户管理（仅老板可访问） ==========
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
            if group_id:
                user.groups.clear()
                user.groups.add(Group.objects.get(id=group_id))

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
            if group_id and group_id.isdigit():
                try:
                    group = Group.objects.get(id=group_id)
                    user.groups.add(group)
                except Group.DoesNotExist:
                    messages.warning(request, '所选权限组不存在，已忽略')

            # 密码修改（可选）
            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                user.set_password(new_password)

            user.save()
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
def user_delete(request, user_id):
    """删除用户（逻辑删除：禁用账户）"""
    if request.method == 'POST':
        try:
            user = get_object_or_404(User, id=user_id)
            # 逻辑删除：禁用账户（保留数据）
            user.is_active = False
            user.save()
            return JsonResponse({'code': 1, 'msg': f'用户 {user.user_code} 已禁用！'})
        except Exception as e:
            return JsonResponse({'code': 0, 'msg': f'操作失败：{str(e)}'})
    return JsonResponse({'code': 0, 'msg': '仅支持POST请求'})

# ========== 新增：无权限提示页 ==========
@login_required
def no_permission(request):
    """权限不足提示页"""
    return render(request, 'accounts/no_permission.html', {
        'is_boss': is_boss(request.user)
    })