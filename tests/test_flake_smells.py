#!/usr/bin/env python3
"""Tests for flake_smells.py.

The structure of this file follows the structure of the risk the detector
carries. Every rule gets a pair:

    test_<rule>_fires          -- the true positive
    test_<rule>_stays_silent   -- its near-miss twin

The silent half matters more. A static check that fires wrongly more than
about one time in ten gets switched off, and takes its true positives with
it, so a false positive here is a worse bug than a miss. Where a twin is
missing, the rule is effectively untested however many positives it has.

Sources are analysed in memory rather than written to disk, except where the
test is specifically about file selection, I/O or the CLI.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import flake_smells as fs  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def scan(source, path="tests/test_sample.py", use_ast=True):
    lang = fs.detect_language(path)
    return fs.analyse_file(path, source.splitlines(keepends=True), lang,
                           use_ast=use_ast)


def rules(source, path="tests/test_sample.py", use_ast=True):
    """The set of rule names that fired. Set, not list, because most
    assertions here are about whether a rule fired at all."""
    return {f["rule"] for f in scan(source, path, use_ast).findings}


def _all_findings(result):
    out = []
    for entry in result.get("files", []):
        out.extend(entry.get("findings", []))
    return out


class SmellCase(unittest.TestCase):
    def assertFires(self, rule, source, path="tests/test_sample.py"):
        got = rules(source, path)
        self.assertIn(rule, got, f"expected {rule}, got {sorted(got)}")

    def assertSilent(self, rule, source, path="tests/test_sample.py"):
        got = rules(source, path)
        self.assertNotIn(rule, got, f"{rule} fired on a clean sample: {sorted(got)}")


# --------------------------------------------------------------------------
# the clean file -- the single most important test here
# --------------------------------------------------------------------------

CLEAN_PY = '''
import unittest
from decimal import Decimal

from myapp.billing import total


class TotalTest(unittest.TestCase):
    def setUp(self):
        self.items = [{"price": Decimal("1.50"), "qty": 2}]

    def test_total_multiplies_price_by_quantity(self):
        self.assertEqual(total(self.items), Decimal("3.00"))

    def test_empty_basket_totals_zero(self):
        self.assertEqual(total([]), Decimal("0.00"))

    def test_rejects_negative_quantity(self):
        with self.assertRaises(ValueError):
            total([{"price": Decimal("1.00"), "qty": -1}])
'''

CLEAN_JS = '''
import { total } from "../src/billing";

describe("total", () => {
  let items;

  beforeEach(() => {
    items = [{ price: 150, qty: 2 }];
  });

  it("multiplies price by quantity", () => {
    expect(total(items)).toEqual(300);
  });

  it("totals an empty basket to zero", () => {
    expect(total([])).toEqual(0);
  });
});
'''


class TestCleanFilesProduceNothing(SmellCase):
    """If a well-written test file produces even one finding, the gate is
    worthless -- every PR would carry noise and the check would be disabled."""

    def test_clean_python_file_has_zero_findings(self):
        found = scan(CLEAN_PY).findings
        self.assertEqual(found, [], f"false positives: {[f['rule'] for f in found]}")

    def test_clean_javascript_file_has_zero_findings(self):
        found = scan(CLEAN_JS, path="tests/billing.test.js").findings
        self.assertEqual(found, [], f"false positives: {[f['rule'] for f in found]}")


# --------------------------------------------------------------------------
# scrubbing -- the shared false-positive guard
# --------------------------------------------------------------------------

class TestScrubbing(SmellCase):
    def test_commented_out_code_is_invisible(self):
        src = (
            "import random\n"
            "def test_thing():\n"
            "    # x = random.random()\n"
            "    # time.sleep(5)\n"
            "    assert compute() == 3\n"
        )
        self.assertEqual(rules(src), set())

    def test_block_comment_is_invisible(self):
        src = (
            "/* const x = Math.random();\n"
            "   await page.waitForTimeout(500); */\n"
            "it('works', () => { expect(add(1, 2)).toBe(3); });\n"
        )
        self.assertEqual(rules(src, path="tests/a.test.js"), set())

    def test_python_docstring_is_invisible(self):
        src = (
            "def test_thing():\n"
            '    """Do not call random.random() here, or time.sleep(1)."""\n'
            "    assert compute() == 3\n"
        )
        self.assertEqual(rules(src), set())

    def test_string_literal_mentioning_a_keyword_is_invisible(self):
        src = (
            "def test_thing():\n"
            "    msg = 'never use time.sleep or random.random in tests'\n"
            "    assert render() == msg\n"
        )
        self.assertEqual(rules(src), set())

    def test_scrub_still_reports_real_code_on_the_same_line_as_a_comment(self):
        src = (
            "import time\n"
            "def test_thing():\n"
            "    time.sleep(1)  # waiting for the worker\n"
            "    assert compute() == 3\n"
        )
        self.assertFires("sleep", src)


# --------------------------------------------------------------------------
# A. flake smells
# --------------------------------------------------------------------------

class TestUnseededRandomness(SmellCase):
    def test_fires_on_unseeded_python_random(self):
        self.assertFires("unseeded-random",
                         "import random\n"
                         "def test_pick():\n"
                         "    n = random.randint(1, 10)\n"
                         "    assert pick(n) == n\n")

    def test_fires_on_math_random_and_uuid4(self):
        self.assertFires("unseeded-random",
                         "it('x', () => { const n = Math.random();"
                         " expect(f(n)).toBe(n); });\n",
                         path="tests/a.test.js")
        self.assertFires("unseeded-random",
                         "import uuid\n"
                         "def test_id():\n"
                         "    i = uuid.uuid4()\n"
                         "    assert store(i) == i\n")

    def test_fires_on_unseeded_faker(self):
        self.assertFires("unseeded-random",
                         "def test_name():\n"
                         "    name = faker.name()\n"
                         "    assert save(name) == name\n")

    def test_stays_silent_when_the_file_seeds_the_generator(self):
        self.assertSilent("unseeded-random",
                          "import random\n"
                          "random.seed(0)\n"
                          "def test_pick():\n"
                          "    n = random.randint(1, 10)\n"
                          "    assert pick(n) == n\n")

    def test_stays_silent_when_faker_is_seeded(self):
        self.assertSilent("unseeded-random",
                          "Faker.seed(0)\n"
                          "def test_name():\n"
                          "    name = faker.name()\n"
                          "    assert save(name) == name\n")

    def test_stays_silent_on_a_seeded_java_random(self):
        self.assertSilent("unseeded-random",
                          "@Test\n"
                          "public void picks() {\n"
                          "    Random r = new Random(42);\n"
                          "    assertEquals(3, pick(r));\n"
                          "}\n",
                          path="src/PickTest.java")

    def test_fires_on_an_unseeded_java_random(self):
        self.assertFires("unseeded-random",
                         "@Test\n"
                         "public void picks() {\n"
                         "    Random r = new Random();\n"
                         "    assertEquals(3, pick(r));\n"
                         "}\n",
                         path="src/PickTest.java")


class TestSeedImmuneRandomness(SmellCase):
    """Sources random.seed() cannot reach. The twin that matters is the
    seeded file: the seed silences random.random() correctly and must NOT
    silence these, because it genuinely does not affect them."""

    def test_stays_silent_on_an_ordinary_seeded_draw(self):
        # The near-miss twin, written first: a seeded random.random() is
        # reproducible and neither rule may fire on it.
        got = rules("import random\n"
                    "random.seed(0)\n\n"
                    "def test_pick():\n"
                    "    assert random.random() < 1\n")
        self.assertNotIn("seed-immune-random", got)
        self.assertNotIn("unseeded-random", got)

    def test_stays_silent_on_a_seeded_randrange(self):
        self.assertSilent("seed-immune-random",
                          "import random\n"
                          "random.seed(0)\n\n"
                          "def test_pick():\n"
                          "    assert random.randrange(100) < 95\n")

    def test_fires_on_system_random_even_when_the_file_is_seeded(self):
        # The point of the whole rule: SystemRandom reads the OS entropy
        # pool, so the seed above it is decoration.
        self.assertFires("seed-immune-random",
                         "import random\n"
                         "random.seed(0)\n\n"
                         "def test_sample():\n"
                         "    entropy = random.SystemRandom()\n"
                         "    assert entropy.randrange(100) < 95\n")

    def test_fires_on_a_bare_system_random_import(self):
        self.assertFires("seed-immune-random",
                         "from random import SystemRandom\n\n"
                         "def test_sample():\n"
                         "    assert SystemRandom().randrange(100) < 95\n")

    def test_fires_on_secrets_and_urandom(self):
        for call in ("secrets.randbelow(100)", "secrets.token_hex(8)",
                     "os.urandom(4)"):
            with self.subTest(call=call):
                self.assertFires("seed-immune-random",
                                 "import os\nimport secrets\nimport random\n"
                                 "random.seed(0)\n\n"
                                 "def test_token():\n"
                                 "    value = %s\n"
                                 "    assert value is not None\n" % call)

    def test_stays_silent_on_an_ordinary_name_containing_secrets(self):
        self.assertSilent("seed-immune-random",
                          "def test_env():\n"
                          "    assert secrets_path.exists()\n")

    def test_severity_is_block(self):
        found = scan("import random\n\n"
                     "def test_sample():\n"
                     "    assert random.SystemRandom().random() < 1\n").findings
        self.assertEqual([f["severity"] for f in found
                          if f["rule"] == "seed-immune-random"], ["block"])


class TestNaiveLocalTime(SmellCase):
    """Timestamp conversion that silently uses the machine's timezone. The
    aware twin is the test that matters: it is deterministic."""

    def test_stays_silent_on_the_timezone_aware_twin(self):
        self.assertSilent("naive-local-time",
                          "from datetime import datetime, timezone\n\n"
                          "def test_epoch():\n"
                          "    assert datetime.fromtimestamp(0, tz=timezone.utc).hour == 0\n")

    def test_stays_silent_on_a_positional_timezone_argument(self):
        self.assertSilent("naive-local-time",
                          "from datetime import datetime, timezone\n\n"
                          "def test_epoch():\n"
                          "    assert datetime.fromtimestamp(0, timezone.utc).hour == 0\n")

    def test_stays_silent_on_utcfromtimestamp(self):
        # Deprecated, but deterministic: it converts to UTC, not to local.
        self.assertSilent("naive-local-time",
                          "from datetime import datetime\n\n"
                          "def test_epoch():\n"
                          "    assert datetime.utcfromtimestamp(0).hour == 0\n")

    def test_stays_silent_on_astimezone_with_a_zone(self):
        self.assertSilent("naive-local-time",
                          "from zoneinfo import ZoneInfo\n\n"
                          "def test_epoch():\n"
                          "    got = stamp.astimezone(ZoneInfo('UTC'))\n"
                          "    assert got.hour == 0\n")

    def test_fires_on_naive_fromtimestamp_in_an_assertion(self):
        self.assertFires("naive-local-time",
                         "from datetime import datetime\n\n"
                         "def test_epoch():\n"
                         "    assert datetime.fromtimestamp(0).hour == 0\n")

    def test_fires_on_local_time_and_bare_astimezone(self):
        for call in ("time.localtime(0).tm_year", "stamp.astimezone().hour"):
            with self.subTest(call=call):
                self.assertFires("naive-local-time",
                                 "import time\n\n"
                                 "def test_epoch():\n"
                                 "    assert %s == 0\n" % call)

    def test_blocks_inside_an_assertion_and_only_warns_outside_one(self):
        # Same tiering as real-clock: an assertion that depends on the zone
        # fails on every run in the wrong zone, so it blocks.
        asserted = [f for f in scan(
            "from datetime import datetime\n\n"
            "def test_epoch():\n"
            "    assert datetime.fromtimestamp(0).hour == 0\n").findings
            if f["rule"] == "naive-local-time"]
        self.assertEqual([f["severity"] for f in asserted], ["block"])

        assigned = [f for f in scan(
            "from datetime import datetime\n\n"
            "def test_epoch():\n"
            "    got = datetime.fromtimestamp(0)\n"
            "    assert got.hour == 0\n").findings
            if f["rule"] == "naive-local-time"]
        self.assertEqual([f["severity"] for f in assigned], ["warn"])

    def test_stays_silent_when_the_conversion_is_never_asserted_on(self):
        self.assertSilent("naive-local-time",
                          "from datetime import datetime\n\n"
                          "def test_logs():\n"
                          "    started = datetime.fromtimestamp(0)\n"
                          "    run()\n"
                          "    assert finished() is True\n")


class TestRealClock(SmellCase):
    def test_fires_when_the_clock_is_read_inside_an_assertion(self):
        self.assertFires("real-clock",
                         "import datetime\n"
                         "def test_stamp():\n"
                         "    assert record().created == datetime.datetime.now()\n")

    def test_fires_when_a_clock_value_is_asserted_on_later(self):
        self.assertFires("real-clock",
                         "import datetime\n"
                         "def test_stamp():\n"
                         "    expected = datetime.datetime.now()\n"
                         "    assert record().created == expected\n")

    def test_stays_silent_on_a_fixed_date(self):
        self.assertSilent("real-clock",
                          "import datetime\n"
                          "def test_stamp():\n"
                          "    expected = datetime.datetime(2020, 1, 1)\n"
                          "    assert record(expected).created == expected\n")

    def test_stays_silent_when_the_clock_result_is_never_asserted_on(self):
        # Timing a block for a log line is not a flaky assertion.
        self.assertSilent("real-clock",
                          "import time\n"
                          "def test_runs():\n"
                          "    started = time.time()\n"
                          "    run()\n"
                          "    assert finished() is True\n")


class TestSleeps(SmellCase):
    def test_fires_on_python_sleep(self):
        self.assertFires("sleep",
                         "import time\n"
                         "def test_worker():\n"
                         "    time.sleep(2)\n"
                         "    assert done() is True\n")

    def test_fires_on_playwright_wait_for_timeout(self):
        self.assertFires("sleep",
                         "it('loads', async () => {\n"
                         "  await page.waitForTimeout(500);\n"
                         "  expect(await title()).toBe('Home');\n"
                         "});\n",
                         path="tests/home.spec.ts")

    def test_fires_on_thread_sleep(self):
        self.assertFires("sleep",
                         "@Test\n"
                         "public void waits() throws Exception {\n"
                         "    Thread.sleep(1000);\n"
                         "    assertEquals(1, queue.size());\n"
                         "}\n",
                         path="src/QueueTest.java")

    def test_stays_silent_on_a_condition_wait(self):
        self.assertSilent("sleep",
                          "it('loads', async () => {\n"
                          "  await page.waitForSelector('#done');\n"
                          "  expect(await title()).toBe('Home');\n"
                          "});\n",
                          path="tests/home.spec.ts")


class TestRealNetwork(SmellCase):
    def test_fires_on_requests_get(self):
        self.assertFires("real-network",
                         "import requests\n"
                         "def test_api():\n"
                         "    r = requests.get(url)\n"
                         "    assert r.status_code == 200\n")

    def test_fires_on_a_remote_url_literal(self):
        self.assertFires("real-network",
                         "def test_api():\n"
                         "    r = client.open('https://api.stripe.com/v1/charges')\n"
                         "    assert r.status == 200\n")

    def test_fires_on_global_fetch(self):
        self.assertFires("real-network",
                         "it('loads', async () => {\n"
                         "  const r = await fetch(url);\n"
                         "  expect(r.status).toBe(200);\n"
                         "});\n",
                         path="tests/api.test.js")

    def test_stays_silent_on_localhost(self):
        self.assertSilent("real-network",
                          "def test_api():\n"
                          "    r = client.open('http://127.0.0.1:8000/charges')\n"
                          "    assert r.status == 200\n")

    def test_stays_silent_on_reserved_documentation_domains(self):
        # example.com cannot resolve to a real dependency; it is the standard
        # placeholder in fixtures and firing on it would be pure noise.
        self.assertSilent("real-network",
                          "def test_api():\n"
                          "    r = client.open('https://example.com/charges')\n"
                          "    assert r.status == 200\n")

    def test_stays_silent_on_an_xml_namespace(self):
        self.assertSilent("real-network",
                          "def test_xml():\n"
                          "    doc = render(xmlns='http://www.w3.org/2000/svg')\n"
                          "    assert doc.tag == 'svg'\n")

    def test_stays_silent_on_a_url_embedded_in_fixture_data(self):
        # The string is a .env file's contents, not a request target.
        self.assertSilent("real-network",
                          "def test_config():\n"
                          "    f = parse('API_URL=https://api.acme.com/v2')\n"
                          "    assert f.host == 'api.acme.com'\n")

    def test_stays_silent_on_a_method_named_fetch(self):
        self.assertSilent("real-network",
                          "def test_cache():\n"
                          "    assert cache.fetch('k') == 'v'\n")


class TestFilesystemOrder(SmellCase):
    def test_fires_on_unsorted_listdir(self):
        self.assertFires("fs-order",
                         "import os\n"
                         "def test_files():\n"
                         "    names = os.listdir(tmp)\n"
                         "    assert names[0] == 'a.txt'\n")

    def test_fires_on_readdir_sync(self):
        self.assertFires("fs-order",
                         "it('lists', () => {\n"
                         "  const names = fs.readdirSync(dir);\n"
                         "  expect(names[0]).toBe('a.txt');\n"
                         "});\n",
                         path="tests/files.test.js")

    def test_stays_silent_when_the_listing_is_sorted(self):
        self.assertSilent("fs-order",
                          "import os\n"
                          "def test_files():\n"
                          "    names = sorted(os.listdir(tmp))\n"
                          "    assert names[0] == 'a.txt'\n")


class TestIterationOrder(SmellCase):
    def test_fires_on_dict_keys_compared_to_an_ordered_literal(self):
        self.assertFires("iteration-order",
                         "def test_keys():\n"
                         "    assert list(d.keys()) == ['a', 'b']\n")

    def test_fires_on_object_keys_in_javascript(self):
        self.assertFires("iteration-order",
                         "it('keys', () => {\n"
                         "  expect(Object.keys(d)).toEqual(['a', 'b']);\n"
                         "});\n",
                         path="tests/keys.test.js")

    def test_stays_silent_when_sorted_first(self):
        self.assertSilent("iteration-order",
                          "def test_keys():\n"
                          "    assert sorted(d.keys()) == ['a', 'b']\n")

    def test_stays_silent_when_the_bracket_is_a_subscript(self):
        # `s["by_rule"].values()` is dictionary access, not an expected list.
        self.assertSilent("iteration-order",
                          "def test_counts():\n"
                          "    assert sum(s['by_rule'].values()) == 3\n")

    def test_stays_silent_on_an_ordinary_list_assertion(self):
        self.assertSilent("iteration-order",
                          "def test_rows():\n"
                          "    assert rows() == ['a', 'b']\n")


class TestFloatEquality(SmellCase):
    def test_fires_on_exact_float_comparison(self):
        self.assertFires("float-equality",
                         "def test_sum():\n"
                         "    self.assertEqual(add(0.1, 0.2), 0.3)\n")

    def test_fires_on_jest_to_be_with_a_float(self):
        self.assertFires("float-equality",
                         "it('adds', () => { expect(add(0.1, 0.2)).toBe(0.3); });\n",
                         path="tests/add.test.js")

    def test_stays_silent_on_an_approximate_comparison(self):
        self.assertSilent("float-equality",
                          "def test_sum():\n"
                          "    self.assertAlmostEqual(add(0.1, 0.2), 0.3, places=7)\n")

    def test_stays_silent_on_to_be_close_to(self):
        self.assertSilent("float-equality",
                          "it('adds', () => "
                          "{ expect(add(0.1, 0.2)).toBeCloseTo(0.3); });\n",
                          path="tests/add.test.js")

    def test_stays_silent_on_pytest_approx(self):
        # `assert x == pytest.approx(0.3)` is an exact-equality shape with an
        # approximate comparator inside it; only the approx guard saves it.
        self.assertSilent("float-equality",
                          "def test_sum():\n"
                          "    assert add(0.1, 0.2) == pytest.approx(0.3)\n")

    def test_stays_silent_on_junit_assert_equals_with_a_delta(self):
        self.assertSilent("float-equality",
                          "@Test\n"
                          "public void adds() {\n"
                          "    assertEquals(0.3, add(0.1, 0.2), 0.0001);\n"
                          "}\n",
                          path="src/AddTest.java")

    def test_fires_on_junit_assert_equals_without_a_delta(self):
        self.assertFires("float-equality",
                         "@Test\n"
                         "public void adds() {\n"
                         "    assertEquals(0.3, add(0.1, 0.2));\n"
                         "}\n",
                         path="src/AddTest.java")

    def test_stays_silent_on_integer_comparison(self):
        self.assertSilent("float-equality",
                          "def test_status():\n"
                          "    self.assertEqual(resp.status_code, 200)\n")

    def test_stays_silent_on_decimals_binary_floats_hold_exactly(self):
        # 0.0, 0.5, 0.75, 75.0 and 10.50 all have exact binary
        # representations, so == against them is deterministic. Firing here
        # was the largest false-positive source when the detector was first
        # run over a real suite.
        for literal in ("0.0", "1.0", "0.5", "0.75", "75.0", "10.50", "40.0"):
            with self.subTest(literal=literal):
                self.assertSilent("float-equality",
                                  "def test_pct():\n"
                                  "    self.assertEqual(pct(), %s)\n" % literal)

    def test_fires_on_decimals_binary_floats_cannot_hold(self):
        for literal in ("0.3", "0.1", "83.33", "1.1"):
            with self.subTest(literal=literal):
                self.assertFires("float-equality",
                                 "def test_pct():\n"
                                 "    self.assertEqual(pct(), %s)\n" % literal)

    def test_stays_silent_when_the_float_is_an_argument_not_an_operand(self):
        # The twin that matters: both sides of `==` are ints, so the
        # comparison is exact. The 1.15 is an input to the code under test.
        for src in ("def test_cents():\n    assert cents(1.15) == 115\n",
                    "def test_cents():\n    self.assertEqual(cents(1.15), 115)\n",
                    "def test_cents():\n    assert round(1.15, 2) == len(xs)\n"):
            with self.subTest(src=src):
                self.assertSilent("float-equality", src)

    def test_stays_silent_on_a_float_argument_in_a_javascript_matcher(self):
        self.assertSilent("float-equality",
                          "it('rounds', () => { expect(cents(1.15)).toBe(115); });\n",
                          path="tests/cents.test.js")

    def test_fires_when_the_inexact_float_is_the_expected_side(self):
        for src in ("def test_sum():\n    assert total == 0.3\n",
                    "def test_sum():\n    assert f(x) == 1.15\n",
                    "def test_sum():\n    assert cents(2.0) == 1.15\n"):
            with self.subTest(src=src):
                self.assertFires("float-equality", src)

    def test_fires_when_the_inexact_float_is_the_actual_side(self):
        self.assertFires("float-equality",
                         "def test_sum():\n    assert 0.1 + 0.2 == expected\n")

    def test_operand_helper_ignores_nested_call_arguments(self):
        self.assertFalse(fs._compares_inexact_float("assert cents(1.15) == 115"))
        self.assertTrue(fs._compares_inexact_float("assert total == 0.3"))
        self.assertTrue(fs._compares_inexact_float("self.assertEqual(pct(), 0.3)"))

    def test_exact_in_binary_helper(self):
        self.assertTrue(fs._exact_in_binary("0.75"))
        self.assertTrue(fs._exact_in_binary("10.50"))
        self.assertFalse(fs._exact_in_binary("0.3"))

    def test_stays_silent_on_a_version_string(self):
        # "1.2.3" is a string, not a float; the scrubber hides it.
        self.assertSilent("float-equality",
                          "def test_version():\n"
                          "    self.assertEqual(version(), '1.2')\n")


class TestLocaleAndTimezone(SmellCase):
    def test_fires_on_strftime_without_a_timezone(self):
        self.assertFires("locale-timezone",
                         "def test_label():\n"
                         "    assert label(d) == d.strftime('%d %B %Y')\n")

    def test_fires_on_to_locale_string(self):
        self.assertFires("locale-timezone",
                         "it('formats', () => "
                         "{ expect(label(n)).toBe(n.toLocaleString()); });\n",
                         path="tests/fmt.test.js")

    def test_stays_silent_when_the_timezone_is_explicit(self):
        self.assertSilent("locale-timezone",
                          "def test_label():\n"
                          "    assert label(d) == d.strftime('%d %B %Y', tz=UTC)\n")

    def test_stays_silent_when_a_locale_is_passed(self):
        self.assertSilent("locale-timezone",
                          "@Test\n"
                          "public void formats() {\n"
                          "    new SimpleDateFormat(pattern, Locale.UK);\n"
                          "    assertEquals(\"x\", label(d));\n"
                          "}\n",
                          path="src/FmtTest.java")


class TestSharedMutableState(SmellCase):
    def test_fires_on_a_module_level_list_written_by_a_test(self):
        self.assertFires("shared-mutable-state",
                         "SEEN = []\n"
                         "def test_records():\n"
                         "    SEEN.append(1)\n"
                         "    assert len(SEEN) == 1\n")

    def test_fires_on_a_mutable_static_field(self):
        self.assertFires("shared-mutable-state",
                         "static List<String> SEEN = new ArrayList<>();\n"
                         "@Test\n"
                         "public void records() {\n"
                         "    SEEN.add(\"x\");\n"
                         "    assertEquals(1, SEEN.size());\n"
                         "}\n",
                         path="src/SeenTest.java")

    def test_stays_silent_when_the_value_is_rebuilt_per_test(self):
        self.assertSilent("shared-mutable-state",
                          "def test_records():\n"
                          "    seen = []\n"
                          "    seen.append(1)\n"
                          "    assert len(seen) == 1\n")

    def test_stays_silent_on_a_module_level_constant_that_is_only_read(self):
        self.assertSilent("shared-mutable-state",
                          "ROWS = []\n"
                          "def test_rows():\n"
                          "    assert load(ROWS) == []\n")

    def test_stays_silent_on_a_static_final_field(self):
        self.assertSilent("shared-mutable-state",
                          "static final List<String> SEEN = new ArrayList<>();\n"
                          "@Test\n"
                          "public void reads() {\n"
                          "    assertEquals(0, SEEN.size());\n"
                          "}\n",
                          path="src/SeenTest.java")


# --------------------------------------------------------------------------
# B. meaningless-test smells
# --------------------------------------------------------------------------

class TestEmptyAndAssertionless(SmellCase):
    def test_fires_on_an_empty_body(self):
        self.assertFires("empty-test", "def test_nothing():\n    pass\n")

    def test_fires_on_a_docstring_only_body(self):
        self.assertFires("empty-test",
                         'def test_nothing():\n    """Later."""\n')

    def test_fires_on_a_test_with_no_assertion(self):
        self.assertFires("no-assertion",
                         "def test_runs():\n"
                         "    compute(1, 2)\n"
                         "    render()\n")

    def test_fires_on_an_assertionless_javascript_test(self):
        self.assertFires("no-assertion",
                         "it('runs', () => {\n"
                         "  compute(1, 2);\n"
                         "});\n",
                         path="tests/a.test.js")

    def test_stays_silent_when_an_assertion_is_present(self):
        self.assertSilent("no-assertion",
                          "def test_runs():\n"
                          "    assert compute(1, 2) == 3\n")

    def test_stays_silent_on_a_non_test_helper_with_no_assertion(self):
        # Helpers legitimately contain no assertions. Firing here would put a
        # finding on every fixture in the file.
        self.assertEqual(
            rules("def build_user():\n"
                  "    return {'name': 'a'}\n"
                  "def test_user():\n"
                  "    assert build_user()['name'] == 'a'\n"),
            set())

    def test_stays_silent_when_a_context_manager_carries_the_assertion(self):
        self.assertSilent("no-assertion",
                          "def test_raises():\n"
                          "    with pytest.raises(ValueError):\n"
                          "        parse('x')\n")


class TestTautologies(SmellCase):
    def test_fires_on_assert_true_true(self):
        self.assertFires("tautological-assertion",
                         "def test_x():\n    self.assertTrue(True)\n")

    def test_fires_on_expect_one_to_be_one(self):
        self.assertFires("tautological-assertion",
                         "it('x', () => { expect(1).toBe(1); });\n",
                         path="tests/a.test.js")

    def test_fires_when_both_sides_are_the_same_expression(self):
        self.assertFires("tautological-assertion",
                         "def test_x():\n    assert user.name == user.name\n")

    def test_fires_on_assert_equal_with_identical_arguments(self):
        self.assertFires("tautological-assertion",
                         "def test_x():\n    self.assertEqual(total, total)\n")

    def test_stays_silent_on_a_real_comparison(self):
        self.assertSilent("tautological-assertion",
                          "def test_x():\n    self.assertEqual(total(), 42)\n")

    def test_stays_silent_on_assert_true_of_a_computed_value(self):
        self.assertSilent("tautological-assertion",
                          "def test_x():\n    self.assertTrue(is_valid(user))\n")

    def test_stays_silent_when_the_two_sides_merely_look_alike(self):
        self.assertSilent("tautological-assertion",
                          "def test_x():\n    assert user.name == other.name\n")


class TestTodoScaffolds(SmellCase):
    def test_fires_on_a_todo_add_assertion_comment(self):
        self.assertFires("todo-assertion",
                         "def test_future():\n"
                         "    result = compute(1, 2)\n"
                         "    # TODO: add assertion\n")

    def test_fires_on_a_not_implemented_stub(self):
        self.assertFires("todo-assertion",
                         "def test_future():\n"
                         "    raise NotImplementedError\n")

    def test_todo_is_its_own_category_not_merged_into_no_assertion(self):
        # Research finding: reviewers reject TODO scaffolds specifically, even
        # when they raise coverage. Reporting them as a generic missing
        # assertion loses the reason they get rejected.
        got = rules("def test_future():\n"
                    "    result = compute(1, 2)\n"
                    "    # TODO: add assertion\n")
        self.assertEqual(got, {"todo-assertion"})

    def test_stays_silent_on_a_lowercase_identifier_called_todo_assertion(self):
        # TODO is shouted by convention. Matching case-insensitively made the
        # rule fire on the identifier `todo-assertion` in ordinary code.
        self.assertSilent("todo-assertion",
                          "def test_rule_names():\n"
                          "    assert RULES['todo-assertion']['severity'] "
                          "== 'block'\n")

    def test_stays_silent_on_an_unrelated_todo(self):
        self.assertSilent("todo-assertion",
                          "def test_total():\n"
                          "    # TODO: refactor this helper one day\n"
                          "    assert total() == 3\n")

    def test_stays_silent_on_a_todo_in_a_neighbouring_test(self):
        got = [f for f in scan(
            "def test_one():\n"
            "    assert total() == 3\n"
            "\n"
            "def test_two():\n"
            "    result = compute()\n"
            "    # TODO: add assertion\n").findings
            if f["rule"] == "todo-assertion"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["line"], 6)


class TestMockReturnTautology(SmellCase):
    def test_fires_when_the_only_assertion_echoes_the_configured_return(self):
        self.assertFires("mock-return-tautology",
                         "def test_fetch():\n"
                         "    svc = Mock()\n"
                         "    svc.load.return_value = 42\n"
                         "    assert svc.load() == 42\n")

    def test_fires_on_a_jest_mock_resolved_value(self):
        self.assertFires("mock-return-tautology",
                         "it('loads', async () => {\n"
                         "  api.load.mockResolvedValue(42);\n"
                         "  expect(await api.load()).toBe(42);\n"
                         "});\n",
                         path="tests/api.test.js")

    def test_stays_silent_when_the_assertion_checks_a_transformation(self):
        self.assertSilent("mock-return-tautology",
                          "def test_doubled():\n"
                          "    svc = Mock()\n"
                          "    svc.load.return_value = 42\n"
                          "    assert double(svc.load()) == 84\n")

    def test_stays_silent_when_other_real_assertions_exist(self):
        self.assertSilent("mock-return-tautology",
                          "def test_fetch():\n"
                          "    svc = Mock()\n"
                          "    svc.load.return_value = 42\n"
                          "    assert svc.load() == 42\n"
                          "    assert svc.load.call_count == 1\n")


class TestWeakOracle(SmellCase):
    def test_fires_when_not_none_is_the_only_assertion(self):
        self.assertFires("weak-oracle",
                         "def test_user():\n"
                         "    self.assertIsNotNone(load_user(1))\n")

    def test_fires_on_to_be_defined_as_the_only_assertion(self):
        self.assertFires("weak-oracle",
                         "it('loads', () => { expect(loadUser(1)).toBeDefined(); });\n",
                         path="tests/user.test.js")

    def test_stays_silent_when_a_real_assertion_accompanies_it(self):
        self.assertSilent("weak-oracle",
                          "def test_user():\n"
                          "    user = load_user(1)\n"
                          "    self.assertIsNotNone(user)\n"
                          "    self.assertEqual(user.name, 'Ada')\n")

    def test_stays_silent_on_a_specific_assertion(self):
        self.assertSilent("weak-oracle",
                          "def test_user():\n"
                          "    self.assertEqual(load_user(1).name, 'Ada')\n")


class TestSkippedAndFocused(SmellCase):
    def test_fires_on_a_skipped_python_test(self):
        self.assertFires("skipped-test",
                         "@unittest.skip('later')\n"
                         "def test_x():\n"
                         "    assert total() == 3\n")

    def test_fires_on_xit(self):
        self.assertFires("skipped-test",
                         "xit('works', () => { expect(add(1, 2)).toBe(3); });\n",
                         path="tests/a.test.js")

    def test_fires_on_a_java_ignore(self):
        self.assertFires("skipped-test",
                         "@Ignore\n"
                         "@Test\n"
                         "public void adds() {\n"
                         "    assertEquals(3, add(1, 2));\n"
                         "}\n",
                         path="src/AddTest.java")

    def test_focused_test_is_blocking_because_it_disables_the_rest(self):
        found = [f for f in scan("it.only('works', () => "
                                 "{ expect(add(1, 2)).toBe(3); });\n",
                                 path="tests/a.test.js").findings
                 if f["rule"] == "focused-test"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "block")

    def test_fires_on_fdescribe(self):
        self.assertFires("focused-test",
                         "fdescribe('suite', () => {\n"
                         "  it('works', () => { expect(add(1, 2)).toBe(3); });\n"
                         "});\n",
                         path="tests/a.test.js")

    def test_stays_silent_when_only_appears_as_an_ordinary_word(self):
        self.assertSilent("focused-test",
                          "it('handles only one item', () => "
                          "{ expect(add(1, 2)).toBe(3); });\n",
                          path="tests/a.test.js")

    def test_stays_silent_on_a_conditional_skip(self):
        # skipIf's condition is usually "this platform cannot run it", which
        # is a guard, not a disabled test.
        self.assertSilent("skipped-test",
                          "@unittest.skipIf(os.geteuid() == 0, 'root')\n"
                          "def test_x():\n"
                          "    assert total() == 3\n")

    def test_stays_silent_when_skip_appears_in_a_description(self):
        self.assertSilent("skipped-test",
                          "it('lets the user skip onboarding', () => "
                          "{ expect(skipped()).toBe(true); });\n",
                          path="tests/a.test.js")


# --------------------------------------------------------------------------
# language coverage
# --------------------------------------------------------------------------

class TestLanguages(SmellCase):
    def test_go(self):
        self.assertFires("sleep",
                         "func TestAdd(t *testing.T) {\n"
                         "\ttime.Sleep(2 * time.Second)\n"
                         "\tif add(1, 2) != 3 { t.Errorf(\"bad\") }\n"
                         "}\n",
                         path="pkg/add_test.go")

    def test_go_assertionless_test_is_caught(self):
        self.assertFires("no-assertion",
                         "func TestAdd(t *testing.T) {\n"
                         "\tadd(1, 2)\n"
                         "}\n",
                         path="pkg/add_test.go")

    def test_ruby(self):
        self.assertFires("no-assertion",
                         "def test_adds\n"
                         "  add(1, 2)\n"
                         "end\n",
                         path="spec/add_spec.rb")

    def test_ruby_with_an_assertion_is_silent(self):
        self.assertSilent("no-assertion",
                          "def test_adds\n"
                          "  assert_equal 3, add(1, 2)\n"
                          "end\n",
                          path="spec/add_spec.rb")

    def test_php(self):
        self.assertFires("no-assertion",
                         "<?php\n"
                         "class AddTest extends TestCase {\n"
                         "    public function testAdds() {\n"
                         "        add(1, 2);\n"
                         "    }\n"
                         "}\n",
                         path="tests/AddTest.php")

    def test_csharp_with_brace_on_the_next_line(self):
        self.assertFires("no-assertion",
                         "[Fact]\n"
                         "public void Adds()\n"
                         "{\n"
                         "    Add(1, 2);\n"
                         "}\n",
                         path="tests/AddTests.cs")

    def test_dart(self):
        self.assertFires("sleep",
                         "void main() {\n"
                         "  test('adds', () async {\n"
                         "    await Future.delayed(Duration(seconds: 1));\n"
                         "    expect(add(1, 2), 3);\n"
                         "  });\n"
                         "}\n",
                         path="test/add_test.dart")

    def test_kotlin_is_handled_as_java(self):
        self.assertEqual(fs.detect_language("src/AddTest.kt"), "java")


# --------------------------------------------------------------------------
# per-language weighting
# --------------------------------------------------------------------------

class TestLanguageNotes(SmellCase):
    def _note(self, source, path, rule):
        for f in scan(source, path).findings:
            if f["rule"] == rule:
                return f.get("note")
        self.fail(f"{rule} did not fire")

    def test_java_sleep_carries_the_async_wait_weighting(self):
        note = self._note("@Test\npublic void waits() throws Exception {\n"
                          "    Thread.sleep(1000);\n"
                          "    assertEquals(1, size());\n}\n",
                          "src/QueueTest.java", "sleep")
        self.assertIn("45%", note)

    def test_python_shared_state_carries_the_order_dependency_weighting(self):
        note = self._note("SEEN = []\n"
                          "def test_records():\n"
                          "    SEEN.append(1)\n"
                          "    assert len(SEEN) == 1\n",
                          "tests/test_a.py", "shared-mutable-state")
        self.assertIn("59%", note)

    def test_python_sleep_is_marked_as_a_rare_python_flake_cause(self):
        note = self._note("import time\n"
                          "def test_worker():\n"
                          "    time.sleep(2)\n"
                          "    assert done() is True\n",
                          "tests/test_a.py", "sleep")
        self.assertIn("nearly absent", note)

    def test_javascript_order_dependency_is_marked_lower_priority(self):
        note = self._note("it('keys', () => "
                          "{ expect(Object.keys(d)).toEqual(['a']); });\n",
                          "tests/a.test.js", "iteration-order")
        self.assertIn("nonexistent", note)

    def test_the_note_never_suppresses_the_finding(self):
        # Weighting is triage information, not a filter: a sleep in Python is
        # still reported even though async-wait barely causes Python flakes.
        self.assertFires("sleep",
                         "import time\n"
                         "def test_worker():\n"
                         "    time.sleep(2)\n"
                         "    assert done() is True\n")


# --------------------------------------------------------------------------
# python ast path vs regex path
# --------------------------------------------------------------------------

AST_SAMPLE = '''
import random
import time


def test_one():
    pass


def test_two():
    compute(1, 2)


def test_three():
    time.sleep(1)
    assert compute(1, 2) == 3


def test_four():
    n = random.randint(1, 5)
    assert pick(n) == n
'''


class TestAstAndRegexPathsAgree(SmellCase):
    def test_both_paths_find_the_same_rules(self):
        with_ast = scan(AST_SAMPLE, use_ast=True)
        without = scan(AST_SAMPLE, use_ast=False)
        self.assertTrue(with_ast.ast_used)
        self.assertFalse(without.ast_used)
        self.assertEqual({f["rule"] for f in with_ast.findings},
                         {f["rule"] for f in without.findings})

    def test_both_paths_find_the_same_number_of_test_functions(self):
        self.assertEqual(len(scan(AST_SAMPLE, use_ast=True).blocks),
                         len(scan(AST_SAMPLE, use_ast=False).blocks))

    def test_unparseable_python_falls_back_to_the_regex_path(self):
        # A half-generated test file is exactly when we most want a report.
        broken = "def test_x():\n    assert compute( == 3\n\ndef test_y(:\n    pass\n"
        s = scan(broken)
        self.assertFalse(s.ast_used)
        self.assertIsNone(fs.python_blocks_ast(broken))

    def test_ast_path_is_used_for_valid_python(self):
        self.assertTrue(scan(CLEAN_PY).ast_used)


# --------------------------------------------------------------------------
# file selection and I/O
# --------------------------------------------------------------------------

class TestFileSelection(unittest.TestCase):
    def test_test_paths_are_recognised_across_conventions(self):
        for path in ["tests/test_a.py", "src/a.test.ts", "spec/b_spec.rb",
                     "src/__tests__/c.js", "app/TaxTest.java", "pkg/a_test.go",
                     "test/a_test.dart", "tests/AddTests.cs"]:
            with self.subTest(path=path):
                self.assertTrue(fs.is_test_file(path), path)

    def test_production_paths_are_not_mistaken_for_tests(self):
        for path in ["src/latest.py", "src/contest.js", "src/billing/tax.py",
                     "lib/protester.rb"]:
            with self.subTest(path=path):
                self.assertFalse(fs.is_test_file(path), path)


class TempTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, content, binary=False):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if binary else "w"
        with open(full, mode) as fh:
            fh.write(content)
        return full


class TestScanPaths(TempTree):
    def test_non_test_files_are_skipped_not_scanned(self):
        # A sleep in production code is correct, not a smell.
        self.write("src/worker.py",
                   "import time\n"
                   "def run():\n"
                   "    time.sleep(2)\n")
        result = fs.scan_paths([self.root])
        self.assertEqual(result["summary"]["findings"], 0)
        self.assertEqual(result["summary"]["files_scanned"], 0)
        self.assertEqual(result["summary"]["files_skipped"], 1)

    def test_binary_file_is_skipped_gracefully(self):
        self.write("tests/test_blob.py", b"\x00\x01\x02\xff" * 64, binary=True)
        result = fs.scan_paths([self.root])
        self.assertEqual(result["summary"]["files_scanned"], 0)
        reasons = [s["reason"] for s in result["summary"]["skipped"]]
        # The reason is now specific: a permission error, a broken symlink
        # and a NUL-byte file used to answer "unreadable or binary" alike,
        # and they need three different reactions from a reader.
        self.assertTrue(any("NUL byte" in r for r in reasons), reasons)
        self.assertFalse(any("unreadable" in r for r in reasons), reasons)

    def test_unreadable_file_is_skipped_gracefully(self):
        path = self.write("tests/test_locked.py", "def test_x():\n    pass\n")
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        result = fs.scan_paths([self.root])
        if os.geteuid() == 0:  # root can read anything; the branch is moot
            self.skipTest("running as root")
        self.assertEqual(result["summary"]["files_scanned"], 0)
        self.assertEqual(result["summary"]["files_skipped"], 1)

    def test_unknown_extension_is_ignored_entirely(self):
        self.write("tests/notes.md", "time.sleep(5)\n")
        result = fs.scan_paths([self.root])
        self.assertEqual(result["summary"]["files_scanned"], 0)
        self.assertEqual(result["summary"]["files_skipped"], 0)

    def test_vendor_directories_are_not_walked(self):
        self.write("node_modules/pkg/tests/test_a.py",
                   "def test_x():\n    pass\n")
        result = fs.scan_paths([self.root])
        self.assertEqual(result["summary"]["files_scanned"], 0)

    def test_clean_tree_reports_zero_findings(self):
        self.write("tests/test_clean.py", CLEAN_PY)
        result = fs.scan_paths([self.root])
        self.assertEqual(result["summary"]["files_scanned"], 1)
        self.assertEqual(result["summary"]["findings"], 0)
        self.assertEqual(result["summary"]["clean_files"], 1)


# --------------------------------------------------------------------------
# output shape
# --------------------------------------------------------------------------

DIRTY_PY = (
    "import random\n"
    "def test_pick():\n"
    "    n = random.randint(1, 10)\n"
    "    assert pick(n) == n\n"
)


class TestJsonShape(TempTree):
    def setUp(self):
        super().setUp()
        self.write("tests/test_dirty.py", DIRTY_PY)
        self.result = fs.scan_paths([self.root])

    def test_top_level_keys(self):
        self.assertEqual(self.result["schema"], fs.SCHEMA)
        for key in ("schema", "generated_at", "files", "summary"):
            self.assertIn(key, self.result)

    def test_summary_counts_add_up(self):
        s = self.result["summary"]
        self.assertEqual(s["findings"], sum(s["by_severity"].values()))
        self.assertEqual(s["findings"], sum(s["by_category"].values()))
        self.assertEqual(s["findings"], sum(s["by_rule"].values()))

    def test_every_finding_carries_the_full_contract(self):
        for f in self.result["files"][0]["findings"]:
            for key in ("file", "line", "category", "rule", "severity",
                        "snippet", "why", "fix"):
                self.assertIn(key, f)
            self.assertIn(f["category"], ("flake", "meaningless"))
            self.assertIn(f["severity"], fs.SEVERITIES)
            self.assertTrue(f["why"].strip())
            self.assertTrue(f["fix"].strip())

    def test_snippet_is_trimmed(self):
        long_line = ("def test_x():\n    n = random.randint(1, 10)  # "
                     + "x" * 400 + "\n    assert n == n\n")
        found = scan(long_line).findings
        for f in found:
            self.assertLessEqual(len(f["snippet"]), 100)

    def test_every_rule_in_the_catalogue_has_a_why_and_a_fix(self):
        for name, spec in fs.RULES.items():
            with self.subTest(rule=name):
                self.assertTrue(spec["why"].strip(), name)
                self.assertTrue(spec["fix"].strip(), name)
                self.assertIn(spec["severity"], fs.SEVERITIES)
                self.assertIn(spec["category"], ("flake", "meaningless"))

    def test_fix_is_concrete_enough_to_act_on(self):
        # "why" explains the consequence, "fix" names the remedy. A fix that
        # just restates the problem is why static checks get ignored.
        self.assertIn("random.seed(0)", fs.RULES["unseeded-random"]["fix"])

    def test_result_is_json_serialisable(self):
        self.assertEqual(json.loads(json.dumps(self.result)), self.result)


class TestSeverityPolicy(unittest.TestCase):
    def test_meaningless_rules_all_block_except_the_weak_ones(self):
        for rule in ("empty-test", "no-assertion", "tautological-assertion",
                     "todo-assertion", "mock-return-tautology", "focused-test"):
            with self.subTest(rule=rule):
                self.assertEqual(fs.RULES[rule]["severity"], "block")

    def test_unseeded_randomness_and_network_block(self):
        self.assertEqual(fs.RULES["unseeded-random"]["severity"], "block")
        self.assertEqual(fs.RULES["real-network"]["severity"], "block")

    def test_timing_and_precision_smells_only_warn(self):
        for rule in ("sleep", "real-clock", "float-equality", "weak-oracle"):
            with self.subTest(rule=rule):
                self.assertEqual(fs.RULES[rule]["severity"], "warn")

    def test_environmental_smells_are_informational(self):
        for rule in ("fs-order", "iteration-order", "locale-timezone",
                     "shared-mutable-state", "skipped-test"):
            with self.subTest(rule=rule):
                self.assertEqual(fs.RULES[rule]["severity"], "info")


class TestTextFormat(TempTree):
    def test_one_line_per_finding_in_path_line_rule_why_order(self):
        self.write("tests/test_dirty.py", DIRTY_PY)
        text = fs.render_text(fs.scan_paths([self.root]))
        first = text.splitlines()[0]
        self.assertIn("test_dirty.py:3", first)
        self.assertIn("unseeded-random", first)
        # The fix belongs in the JSON; the terminal line stays skimmable.
        self.assertNotIn("random.seed(0)", first)

    def test_text_output_ends_with_a_total(self):
        self.write("tests/test_dirty.py", DIRTY_PY)
        text = fs.render_text(fs.scan_paths([self.root]))
        self.assertIn("1 findings in 1 files", text)

    def test_clean_tree_text_output_lists_nothing(self):
        self.write("tests/test_clean.py", CLEAN_PY)
        text = fs.render_text(fs.scan_paths([self.root]))
        self.assertIn("0 findings", text)
        self.assertNotIn(":", text.splitlines()[0])


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

INFO_ONLY_PY = (
    "import os\n"
    "def test_files():\n"
    "    names = os.listdir(tmp)\n"
    "    assert names[0] == 'a.txt'\n"
)


class TestCli(TempTree):
    def out(self):
        return os.path.join(self.root, "report.json")

    def test_clean_tree_exits_zero(self):
        self.write("tests/test_clean.py", CLEAN_PY)
        self.assertEqual(
            fs.main([self.root, "--out", self.out(), "--fail-on", "info"]), 0)

    def test_block_finding_exits_one(self):
        self.write("tests/test_dirty.py", DIRTY_PY)
        self.assertEqual(fs.main([self.root, "--out", self.out()]), 1)

    def test_info_finding_does_not_breach_the_default_block_threshold(self):
        self.write("tests/test_info.py", INFO_ONLY_PY)
        self.assertEqual(fs.main([self.root, "--out", self.out()]), 0)

    def test_info_finding_breaches_fail_on_info(self):
        self.write("tests/test_info.py", INFO_ONLY_PY)
        self.assertEqual(
            fs.main([self.root, "--out", self.out(), "--fail-on", "info"]), 1)

    def test_info_finding_does_not_breach_fail_on_warn(self):
        self.write("tests/test_info.py", INFO_ONLY_PY)
        self.assertEqual(
            fs.main([self.root, "--out", self.out(), "--fail-on", "warn"]), 0)

    def test_missing_path_exits_two(self):
        stderr, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(fs.main([os.path.join(self.root, "nope")]), 2)
            self.assertIn("no such path", sys.stderr.getvalue())
        finally:
            sys.stderr = stderr

    def test_out_file_contains_valid_json(self):
        self.write("tests/test_dirty.py", DIRTY_PY)
        fs.main([self.root, "--out", self.out()])
        with open(self.out()) as fh:
            data = json.load(fh)
        self.assertEqual(data["schema"], fs.SCHEMA)
        self.assertEqual(data["summary"]["findings"], 1)

    def test_text_format_writes_text_not_json(self):
        self.write("tests/test_dirty.py", DIRTY_PY)
        fs.main([self.root, "--out", self.out(), "--format", "text"])
        with open(self.out()) as fh:
            body = fh.read()
        self.assertIn("unseeded-random", body)
        with self.assertRaises(ValueError):
            json.loads(body)

    def test_a_single_file_argument_works(self):
        path = self.write("tests/test_dirty.py", DIRTY_PY)
        self.assertEqual(fs.main([path, "--out", self.out()]), 1)


class TestBreachThreshold(unittest.TestCase):
    def _result(self, block=0, warn=0, info=0):
        return {"summary": {"by_severity": {"block": block, "warn": warn,
                                            "info": info}}}

    def test_block_threshold_ignores_warn_and_info(self):
        self.assertFalse(fs.breaches(self._result(warn=3, info=9), "block"))
        self.assertTrue(fs.breaches(self._result(block=1), "block"))

    def test_warn_threshold_includes_block(self):
        self.assertTrue(fs.breaches(self._result(block=1), "warn"))
        self.assertTrue(fs.breaches(self._result(warn=1), "warn"))
        self.assertFalse(fs.breaches(self._result(info=5), "warn"))

    def test_info_threshold_includes_everything(self):
        self.assertTrue(fs.breaches(self._result(info=1), "info"))
        self.assertFalse(fs.breaches(self._result(), "info"))



class TestWeakOraclePytestIdiom(unittest.TestCase):
    """pytest's bare assert is the dominant Python idiom, so a weak-oracle rule
    that only knows unittest's assertIsNotNone misses most real cases.

    The near-miss twins matter more than the positives here: a rule this broad
    would wreck the false-positive budget if it fired on ordinary comparisons.
    """

    def _rules(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test_x.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            result = fs.scan_paths([path])
        return [f["rule"] for f in _all_findings(result)]

    def test_bare_is_not_none_is_a_weak_oracle(self):
        self.assertIn("weak-oracle",
                      self._rules("def test_a():\n    assert compute(2, 2) is not None\n"))

    def test_truthy_only_assert_is_a_weak_oracle(self):
        self.assertIn("weak-oracle",
                      self._rules("def test_a():\n    assert compute(2, 2)\n"))

    def test_real_comparison_is_not_flagged(self):
        self.assertNotIn("weak-oracle",
                         self._rules("def test_a():\n    assert compute(2, 2) == 4\n"))

    def test_membership_is_not_flagged(self):
        self.assertNotIn("weak-oracle",
                         self._rules("def test_a():\n    assert 'x' in compute()\n"))

    def test_comparison_operator_is_not_flagged(self):
        self.assertNotIn("weak-oracle",
                         self._rules("def test_a():\n    assert len(compute()) > 0\n"))

    def test_is_none_is_a_real_assertion_not_a_weak_oracle(self):
        # Asserting something IS None constrains behaviour; asserting it is
        # NOT None only proves the call returned.
        self.assertNotIn("weak-oracle",
                         self._rules("def test_a():\n    assert compute() is None\n"))

    def test_assert_with_message_still_compares(self):
        self.assertNotIn("weak-oracle",
                         self._rules("def test_a():\n    assert a == b, 'totals differ'\n"))


class TestInlineSuppression(unittest.TestCase):
    """A developer who cannot dismiss one finding dismisses the whole tool.
    The escape hatch requires a reason, so suppressions stay auditable."""

    def _rules(self, source, name="test_x.py"):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            return [f["rule"] for f in _all_findings(fs.scan_paths([path]))]

    def test_named_rule_is_silenced(self):
        src = ("def test_a():\n"
               "    assert total == 0.3  # testsquad:ignore float-equality -- rounded\n")
        self.assertNotIn("float-equality", self._rules(src))

    def test_bare_ignore_silences_everything_on_that_line(self):
        src = "def test_a():\n    assert total == 0.3  # testsquad:ignore -- fixture\n"
        self.assertNotIn("float-equality", self._rules(src))

    def test_silencing_one_rule_leaves_the_others(self):
        src = ("import random\n\n"
               "def test_a():\n"
               "    assert f(random.random()) == 0.3  # testsquad:ignore float-equality -- x\n")
        rules = self._rules(src)
        self.assertNotIn("float-equality", rules)
        self.assertIn("unseeded-random", rules)

    def test_suppression_on_another_line_does_not_leak(self):
        src = ("def test_a():\n"
               "    assert a == 0.3  # testsquad:ignore float-equality -- ok\n"
               "    assert b == 0.7\n")
        self.assertIn("float-equality", self._rules(src))

    def test_an_unrelated_comment_suppresses_nothing(self):
        src = "def test_a():\n    assert total == 0.3  # ignore this for now\n"
        self.assertIn("float-equality", self._rules(src))


class TestBareUrlSeverity(unittest.TestCase):
    """A URL literal may be a request or may be fixture data; statically the
    two are identical. Blocking a commit on an ambiguous signal is how a gate
    gets switched off, so the literal warns and the real call blocks."""

    def _findings(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test_x.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            return _all_findings(fs.scan_paths([path]))

    def test_bare_url_literal_warns(self):
        got = [f for f in self._findings(
            'def test_a():\n    url = "https://api.example.org/v1"\n    assert url\n')
            if f["rule"] == "real-network"]
        self.assertTrue(got)
        self.assertEqual(got[0]["severity"], "warn")

    def test_an_actual_request_blocks(self):
        got = [f for f in self._findings(
            'import requests\n\ndef test_a():\n'
            '    assert requests.get("https://api.example.org/v1").status_code == 200\n')
            if f["rule"] == "real-network"]
        self.assertTrue(got)
        self.assertEqual(got[0]["severity"], "block")


class TestTodoLivesInCommentsNotData(unittest.TestCase):
    """A `block`-severity rule that fires on its own test suite is how the
    whole tool gets switched off. TODO is a marker an author writes in a
    comment; inside a string literal it is data."""

    def _rules(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test_x.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            return [f["rule"] for f in _all_findings(fs.scan_paths([path]))]

    def test_todo_in_a_comment_still_fires(self):
        self.assertIn("todo-assertion", self._rules(
            "def test_a():\n    result = compute()\n    # TODO: add assertion\n"))

    def test_todo_in_a_docstring_is_a_known_accepted_miss(self):
        # Telling a docstring from any other literal needs real parsing, and
        # the proximity heuristic that approximates it re-admits the false
        # positives below. A missed marker costs one finding; a false one
        # costs the tool. The body still gets reported as assertion-free.
        rules = self._rules(
            'def test_a():\n    """TODO: add assertion"""\n    result = compute()\n')
        self.assertNotIn("todo-assertion", rules)
        self.assertIn("no-assertion", rules)

    def test_todo_inside_a_fixture_string_does_not_fire(self):
        # This is the shape of our own detector tests: a fixture describing
        # the thing being detected.
        src = ('def test_a():\n'
               '    fixture = "def test_b():\\n    # TODO: add assertion\\n"\n'
               '    assert detect(fixture) == ["todo-assertion"]\n')
        self.assertNotIn("todo-assertion", self._rules(src))

    def test_todo_in_an_error_message_does_not_fire(self):
        src = ('def test_a():\n'
               '    with pytest.raises(ValueError, match="TODO: add assertion"):\n'
               '        parse("")\n')
        self.assertNotIn("todo-assertion", self._rules(src))


# --------------------------------------------------------------------------
# live-repo -- ambient filesystem / VCS coupling
# --------------------------------------------------------------------------

class TestLiveRepo(SmellCase):
    """The rule's whole difficulty is the silent half, so there are more
    negatives here than positives and they are the real suite. This repo's
    own tests run subprocesses, build temp trees and read a committed
    fixture directory constantly; a rule that fires on any of those is above
    the ~10% line on its first run and gets the gate switched off."""

    # -- (1) git asked about "here" -------------------------------------
    def test_git_status_without_a_named_tree_fires(self):
        self.assertFires("live-repo",
                         'import subprocess\n'
                         'def test_a():\n'
                         '    out = subprocess.run(["git", "status", "--porcelain"])\n'
                         '    assert out.returncode == 0\n')

    def test_git_call_split_over_lines_finds_the_cwd_on_a_later_line(self):
        """The suppressor has to survive the commonest way to write the
        call. Reading one line at a time reports every multi-line
        subprocess as unscoped, which is a false positive on good code."""
        self.assertSilent("live-repo",
                          'import subprocess\n'
                          'def test_a():\n'
                          '    out = subprocess.run(\n'
                          '        ["git", "status", "--porcelain"],\n'
                          '        cwd=self.tmp,\n'
                          '    )\n'
                          '    assert out.returncode == 0\n')

    def test_git_dash_c_names_the_tree_and_stays_silent(self):
        """Every git call in this repository's own suite is written this way.

        Measured, so the comment does not mislead: this is silenced by the
        subcommand having to follow `git` immediately -- `-C` is in the way,
        so GIT_AMBIENT_RE never matches -- and not by GIT_SCOPED_RE. Both
        answers are "silent"; only one of them is the reason.
        """
        src = ('import subprocess\n'
               'def test_a():\n'
               '    out = subprocess.run(["git", "-C", repo, "status"])\n'
               '    assert out.returncode == 0\n')
        self.assertSilent("live-repo", src)
        self.assertFalse(fs.GIT_AMBIENT_RE.search(src),
                         "the -C form is supposed to be excluded before the "
                         "suppressor is ever consulted")

    def test_git_dir_in_the_argv_names_the_tree_and_stays_silent(self):
        """The reachable half of GIT_SCOPED_RE: a flag that names the tree
        AFTER the subcommand, where the adjacency rule cannot see it."""
        self.assertSilent("live-repo",
                          'import subprocess\n'
                          'def test_a():\n'
                          '    out = subprocess.run(["git", "status", "--git-dir", d])\n'
                          '    assert out.returncode == 0\n')

    def test_git_clone_is_not_ambient(self):
        """`clone` is told its source and its destination, so the working
        directory does not change what it does. It is absent from the
        subcommand list rather than suppressed afterwards."""
        self.assertSilent("live-repo",
                          'import subprocess\n'
                          'def test_a():\n'
                          '    out = subprocess.run(["git", "clone", "-q", url, dst])\n'
                          '    assert out.returncode == 0\n')

    def test_a_commented_out_git_call_stays_silent(self):
        """This is the one rule that reads `raw` instead of `code`, because
        the program name lives in a string literal. The guard is that the
        statement's CODE must independently show a subprocess call."""
        self.assertSilent("live-repo",
                          'def test_a():\n'
                          '    # subprocess.run(["git", "status"])\n'
                          '    assert run() == 0\n')

    def test_two_ambient_git_calls_are_two_findings(self):
        src = ('import subprocess\n'
               'def test_a():\n'
               '    assert subprocess.run(["git", "status"]).returncode == 0\n'
               'def test_b():\n'
               '    assert subprocess.run(["git", "log", "-1"]).returncode == 0\n')
        hits = [f for f in scan(src).findings if f["rule"] == "live-repo"]
        self.assertEqual(len(hits), 2, hits)

    # -- (2) enumerating the checkout ------------------------------------
    def test_walking_the_repository_root_fires(self):
        self.assertFires("live-repo",
                         'import os\n'
                         'ROOT = os.path.dirname(os.path.dirname(__file__))\n'
                         'def test_a():\n'
                         '    assert list(os.walk(ROOT))\n')

    def test_globbing_the_repository_root_fires(self):
        self.assertFires("live-repo",
                         'import glob, os\n'
                         'def test_a():\n'
                         '    assert glob.glob(os.path.join(ROOT, "*", "*.py"))\n')

    def test_scanning_the_working_directory_fires(self):
        self.assertFires("live-repo",
                         'import os\n'
                         'def test_a():\n'
                         '    assert list(os.scandir(os.getcwd()))\n')

    def test_walking_a_tempdir_stays_silent(self):
        self.assertSilent("live-repo",
                          'import os\n'
                          'def test_a():\n'
                          '    assert list(os.walk(self.tmp))\n')

    def test_walking_a_committed_fixture_directory_stays_silent(self):
        """tests/test_fixture_manifest.py is written exactly like this, on
        purpose. The word that says so -- "fixtures" -- is a string literal,
        which the scrubber has already collapsed, so the controlled half has
        to be read from `raw`. Reading only `code` made this rule's first
        measured finding a false positive on this shape."""
        self.assertSilent("live-repo",
                          'import os\n'
                          'def test_a():\n'
                          '    base = os.path.join(ROOT, "examples", "fixtures")\n'
                          '    assert list(os.walk(os.path.join(ROOT, "examples", "fixtures")))\n')

    def test_a_lowercase_local_named_root_stays_silent(self):
        """`root` is the commonest local name for a tempdir in this repo's
        own suite (tests/test_atomic_io.py). Matching the repository root
        case-insensitively would have made this rule's first finding a
        false positive."""
        self.assertSilent("live-repo",
                          'import os, tempfile\n'
                          'def test_a():\n'
                          '    root = tempfile.mkdtemp()\n'
                          '    assert sorted(os.listdir(root)) == []\n')

    def test_reading_one_named_committed_file_stays_silent(self):
        """Not every repository-relative path is ambient. scripts/foo.py is
        the same bytes on CI and on a dirty tree; what is ambient is the SET
        of files, not a file."""
        self.assertSilent("live-repo",
                          'import os\n'
                          'def test_a():\n'
                          '    text = open(os.path.join(ROOT, "scripts", "x.py")).read()\n'
                          '    assert "max_per_file" in text\n')

    # -- (3) a module-level root left pointing at the real checkout ------
    #
    # This is the shape that prompted the rule. Two tests in this repo's own
    # tests/test_robustness.py called project_state.collect() with the
    # module's ROOT still pointing at the real repository: they walked the
    # whole checkout, shelled out to git four times and ran
    # scripts/validate_plugin.py as a subprocess. They passed on CI and
    # failed on a dirty tree.
    PRE_FIX = (
        'import unittest\n'
        'from unittest import mock\n'
        'import project_state as pstate\n'
        'class T(unittest.TestCase):\n'
        '    def test_reports_the_file_it_could_not_parse(self):\n'
        '        state = pstate.collect(skip_tests=True)\n'
        '        rendered = pstate.render(state)\n'
        '        self.assertIn("NOT EXACT", rendered)\n'
        '    def test_does_not_announce_a_half_written_file(self):\n'
        '        with mock.patch.object(pstate, "ROOT", self.tmp):\n'
        '            markers = pstate.section_markers()\n'
        '        self.assertTrue(markers["totals_exact"])\n')

    FIXED = (
        'import contextlib\n'
        'import unittest\n'
        'from unittest import mock\n'
        'import project_state as pstate\n'
        'class T(unittest.TestCase):\n'
        '    def collect_fixture(self):\n'
        '        with contextlib.ExitStack() as stack:\n'
        '            stack.enter_context(mock.patch.object(pstate, "ROOT", self.tmp))\n'
        '            return pstate.collect(skip_tests=True)\n'
        '    def test_reports_the_file_it_could_not_parse(self):\n'
        '        state = self.collect_fixture()\n'
        '        rendered = pstate.render(state)\n'
        '        self.assertIn("NOT EXACT", rendered)\n'
        '    def test_does_not_announce_a_half_written_file(self):\n'
        '        with mock.patch.object(pstate, "ROOT", self.tmp):\n'
        '            markers = pstate.section_markers()\n'
        '        self.assertTrue(markers["totals_exact"])\n')

    def test_the_real_pre_fix_shape_fires_once_on_the_uncovered_call(self):
        hits = [f for f in scan(self.PRE_FIX).findings if f["rule"] == "live-repo"]
        self.assertEqual([f["line"] for f in hits], [6], hits)

    def test_the_shipped_fixed_shape_is_silent(self):
        """The fix moves the call into a helper and patches ROOT with an
        ExitStack, so there is no lexically enclosing `with` to be inside --
        which is why coverage is computed over the whole enclosing `def`."""
        self.assertSilent("live-repo", self.FIXED)

    def test_a_call_handed_its_subject_stays_silent(self):
        """pstate.render(state) renders what it was given. Only a call told
        nothing can be taking its input from the environment -- and this is
        the suppressor that keeps the FIXED sample above quiet."""
        self.assertSilent("live-repo",
                          'from unittest import mock\n'
                          'import project_state as pstate\n'
                          'def test_a():\n'
                          '    with mock.patch.object(pstate, "ROOT", tmp):\n'
                          '        pass\n'
                          'def test_b():\n'
                          '    assert pstate.render(state) == ""\n')

    def test_without_a_root_patch_anywhere_the_file_is_not_judged(self):
        """The evidence is intra-file: a file that never patches a module
        root has not told us any module has one, so this detector has
        nothing to go on and says nothing."""
        self.assertSilent("live-repo",
                          'import project_state as pstate\n'
                          'def test_a():\n'
                          '    assert pstate.collect(skip_tests=True)\n')

    # -- severity and catalogue -----------------------------------------
    def test_live_repo_warns_rather_than_blocks(self):
        """Deliberate, and the reason is in the rule's comment: this project
        runs `--fail-on block` in CI, the evidence here is the ABSENCE of a
        cwd or a patch rather than a call anyone can see, and the last rule
        shipped at `block` on reasoning instead of field data
        (`no-assertion`) was measured at a 100% false-positive rate on this
        repository. Promotion is a one-word change once there is field data;
        recovering a gate that has been switched off is not."""
        self.assertEqual(fs.RULES["live-repo"]["severity"], "warn")

    def test_live_repo_names_the_remedy_not_the_regex(self):
        spec = fs.RULES["live-repo"]
        self.assertIn("cwd", spec["fix"])
        self.assertIn("ROOT", spec["fix"])
        self.assertNotIn("live-repo", spec["why"])

    def test_the_suppression_comment_silences_it_like_every_other_rule(self):
        self.assertSilent("live-repo",
                          'import subprocess\n'
                          'def test_a():\n'
                          '    out = subprocess.run(["git", "status"])  '
                          '# testsquad:ignore live-repo -- deliberate\n'
                          '    assert out.returncode == 0\n')


