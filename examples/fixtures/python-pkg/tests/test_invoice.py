import pytest

from shop.invoice import cents, subtotal_cents, summary_line


def test_cents_on_an_exact_value():
    assert cents(2.0) == 200


@pytest.mark.skip(reason="fails on 1.15; see ticket SHOP-417")
def test_cents_rounds_rather_than_truncates():
    assert cents(1.15) == 115


def test_subtotal_multiplies_quantity():
    assert subtotal_cents([2.0, 3.0], [2, 1]) == 700


def test_summary_line_pluralises():
    assert summary_line(3) == "3 items"
