#!/usr/bin/env python3
"""Tests for arid_nodes.py.

Every suppression rule gets a pair:

    test_<rule>_suppresses       -- the arid node it exists for
    test_<rule>_leaves_..._mutable -- its near-miss twin

The second half is the important half, and more so here than anywhere else
in this repo. A suppression rule that over-reaches does not produce a noisy
finding someone can ignore; it silently deletes a mutant, and a mutant that
was never generated cannot be missed by anyone reading the report. The
failure mode is invisible, so it has to be tested explicitly.

TestMustRemainMutable comes first on purpose. Those four shapes -- money
arithmetic, a boundary comparison, an authorization connector and a loop
condition -- are the ones a mutation run exists to question. If any rule
ever swallows one of them, that is a release blocker and these tests say so
before anything else runs.

Sources are analysed in memory except where the test is specifically about
file selection, I/O or the CLI.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import arid_nodes as an  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def analyse(source, path="src/billing.py", **kw):
    lang = kw.pop("lang", None) or an.detect_language(path)
    kw.setdefault("max_per_file", 0)      # cap off unless a test is about it
    return an.analyse_file(path, source.splitlines(keepends=True), lang, **kw)


def rule_for(source, needle, path="src/billing.py", **kw):
    """The suppression rule on the line containing `needle`, or None when
    that line was selected for mutation instead."""
    entry = analyse(source, path, **kw)
    line = _line_of(source, needle)
    for s in entry["suppressed"]:
        if s["line"] == line:
            return s["rule"]
    for s in entry["selected"]:
        if s["line"] == line:
            return None
    return "not-a-candidate"


def _line_of(source, needle):
    for i, text in enumerate(source.splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(f"{needle!r} is not in the fixture")


class AridCase(unittest.TestCase):
    def assertSuppressed(self, rule, source, needle, path="src/billing.py", **kw):
        got = rule_for(source, needle, path, **kw)
        self.assertEqual(rule, got,
                         f"expected {needle!r} suppressed by {rule}, got {got}")

    def assertMutable(self, source, needle, path="src/billing.py", **kw):
        got = rule_for(source, needle, path, **kw)
        self.assertIsNone(
            got, f"{needle!r} must stay mutable, but {got} took it away")


# --------------------------------------------------------------------------
# the non-negotiables
# --------------------------------------------------------------------------

class TestMustRemainMutable(AridCase):
    """Four shapes no suppression rule may ever reach. Each is written the
    way the rule that most nearly applies would have to see it."""

    def test_arithmetic_on_money_stays_mutable(self):
        src = ("def charge(price_cents, quantity):\n"
               "    logger.info(\"charging\")\n"
               "    total_cents = price_cents * quantity\n"
               "    return total_cents\n")
        self.assertMutable(src, "price_cents * quantity")

    def test_money_arithmetic_in_a_shouting_constant_stays_mutable(self):
        # The constant rule suppresses `RATE = 0.05`. It must not suppress
        # `RATE = BASE * 1.05`: the name shouts, but the line does arithmetic.
        src = "BASE_RATE = 100\nRATE = BASE_RATE * 1.05\n"
        self.assertMutable(src, "BASE_RATE * 1.05")

    def test_boundary_comparison_stays_mutable(self):
        src = ("def allow(balance, minimum):\n"
               "    if balance >= minimum:\n"
               "        return True\n"
               "    return False\n")
        self.assertMutable(src, "balance >= minimum")

    def test_authorization_connector_stays_mutable(self):
        src = ("def can_edit(user, doc):\n"
               "    if user.is_admin or user.owns(doc):\n"
               "        return True\n"
               "    return False\n")
        self.assertMutable(src, "user.is_admin or user.owns(doc)")

    def test_loop_condition_stays_mutable(self):
        src = ("def drain(items):\n"
               "    i = 0\n"
               "    while i < len(items):\n"
               "        i = i + 1\n")
        self.assertMutable(src, "while i < len(items)")

    def test_boundary_comparison_survives_in_every_language(self):
        for path, src, needle in (
            ("src/a.js", "function f(b, m) {\n  if (b >= m) { return 1; }\n}\n",
             "b >= m"),
            ("src/A.java",
             "class A {\n  int f(int b, int m) {\n    if (b >= m) { return 1; }\n"
             "    return 0;\n  }\n}\n", "b >= m"),
            ("src/a.go",
             "package x\n\nfunc f(b int, m int) int {\n\tif b >= m {\n"
             "\t\treturn 1\n\t}\n\treturn 0\n}\n", "b >= m"),
            ("src/a.rb", "def f(b, m)\n  if b >= m\n    1\n  end\nend\n",
             "b >= m"),
        ):
            with self.subTest(path=path):
                self.assertMutable(src, needle, path=path)


# --------------------------------------------------------------------------
# file-level suppression
# --------------------------------------------------------------------------

class TestFileLevel(AridCase):
    def test_a_test_file_is_never_mutated(self):
        entry = analyse("def test_total():\n    assert total(2, 3) == 6\n",
                        path="tests/test_billing.py")
        self.assertEqual("test-file", entry["file_suppressed_by"])
        self.assertEqual([], entry["selected"])

    def test_a_production_file_with_a_similar_name_is_mutated(self):
        entry = analyse("def latest(a, b):\n    return a * b\n",
                        path="src/testimonials.py")
        self.assertNotIn("file_suppressed_by", entry)
        self.assertTrue(entry["selected"])

    def test_a_generated_file_is_never_mutated_by_path(self):
        for path in ("src/schema_pb2.py", "lib/model.g.dart",
                     "lib/model.freezed.dart", "src/api.generated.ts",
                     "src/api.pb.go"):
            with self.subTest(path=path):
                entry = analyse("x = a * b\n", path=path,
                                lang=an.detect_language(path) or "python")
                self.assertEqual("generated-code", entry["file_suppressed_by"])

    def test_a_generated_file_is_never_mutated_by_header(self):
        src = "# Code generated by protoc. DO NOT EDIT.\nx = a * b\n"
        self.assertEqual("generated-code",
                         analyse(src, path="src/thing.py")["file_suppressed_by"])

    def test_the_phrase_generated_in_prose_far_down_the_file_is_ignored(self):
        # Only the header is inspected. A docstring halfway down a real file
        # that happens to say "automatically generated" is prose.
        src = "def f(a, b):\n" + "    pass\n" * 20 + \
              "    # the report is automatically generated\n    return a * b\n"
        entry = analyse(src, path="src/report.py")
        self.assertNotIn("file_suppressed_by", entry)

    def test_file_level_suppression_reports_once_not_per_line(self):
        entry = analyse("a = b * c\n" * 50, path="tests/test_x.py")
        self.assertEqual(1, len(entry["suppressed"]))
        self.assertEqual("file", entry["suppressed"][0]["scope"])


# --------------------------------------------------------------------------
# rule 1: logging, telemetry, tracing
# --------------------------------------------------------------------------

class TestLoggingAndTelemetry(AridCase):
    def test_python_logger_call_is_suppressed(self):
        src = "def f(x):\n    logger.info(\"x is %s\", x)\n    return x * 2\n"
        self.assertSuppressed("logging-or-telemetry", src, "logger.info")

    def test_print_is_suppressed(self):
        src = "def f(x):\n    print(x + 1)\n    return x\n"
        self.assertSuppressed("logging-or-telemetry", src, "print(")

    def test_console_log_is_suppressed(self):
        src = "function f(x) {\n  console.log(x + 1);\n  return x;\n}\n"
        self.assertSuppressed("logging-or-telemetry", src, "console.log",
                              path="src/a.js")

    def test_go_fmt_println_is_suppressed(self):
        src = "package x\n\nfunc f(a int) int {\n\tfmt.Println(a + 1)\n\treturn a\n}\n"
        self.assertSuppressed("logging-or-telemetry", src, "fmt.Println",
                              path="src/a.go")

    def test_java_system_out_is_suppressed(self):
        src = ("class A {\n  int f(int a) {\n    System.out.println(a + 1);\n"
               "    return a;\n  }\n}\n")
        self.assertSuppressed("logging-or-telemetry", src, "System.out",
                              path="src/A.java")

    def test_csharp_ilogger_is_suppressed(self):
        src = ("class A {\n  int F(int a) {\n    _logger.LogInformation(\"x\", a + 1);\n"
               "    return a;\n  }\n}\n")
        self.assertSuppressed("logging-or-telemetry", src, "LogInformation",
                              path="src/A.cs")

    def test_metrics_counter_is_suppressed(self):
        src = "def f(x):\n    statsd.increment(\"calls\", x + 1)\n    return x\n"
        self.assertSuppressed("logging-or-telemetry", src, "statsd.increment")

    def test_tracing_span_is_suppressed(self):
        src = "def f(x):\n    tracer.start_span(\"work\", x + 1)\n    return x\n"
        self.assertSuppressed("logging-or-telemetry", src, "tracer.start_span")

    def test_a_logged_value_that_is_computed_stays_mutable(self):
        # The call result is used, so this line decides something.
        src = "def f(x):\n    audit_id = logger.record(x * 2)\n    return audit_id\n"
        self.assertMutable(src, "logger.record")

    def test_a_condition_mentioning_a_logger_stays_mutable(self):
        src = ("def f(amount, limit):\n"
               "    if logger.enabled and amount > limit:\n"
               "        return 1\n    return 0\n")
        self.assertMutable(src, "amount > limit")


# --------------------------------------------------------------------------
# rule 2: debug and assertion statements
# --------------------------------------------------------------------------

class TestDebugStatements(AridCase):
    def test_production_assert_is_suppressed(self):
        src = "def f(x):\n    assert x > 0\n    return x * 2\n"
        self.assertSuppressed("debug-statement", src, "assert x > 0")

    def test_breakpoint_is_suppressed(self):
        src = "def f(x):\n    breakpoint()\n    return x * 2\n"
        self.assertSuppressed("debug-statement", src, "breakpoint()")

    def test_js_debugger_is_suppressed(self):
        src = "function f(x) {\n  debugger;\n  return x * 2;\n}\n"
        self.assertSuppressed("debug-statement", src, "debugger",
                              path="src/a.js")

    def test_a_variable_named_assertion_stays_mutable(self):
        src = "def f(x):\n    assertions = x * 2\n    return assertions\n"
        self.assertMutable(src, "assertions = x * 2")


# --------------------------------------------------------------------------
# rule 3: trivial accessors
# --------------------------------------------------------------------------

class TestTrivialAccessors(AridCase):
    def test_python_property_getter_is_suppressed(self):
        src = ("class Order:\n    @property\n    def total(self):\n"
               "        return self._total\n")
        self.assertSuppressed("trivial-accessor", src, "return self._total")

    def test_python_get_prefixed_method_is_suppressed(self):
        src = "class Order:\n    def get_id(self):\n        return self.id\n"
        self.assertSuppressed("trivial-accessor", src, "return self.id")

    def test_python_setter_is_suppressed(self):
        src = ("class Order:\n    def set_id(self, value):\n"
               "        self.id = value\n")
        self.assertSuppressed("trivial-accessor", src, "self.id = value")

    def test_java_one_line_getter_is_suppressed(self):
        src = "class A {\n  public int getCents() { return cents; }\n}\n"
        self.assertSuppressed("trivial-accessor", src, "getCents",
                              path="src/A.java")

    def test_js_one_line_getter_is_suppressed(self):
        src = "class A {\n  getItems() { return this._items; }\n}\n"
        self.assertSuppressed("trivial-accessor", src, "getItems",
                              path="src/a.js")

    def test_ruby_implicit_return_of_an_ivar_is_suppressed(self):
        src = "class A\n  def id\n    @id\n  end\nend\n"
        self.assertSuppressed("trivial-accessor", src, "@id", path="src/a.rb")

    def test_a_one_line_calculation_stays_mutable(self):
        # Same shape as a getter, except that it computes something.
        src = ("class Order:\n    def total(self):\n"
               "        return self.price * self.qty\n")
        self.assertMutable(src, "self.price * self.qty")

    def test_a_one_line_method_not_named_like_an_accessor_stays_mutable(self):
        # Same body shape as a getter -- one field read -- but nothing says
        # this is an accessor, so we do not get to assume it is one. Dropping
        # the name requirement would quietly suppress every one-line method
        # in the codebase that happens to end in a field.
        src = ("class Order:\n    def settle(self):\n"
               "        return self.pending_total\n")
        self.assertMutable(src, "return self.pending_total")

    def test_a_getter_with_a_real_body_stays_mutable(self):
        src = ("class Order:\n    def get_total(self):\n"
               "        if self._total is None:\n"
               "            self._total = self.price * self.qty\n"
               "        return self._total\n")
        self.assertMutable(src, "self.price * self.qty")


# --------------------------------------------------------------------------
# rule 4: pure delegation
# --------------------------------------------------------------------------

class TestPureDelegation(AridCase):
    def test_python_forwarding_call_is_suppressed(self):
        src = ("class Service:\n    def save(self, order):\n"
               "        return self._repo.save(order)\n")
        self.assertSuppressed("pure-delegation", src, "_repo.save(order)")

    def test_js_forwarding_call_is_suppressed(self):
        src = "class A {\n  add(item) { return this._items.push(item); }\n}\n"
        self.assertSuppressed("pure-delegation", src, "push(item)",
                              path="src/a.js")

    def test_a_call_with_an_extra_argument_stays_mutable(self):
        # Something was decided on this line, so it is not a hand-off.
        src = ("class Service:\n    def save(self, order):\n"
               "        return self._repo.save(order, now())\n")
        self.assertMutable(src, "_repo.save(order, now())")

    def test_a_call_that_transforms_an_argument_stays_mutable(self):
        src = ("class Service:\n    def save(self, order):\n"
               "        return self._repo.save(order.total * 2)\n")
        self.assertMutable(src, "_repo.save(order.total * 2)")


# --------------------------------------------------------------------------
# rule 5: human-facing string formatting
# --------------------------------------------------------------------------

class TestHumanStringFormatting(AridCase):
    def test_repr_body_is_suppressed(self):
        src = ("class Order:\n    def __repr__(self):\n"
               "        return \"Order(%d)\" % (self.id + 1)\n")
        self.assertSuppressed("human-string-formatting", src, "Order(%d)")

    def test_java_tostring_is_suppressed(self):
        src = "class A {\n  public String toString() { return \"A\" + id; }\n}\n"
        self.assertSuppressed("human-string-formatting", src, "toString",
                              path="src/A.java")

    def test_a_business_formatter_stays_mutable(self):
        # Formatting that produces a value the program uses is behaviour.
        src = ("class Invoice:\n    def format_number(self, seq):\n"
               "        return \"INV-%04d\" % (seq + 1)\n")
        self.assertMutable(src, "INV-%04d")


# --------------------------------------------------------------------------
# rule 6: error-message constants
# --------------------------------------------------------------------------

class TestErrorMessages(AridCase):
    def test_raise_with_a_message_is_suppressed(self):
        src = "def f(x):\n    raise ValueError(\"bad %d\" % (x + 1))\n"
        self.assertSuppressed("error-message-constant", src, "raise ValueError")

    def test_js_throw_is_suppressed(self):
        src = "function f(x) {\n  throw new Error(\"bad \" + x);\n}\n"
        self.assertSuppressed("error-message-constant", src, "throw new Error",
                              path="src/a.js")

    def test_the_condition_that_decides_to_raise_stays_mutable(self):
        src = ("def f(amount):\n"
               "    if amount < 0:\n"
               "        raise ValueError(\"negative\")\n"
               "    return amount\n")
        self.assertMutable(src, "amount < 0")


# --------------------------------------------------------------------------
# rule 7: timing and clocks
# --------------------------------------------------------------------------

class TestTimingAndClocks(AridCase):
    def test_sleep_is_suppressed(self):
        src = "def f(x):\n    time.sleep(2)\n    return x * 2\n"
        self.assertSuppressed("timing-or-clock", src, "time.sleep")

    def test_a_timer_read_is_suppressed(self):
        src = "def f(x):\n    started_at = time.time()\n    return x * 2\n"
        self.assertSuppressed("timing-or-clock", src, "started_at")

    def test_an_elapsed_calculation_is_suppressed(self):
        src = ("def f(x):\n    t0 = time.time()\n"
               "    elapsed = time.time() - t0\n    return x\n")
        self.assertSuppressed("timing-or-clock", src, "elapsed =")

    def test_a_business_deadline_from_the_clock_stays_mutable(self):
        # Not instrumentation: when something stops being valid is behaviour.
        src = "def f(ttl):\n    expires_at = time.time() + ttl\n    return expires_at\n"
        self.assertMutable(src, "expires_at =")

    def test_a_comparison_against_a_clock_value_stays_mutable(self):
        src = ("def expired(now, expires_at):\n"
               "    if now > expires_at:\n        return True\n    return False\n")
        self.assertMutable(src, "now > expires_at")


# --------------------------------------------------------------------------
# rule 8: cache lookups
# --------------------------------------------------------------------------

class TestCacheLookups(AridCase):
    def test_a_cache_read_is_suppressed(self):
        src = "def f(key):\n    hit = cache.get(key)\n    return hit\n"
        self.assertSuppressed("cache-lookup", src, "cache.get")

    def test_a_cache_write_is_suppressed(self):
        src = "def f(key, value):\n    _cache[key] = value\n    return value\n"
        self.assertSuppressed("cache-lookup", src, "_cache[key]")

    def test_arithmetic_on_a_cache_setting_stays_mutable(self):
        # The name says cache; the line does arithmetic. Arithmetic wins.
        src = "def f(days):\n    cache_ttl = days * 86400\n    return cache_ttl\n"
        self.assertMutable(src, "cache_ttl =")

    def test_an_ordinary_get_stays_mutable(self):
        src = "def f(d, k):\n    value = d.get(k, 0)\n    return value + 1\n"
        self.assertMutable(src, "d.get(k, 0)")


# --------------------------------------------------------------------------
# rule 9: defensive null guards
# --------------------------------------------------------------------------

class TestDefensiveGuards(AridCase):
    def test_none_guard_returning_unchanged_is_suppressed(self):
        src = ("def f(x):\n    if x is None:\n        return x\n"
               "    return x * 2\n")
        self.assertSuppressed("defensive-guard", src, "if x is None")

    def test_the_guards_own_return_is_suppressed_too(self):
        src = ("def f(x):\n    if x is None:\n        return x\n"
               "    return x * 2\n")
        self.assertSuppressed("defensive-guard", src, "        return x")

    def test_go_err_check_is_suppressed(self):
        src = ("package x\n\nfunc f() error {\n\tif err != nil {\n"
               "\t\treturn err\n\t}\n\treturn nil\n}\n")
        self.assertSuppressed("defensive-guard", src, "err != nil",
                              path="src/a.go")

    def test_js_braceless_null_guard_is_suppressed(self):
        src = "function f(user) {\n  if (!user) return user;\n  return user.id;\n}\n"
        self.assertSuppressed("defensive-guard", src, "if (!user)",
                              path="src/a.js")

    def test_a_business_early_return_stays_mutable(self):
        src = ("def f(amount, limit):\n    if amount > limit:\n"
               "        return DENIED\n    return ALLOWED\n")
        self.assertMutable(src, "amount > limit")

    def test_a_null_check_with_a_real_consequence_stays_mutable(self):
        src = ("def f(x):\n    if x is None:\n        return compute_default()\n"
               "    return x * 2\n")
        self.assertMutable(src, "if x is None")


# --------------------------------------------------------------------------
# rule 11: declarations and constants
# --------------------------------------------------------------------------

class TestDeclarations(AridCase):
    def test_import_is_suppressed(self):
        src = "import os\nx = a * b\n"
        self.assertSuppressed("declaration", src, "import os")

    def test_literal_constant_is_suppressed(self):
        src = "MAX_RETRIES = 3\nx = a * b\n"
        self.assertSuppressed("declaration", src, "MAX_RETRIES")

    def test_java_static_final_is_suppressed(self):
        src = "class A {\n  private static final int SCALE = 2;\n}\n"
        self.assertSuppressed("declaration", src, "SCALE", path="src/A.java")

    def test_js_const_literal_is_suppressed(self):
        src = "const MAX = 10;\nlet y = a * b;\n"
        self.assertSuppressed("declaration", src, "const MAX", path="src/a.js")

    def test_interface_declaration_is_suppressed(self):
        src = "export interface Order {\n  total: number;\n}\n"
        self.assertSuppressed("declaration", src, "interface Order",
                              path="src/a.ts")

    def test_a_row_of_a_literal_table_is_suppressed(self):
        src = 'TABLE = {\n    "a": "alpha",\n}\nx = a * b\n'
        self.assertSuppressed("declaration", src, '"a": "alpha"')

    def test_a_constant_computed_from_another_stays_mutable(self):
        src = "BASE = 100\nLIMIT = BASE * 3\n"
        self.assertMutable(src, "LIMIT")

    def test_module_entry_guard_is_suppressed(self):
        src = "def main():\n    return 0\nif __name__ == \"__main__\":\n    main()\n"
        self.assertSuppressed("module-entry-guard", src, "__main__")


# --------------------------------------------------------------------------
# rule 12: equality and hash boilerplate
# --------------------------------------------------------------------------

class TestEqualityBoilerplate(AridCase):
    def test_python_eq_is_suppressed(self):
        src = ("class A:\n    def __eq__(self, other):\n"
               "        return self.id == other.id\n")
        self.assertSuppressed("equality-boilerplate", src, "self.id == other.id")

    def test_java_equals_is_suppressed(self):
        src = ("class A {\n  public boolean equals(Object o) { return id == 1; }\n}\n")
        self.assertSuppressed("equality-boilerplate", src, "equals(Object o)",
                              path="src/A.java")

    def test_a_business_comparison_method_stays_mutable(self):
        # compareTo-style ordering is business logic, not generated plumbing.
        src = ("class A:\n    def is_better_than(self, other):\n"
               "        return self.score > other.score\n")
        self.assertMutable(src, "self.score > other.score")


# --------------------------------------------------------------------------
# rule 13: unreachable code
# --------------------------------------------------------------------------

class TestUnreachableCode(AridCase):
    def test_a_line_after_a_return_is_suppressed(self):
        src = "def f(a, b):\n    return a * b\n    leftover = a + b\n"
        self.assertSuppressed("unreachable-code", src, "leftover")

    def test_a_line_after_a_raise_is_suppressed(self):
        src = "def f(a, b):\n    raise ValueError(\"x\")\n    leftover = a + b\n"
        self.assertSuppressed("unreachable-code", src, "leftover")

    def test_a_line_after_a_conditional_return_stays_mutable(self):
        # Ruby's trailing modifier: the return only sometimes happens.
        src = "def f(a, b)\n  return a if a > b\n  a * b\nend\n"
        self.assertMutable(src, "a * b", path="src/a.rb")

    def test_a_dedented_line_after_a_return_stays_mutable(self):
        src = ("def f(a, b):\n    if a:\n        return a\n"
               "    return a * b\n")
        self.assertMutable(src, "return a * b")


# --------------------------------------------------------------------------
# the fuzzy function-family matcher
# --------------------------------------------------------------------------

class TestFunctionFamilies(AridCase):
    def test_a_new_name_in_a_known_family_is_covered_without_a_new_rule(self):
        for call in ("emit_render_metrics(x)", "trace_request(x)",
                     "dump_state(x)", "report_progress(x)",
                     "recordLatency(x)", "instrument_call(x)"):
            with self.subTest(call=call):
                src = f"def f(x):\n    {call}\n    return x * 2\n"
                self.assertSuppressed("arid-function-family", src, call)

    def test_a_family_call_whose_result_is_used_stays_mutable(self):
        src = "def f(x):\n    sale = record_sale(x)\n    return sale\n"
        self.assertMutable(src, "record_sale")

    def test_an_unrelated_name_stays_mutable(self):
        src = "def f(x):\n    apply_discount(x + 1)\n    return x\n"
        self.assertMutable(src, "apply_discount")

    def test_a_word_merely_containing_a_family_token_stays_mutable(self):
        # `catalogue` contains "log"; the matcher works on whole words.
        src = "def f(x):\n    catalogue(x + 1)\n    return x\n"
        self.assertMutable(src, "catalogue")


# --------------------------------------------------------------------------
# one mutant per line
# --------------------------------------------------------------------------

class TestOneMutantPerLine(unittest.TestCase):
    SRC = ("def f(a, b, c):\n"
           "    if a > b and c < a + b:\n"
           "        return a * b\n"
           "    return c\n")

    def test_a_line_with_several_applicable_operators_yields_one(self):
        entry = analyse(self.SRC)
        line = _line_of(self.SRC, "a > b and")
        chosen = [s for s in entry["selected"] if s["line"] == line]
        self.assertEqual(1, len(chosen))
        self.assertGreater(len(chosen[0]["operators"]), 1,
                           "fixture must have several operators to choose from")

    def test_no_line_appears_twice_in_the_selection(self):
        entry = analyse("x = a * b > c\n" * 30)
        lines = [s["line"] for s in entry["selected"]]
        self.assertEqual(len(lines), len(set(lines)))

    def test_only_the_five_google_operators_are_ever_used(self):
        src = "".join(f"def f{i}(a, b):\n    return a * b + {i} > b and a\n"
                      for i in range(60))
        entry = analyse(src)
        used = {s["operator"] for s in entry["selected"]}
        self.assertTrue(used)
        self.assertTrue(used <= {"SBR", "UOI", "LCR", "ROR", "AOR"},
                        f"unexpected operator in {used}")

    def test_selection_leans_toward_ror_and_away_from_uoi(self):
        # Same line shape 300 times with different names, so the only thing
        # varying is the draw. ROR is weighted 4x against UOI's 1x.
        src = "".join(f"def f{i}(a, b):\n    if a{i} > b{i}:\n        return 1\n"
                      for i in range(300))
        entry = analyse(src)
        counts = {}
        for s in entry["selected"]:
            if "ROR" in s["operators"] and "UOI" in s["operators"]:
                counts[s["operator"]] = counts.get(s["operator"], 0) + 1
        self.assertGreater(counts.get("ROR", 0), counts.get("UOI", 0) * 2,
                           f"weighting is not being applied: {counts}")
        self.assertGreater(counts.get("UOI", 0), 0,
                           "UOI must still be reachable, just rarer")


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

class TestDeterminism(unittest.TestCase):
    SRC = "".join(f"def f{i}(a, b):\n    return a{i} * b + {i} > b\n"
                  for i in range(80))

    def _ops(self, **kw):
        entry = analyse(self.SRC, **kw)
        return [(s["line"], s["operator"]) for s in entry["selected"]]

    def test_the_same_seed_gives_the_same_selection(self):
        self.assertEqual(self._ops(seed=7), self._ops(seed=7))

    def test_a_different_seed_gives_a_different_selection(self):
        self.assertNotEqual(self._ops(seed=1), self._ops(seed=2))

    def test_the_choice_does_not_depend_on_python_hash_randomisation(self):
        """Two fresh interpreters with different PYTHONHASHSEED must agree.

        This is the test that `hash()` or `random` would fail. An in-process
        comparison cannot see the difference, because both calls would share
        the same randomised seed -- so this one pays for a subprocess.
        """
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "import arid_nodes as an, json\n"
            "src = ''.join('def f%%d(a, b):\\n    return a%%d * b + %%d > b\\n' %% (i, i, i)"
            " for i in range(40))\n"
            "e = an.analyse_file('src/fixed.py', src.splitlines(True), 'python',"
            " seed=0, max_per_file=0)\n"
            "print(json.dumps([(s['line'], s['operator']) for s in e['selected']]))\n"
            % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
        )
        outs = []
        for hashseed in ("0", "1"):
            env = dict(os.environ, PYTHONHASHSEED=hashseed)
            outs.append(subprocess.run([sys.executable, "-c", script], env=env,
                                       capture_output=True, text=True,
                                       check=True).stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertTrue(json.loads(outs[0]))

    def test_editing_one_line_does_not_re_roll_the_others(self):
        before = dict(self._ops(seed=3))
        edited = self.SRC.replace("def f40(a, b):", "def f40(a, b, c):")
        after = dict((s["line"], s["operator"])
                     for s in analyse(edited, seed=3)["selected"])
        shared = [ln for ln in before if ln in after and ln != 81]
        self.assertEqual([before[ln] for ln in shared],
                         [after[ln] for ln in shared])


# --------------------------------------------------------------------------
# the per-file cap
# --------------------------------------------------------------------------

class TestPerFileCap(unittest.TestCase):
    SRC = "".join(f"def f{i}(a, b):\n    return a{i} * b{i}\n" for i in range(20))

    def test_default_cap_is_seven(self):
        entry = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python")
        self.assertEqual(7, len(entry["selected"]))

    def test_the_overflow_is_reported_not_silently_dropped(self):
        entry = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python")
        capped = [s for s in entry["suppressed"] if s["rule"] == "per-file-cap"]
        self.assertEqual(20 - 7, len(capped))
        self.assertEqual(20, len(entry["selected"]) + len(capped))

    def test_the_cap_is_configurable(self):
        entry = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python", max_per_file=3)
        self.assertEqual(3, len(entry["selected"]))

    def test_zero_disables_the_cap(self):
        entry = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python", max_per_file=0)
        self.assertEqual(20, len(entry["selected"]))

    def test_which_seven_survive_is_deterministic(self):
        def kept():
            e = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python", seed=5)
            return [s["line"] for s in e["selected"]]
        self.assertEqual(kept(), kept())

    def test_selected_lines_are_reported_in_file_order(self):
        entry = an.analyse_file("src/big.py", self.SRC.splitlines(keepends=True),
                                "python")
        lines = [s["line"] for s in entry["selected"]]
        self.assertEqual(sorted(lines), lines)

    def test_the_cap_does_not_collapse_the_operator_mix(self):
        """A big file must not surface seven mutants of one kind.

        Ranking the cap by operator productivity does exactly that -- the
        best operator wins every slot -- and it hides whole operator classes
        in precisely the files that have the most code in them.
        """
        src = "".join(
            f"def f{i}(a, b):\n    if a{i} > b:\n        return a * b + {i}\n"
            for i in range(60))
        entry = an.analyse_file("src/big.py", src.splitlines(keepends=True),
                                "python")
        self.assertGreater(len({s["operator"] for s in entry["selected"]}), 1)

    def test_the_summary_counts_the_cap_separately_from_the_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "big.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.SRC)
            result = an.plan([path])
        self.assertEqual(13, result["summary"]["suppressed_by_cap"])
        self.assertEqual(7, result["summary"]["selected"])


# --------------------------------------------------------------------------
# diff scoping
# --------------------------------------------------------------------------

DIFF = """diff --git a/src/billing.py b/src/billing.py
index 111..222 100644
--- a/src/billing.py
+++ b/src/billing.py
@@ -1,3 +1,5 @@
 def charge(a, b):
