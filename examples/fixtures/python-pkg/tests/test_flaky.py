import random
import time
from datetime import datetime

from shop.invoice import cents

SEEN: list[str] = []


def test_records_the_visitor():
    SEEN.append("alice")
    assert "alice" in SEEN


def test_starts_from_an_empty_visitor_list():
    assert len(SEEN) == 0


def test_sample_falls_inside_the_expected_band():
    entropy = random.SystemRandom()
    assert entropy.randrange(100) < 95


def test_epoch_renders_at_midnight():
    assert datetime.fromtimestamp(0).hour == 0


def test_totals_occasionally_disagree_under_load():
    time.sleep(0.01)
    assert cents(1.15) == 115
