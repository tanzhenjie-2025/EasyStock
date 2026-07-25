# export_import/registry.py
from collections import OrderedDict

MODULE_HANDLERS = OrderedDict([
    ('area', {
        'export': 'area_manage.views.export_areas_to_io',   # 返回 BytesIO
        'import': 'area_manage.views.import_areas_from_io',
        'dependencies': [],  # 无依赖
    }),
    ('customer', {
        'export': 'customer_manage.views.export_customers_to_io',
        'import': 'customer_manage.views.import_customers_from_io',
        'dependencies': ['area'],  # 客户依赖区域
    }),
    ('product', {
        'export': 'product.views.export_products_to_io',
        'import': 'product.views.import_products_from_io',
        'dependencies': [],  # 商品不依赖其他（标签可有可无）
    }),
    ('user', {
        'export': 'accounts.views.export_users_to_io',
        'import': 'accounts.views.import_users_from_io',
        'dependencies': [],  # 用户可能依赖角色，但角色未提供导入，可先忽略
    }),
    # ('order', {
    #     'export': 'bill.views.export_orders_to_io',
    #     'import': 'bill.views.import_orders_from_io',
    #     'dependencies': ['customer', 'product', 'area'],  # 订单依赖客户、商品、区域
    # }),
])