class TestLiveRepoOnThisRepository(unittest.TestCase):
    """The measurement, pinned. A rule whose false-positive rate is only
    ever claimed in a report rots the moment someone edits the suite."""

    def test_the_rule_is_silent_on_this_project_s_own_suite(self):
        here = os.path.dirname(os.path.abspath(__file__))
        # normpath, deliberately: "tests/../scripts" contains the substring
        # "/tests/", so TEST_FILE_PATTERN classifies every production script
        # reached that way as a test file and every rule in this gate runs on
        # it. That is a property of path spelling, not of this rule, and it
        # is recorded rather than worked around silently.
        scripts = os.path.normpath(os.path.join(here, os.pardir, "scripts"))
        result = fs.scan_paths([here, scripts])
        hits = [f for f in _all_findings(result) if f["rule"] == "live-repo"]
        self.assertEqual(hits, [], "live-repo fired on this repository's own "
                                   "suite; that is its false-positive rate "
                                   "speaking, and the rule needs rethinking "
                                   "rather than the suite needing edits")
        # Guard the guard: a scan that read nothing would pass the line above
        # without exercising one byte of the rule.
        self.assertGreaterEqual(result["summary"]["files_scanned"], 10,
                                result["summary"])


# --------------------------------------------------------------------------
# unread files -- which of the three reasons it was
# --------------------------------------------------------------------------

