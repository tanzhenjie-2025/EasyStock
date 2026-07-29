# goods/management/commands/update_pinyin_full.py

from django.core.management.base import BaseCommand
from django.apps import apps
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = '更新所有商品、别名、单位的拼音全拼字段为空格分隔格式'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='批量处理大小（默认500）'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        models_to_update = [
            ('product', 'Product'),    # 请将 'goods' 替换为你的实际 app 名称
            ('product', 'ProductAlias'),
            ('product', 'Unit'),
        ]

        for app_label, model_name in models_to_update:
            model = apps.get_model(app_label, model_name)
            count = 0
            total = model.objects.count()
            self.stdout.write(f"开始更新 {model_name}，共 {total} 条记录...")

            # 分批更新，避免内存溢出
            pk = 0
            while True:
                queryset = model.objects.filter(pk__gt=pk).order_by('pk')[:batch_size]
                if not queryset:
                    break
                for obj in queryset:
                    obj.save()   # 调用 save 重新生成拼音
                    count += 1
                    pk = obj.pk
                self.stdout.write(f"  已更新 {count} 条")

            self.stdout.write(self.style.SUCCESS(f"✅ {model_name} 更新完成，共 {count} 条"))

        self.stdout.write(self.style.SUCCESS("🎉 所有数据更新完毕！"))