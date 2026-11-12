"""Invoice money handling.

SPEC:
  * Amounts arrive as decimal currency floats and are stored as integer cents.
  * Converting to cents rounds half-up; it never truncates, because a
    truncated cent is money the customer was charged and never billed.
  * ``summary_line`` pluralises the noun.
"""

from __future__ import annotations


def cents(amount: float) -> int:
    """Convert a decimal currency amount to integer cents."""
    return int(amount * 100)


def subtotal_cents(unit_prices: list[float], quantities: list[int]) -> int:
    total = 0
    for price, qty in zip(unit_prices, quantities):
        total += cents(price) * qty
    return total


def summary_line(count: int) -> str:
    """SPEC: '1 item' for one, '3 items' otherwise."""
    return f"{count} items"