class TestWhyAFileWasNotRead(TempTree):
    """`skipped_files` always existed. What did not was a reason a reader can
    act on: "unreadable or binary" answered a permission error, a broken
    symlink and a NUL-byte file identically."""

    def test_a_binary_file_says_it_is_binary(self):
        self.write("tests/test_x.py", b"def test_a():\n\x00\xff\n", binary=True)
        result = fs.scan_paths([self.root])
        reason = result["skipped_files"][0]["reason"]
        self.assertIn("NUL byte", reason)
        self.assertIn("NOT scanned", reason)
        self.assertNotIn("unreadable", reason)

    def test_an_unreadable_file_says_so_and_names_the_errno(self):
        full = self.write("tests/test_x.py", "def test_a():\n    assert 1\n")
        os.chmod(full, 0o000)
        self.addCleanup(os.chmod, full, 0o644)
        if os.access(full, os.R_OK):      # running as root: the probe cannot work
            self.skipTest("this process can read a mode-000 file, so there is "
                          "no permission error here to report on")
        result = fs.scan_paths([self.root])
        reason = result["skipped_files"][0]["reason"]
        self.assertIn("unreadable", reason)
        self.assertIn("Permission denied", reason)
        self.assertNotIn("NUL byte", reason)

    def test_an_oversize_file_still_names_the_ceiling(self):
        self.write("tests/test_x.py", "def test_a():\n    assert 1\n")
        result = fs.scan_paths([self.root], max_bytes=4)
        reason = result["skipped_files"][0]["reason"]
        self.assertIn("--max-file-bytes", reason)
        self.assertNotIn("unreadable", reason)

    def test_latin1_cannot_fail_so_there_is_no_fourth_reason(self):
        """Measured, not assumed: latin-1 maps all 256 byte values, so the
        decode fallback cannot raise on a bytes input. The `except
        Exception: return None` that used to guard it was unreachable -- a
        reachable-looking guard around an unreachable branch tells a reader
        a case is handled when nothing handles it."""
        blob = bytes(range(256)) * 8
        self.assertEqual(len(blob.decode("latin-1")), len(blob))

    def test_the_wrapper_still_returns_two_values(self):
        full = self.write("tests/test_x.py", "def test_a():\n    assert 1\n")
        self.assertEqual(len(fs.read_lines_bounded(full)), 2)
        self.assertIsNotNone(fs.read_lines(full))


