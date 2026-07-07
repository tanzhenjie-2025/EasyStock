# accounts/management/commands/create_superadmin.py
from django.core.management.base import BaseCommand, CommandError
from accounts.models import User, Role


class Command(BaseCommand):
    help = '创建一个拥有超级管理员角色的用户'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='登录用户名')
        parser.add_argument('--user_code', required=True, help='用户编号')
        parser.add_argument('--email', default='admin@example.com', help='邮箱')
        parser.add_argument('--password', required=True, help='密码')

    def handle(self, *args, **options):
        try:
            role = Role.objects.get(code='super_admin')
        except Role.DoesNotExist:
            raise CommandError('超级管理员角色不存在，请先运行 migrate')

        username = options['username']
        user_code = options['user_code']
        email = options['email']
        password = options['password']

        if User.objects.filter(username=username).exists():
            raise CommandError(f'用户名 {username} 已存在')

        if User.objects.filter(user_code=user_code).exists():
            raise CommandError(f'用户编号 {user_code} 已存在')

        user = User.objects.create(
            username=username,
            user_code=user_code,
            email=email,
            role=role,
            is_staff=True,
            is_superuser=True,
        )
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS(f'超级管理员 "{username}" 创建成功！'))