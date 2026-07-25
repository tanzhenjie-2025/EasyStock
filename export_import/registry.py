from collections import OrderedDict

MODULE_HANDLERS = OrderedDict([
    ('area', {
        'export': 'area_manage.views.export_areas_to_io',
        'import': 'area_manage.views.import_areas_from_io',
        'dependencies': [],
    }),
('group', {   # 新增区域组，依赖区域
        'export': 'area_manage.views.export_groups_to_io',
        'import': 'area_manage.views.import_groups_from_io',
        'dependencies': ['area'],
    }),
    ('customer', {
        'export': 'customer_manage.views.export_customers_to_io',
        'import': 'customer_manage.views.import_customers_from_io',
        'dependencies': ['area'],
    }),
    ('product', {
        'export': 'product.views.export_products_to_io',
        'import': 'product.views.import_products_from_io',
        'dependencies': [],
    }),
('customer_price', {   # 新增客户专属价，依赖客户和商品
        'export': 'customer_manage.views.export_customer_prices_to_io',
        'import': 'customer_manage.views.import_customer_prices_from_io',
        'dependencies': ['customer', 'product'],
    }),
    ('sort_rule', {                             # 新增
        'export': 'bill.views.export_sort_rules_to_io',
        'import': 'bill.views.import_sort_rules_from_io',
        'dependencies': ['product'],            # 依赖商品（标签）
    }),
    ('user', {
        'export': 'accounts.views.export_users_to_io',
        'import': 'accounts.views.import_users_from_io',
        'dependencies': [],
    }),
    ('order', {
        'export': 'bill.views.export_orders_to_io',
        'import': 'bill.views.import_orders_from_io',
        'dependencies': ['customer', 'product', 'area'],
    }),
])