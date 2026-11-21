from decimal import Decimal


def stack_outlay(qty: int, price: int, buyer_tax: float) -> int:
    subtotal = qty * price
    return subtotal + int(subtotal * Decimal(str(buyer_tax)))


def profit_per_unit(
    sell: float,
    buy: int,
    *,
    seller_tax: float,
    buyer_tax: float,
    travel_cost: int = 0,
    qty: int = 1,
) -> float:
    return sell * (1 - seller_tax) - buy * (1 + buyer_tax) - travel_cost / qty


def sellable_qty(qty: int, velocity: float, days: float) -> int:
    return min(qty, int(velocity * days))


def wait_days(rival_stock: int, qty: int, velocity: float) -> float | None:
    return (rival_stock + qty) / velocity if velocity else None
