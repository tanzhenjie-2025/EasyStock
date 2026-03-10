# bill/utils.py
from datetime import date, datetime, timedelta
from django.db.models import Sum
from .models import Order, OrderItem, DailySalesSummary, Product


def generate_daily_summary(target_date: date, is_manual=False):
    """
    生成/重置指定日期的销售汇总（核心函数，极简版）
    :param target_date: 要汇总的日期（date类型）
    :param is_manual: 是否手动汇总
    :return: 汇总的商品数量
    """
    # 1. 先删除该日期已有汇总（实现“重置”）
    DailySalesSummary.objects.filter(summary_date=target_date).delete()

    # 2. 筛选指定日期的所有订单，汇总商品销量
    start_time = datetime.combine(target_date, datetime.min.time())
    end_time = datetime.combine(target_date, datetime.max.time())

    # 按商品分组，统计销量（仅统计订单明细）
    sales_data = OrderItem.objects.filter(
        order__create_time__range=(start_time, end_time)  # 仅筛选当天的订单
    ).values(
        'product_id', 'product__name', 'product__unit'
    ).annotate(
        total_quantity=Sum('quantity')  # 汇总该商品当日销量
    )

    # 3. 批量创建汇总记录（高效）
    summary_list = []
    for item in sales_data:
        product = Product.objects.get(id=item['product_id'])
        summary_list.append(
            DailySalesSummary(
                summary_date=target_date,
                product=product,
                sale_quantity=item['total_quantity'],
                is_manual=is_manual
            )
        )
    DailySalesSummary.objects.bulk_create(summary_list)

    return len(summary_list)


def auto_summary_yesterday():
    """自动汇总昨天的销售数据（用于定时任务）"""
    yesterday = date.today() - timedelta(days=1)
    return generate_daily_summary(target_date=yesterday, is_manual=False)