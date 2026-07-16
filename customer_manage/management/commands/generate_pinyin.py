# customer/management/commands/generate_pinyin.py
from django.core.management.base import BaseCommand
from pypinyin import lazy_pinyin
from customer_manage.models import Customer      # 根据实际路径调整
from product.models import Product        # 根据实际路径调整
from django.db.models import Q


class Command(BaseCommand):
    help = '为已有客户和商品批量生成拼音字段（pinyin_full, pinyin_abbr）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制更新所有记录（即使拼音已存在）',
        )

    def handle(self, *args, **options):
        force = options['force']
        self.generate_for_customers(force)
        self.generate_for_products(force)

    def generate_for_customers(self, force):
        self.stdout.write('==== 处理客户拼音 ====')
        # 使用 all_objects 以包含被软删除的客户
        if force:
            queryset = Customer.all_objects.all()
        else:
            queryset = Customer.all_objects.filter(
                Q(pinyin_full='') | Q(pinyin_abbr='')
            )
        total = queryset.count()
        if total == 0:
            self.stdout.write('无需更新，客户拼音已完整')
            return

        self.stdout.write(f'待更新客户数: {total}')
        batch_size = 200
        updated = 0
        # 分批处理，避免内存占用过大
        for i in range(0, total, batch_size):
            batch = list(queryset[i:i+batch_size])
            for obj in batch:
                if obj.name:
                    obj.pinyin_full = ''.join(lazy_pinyin(obj.name, style=0))
                    obj.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(obj.name, style=0)])
            Customer.all_objects.bulk_update(batch, ['pinyin_full', 'pinyin_abbr'])
            updated += len(batch)
            self.stdout.write(f'  客户进度: {updated}/{total}')
        self.stdout.write(self.style.SUCCESS('客户拼音生成完成！'))

    def generate_for_products(self, force):
        self.stdout.write('==== 处理商品拼音 ====')
        if force:
            queryset = Product.all_objects.all()
        else:
            queryset = Product.all_objects.filter(
                Q(pinyin_full='') | Q(pinyin_abbr='')
            )
        total = queryset.count()
        if total == 0:
            self.stdout.write('无需更新，商品拼音已完整')
            return

        self.stdout.write(f'待更新商品数: {total}')
        batch_size = 200
        updated = 0
        for i in range(0, total, batch_size):
            batch = list(queryset[i:i+batch_size])
            for obj in batch:
                if obj.name:
                    obj.pinyin_full = ''.join(lazy_pinyin(obj.name, style=0))
                    obj.pinyin_abbr = ''.join([p[0] for p in lazy_pinyin(obj.name, style=0)])
            Product.all_objects.bulk_update(batch, ['pinyin_full', 'pinyin_abbr'])
            updated += len(batch)
            self.stdout.write(f'  商品进度: {updated}/{total}')
        self.stdout.write(self.style.SUCCESS('商品拼音生成完成！'))