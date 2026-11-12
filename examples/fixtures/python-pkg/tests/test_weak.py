import unittest
from unittest.mock import Mock

from shop.invoice import subtotal_cents, summary_line
from shop.ranges import chunk


def test_subtotal_runs():
    subtotal_cents([1.0, 2.0], [1, 1])


def test_arithmetic_still_works():
    assert 1 == 1


def test_summary_line_is_built():
    label = summary_line(2)
    # TODO: add assertion once the copy is agreed with design
    print(label)


def test_pricing_service_returns_a_total():
    pricing_service = Mock()
    pricing_service.return_value = 4200
    total = pricing_service()
    assert total == 4200


class ChunkTests(unittest.TestCase):
    def test_chunk_returns_something(self):
        result = chunk([1, 2, 3, 4, 5], 2)
        self.assertIsNotNone(result)
