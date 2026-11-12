#!/usr/bin/env python3
"""Tests for env_safety_check.py.

Fixtures are real directory trees written to a temp dir, because the thing
under test is "what does this script conclude about a repository" -- mocking
the filesystem would test the mock.

Three groups matter more than the rest:

  * TestNeverEchoesASecret -- the redaction guarantee. This output is pasted
    into reports and PR comments. A scanner that prints what it finds has
    published the credential to everyone who can read the PR. These tests
    assert the literal secret, and every 8-character window of it, is absent
    from the serialized JSON.
  * TestFalsePositivesStaySilent -- every value a normal dev repo contains
    must produce nothing. A check that cries wolf gets ignored, and then the
    one real finding is ignored with it.
  * TestDetectorsFire -- the other half of that trade. Silence is only
    acceptable where a live credential cannot be hiding.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import env_safety_check as esc  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_repo(files: dict) -> tempfile.TemporaryDirectory:
    """files maps relative path -> str (written utf-8) or bytes (written raw)."""
    tmp = tempfile.TemporaryDirectory()
    for rel, content in files.items():
        full = os.path.join(tmp.name, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(full, mode, **kwargs) as fh:
            fh.write(content)
    return tmp


def scan_files(files: dict) -> dict:
    with make_repo(files) as path:
        return esc.scan(path)


def detectors(result: dict) -> set:
    return {f["detector"] for f in result["findings"]}


def one(files: dict, detector: str) -> dict:
    result = scan_files(files)
    matches = [f for f in result["findings"] if f["detector"] == detector]
    assert matches, f"{detector} did not fire; got {sorted(detectors(result))}"
    return matches[0]


def run_cli(argv: list) -> tuple:
    """main() with both streams captured, so the suite stays readable."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = esc.main(argv)
    return code, out.getvalue(), err.getvalue()


# These values are shaped like the real thing but are not real. They are
# chosen to share no 8-character run with any key name, detector name or
# explanation text, so the leak test below cannot pass by coincidence.
SECRET_STRIPE = "sk_live_51H8xQ2KbNcPqRtUvWxYz0123"
SECRET_AWS = "AKIAZZ7Q3MNBVCXZLKJH"
SECRET_TOKEN = "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f00"
SECRET_DB_PASSWORD = "Tr0ub4dor3Zyx9QwErTy"
SECRET_GITHUB = "ghp_9ZxWvUtSrQpOnMlKjIhGfEdCbA1234567"


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------

class TestRedaction(unittest.TestCase):
    def test_shows_four_characters_and_a_length(self):
        self.assertEqual(esc.redact(SECRET_STRIPE), "sk_l...(33 chars)")

    def test_short_values_disclose_nothing_at_all(self):
        # Four of twelve characters is a quarter of the secret.
        self.assertEqual(esc.redact("abcdefghijkl"), "...(12 chars)")

    def test_empty_value_is_labelled_not_blank(self):
        self.assertEqual(esc.redact(""), "(empty)")
        self.assertEqual(esc.redact("   "), "(empty)")

    def test_never_returns_more_than_four_characters_of_the_input(self):
        for value in [SECRET_STRIPE, SECRET_AWS, SECRET_TOKEN, SECRET_GITHUB,
                      "postgres://u:p@db.prod.acme.com/app", "a" * 300]:
            with self.subTest(value=value[:6]):
                preview = esc.redact(value)
                for size in range(5, min(len(value), 40) + 1):
                    self.assertNotIn(value[:size], preview)

    def test_no_window_of_the_secret_survives_redaction(self):
        preview = esc.redact(SECRET_TOKEN)
        for i in range(len(SECRET_TOKEN) - 7):
            self.assertNotIn(SECRET_TOKEN[i:i + 8], preview)

    def test_length_is_honest(self):
        self.assertIn("(33 chars)", esc.redact(SECRET_STRIPE))
        self.assertIn("(20 chars)", esc.redact(SECRET_AWS))


class TestNeverEchoesASecret(unittest.TestCase):
    """The single most important behaviour in this file.

    Findings travel into reports and PR comments. If any part of a value
    reaches the JSON, the scanner has leaked the credential to a wider
    audience than the repo it found it in.
    """

    @classmethod
    def setUpClass(cls):
        cls.result = scan_files({
            ".env": (
                f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"
                f"AWS_ACCESS_KEY_ID={SECRET_AWS}\n"
                f"ANALYTICS_TOKEN={SECRET_TOKEN}\n"
                f"GITHUB_TOKEN={SECRET_GITHUB}\n"
                f"DATABASE_URL=postgres://appuser:{SECRET_DB_PASSWORD}"
                f"@db.prod.acme.com:5432/appdb\n"
            ),
            "package.json": json.dumps(
                {"scripts": {"e2e": f"curl -H 'auth: {SECRET_TOKEN}' https://api.acme.com"}}
            ),
        })
        cls.payload = json.dumps(cls.result, indent=2)

    def test_the_scan_actually_found_things(self):
        # Guards against this whole class passing because nothing fired.
        self.assertGreaterEqual(len(self.result["findings"]), 4)

    def test_literal_secret_values_are_absent_from_the_json(self):
        for secret in [SECRET_STRIPE, SECRET_AWS, SECRET_TOKEN,
                       SECRET_GITHUB, SECRET_DB_PASSWORD]:
            with self.subTest(secret=secret[:6]):
                self.assertNotIn(secret, self.payload)

    def test_no_eight_character_window_of_any_secret_appears(self):
        # Substring, not equality: a scanner that printed "half" the key
        # would still pass an equality check and still leak.
        for secret in [SECRET_STRIPE, SECRET_AWS, SECRET_TOKEN,
                       SECRET_GITHUB, SECRET_DB_PASSWORD]:
            for i in range(len(secret) - 7):
                window = secret[i:i + 8]
                with self.subTest(window=i):
                    self.assertNotIn(window, self.payload)

    def test_source_lines_are_never_included(self):
        for finding in self.result["findings"]:
            self.assertNotIn("=", finding["redacted_preview"])
            for field in ("why", "detector", "key"):
                self.assertNotIn(SECRET_TOKEN[:12], finding[field])

    def test_findings_still_locate_the_problem(self):
        # Redaction must not make the report useless: file, line, key name
        # and detector are what a human needs to go and look.
        f = self.result["findings"][0]
        for field in ("severity", "file", "line", "key", "detector",
                      "redacted_preview", "why"):
            self.assertIn(field, f)
        keys = {f["key"] for f in self.result["findings"]}
        self.assertIn("STRIPE_SECRET_KEY", keys)

    def test_every_finding_carries_a_plain_language_reason(self):
        for f in self.result["findings"]:
            self.assertGreater(len(f["why"].split()), 8, f["detector"])


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

