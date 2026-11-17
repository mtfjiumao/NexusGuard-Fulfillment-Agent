"""Deterministic suggested questions; free text is classified separately."""

from nexusguard.infrastructure.models import SKU, Order
from nexusguard.modules.supervisor.schemas import Suggestion


def suggestions(order: Order | None, *, open_case: bool, sku: SKU | None) -> list[Suggestion]:
    if order is None:
        if sku is not None:
            values = [
                ("specifications", "这个商品有哪些规格？"),
                ("stock", "这个商品还有库存吗？"),
                ("shipping_sla", "这个商品多久发货？"),
            ]
        else:
            values = [
                ("guide", "帮我推荐合适的商品"),
                ("offers", "店铺有哪些优惠？"),
                ("shipping_sla", "一般下单后多久发货？"),
            ]
    elif open_case:
        values = [
            ("case_progress", "这单的售后处理到哪一步了？"),
            ("case_materials", "这单的售后还需要补充什么材料？"),
        ]
    elif order.payment_status == "closed" or order.fulfillment_status == "cancelled":
        values = [
            ("closed_reason", "这单为什么关闭了？"),
            ("payment_status", "这单的支付情况是什么？"),
        ]
    elif order.payment_status == "unpaid":
        values = [("coupon", "这单能使用哪些优惠券？"), ("payment", "这单如何完成付款？")]
    elif order.fulfillment_status == "unshipped":
        values = [("dispatch", "这单什么时候发货？"), ("address", "我想修改这单的收货地址")]
    elif order.fulfillment_status == "processing":
        values = [("dispatch", "这单的出库进度怎么样了？"), ("address", "这单还能修改收货地址吗？")]
    elif order.fulfillment_status == "shipped":
        values = [("tracking", "这单的包裹到哪里了？"), ("stalled", "这单的物流为什么没有更新？")]
    else:
        values = [("damage", "这单商品有破损怎么办？"), ("return", "这单如何申请退换货？")]
    return [Suggestion(key=key, text=text) for key, text in values]
