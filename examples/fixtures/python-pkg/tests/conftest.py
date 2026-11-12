import random

import pytest


@pytest.fixture(autouse=True)
def _deterministic_random():
    """Seed the process-global RNG before every test.

    This is what makes ``shop.sampling.pick`` -- which looks unseeded when you
    read it on its own -- fully deterministic under test.
    """
    random.seed(1234)
    yield