# --------------------------------------------------------------------------
# snippet redaction -- docs/SECURITY-REVIEW.md S3-1
# --------------------------------------------------------------------------

class TestSnippetRedaction(SmellCase):
    """Findings carry verbatim source into `.testing/` (committed), the CI
    log (public) and the uploaded artifact. The patterns are not redefined
    here: pr_comment.redact() is reused, which itself reuses
    env_safety_check's, so this is the third consumer of one definition
    rather than a third copy of it."""

    def _snippet(self, line_source):
        src = "def test_a():\n" + line_source
        hits = scan(src).findings
        self.assertTrue(hits, "the probe line must make some rule fire")
        return hits[0]["snippet"]

    def test_a_live_stripe_key_is_not_published(self):
        got = self._snippet('    assert f("sk_live_4eC39HqLyjWDarjtT1zdp7dcAbCd") '
                            '== random.random()\n')
        self.assertNotIn("sk_live_4eC39", got)
        self.assertIn("redacted", got)

    def test_an_aws_key_id_is_not_published(self):
        got = self._snippet('    assert f("AKIAIOSFODNN7EXAMPLE") == random.random()\n')
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", got)

    def test_a_credential_url_is_not_published(self):
        got = self._snippet('    assert f("postgres://admin:hunter2secretpw@db:5432/a") '
                            '== random.random()\n')
        self.assertNotIn("hunter2secretpw", got)

    def test_redaction_runs_before_truncation(self):
        """Truncating first publishes whatever fell inside the first 97
        characters, and half a key is still a leak."""
        pad = "x" * 70
        got = self._snippet('    assert f("%s", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") '
                            '== random.random()\n' % pad)
        self.assertNotIn("ghp_", got)

    def test_developer_obvious_values_stay_readable(self):
        """The opposite false positive, and the one that kills the tool: a
        finding whose evidence reads `[redacted]` cannot be judged, so it
        gets ignored and the gate goes with it."""
        got = self._snippet('    assert f(password="change-me-later", host="localhost") '
                            '== random.random()\n')
        self.assertIn("change-me-later", got)
        self.assertIn("localhost", got)

    def test_the_report_states_whether_redaction_was_applied(self):
        """An unredacted report that claims to be redacted is worse than one
        that admits it is not, so `applied` is in the summary either way."""
        status = fs.redaction_status()
        self.assertIn("applied", status)
        self.assertTrue(status["applied"], status)
        self.assertEqual(status["source"], "pr_comment.redact")

    def test_the_scan_summary_carries_the_redaction_record(self):
        result = fs.scan_paths([os.path.dirname(os.path.abspath(__file__))])
        self.assertTrue(result["summary"]["redaction"]["applied"])

    def test_the_patterns_are_borrowed_not_redefined(self):
        """Two copies of a redaction rule drifting apart leaks exactly the
        shapes the stale copy has not heard of, so there must be no local
        secret pattern in this script at all."""
        import pr_comment
        self.assertIs(fs._redactor()[0], pr_comment.redact)
        # Not a source grep for key prefixes: this module's own comment names
        # `sk_live_` to explain what it is protecting against, and a grep
        # cannot tell an explanation from a second copy. Identity can.
        self.assertNotIn("re.compile", fs.redact_snippet.__doc__ or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
