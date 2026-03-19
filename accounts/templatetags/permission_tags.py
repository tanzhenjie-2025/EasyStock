from django import template

register = template.Library()

@register.filter(name='has_perm')  # 移除 @stringfilter 装饰器！
def has_permission(user, perm_code):
    """
    自定义模板过滤器：判断用户是否拥有指定权限
    使用方式：{{ user|has_perm:"order_create" }}
    """
    # 加强类型判断：确保user是用户对象，而不是字符串/None
    if not user or not hasattr(user, 'is_authenticated'):
        return False
    if not user.is_authenticated:
        return False
    # 调用User模型的has_permission方法
    return user.has_permission(perm_code)

@register.filter(name='has_any_perm')
def has_any_permission(user, perm_codes):
    """
    自定义模板过滤器：判断用户是否拥有任意一个指定权限
    使用方式：{{ user|has_any_perm:"log_view,log_view_all" }}
    """
    # 加强类型判断
    if not user or not hasattr(user, 'is_authenticated'):
        return False
    if not user.is_authenticated:
        return False
    # 处理逗号分隔的字符串（模板中传多个参数的兼容写法）
    if isinstance(perm_codes, str):
        perm_codes = [code.strip() for code in perm_codes.split(',') if code.strip()]
    # 调用User模型的has_any_permission方法
    return user.has_any_permission(*perm_codes)