-    return a * b
+    total = a * b
+    if total > 100:
+        total = total - 5
     return total
"""


class TestDiffScoping(unittest.TestCase):
    def test_parse_diff_returns_added_lines_in_new_numbering(self):
        self.assertEqual({"src/billing.py": {2, 3, 4}}, an.parse_diff(DIFF))

    def test_deleted_lines_are_not_candidates(self):
        # Only `+` lines count: an author answers for what they added.
        self.assertNotIn(0, an.parse_diff(DIFF)["src/billing.py"])

    def test_a_new_file_is_fully_in_scope(self):
        diff = ("--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,2 @@\n"
                "+def f(a):\n+    return a * 2\n")
        self.assertEqual({"src/new.py": {1, 2}}, an.parse_diff(diff))

    def test_a_deleted_file_produces_no_entry(self):
        diff = "--- a/src/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n"
        self.assertEqual({}, an.parse_diff(diff))

    def test_paths_match_by_suffix_so_prefixes_do_not_matter(self):
        diff = {"src/billing.py": {2}}
        self.assertEqual({2}, an.changed_lines_for("repo/src/billing.py", diff))
        self.assertIsNone(an.changed_lines_for("src/other.py", diff))

    def test_only_changed_lines_are_considered(self):
        src = ("def charge(a, b):\n    total = a * b\n    if total > 100:\n"
               "        total = total - 5\n    return total\n")
        entry = analyse(src, changed={3})
        self.assertEqual([3], [c["line"] for c in entry["candidates"]])

    def test_a_file_absent_from_the_diff_is_skipped_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "untouched.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("def f(a, b):\n    return a * b\n")
            result = an.plan([path], diff={"src/other.py": {1}})
        self.assertEqual([], result["files"])
        self.assertTrue(result["settings"]["diff_scoped"])


# --------------------------------------------------------------------------
# the feedback loop
# --------------------------------------------------------------------------

class TestFeedback(AridCase):
    SRC = "def f(a, b):\n    total = a * b\n    return total\n"

    def test_a_node_type_marked_useless_is_suppressed(self):
        fb = an.Feedback([{"node_type": "assignment", "verdict": "useless"}])
        self.assertSuppressed("feedback", self.SRC, "total = a * b", feedback=fb)

    def test_a_pattern_marked_useless_is_suppressed(self):
        fb = an.Feedback([{"pattern": r"total\s*="}])
        self.assertSuppressed("feedback", self.SRC, "total = a * b", feedback=fb)

    def test_a_function_family_marked_useless_is_suppressed(self):
        src = "def f(x):\n    publish_event(x + 1)\n    return x\n"
        fb = an.Feedback([{"function": r"^publish_"}])
        self.assertSuppressed("feedback", src, "publish_event", feedback=fb)

    def test_an_operator_marked_useless_is_never_chosen(self):
        src = "".join(f"def f{i}(a, b):\n    if a{i} > b:\n        return 1\n"
                      for i in range(60))
        fb = an.Feedback([{"operator": "UOI"}])
        entry = analyse(src, feedback=fb)
        self.assertNotIn("UOI", {s["operator"] for s in entry["selected"]})

    def test_a_line_whose_only_operator_was_withdrawn_stops_being_a_candidate(self):
        src = "def f(a):\n    return a\n"
        entry = analyse(src, feedback=an.Feedback([{"operator": "SBR"}]))
        self.assertEqual([], [c["line"] for c in entry["candidates"]
                              if c["line"] == 2])

    def test_a_verdict_of_useful_suppresses_nothing(self):
        fb = an.Feedback([{"node_type": "assignment", "verdict": "useful"}])
        self.assertMutable(self.SRC, "total = a * b", feedback=fb)

    def test_feedback_suppressions_are_labelled_as_such(self):
        fb = an.Feedback([{"node_type": "assignment", "note": "we never read these"}])
        entry = analyse(self.SRC, feedback=fb)
        hit = [s for s in entry["suppressed"] if s["rule"] == "feedback"][0]
        self.assertTrue(hit["from_feedback"])
        self.assertEqual("we never read these", hit["why"])

    def test_a_built_in_rule_still_wins_over_feedback_labelling(self):
        # A line that is arid for a known reason is reported with that reason,
        # not as "a developer said so" -- the explanation has to be the real
        # one or the rule table stops being readable.
        src = "def f(x):\n    logger.info(x + 1)\n    return x\n"
        fb = an.Feedback([{"pattern": "logger"}])
        self.assertSuppressed("logging-or-telemetry", src, "logger.info",
                              feedback=fb)

    def test_a_broken_regex_is_recorded_rather_than_crashing_the_run(self):
        fb = an.Feedback([{"pattern": "([unclosed"}])
        self.assertEqual(1, len(fb.ignored))
        self.assertMutable(self.SRC, "total = a * b", feedback=fb)

    def test_the_summary_records_what_feedback_was_applied(self):
        fb = an.Feedback([{"operator": "UOI"}, {"node_type": "call"},
                          {"rule": "logging-or-telemetry"}])
        summary = fb.summary()
        self.assertEqual(["UOI"], summary["operators"])
        self.assertEqual(["call"], summary["node_types"])
        self.assertEqual(["logging-or-telemetry"], summary["reasserted_rules"])

    def test_feedback_file_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for payload in ([{"node_type": "call"}],
                            {"entries": [{"node_type": "call"}]},
                            {"feedback": [{"node_type": "call"}]}):
                path = os.path.join(tmp, "feedback.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                with self.subTest(payload=type(payload).__name__):
                    self.assertEqual(["call"],
                                     sorted(an.load_feedback(path).node_types))

    def test_counts_of_feedback_suppressions_reach_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.py")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(self.SRC)
            result = an.plan([src],
                             feedback=an.Feedback([{"node_type": "assignment"}]))
        self.assertEqual(1, result["summary"]["suppressed_by_feedback"])


# --------------------------------------------------------------------------
# output shape
# --------------------------------------------------------------------------

class TestOutputShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls._tmp.name, "billing.py")
        with open(cls.path, "w", encoding="utf-8") as fh:
            fh.write("import os\n"
                     "def charge(a, b):\n"
                     "    logger.info(\"go\")\n"
                     "    return a * b\n")
        cls.result = an.plan([cls.path])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_schema_key_is_present_and_versioned(self):
        self.assertEqual("testsquad.aridnodes/1", self.result["schema"])

    def test_summary_carries_the_four_headline_numbers(self):
        s = self.result["summary"]
        for key in ("lines_considered", "suppressed_by_rule",
                    "suppressed_by_cap", "selected"):
            self.assertIn(key, s)

    def test_each_file_carries_candidates_suppressed_and_selected(self):
        entry = self.result["files"][0]
        for key in ("file", "language", "candidates", "suppressed", "selected"):
            self.assertIn(key, entry)

    def test_a_selected_entry_names_its_operator_and_explains_it(self):
        sel = self.result["files"][0]["selected"][0]
        self.assertIn(sel["operator"], an.OPERATORS)
        self.assertTrue(sel["why"])
        self.assertTrue(sel["snippet"])

    def test_a_suppressed_entry_names_its_rule_and_explains_it(self):
        sup = self.result["files"][0]["suppressed"][0]
        self.assertIn(sup["rule"], an.RULES)
        self.assertEqual(an.RULES[sup["rule"]]["why"], sup["why"])

    def test_candidates_cover_both_suppressed_and_selected_lines(self):
        entry = self.result["files"][0]
        candidate_lines = {c["line"] for c in entry["candidates"]}
        for s in entry["suppressed"] + entry["selected"]:
            self.assertIn(s["line"], candidate_lines)

    def test_every_rule_in_the_table_explains_itself_in_plain_language(self):
        for name, spec in an.RULES.items():
            with self.subTest(rule=name):
                self.assertGreater(len(spec["why"]), 80,
                                   "a one-line reason gets read as noise")

    def test_snippets_are_trimmed(self):
        entry = analyse("x = " + " + ".join(["aaaa"] * 60) + "\n")
        self.assertLessEqual(len(entry["candidates"][0]["snippet"]), 100)

    def test_the_whole_report_round_trips_through_json(self):
        # Caught by our own flake_smells `no-assertion` rule in its first
        # form, which was just `json.dumps(self.result)` -- that only fails
        # if dumps throws, which is a smoke test wearing a test's clothes.
        # The report is consumed as JSON by every downstream skill, so what
        # matters is that it survives the trip unchanged.
        restored = json.loads(json.dumps(self.result))
        self.assertEqual(restored, self.result)
        self.assertEqual(restored["schema"], "testsquad.aridnodes/1")
        self.assertIn("summary", restored)
        self.assertIn("files", restored)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "billing.py")
        with open(self.src, "w", encoding="utf-8") as fh:
            fh.write("def charge(a, b):\n    logger.info(\"x\")\n    return a * b\n")

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = an.main(argv)
        return code, buf.getvalue()

    def test_json_is_the_default_output(self):
        code, out = self._run([self.src])
        self.assertEqual(0, code)
        self.assertEqual("testsquad.aridnodes/1", json.loads(out)["schema"])

    def test_text_output_is_one_line_per_mutant(self):
        code, out = self._run([self.src, "--format", "text"])
        self.assertEqual(0, code)
        self.assertIn("billing.py:3", out)
        self.assertIn("logging-or-telemetry", out)

    def test_out_writes_a_file(self):
        dest = os.path.join(self.tmp.name, "plan.json")
        code, out = self._run([self.src, "--out", dest])
        self.assertEqual(0, code)
        self.assertEqual("", out)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual("testsquad.aridnodes/1", json.load(fh)["schema"])

    def test_a_missing_path_exits_two(self):
        code, _ = self._run([os.path.join(self.tmp.name, "nope.py")])
        self.assertEqual(2, code)

    def test_a_broken_feedback_file_exits_two(self):
        bad = os.path.join(self.tmp.name, "feedback.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        code, _ = self._run([self.src, "--feedback", bad])
        self.assertEqual(2, code)

    def test_seed_and_cap_reach_the_report(self):
        code, out = self._run([self.src, "--seed", "9", "--max-per-file", "2"])
        settings = json.loads(out)["settings"]
        self.assertEqual(9, settings["seed"])
        self.assertEqual(2, settings["max_per_file"])

    def test_diff_scoping_from_the_command_line(self):
        diff_path = os.path.join(self.tmp.name, "pr.diff")
        with open(diff_path, "w", encoding="utf-8") as fh:
            fh.write("--- a/billing.py\n+++ b/billing.py\n@@ -1,1 +1,3 @@\n"
                     " def charge(a, b):\n+    logger.info(\"x\")\n"
                     "+    return a * b\n")
        code, out = self._run([self.src, "--diff", diff_path])
        self.assertEqual(0, code)
        result = json.loads(out)
        self.assertTrue(result["settings"]["diff_scoped"])
        self.assertEqual([2, 3],
                         [c["line"] for c in result["files"][0]["candidates"]])

    def test_a_directory_is_walked(self):
        code, out = self._run([self.tmp.name])
        self.assertEqual(0, code)
        self.assertTrue(json.loads(out)["files"])


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

class TestRobustness(unittest.TestCase):
    def test_a_file_that_does_not_parse_still_produces_a_plan(self):
        src = "def f(a, b):\n    return a * b\n  oops(\n"
        entry = analyse(src)
        self.assertEqual("regex", entry["parsed_with"])
        self.assertTrue(entry["selected"])

    def test_a_parsing_file_uses_the_ast(self):
        self.assertEqual("ast", analyse("def f(a):\n    return a * 2\n")["parsed_with"])

    def test_comments_cannot_create_candidates(self):
        entry = analyse("# total = a * b > c\ndef f():\n    pass\n")
        self.assertEqual([], [c["line"] for c in entry["candidates"]
                              if c["line"] == 1])

    def test_a_string_mentioning_operators_cannot_create_candidates(self):
        entry = analyse('def f():\n    q = "a > b and c"\n')
        line = [c for c in entry["candidates"] if c["line"] == 2]
        self.assertEqual([["SBR"]], [c["operators"] for c in line])

    def test_an_empty_file_is_handled(self):
        entry = analyse("")
        self.assertEqual([], entry["selected"])

    def test_a_binary_file_is_skipped_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "blob.py")
            with open(path, "wb") as fh:
                fh.write(b"\x00\x01\x02")
            self.assertEqual([], an.plan([path])["files"])

    def test_an_unknown_extension_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notes.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("a * b > c\n")
            self.assertEqual([], an.plan([path])["files"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