class TestDetectorsFire(unittest.TestCase):
    def test_production_hostname(self):
        f = one({".env": "API_BASE_URL=https://api.production.acme.com\n"},
                "production_hostname")
        self.assertEqual(f["severity"], "CRITICAL")
        self.assertEqual(f["key"], "API_BASE_URL")
        self.assertEqual(f["line"], 1)

    def test_prod_abbreviation_in_hostname(self):
        f = one({".env": "HOST=app.prod.acme.com\n"}, "production_hostname")
        self.assertEqual(f["severity"], "CRITICAL")

    def test_plain_external_dot_com_in_config(self):
        f = one({".env": "API_URL=https://api.acmecorp.com/v2\n"}, "external_hostname")
        self.assertEqual(f["severity"], "CRITICAL")

    def test_remote_postgres_url(self):
        f = one({".env": "DATABASE_URL=postgres://u:p@db.acmecorp.com:5432/app\n"},
                "remote_database_url")
        self.assertEqual(f["severity"], "CRITICAL")
        self.assertIn("PostgreSQL", f["why"])

    def test_remote_mysql_url(self):
        self.assertEqual(
            one({".env": "DB=mysql://u:p@mysql.acmecorp.com/app\n"},
                "remote_database_url")["severity"], "CRITICAL")

    def test_mongodb_srv_is_always_remote(self):
        # +srv resolves through DNS to an Atlas cluster; there is no local
        # form of it, so it is flagged regardless of the hostname.
        self.assertEqual(
            one({".env": "MONGO=mongodb+srv://u:p@cluster0.mongodb.net/app\n"},
                "remote_database_url")["severity"], "CRITICAL")

    def test_stripe_live_secret_key(self):
        f = one({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"}, "stripe_live_key")
        self.assertEqual(f["severity"], "CRITICAL")
        self.assertIn("real charges", f["why"])

    def test_stripe_live_publishable_key(self):
        self.assertEqual(
            one({".env": "STRIPE_PK=pk_live_51H8xQ2KbNcPqRtUvWxYz\n"},
                "stripe_live_key")["severity"], "CRITICAL")

    def test_paypal_live_endpoint(self):
        self.assertEqual(
            one({".env": "PAYPAL_API=https://api-m.paypal.com\n"},
                "paypal_live_credential")["severity"], "CRITICAL")

    def test_braintree_production_environment(self):
        self.assertEqual(
            one({".env": "BRAINTREE_ENVIRONMENT=production\nBRAINTREE_MERCHANT=abc\n"},
                "braintree_production")["severity"], "CRITICAL")

    def test_real_smtp_relay(self):
        for host in ["smtp.gmail.com", "smtp.sendgrid.net", "api.mailgun.net",
                     "email-smtp.us-east-1.amazonaws.com"]:
            with self.subTest(host=host):
                f = one({".env": f"SMTP_HOST={host}\n"}, "live_smtp_host")
                self.assertEqual(f["severity"], "CRITICAL")

    def test_aws_access_key(self):
        f = one({".env": f"AWS_ACCESS_KEY_ID={SECRET_AWS}\n"}, "aws_access_key")
        self.assertEqual(f["severity"], "HIGH")

    def test_gcp_service_account(self):
        self.assertEqual(
            one({".env": 'GOOGLE_CREDENTIALS={"type": "service_account"}\n'},
                "gcp_service_account")["severity"], "HIGH")

    def test_azure_connection_string(self):
        self.assertEqual(
            one({".env": "AZURE_STORAGE=DefaultEndpointsProtocol=https;"
                         "AccountName=acme;AccountKey=aGVsbG93b3JsZGhlbGxvd29ybGQxMjM0NTY=\n"},
                "azure_connection_string")["severity"], "HIGH")

    def test_vendor_token_prefixes(self):
        for value in [SECRET_GITHUB, "glpat-ZyXwVuTsRqPoNmLk",
                      "xoxb-1234567890-ABCDEFGHIJKLMN",
                      "SG.ZyXwVuTsRqPoNmLkJi.ZyXwVuTsRqPoNmLkJiHgFe"]:
            with self.subTest(value=value[:6]):
                f = one({".env": f"SOME_TOKEN={value}\n"}, "vendor_api_token")
                self.assertEqual(f["severity"], "HIGH")

    def test_long_high_entropy_value_on_a_credential_key(self):
        for key in ["SESSION_SECRET", "API_TOKEN", "ADMIN_PASSWORD", "ENCRYPTION_KEY"]:
            with self.subTest(key=key):
                f = one({".env": f"{key}={SECRET_TOKEN}\n"}, "live_looking_secret")
                self.assertEqual(f["severity"], "HIGH")

    def test_curl_in_a_package_json_test_script(self):
        f = one({"package.json": json.dumps(
            {"scripts": {"test": "curl -X POST http://seed.acmecorp.com && jest"}})},
            "network_in_test_command")
        self.assertEqual(f["severity"], "MEDIUM")
        self.assertEqual(f["key"], "test")

    def test_shelling_out_to_curl_is_medium_even_against_localhost(self):
        # Non-hermetic, not catastrophic: the suite depends on a service
        # being up. Escalating this to CRITICAL is how the check gets muted.
        f = one({"package.json": json.dumps(
            {"scripts": {"test": "curl -sf http://localhost:3000/health && jest"}})},
            "network_in_test_command")
        self.assertEqual(f["severity"], "MEDIUM")

    def test_real_url_in_a_ci_run_step_is_medium_not_critical(self):
        # A command that fetches something is "not hermetic"; it is not the
        # same risk as a config file aiming the whole suite at a live system.
        result = scan_files({".github/workflows/ci.yml":
                             "jobs:\n  t:\n    steps:\n"
                             "      - run: wget https://fixtures.acmecorp.com/data.json\n"})
        self.assertIn("network_in_test_command", detectors(result))
        self.assertEqual(result["verdict"], "CAUTION")

    def test_a_ci_env_block_is_configuration_not_a_command(self):
        # env: sits inside a step, often right after a run:. Reading it as
        # part of the command would quietly demote the single most dangerous
        # thing a workflow can contain to MEDIUM.
        f = one({".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: npm test\n"
            "        env:\n"
            "          API_URL: https://api.acmecorp.com\n"
        )}, "external_hostname")
        self.assertEqual(f["severity"], "CRITICAL")
        self.assertEqual(f["key"], "API_URL")

    def test_prod_host_in_a_command_is_still_critical(self):
        result = scan_files({"package.json": json.dumps(
            {"scripts": {"seed": "curl https://api.prod.acme.com/reset"}})})
        self.assertIn("production_hostname", detectors(result))
        self.assertEqual(result["verdict"], "UNSAFE")

    def test_env_example_placeholders_are_low_and_informational(self):
        result = scan_files({".env.example": "API_TOKEN=your-key-here\nDB_PASSWORD=changeme\n"})
        self.assertEqual([f["severity"] for f in result["findings"]], ["LOW"])
        self.assertEqual(result["verdict"], "SAFE")
        self.assertIn("checked", result["findings"][0]["why"])

    def test_a_real_key_committed_to_a_template_still_fires(self):
        # The most dangerous template is the one that stopped being one.
        result = scan_files({".env.sample": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertIn("stripe_live_key", detectors(result))
        self.assertEqual(result["verdict"], "UNSAFE")

    def test_template_suppresses_only_the_low_severity_noise(self):
        # A template is SUPPOSED to list external-looking example URLs.
        result = scan_files({".env.template": "API_URL=https://api.acmecorp.com\n"})
        self.assertNotIn("external_hostname", detectors(result))


class TestEveryScannedSource(unittest.TestCase):
    """Each configured source is actually read. A source silently not
    scanned is indistinguishable from a source that was clean."""

    def test_sources_are_discovered(self):
        files = {
            ".env": "A=1\n",
            ".env.local": "B=2\n",
            ".github/workflows/ci.yml": "name: ci\n",
            "bitbucket-pipelines.yml": "image: node\n",
            ".gitlab-ci.yml": "stages: [test]\n",
            ".circleci/config.yml": "version: 2.1\n",
            "jest.config.js": "module.exports = {}\n",
            "playwright.config.ts": "export default {}\n",
            "cypress.config.js": "module.exports = {}\n",
            "pytest.ini": "[pytest]\n",
            "conftest.py": "import os\n",
            "application-prod.yml": "server:\n",
            "appsettings.Production.json": "{}\n",
            "docker-compose.yml": "services:\n",
            "docker-compose.override.yml": "services:\n",
            "package.json": "{}\n",
            "README.md": "https://api.prod.acme.com\n",
            "src/app.js": "const url = 'https://api.prod.acme.com'\n",
        }
        result = scan_files(files)
        scanned = set(result["scanned_files"])
        for rel in files:
            if rel in ("README.md", "src/app.js"):
                # Source and docs are out of scope: this script answers
                # "what is this test run configured against", not "does the
                # product talk to production", which it obviously does.
                self.assertNotIn(rel, scanned)
            else:
                self.assertIn(rel, scanned, rel)

    def test_db_url_is_found_in_each_config_kind(self):
        value = "postgres://u:p@db.acmecorp.com:5432/app"
        for rel, content in [
            (".env", f"DATABASE_URL={value}\n"),
            ("docker-compose.yml",
             f"services:\n  app:\n    environment:\n      - DATABASE_URL={value}\n"),
            ("application.yml", f"spring:\n  datasource:\n    url: {value}\n"),
            ("conftest.py", f'DSN = "{value}"\n'),
            (".circleci/config.yml", f"jobs:\n  t:\n    environment:\n      DB: {value}\n"),
        ]:
            with self.subTest(rel=rel):
                self.assertIn("remote_database_url", detectors(scan_files({rel: content})))

    def test_package_json_dependencies_are_not_scanned(self):
        # Registry and homepage URLs in dependency metadata are not where a
        # test run gets its target, and flagging them is pure noise.
        result = scan_files({"package.json": json.dumps({
            "homepage": "https://acmecorp.com",
            "repository": {"url": "git+https://github.com/acme/app.git"},
            "dependencies": {"left-pad": "^1.0.0"},
            "scripts": {"test": "jest"},
        })})
        # A truly binary .env now yields a finding of its own: it could not
        # be checked, and silence there is indistinguishable from safety.
        self.assertTrue(
            all(f.get("detector") == "unreadable-sensitive-file"
                for f in result["findings"]),
            result["findings"])


# --------------------------------------------------------------------------
# false positives
# --------------------------------------------------------------------------

class TestFalsePositivesStaySilent(unittest.TestCase):
    """A check with a >10% false-positive rate stops being read, and then
    the real finding inside the noise is missed too. Every value here is
    one a normal development repo contains."""

    LOCAL_HOSTS = [
        "http://localhost:3000", "http://127.0.0.1:8080", "http://0.0.0.0:5000",
        "http://[::1]:9000", "https://api.myapp.local", "https://api.myapp.test",
        "https://example.com", "https://example.org", "http://test.com",
        "https://foo.invalid", "http://host.docker.internal:5432",
        "http://192.168.1.10:8000", "http://10.0.0.5", "http://172.16.0.2",
        "http://db", "http://redis:6379", "https://api.sandbox.paypal.com",
        "sandbox.smtp.mailtrap.io", "http://mailhog:8025",
    ]

    PLACEHOLDERS = [
        "xxx", "xxxxxxxxxxxxxxxxxxxxxxxx", "changeme", "your-key-here",
        "<your-api-key>", "${STRIPE_SECRET_KEY}", "{{ secret_key }}",
        "$(vault read secret)", "%API_TOKEN%", "***", "dummy", "fake-key",
        "placeholder", "TODO", "replace_me", "sk_test_dummy_value",
        "", "changeme123", "your_token_here", "[[token]]",
    ]

    def test_local_and_example_hosts_never_fire(self):
        for host in self.LOCAL_HOSTS:
            with self.subTest(host=host):
                result = scan_files({".env": f"API_URL={host}\n"})
                self.assertEqual(result["findings"], [],
                                 f"{host} produced {detectors(result)}")

    def test_placeholder_values_never_fire(self):
        for value in self.PLACEHOLDERS:
            with self.subTest(value=value):
                result = scan_files({".env": f"API_TOKEN={value}\nDB_PASSWORD={value}\n"})
                self.assertEqual(result["findings"], [],
                                 f"{value!r} produced {detectors(result)}")

    def test_template_interpolation_is_a_reference_not_a_secret(self):
        result = scan_files({".github/workflows/ci.yml":
                             "jobs:\n  t:\n    env:\n"
                             "      STRIPE_SECRET_KEY: ${{ secrets.STRIPE_SECRET_KEY }}\n"
                             "      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID}\n"})
        self.assertEqual(result["findings"], [])

    def test_interpolation_is_the_only_thing_keeping_these_quiet(self):
        # Each value would fire on shape alone; it is silent purely because
        # it is a reference to a secret rather than a secret.
        for value in ["${CI_SECRET_TOKEN_VALUE_1234}", "{{ vault_api_token_value_x }}",
                      "<PASTE_YOUR_LIVE_TOKEN_VALUE>"]:
            with self.subTest(value=value):
                result = scan_files({".env": f"API_TOKEN={value}\n"})
                self.assertEqual(result["findings"], [], f"{value} -> {detectors(result)}")

    def test_an_interpolated_hostname_is_not_a_target(self):
        result = scan_files({".env": "API_URL=https://api.${DEPLOY_ENV}.acmecorp.com\n"})
        self.assertEqual(result["findings"], [])

    def test_a_masked_value_is_not_a_live_key_even_with_a_live_prefix(self):
        # `sk_live_xxxxxxxx` in a committed file is documentation of the
        # shape, not a key. The mask is the whole signal.
        for value in ["sk_live_xxxxxxxxxxxxxxxx", "sk_live_************",
                      "pk_live_....................."]:
            with self.subTest(value=value):
                result = scan_files({".env": f"STRIPE_SECRET_KEY={value}\n"})
                self.assertEqual(result["findings"], [], f"{value} -> {detectors(result)}")

    def test_low_entropy_repetition_is_not_a_secret(self):
        # Long enough, three character classes, and still obviously typed by
        # a human. Length and charset alone would flag it; entropy is what
        # keeps it quiet.
        for value in ["Aa1Aa1Aa1Aa1Aa1Aa1Aa1Aa1", "Password1Password1Password1",
                      "abcabcabcabcabcabcabc123"]:
            with self.subTest(value=value):
                result = scan_files({".env": f"DB_PASSWORD={value}\n"})
                self.assertEqual(result["findings"], [], f"{value} -> {detectors(result)}")

    def test_a_dollar_sign_inside_a_real_secret_does_not_buy_silence(self):
        # The mirror image of the test above: `Ab$cdef...` is a password,
        # not a shell variable, and must not be mistaken for one.
        result = scan_files({".env": "SESSION_SECRET=Kj7$Wq2Nz9Pb4Xr6Td1Ms8Gv3\n"})
        self.assertIn("live_looking_secret", detectors(result))

    def test_ordinary_dev_repo_is_safe_end_to_end(self):
        result = scan_files({
            ".env": (
                "NODE_ENV=development\n"
                "PORT=3000\n"
                "DATABASE_URL=postgres://postgres:postgres@localhost:5432/app_dev\n"
                "REDIS_URL=redis://localhost:6379\n"
                "BASE_URL=http://localhost:3000\n"
                "STRIPE_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxx\n"
                "JWT_SECRET=dev-secret\n"
                "SMTP_HOST=mailhog\n"
                "SMTP_PORT=1025\n"
                "QA_EMAIL=qa@example.com\n"
                "# DATABASE_URL=postgres://u:p@db.prod.acme.com/app\n"
            ),
            "package.json": json.dumps({"scripts": {
                "test": "jest --coverage", "e2e": "playwright test"}}),
            "docker-compose.yml": (
                "services:\n"
                "  db:\n"
                "    image: postgres:15\n"
                "    environment:\n"
                "      POSTGRES_PASSWORD: postgres\n"
                "  app:\n"
                "    environment:\n"
                "      DATABASE_URL: postgres://postgres:postgres@db:5432/app\n"
            ),
            "jest.config.js": "module.exports = { testEnvironment: 'node' };\n",
            ".github/workflows/ci.yml": (
                "name: ci\n"
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - run: npm ci\n"
                "      - run: npm test\n"
            ),
        })
        self.assertEqual(result["verdict"], "SAFE", detectors(result))
        self.assertEqual(result["findings"], [])

    def test_ci_infrastructure_urls_are_not_production(self):
        result = scan_files({".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: curl -sL https://registry.npmjs.org/ping\n"
            "      - run: pip install -i https://pypi.org/simple pytest\n"
        )})
        self.assertNotIn("external_hostname", detectors(result))
        self.assertNotIn("production_hostname", detectors(result))

    def test_filenames_and_versions_are_not_hostnames(self):
        result = scan_files({".env": (
            "CONFIG=jest.config.js\n"
            "TSCONFIG=tsconfig.json\n"
            "APP_VERSION=1.2.3\n"
            "ENTRY=src/index.ts\n"
            "SCRIPT=scripts/run.sh\n"
        )})
        self.assertEqual(result["findings"], [])

    def test_an_email_address_is_not_a_server(self):
        result = scan_files({".env": "TEST_USER_EMAIL=qa.bot@acmecorp.com\n"})
        self.assertEqual(result["findings"], [])

    def test_short_or_wordy_values_are_not_mistaken_for_secrets(self):
        for value in ["postgres", "dev-password", "local only value",
                      "letmein", "s3cret", "/etc/ssl/key.pem", "./certs/dev.key"]:
            with self.subTest(value=value):
                result = scan_files({".env": f"DB_PASSWORD={value}\n"})
                self.assertEqual(result["findings"], [], value)

    def test_commented_out_lines_are_ignored(self):
        result = scan_files({".env": f"# STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual(result["findings"], [])

    def test_sandbox_marker_rescues_a_real_relay(self):
        result = scan_files({".env": "SMTP_HOST=smtp.sendgrid.net # sandbox mode\n"
                                     "SMTP_SANDBOX=true\n"})
        self.assertNotIn("live_smtp_host", detectors(result))


class TestHostClassification(unittest.TestCase):
    """Unit-level, because is_safe_host is the guard the whole
    false-positive story rests on."""

    def test_safe(self):
        for host in ["localhost", "127.0.0.1", "0.0.0.0", "::1", "api.myapp.local",
                     "api.myapp.test", "example.com", "www.example.org", "test.com",
                     "thing.invalid", "db", "postgres", "10.1.2.3", "192.168.0.1",
                     "172.20.3.4", "github.com", "registry.npmjs.org",
                     "api.sandbox.paypal.com"]:
            with self.subTest(host=host):
                self.assertTrue(esc.is_safe_host(host), host)

    def test_unsafe(self):
        for host in ["api.acmecorp.com", "db.prod.acme.com", "cluster0.mongodb.net",
                     "smtp.sendgrid.net", "app.acme.io", "acme.cloud"]:
            with self.subTest(host=host):
                self.assertFalse(esc.is_safe_host(host), host)

    def test_production_markers(self):
        for host in ["api.prod.acme.com", "production.acme.com", "acme-prod.io",
                     "prd.acme.com"]:
            with self.subTest(host=host):
                self.assertTrue(esc.is_prod_host(host), host)

    def test_production_marker_inside_a_word_does_not_count(self):
        # "reproduction" and "producer" contain "prod".
        for host in ["reproduction.acmecorp.com", "producer.acmecorp.com"]:
            with self.subTest(host=host):
                self.assertFalse(esc.is_prod_host(host), host)

    def test_userinfo_is_stripped_before_the_host_is_read(self):
        self.assertEqual(esc.host_of("user:pass@db.prod.acme.com:5432/app"),
                         "db.prod.acme.com")


# --------------------------------------------------------------------------
# verdict, counts and exit codes
# --------------------------------------------------------------------------

class TestVerdict(unittest.TestCase):
    def test_critical_makes_it_unsafe(self):
        result = scan_files({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual(result["verdict"], "UNSAFE")

    def test_high_alone_is_caution(self):
        result = scan_files({".env": f"AWS_ACCESS_KEY_ID={SECRET_AWS}\n"})
        self.assertEqual(result["counts"]["CRITICAL"], 0)
        self.assertEqual(result["verdict"], "CAUTION")

    def test_medium_alone_is_caution(self):
        result = scan_files({"package.json": json.dumps(
            {"scripts": {"test": "curl -s http://fixtures.acmecorp.com | jest"}})})
        self.assertEqual(result["counts"]["CRITICAL"], 0)
        self.assertEqual(result["verdict"], "CAUTION")

    def test_low_alone_is_still_safe(self):
        result = scan_files({".env.example": "API_TOKEN=changeme\n"})
        self.assertEqual(result["counts"]["LOW"], 1)
        self.assertEqual(result["verdict"], "SAFE")

    def test_nothing_is_safe(self):
        self.assertEqual(scan_files({".env": "PORT=3000\n"})["verdict"], "SAFE")

    def test_counts_match_the_findings(self):
        result = scan_files({
            ".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\nAWS_ACCESS_KEY_ID={SECRET_AWS}\n",
            ".env.example": "API_TOKEN=changeme\n",
        })
        for severity, count in result["counts"].items():
            self.assertEqual(
                count, len([f for f in result["findings"] if f["severity"] == severity]),
                severity)

    def test_findings_are_ordered_worst_first(self):
        result = scan_files({
            ".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\nAWS_ACCESS_KEY_ID={SECRET_AWS}\n",
            ".env.example": "API_TOKEN=changeme\n",
            "package.json": json.dumps({"scripts": {"test": "curl http://x.acmecorp.com"}}),
        })
        ranks = [esc.SEVERITY_RANK[f["severity"]] for f in result["findings"]]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_breaches_respects_the_threshold(self):
        high_only = scan_files({".env": f"AWS_ACCESS_KEY_ID={SECRET_AWS}\n"})
        self.assertFalse(esc.breaches(high_only, "critical"))
        self.assertTrue(esc.breaches(high_only, "high"))
        self.assertTrue(esc.breaches(high_only, "medium"))


class TestCli(unittest.TestCase):
    def test_clean_repo_exits_zero(self):
        with make_repo({".env": "PORT=3000\n"}) as path:
            code, _, _ = run_cli([path])
            self.assertEqual(code, 0)

    def test_critical_finding_exits_one_so_a_wrapper_can_stop(self):
        with make_repo({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"}) as path:
            code, _, _ = run_cli([path])
            self.assertEqual(code, 1)

    def test_fail_on_high_catches_what_critical_lets_through(self):
        with make_repo({".env": f"AWS_ACCESS_KEY_ID={SECRET_AWS}\n"}) as path:
            self.assertEqual(run_cli([path])[0], 0)
            self.assertEqual(run_cli([path, "--fail-on", "high"])[0], 1)

    def test_fail_on_medium(self):
        with make_repo({"package.json": json.dumps(
                {"scripts": {"test": "curl http://x.acmecorp.com"}})}) as path:
            self.assertEqual(run_cli([path, "--fail-on", "high"])[0], 0)
            self.assertEqual(run_cli([path, "--fail-on", "medium"])[0], 1)

    def test_missing_repo_exits_two_not_zero(self):
        # Exit 2 is "the check could not run", which is also not permission
        # to run the suite. It must never be confused with a clean 0.
        code, _, err = run_cli(["/nonexistent/path/for/testsquad"])
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_out_file_contains_the_full_schema(self):
        with make_repo({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"}) as path:
            with tempfile.TemporaryDirectory() as out_dir:
                out = os.path.join(out_dir, "env_safety.json")
                code, stdout, _ = run_cli([path, "--out", out])
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                with open(out) as fh:
                    data = json.load(fh)
        self.assertEqual(data["schema"], esc.SCHEMA)
        for field in ("generated_at", "repo", "verdict", "findings",
                      "scanned_files", "skipped_files"):
            self.assertIn(field, data)

    def test_stdout_is_valid_json(self):
        with make_repo({".env": "PORT=3000\n"}) as path:
            _, stdout, _ = run_cli([path])
        self.assertEqual(json.loads(stdout)["schema"], esc.SCHEMA)

    def test_console_summary_does_not_leak_either(self):
        with make_repo({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"}) as path:
            _, stdout, stderr = run_cli([path, "--out", os.devnull])
        self.assertNotIn(SECRET_STRIPE, stderr)
        for i in range(len(SECRET_STRIPE) - 7):
            self.assertNotIn(SECRET_STRIPE[i:i + 8], stderr)


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

class TestRobustness(unittest.TestCase):
    def test_empty_repo_is_safe_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as path:
            result = esc.scan(path)
        self.assertEqual(result["verdict"], "SAFE")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["scanned_files"], [])
        self.assertEqual(result["skipped_files"], [])

    def test_binary_file_is_skipped_not_crashed_on(self):
        # Skipped is not clean. A .env the gate could not decode is exactly
        # where a production target hides, so the skip raises a finding of
        # its own rather than leaving the file out of the report in silence.
        result = scan_files({".env.bin": b"\x00\x01\x02binary\x00junk"})
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env.bin"])
        self.assertNotIn(".env.bin", result["scanned_files"])
        self.assertEqual(detectors(result), {"unreadable_config"})
        unread = result["findings"][0]
        self.assertEqual(unread["file"], ".env.bin")
        self.assertEqual(unread["severity"], "HIGH")
        self.assertIn("NOT checked", unread["why"])
        # HIGH, so the verdict cannot come back SAFE on a file nobody read.
        self.assertEqual(result["verdict"], "CAUTION")
        self.assertTrue(esc.breaches(result, "high"))

    def test_skipped_files_are_reported_not_silently_counted_as_clean(self):
        # A .env the scanner could not read is exactly where the danger is.
        result = scan_files({".env.bin": b"\x00\x00"})
        self.assertTrue(result["skipped_files"][0]["reason"])

    def test_oversized_file_is_skipped(self):
        result = scan_files({".env.huge": "A=1\n" * 600_000})
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env.huge"])
        # A size ceiling that drops a file quietly is a cost control that has
        # become a blind spot. Too big to read has to leave the report as
        # "unknown", loudly -- never as SAFE.
        self.assertEqual(detectors(result), {"unreadable_config"})
        self.assertNotEqual(result["verdict"], "SAFE")
        self.assertIn("ceiling", result["findings"][0]["why"])

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_unreadable_file_is_skipped_gracefully(self):
        with make_repo({".env": "PORT=3000\n"}) as path:
            target = os.path.join(path, ".env")
            os.chmod(target, 0)
            try:
                result = esc.scan(path)
            finally:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        self.assertEqual([s["file"] for s in result["skipped_files"]], [".env"])

    def test_invalid_utf8_does_not_crash(self):
        result = scan_files({".env": b"API_URL=https://api.prod.acme.com\xff\xfe\n"})
        self.assertIn("production_hostname", detectors(result))

    def test_malformed_package_json_is_ignored_rather_than_fatal(self):
        result = scan_files({"package.json": "{ not json at all,,,"})
        self.assertEqual(result["findings"], [])

    def test_vendored_directories_are_never_walked(self):
        payload = f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"
        result = scan_files({
            "node_modules/pkg/.env": payload,
            "vendor/pkg/.env": payload,
            "dist/.env": payload,
            ".git/.env": payload,
            "build/.env": payload,
        })
        self.assertEqual(result["scanned_files"], [])
        self.assertEqual(result["findings"], [])

    def test_nested_project_configs_are_still_found(self):
        result = scan_files({"apps/api/.env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual(result["findings"][0]["file"], "apps/api/.env")

    def test_line_numbers_point_at_the_right_line(self):
        result = scan_files({".env": "A=1\nB=2\n\n# comment\n"
                                     f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual(result["findings"][0]["line"], 5)

    def test_same_detector_on_one_line_reports_once(self):
        result = scan_files({".env": "HOSTS=https://a.prod.acme.com,https://b.prod.acme.com\n"})
        self.assertEqual(len(result["findings"]), 1)

    def test_a_command_matching_two_network_rules_reports_once(self):
        # `curl https://real.host` matches both the host rule and the
        # shell-tool rule. Two identical lines in a report is how a reader
        # learns to skim it.
        result = scan_files({"package.json": json.dumps(
            {"scripts": {"test": "curl https://api.acmecorp.com | jest"}})})
        self.assertEqual([f["detector"] for f in result["findings"]],
                         ["network_in_test_command"])

    def test_a_named_credential_is_not_also_reported_as_a_generic_one(self):
        # sk_live_ is already identified; adding "and it is also a long
        # high-entropy string" doubles the noise for no new information.
        result = scan_files({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual([f["detector"] for f in result["findings"]],
                         ["stripe_live_key"])

    def test_a_specific_critical_suppresses_the_vaguer_one(self):
        # One line, one finding, and it should be the one that names the
        # database -- not also a generic "external host".
        result = scan_files({".env": "DATABASE_URL=postgres://u:p@db.prod.acme.com/app\n"})
        self.assertEqual([f["detector"] for f in result["findings"]],
                         ["remote_database_url"])

    def test_output_is_json_serializable_and_stable(self):
        result = scan_files({".env": f"STRIPE_SECRET_KEY={SECRET_STRIPE}\n"})
        self.assertEqual(json.loads(json.dumps(result)), result)


# --------------------------------------------------------------------------
# catastrophic backtracking
# --------------------------------------------------------------------------

def _seconds(call) -> float:
    """Wall-clock cost of one call.

    The clock is read here and nowhere near an assertion, and `started` is
    never named in one, so this stays a measurement rather than the
    `real-clock` smell our own flake gate blocks on.
    """
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


class TestPathologicalInputCannotHangTheGate(unittest.TestCase):
    """Growth-curve regressions for the two superlinear regexes.

    This gate runs BEFORE the test command, so a regex that goes quadratic
    does not fail the run -- it stalls it, and a stall is indistinguishable
    from "still working". That makes these timing assertions, which are
    normally a smell, the only oracle that can see the defect at all.

    The ceilings are deliberately far from both sides of the measured range
    rather than tight, because a timing assertion with narrow headroom is a
    flake, and a check that cries wolf gets switched off. Each one carries
    the numbers it was set from. All measured on the machine of record,
    2026-11-12, Python 3.14, best of three.
    """

    # "a.a.a....a_" -- a dotted run that can never end in a usable TLD, so
    # every retry walks the whole remaining run. 32,769 characters.
    HOST_PAYLOAD = "a." * 16384 + "_"
    # A run of local-part characters with no "@" anywhere in it.
    EMAIL_PAYLOAD = "a" * 65536

    def test_email_stripping_does_not_backtrack_quadratically(self):
        # EMAIL_RE without its leading lookbehind restarts at every offset of
        # a local-part run and each restart scans to the end of it.
        # Measured: fixed 0.0005s, unanchored 3.24s -- a 6,000x spread. The
        # 1.0s ceiling sits ~2,000x above the fixed cost and ~3x below the
        # broken one, so only the quadratic shape can reach it.
        self.assertLess(
            _seconds(lambda: esc.EMAIL_RE.sub(" ", self.EMAIL_PAYLOAD)), 1.0,
            "EMAIL_RE has gone superlinear again -- check the lookbehind")

    def test_bare_host_matching_does_not_backtrack_quadratically(self):
        # BARE_HOST_RE is `(label\.)+tld`: a repeated group that can only
        # fail at its very end, so an unbounded `+` retries from every label.
        # Measured: fixed 0.084s, unbounded 5.88s -- a 70x spread. The 1.5s
        # ceiling is ~18x above the fixed cost and ~4x below the broken one.
        self.assertLess(
            _seconds(lambda: esc.BARE_HOST_RE.findall(self.HOST_PAYLOAD)), 1.5,
            "BARE_HOST_RE has gone superlinear again -- check MAX_HOST_LABELS")

    def test_depth_counting_does_not_backtrack_quadratically(self):
        # DOTTED_RUN_RE was added to keep the label bound honest, so it must
        # not reintroduce the defect it reports on. Without its lookbehind it
        # restarts at every offset of a dot-free run: measured 0.006s on 1k,
        # 0.098s on 4k, 0.427s on 8k -- 4x per doubling, so ~1.7s on the
        # 16,384-character payload below, against 0.0003s anchored. The 0.5s
        # ceiling sits well inside that gap from both sides.
        dot_free = "a" * 16384
        self.assertLess(
            _seconds(lambda: esc.deepest_dotted_run(dot_free)), 0.5,
            "DOTTED_RUN_RE has gone superlinear -- check its lookbehind")
        self.assertEqual(esc.deepest_dotted_run(dot_free), 0)

    def test_a_file_of_pathological_lines_finishes_and_stays_loud(self):
        # The per-line cost is what the bound actually buys: nothing caps how
        # many lines a 2MB file holds, so a constant per line is the whole
        # defence. Measured: 40 such lines 0.52s fixed, ~3.5s unbounded.
        # The ceiling here is a hang detector, not a curve detector -- the two
        # tests above own the curve -- so it is loose on purpose.
        payload = "\n".join(f"A{i}=" + "a." * 2048 + "_" for i in range(40))
        out = {}
        with make_repo({".env": payload + "\n"}) as path:
            elapsed = _seconds(lambda: out.__setitem__("result", esc.scan(path)))
        result = out["result"]
        self.assertLess(elapsed, 10.0, "the gate stalled on a pathological config")
        # And it must not have gone quiet to get there: every one of the 40
        # lines says, in the report, which part of it was never examined.
        self.assertEqual(detectors(result),
                         {"host_scan_truncated", "host_labels_truncated"})
        self.assertEqual(len(result["findings"]), 80)
        self.assertNotEqual(result["verdict"], "SAFE")

    def test_a_value_past_the_host_scan_ceiling_is_reported_not_dropped(self):
        # The tail nobody matched against is not a tail with no production
        # host in it, and this is the case that proves it: the host is real,
        # it is past the ceiling, and the report still refuses to say SAFE.
        padding = "z" * (esc.MAX_HOST_SCAN_CHARS + 10)
        result = scan_files({".env": f"BLOB={padding} api.prod.acme.com\n"})
        self.assertIn("host_scan_truncated", detectors(result))
        self.assertNotEqual(result["verdict"], "SAFE")
        finding = [f for f in result["findings"]
                   if f["detector"] == "host_scan_truncated"][0]
        self.assertEqual(finding["severity"], "HIGH")
        self.assertIn("NOT examined", finding["why"])

    def test_the_label_bound_is_above_anything_dns_can_resolve(self):
        # RFC 1035 caps a hostname at 253 octets and the shortest label costs
        # two of them, so 126 labels is the ceiling of what a resolver will
        # accept. The bound has to sit at or above that, or it would be
        # shortening real hostnames rather than only the retry chain.
        self.assertGreaterEqual(esc.MAX_HOST_LABELS, 126)
        # Exactly at the bound: 126 labels and then the TLD.
        deep = ".".join(["a"] * (esc.MAX_HOST_LABELS - 2) + ["prod", "acme", "com"])
        self.assertEqual(deep.count(".") + 1, esc.MAX_HOST_COMPONENTS)
        hosts, truncated = esc.real_hosts_in(deep, bare=True)
        self.assertFalse(truncated)
        self.assertEqual(hosts, [deep])
        self.assertTrue(esc.is_prod_host(deep))
        self.assertEqual(esc.deepest_dotted_run(deep), esc.MAX_HOST_COMPONENTS)

    def test_a_run_too_deep_for_the_matcher_is_announced_not_dropped(self):
        # Past the bound the matcher lands on a "TLD" that is not the run's
        # last component, so the run can drop out of the host list entirely.
        # That is the one thing a cost control may never do quietly.
        too_deep = ".".join(["a"] * (esc.MAX_HOST_COMPONENTS + 4))
        self.assertEqual(esc.real_hosts_in(too_deep, bare=True)[0], [])
        result = scan_files({".env": f"BLOB={too_deep}\n"})
        self.assertIn("host_labels_truncated", detectors(result))
        self.assertNotEqual(result["verdict"], "SAFE")
        finding = [f for f in result["findings"]
                   if f["detector"] == "host_labels_truncated"][0]
        self.assertEqual(finding["severity"], "HIGH")
        self.assertIn("NOT fully", finding["why"])

    def test_an_ordinary_hostname_never_trips_the_depth_report(self):
        # The depth report is only tolerable because nothing real reaches it.
        for value in ("api.prod.acme.com", "a.b.c.svc.cluster.local",
                      "email-smtp.us-east-1.amazonaws.com", "localhost",
                      "1.2.3.4", "db.internal.corp.example.co.uk"):
            self.assertLessEqual(esc.deepest_dotted_run(value),
                                 esc.MAX_HOST_COMPONENTS, value)

    def test_a_deep_hostname_still_reaches_the_report(self):
        deep = ".".join(["sub"] * 30 + ["prod", "acme", "com"])
        result = scan_files({".env": f"API_URL=https://{deep}/v1\n"})
        self.assertIn("production_hostname", detectors(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
