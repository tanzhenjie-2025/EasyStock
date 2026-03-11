from django.db import models

# Create your models here.
# 商品管理模块复用 bill 模块的 Product/ProductAlias Model，无需重复定义
# 引用方式：在 views.py 中 from bill.models import Product, ProductAlias