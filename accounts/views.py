from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.contrib import messages
from .models import User

# ========== 权限校验函数（用于装饰器） ==========
def is_boss(user):
    """判断是否为老板（属于老板组）"""
    return user.groups.filter(name='老板').exists() or user.is_superuser

def is_operator(user):
    """判断是否为开单人（属于开单人组）"""
    return user.groups.filter(name='开单人').exists() or user.is_superuser

# ========== 登录/登出 ==========
def login_view(request):
    """登录页（复用Django auth认证）"""
    if request.user.is_authenticated:
        return redirect('/bill/')  # 已登录直接跳开单页

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
            return redirect('/bill/')
        else:
            messages.error(request, '用户名/密码错误或账户已禁用')

    return render(request, 'accounts/login.html')

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
    """用户列表（老板权限）"""
    users = User.objects.all().order_by('-date_joined')
    groups = Group.objects.all()
    return render(request, 'accounts/user_list.html', {
        'users': users,
        'groups': groups,
        'is_boss': is_boss(request.user)  # 传递变量
    })

@login_required
@user_passes_test(is_boss)
def user_edit(request, user_id):
    """编辑用户（老板权限）"""
    user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        try:
            # 基础信息
            user.username = request.POST.get('username', user.username).strip()
            user.user_code = request.POST.get('user_code', user.user_code).strip()
            user.phone = request.POST.get('phone', user.phone).strip()
            user.is_active = request.POST.get('is_active') == 'on'
            user.is_staff = request.POST.get('is_staff') == 'on'

            # 权限组
            group_id = request.POST.get('group_id')
            if group_id:
                user.groups.clear()
                user.groups.add(Group.objects.get(id=group_id))

            # 密码修改（可选）
            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                user.set_password(new_password)

            user.save()
            messages.success(request, '用户信息修改成功！')
            return redirect('/accounts/user-list/')
        except Exception as e:
            messages.error(request, f'修改失败：{str(e)}')

    # return render(request, 'accounts/user_edit.html', {
    #     'user': user,
    #     'groups': Group.objects.all(),
    #     'is_boss': is_boss(request.user)  # 传递变量
    # })