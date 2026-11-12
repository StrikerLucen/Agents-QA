#!/usr/bin/env python3
"""Refuse to run a test suite that is pointed at production.

This runs BEFORE any test command. The failure mode it exists to prevent is
not hypothetical: this project's own e2e notes describe suites that share a
*production* test company, so a test run that picks up the wrong .env does
not fail loudly -- it quietly writes to, and deletes, real customer data. A
test command is irreversible in a way a build never is, so the gate belongs
in front of the command, not in the report afterwards.

What it does: reads the places a test run actually gets its target from
(.env files, CI configs, test-runner configs, docker-compose, package.json
scripts) and looks for evidence that the target is live -- production
hostnames, real database URLs, live payment keys, real SMTP relays, cloud
credentials and long high-entropy secrets.

TWO DESIGN RULES, both of which cost something:

1. NEVER ECHO A SECRET. Findings carry the file, the line, the KEY NAME, the
   detector that fired and a redacted preview (first four characters plus a
   length, e.g. `sk_l...(31 chars)`) -- never the value, and never the source
   line. This output is written into reports and pasted into PR comments
   that may be public, so a scanner that leaks what it finds is worse than
   no scanner at all. Even hostnames are redacted, because URLs routinely
   carry credentials in their userinfo (postgres://user:pass@host).
   The cost: a finding tells you where to look, not what is there. That is
   the intended trade -- the human opens the file, the report does not.

2. ERR TOWARD SILENCE, BUT NEVER PAST A LIVE CREDENTIAL. A check with a
   false-positive rate above roughly 10% stops being read; people learn the
   noise and then miss the one real finding inside it (the same effect that
   makes static-analysis adoption fail -- see docs/RESEARCH.md on warning
   fatigue). So localhost, private ranges, *.local, *.test, example.com,
   package-registry and CI infrastructure hosts, template interpolation
   (`${...}`, `{{...}}`, `<...>`) and obvious placeholders (`changeme`,
   `your-key-here`, `xxx`, `dummy`) are deliberately silent. The tie-break
   is explicit: where silence and a missed live credential are both
   possible, we flag. The suppressions above are all cases where a live
   credential CANNOT be hiding -- a value of `${DB_URL}` is not a secret,
   it is a reference to one. Suppression is by shape of the value, never by
   "this looks unimportant".
   Accepted blind spots, stated rather than hidden: RFC1918 addresses are
   treated as non-production, and the template-value suppression would hide
   a real secret deliberately disguised as a placeholder.

Severity maps to consequence, not to confidence:
  CRITICAL  a test run could reach live data or move real money
  HIGH      credentials present that would let it, if a test used them
  MEDIUM    the suite talks to the network at all, so it is not hermetic
  LOW       informational; a template file that contains only placeholders

Verdict: UNSAFE if anything is CRITICAL, CAUTION for HIGH/MEDIUM, SAFE
otherwise. Exit 1 when --fail-on is breached so a wrapper script can stop
before it runs the suite; 2 means the check itself could not run, which is
also not permission to proceed.

Standard library only: this runs before anything may be installed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

if __package__ in (None, ""):  # run as a script: scripts/ is already sys.path[0]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atomic_io import write_text_atomic  # noqa: E402

SCHEMA = "testsquad.envsafety/1"

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# Never walked into. Vendored trees are full of other people's example
# credentials, and scanning them produces pages of findings about code the
# test run will never configure itself from.
SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "dist", "build", "out", "target",
    "coverage", "__pycache__", ".venv", "venv", ".tox", ".next", ".nuxt",
    ".gradle", ".idea", ".mypy_cache", ".pytest_cache", "Pods",
})

MAX_BYTES = 2 * 1024 * 1024  # a config file this large is generated, not written


# --------------------------------------------------------------------------
# which files feed a test run
# --------------------------------------------------------------------------

CI_NAMES = frozenset({"bitbucket-pipelines.yml", "bitbucket-pipelines.yaml",
                      ".gitlab-ci.yml", ".gitlab-ci.yaml"})

TEST_CONFIG_RE = re.compile(
    r"""^(
        jest\.config\.[a-z]+
      | playwright\.config\.[a-z]+
      | playwright[.\-][\w.\-]*config\.[a-z]+
      | cypress\.config\.[a-z]+
      | cypress\.json
      | pytest\.ini
      | tox\.ini
      | conftest\.py
      | application[\w.\-]*\.ya?ml
      | appsettings[\w.\-]*\.json
    )$""",
    re.VERBOSE | re.IGNORECASE,
)

COMPOSE_RE = re.compile(r"^(docker-)?compose[\w.\-]*\.ya?ml$", re.IGNORECASE)

# Templates are checked too -- a real key committed to .env.example is still
# a real key -- but only the CRITICAL/HIGH detectors apply, because a
# template is *supposed* to be full of fake values.
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".tpl")


def classify(rel_path: str) -> str | None:
    """Return the kind of config this file is, or None to ignore it."""
    name = os.path.basename(rel_path)
    parts = rel_path.split("/")

    if name.startswith(".env") or name == "env":
        lowered = name.lower()
        if any(lowered.endswith(sfx) for sfx in TEMPLATE_SUFFIXES):
            return "env_template"
        return "env"

    if len(parts) >= 3 and parts[0] == ".github" and parts[1] == "workflows" \
            and name.lower().endswith((".yml", ".yaml")):
        return "ci"
    if name in CI_NAMES:
        return "ci"
    if len(parts) >= 2 and parts[0] == ".circleci" and name.lower().endswith((".yml", ".yaml")):
        return "ci"

    if TEST_CONFIG_RE.match(name):
        return "test_config"
    if COMPOSE_RE.match(name):
        return "compose"
    if name == "package.json":
        return "package_json"
    return None


# --------------------------------------------------------------------------
# hosts
# --------------------------------------------------------------------------

URL_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]{1,20})://([^\s\"'`,;)\]}\\]+)")

# A bare hostname is only recognised when its TLD is one we know, so that
# `jest.config.js`, `tsconfig.json` and `1.2.3` are not read as hosts. This
# is the single biggest false-positive source in a scanner like this.
COMMON_TLDS = frozenset("""
com net org io co dev app cloud ai info biz xyz site online store tech
us uk ca de fr es mx br au jp cn eu nl it se no fi pl ru za ar cl pe ve
gov edu mil
""".split())

BARE_HOST_RE = re.compile(
    r"\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+([a-zA-Z]{2,24}))\b"
)

# The lookbehind is the whole point. Without it the engine retries the match
# at EVERY offset of a long run of local-part characters, and each retry scans
# to the end of the run: cost grows with the square of the value's length.
# Measured on the unanchored version: 8k chars 0.3s, 16k 1.2s, 40k 1.9s,
# 60k 4.2s -- about 4x per doubling, which extrapolates to ~20 minutes on a
# single 2MB line, a line size MAX_BYTES happily accepts. Python has no
# possessive quantifiers, so the fix is to make the failing start positions
# unreachable: a match can only begin where a local part begins. The engine
# then tries one start per run instead of one per character.
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+"
    r"(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)

# Host matching (and only host matching) stops after this many characters of
# a single value. A hostname is at most 253 characters by RFC 1035, so a value
# longer than this is a minified bundle, a base64 blob or a generated fixture
# -- not a server the suite is pointed at. BARE_HOST_RE is `(label\.)+tld`, a
# repeated group around a bounded quantifier, which is superlinear in the
# length of a dotted run; this cap is what keeps it a constant per line.
# Nothing is silently dropped: a value this long is reported as truncated.
MAX_HOST_SCAN_CHARS = 4096

SAFE_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "0:0:0:0:0:0:0:1",
    "host.docker.internal", "localhost.localdomain", "broadcasthost",
})

SAFE_SUFFIXES = (
    ".local", ".test", ".localhost", ".invalid", ".example", ".localdomain",
    ".internal", ".svc", ".cluster.local",
)

SAFE_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "example.edu", "test.com",
    "domain.com", "yourdomain.com", "mydomain.com", "company.com",
})

# Infrastructure every build talks to. These are not where customer data
# lives, and flagging them is how a check earns the reputation that gets it
# switched off.
INFRA_DOMAINS = frozenset({
    "github.com", "raw.githubusercontent.com", "objects.githubusercontent.com",
    "api.github.com", "gitlab.com", "bitbucket.org", "registry.npmjs.org",
    "npmjs.com", "yarnpkg.com", "pypi.org", "files.pythonhosted.org",
    "crates.io", "rubygems.org", "packagist.org", "nuget.org",
    "repo1.maven.org", "repo.maven.apache.org", "maven.apache.org",
    "services.gradle.org", "plugins.gradle.org", "dl.google.com",
    "golang.org", "proxy.golang.org", "nodejs.org", "deb.debian.org",
    "archive.ubuntu.com", "alpinelinux.org", "docker.io", "hub.docker.com",
    "ghcr.io", "quay.io", "json-schema.org", "schemas.android.com",
    "w3.org", "www.w3.org", "schema.org", "codecov.io", "coveralls.io",
    "sonarcloud.io", "storage.googleapis.com", "chromedriver.storage.googleapis.com",
    "playwright.azureedge.net", "cdn.jsdelivr.net", "unpkg.com",
    "opensource.org", "spdx.org", "mozilla.org", "letsencrypt.org",
})

PRIVATE_IP_RE = re.compile(
    r"^(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|127\.\d+\.\d+\.\d+)$"
)

PROD_MARKER_RE = re.compile(r"(^|[.\-_])(prod|production|prd)([.\-_]|$)", re.IGNORECASE)


def host_of(url_rest: str) -> str:
    """Host from the part of a URL after `://`, with userinfo and port gone."""
    authority = re.split(r"[/?#]", url_rest, 1)[0]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    if authority.startswith("["):  # IPv6 literal
        return authority.split("]")[0] + "]"
    return authority.split(":")[0]


def is_safe_host(host: str) -> bool:
    """True when this host cannot be production by construction."""
    h = host.strip().strip(".").lower()
    if not h:
        return True
    if h in SAFE_HOSTS or h in SAFE_DOMAINS or h in INFRA_DOMAINS:
        return True
    if PRIVATE_IP_RE.match(h) or h.startswith("::") or h == "[::1]":
        return True
    if any(h.endswith(sfx) for sfx in SAFE_SUFFIXES):
        return True
    if any(h == d or h.endswith("." + d) for d in SAFE_DOMAINS | INFRA_DOMAINS):
        return True
    if SANDBOX_MARKER_RE.search(h):
        # A host that names itself a sandbox (api.sandbox.paypal.com,
        # sandbox.smtp.mailtrap.io) is the test double, not the real thing.
        return True
    if "." not in h:
        # A single label is a docker-compose service name (`db`, `redis`),
        # which resolves only inside the compose network.
        return True
    return False


def is_prod_host(host: str) -> bool:
    return bool(PROD_MARKER_RE.search(host)) and not is_safe_host(host)


def real_hosts_in(text: str, *, bare: bool) -> tuple[list[str], bool]:
    """(hosts that are not demonstrably local/example/infra, truncated?).

    `truncated` is True when the value was longer than MAX_HOST_SCAN_CHARS and
    only its head was examined. The caller must say so: a tail nobody looked
    at is not a tail with no production hostname in it.
    """
    found: list[str] = []
    seen = set()
    truncated = len(text) > MAX_HOST_SCAN_CHARS
    if truncated:
        text = text[:MAX_HOST_SCAN_CHARS]
    remainder = text
    for scheme, rest in URL_RE.findall(text):
        remainder = remainder.replace(scheme + "://" + rest, " ")
        host = host_of(rest)
        if host and not is_safe_host(host) and host.lower() not in seen:
            seen.add(host.lower())
            found.append(host)
    if bare:
        # Emails carry a domain that is not a target -- QA_EMAIL=qa@acme.com
        # is a login, not a server to point tests at.
        # The `in` test first: str.find is a linear scan in C, and no '@' means
        # no email, so the common case never enters the regex engine at all.
        # The cheap guard is the one that does most of the work here.
        if "@" in remainder:
            remainder = EMAIL_RE.sub(" ", remainder)
        for host, tld in BARE_HOST_RE.findall(remainder):
            if tld.lower() not in COMMON_TLDS:
                continue
            if is_safe_host(host) or host.lower() in seen:
                continue
            seen.add(host.lower())
            found.append(host)
    return found, truncated


# --------------------------------------------------------------------------
# placeholders
# --------------------------------------------------------------------------

INTERPOLATION_RE = re.compile(
    r"(\$\{[^}]*\}|\$\([^)]*\)|\{\{[^}]*\}\}|<[^>]{0,60}>|%[A-Za-z_][A-Za-z0-9_]*%"
    r"|#\{[^}]*\}|\[\[[^\]]*\]\])"
)

# A bare `$VAR` only counts as interpolation when it is the WHOLE value. A
# generated password often contains a `$`, and treating `Ab$cdef...` as a
# template reference would silence a real credential.
BARE_VAR_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")

# Word-boundary matching on purpose: a 32-char random key can easily contain
# the letters "test" by chance, but not `-test-` or `_test_`.
PLACEHOLDER_WORD_RE = re.compile(
    r"(^|[^A-Za-z0-9])("
    r"changeme|change_me|placeholder|dummy|fake|sample|todo|tbd|redacted|"
    r"replaceme|replace_me|yourkey|your_key|yourtoken|your_token|"
    r"yoursecret|your_secret|yourpassword|insert|example|n/?a|none|null|nil"
    r")($|[^A-Za-z0-9])",
    re.IGNORECASE,
)

# Pure masking only. Self-describing words like "secret" deliberately do NOT
# live here: `secrets.prod.acme.com` is a hostname, not a redaction, and
# treating it as one would silence the exact finding this script exists for.
MASK_RE = re.compile(r"(x{3,}|\*{3,}|\.{3,}|_{4,}|-{4,}|0{8,})", re.IGNORECASE)

EXACT_PLACEHOLDERS = frozenset({
    "", "-", "_", "?", "changeme", "change-me", "secret", "password", "pass",
    "token", "key", "apikey", "api-key", "value", "string", "true", "false",
    "test", "testing", "local", "dev", "development", "foo", "bar", "baz",
    "abc123", "123456", "admin", "root", "user", "guest", "your-key-here",
    "your_key_here", "<your-key-here>", "xxx", "***", "...", "n/a", "na",
    "none", "null", "nil", "undefined", "empty",
})


def is_interpolation(value: str) -> bool:
    """Template references cannot BE a credential -- they point at one."""
    v = value.strip()
    if not v:
        return True
    if INTERPOLATION_RE.search(v) or BARE_VAR_RE.match(v):
        return True
    return False


def is_placeholder(value: str) -> bool:
    """Full placeholder test, used by the heuristic detectors.

    The prefix detectors (sk_live_, AKIA...) use only is_interpolation plus
    the mask test, because a literal `sk_live_` prefix is not ambiguous and
    the word "test" appearing inside it must not buy it silence.
    """
    v = value.strip().strip("\"'").strip()
    if v.lower() in EXACT_PLACEHOLDERS:
        return True
    if is_interpolation(v):
        return True
    if MASK_RE.search(v):
        return True
    if PLACEHOLDER_WORD_RE.search(v):
        return True
    if re.match(r"^your[\-_.]", v, re.IGNORECASE) or v.lower().endswith("-here"):
        return True
    return False


def is_masked(value: str) -> bool:
    """Cheap suppression for the unambiguous-prefix detectors."""
    v = value.strip().strip("\"'").strip()
    return is_interpolation(v) or bool(MASK_RE.search(v))


# --------------------------------------------------------------------------
# redaction -- the part that must never regress
# --------------------------------------------------------------------------

def redact(value: str) -> str:
    """A preview that identifies a value without disclosing it.

    Four leading characters is enough for a human to recognise which key
    they are looking at (`sk_l`, `AKIA`, `post`) and far short of anything
    usable. Below 16 characters even that is withheld, because four of
    twelve is a quarter of the secret.
    """
    v = (value or "").strip().strip("\"'")
    n = len(v)
    if n == 0:
        return "(empty)"
    if n < 16:
        return f"...({n} chars)"
    return f"{v[:4]}...({n} chars)"


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------

DB_SCHEMES = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "mysql": "MySQL",
    "mysql2": "MySQL", "mariadb": "MariaDB", "mongodb": "MongoDB",
    "mongodb+srv": "MongoDB Atlas", "mssql": "SQL Server",
    "sqlserver": "SQL Server", "redis": "Redis", "rediss": "Redis",
    "amqp": "RabbitMQ", "amqps": "RabbitMQ",
}

STRIPE_LIVE_RE = re.compile(r"\b([sprk]k_live_[A-Za-z0-9]{8,}|rk_live_[A-Za-z0-9]{8,})")
PAYPAL_LIVE_RE = re.compile(
    r"(access_token\$production\$|\bapi-m\.paypal\.com|\bapi\.paypal\.com)", re.IGNORECASE
)
BRAINTREE_LIVE_RE = re.compile(r"braintree[^\n]{0,40}production|production[^\n]{0,40}braintree",
                               re.IGNORECASE)

SMTP_HOST_RE = re.compile(
    r"\b(smtp\.gmail\.com|smtp\.sendgrid\.net|api\.sendgrid\.com|"
    r"smtp\.mailgun\.org|api\.mailgun\.net|email-smtp\.[a-z0-9\-]+\.amazonaws\.com|"
    r"smtp\.office365\.com|smtp\.mail\.yahoo\.com|smtp\.postmarkapp\.com|"
    r"smtp\.mandrillapp\.com|smtp\.zoho\.com|smtp-relay\.gmail\.com)\b",
    re.IGNORECASE,
)
# A real relay with one of these on the same line is a deliberate test rig.
SANDBOX_MARKER_RE = re.compile(
    r"(sandbox|mailtrap|mailhog|maildev|ethereal|mailcatcher|smtp4dev|"
    r"papercut|fake|stub|dry[-_]?run)", re.IGNORECASE
)

AWS_KEY_RE = re.compile(r"\b((?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16})\b")
GCP_SA_RE = re.compile(
    r"(\"type\"\s*:\s*\"service_account\"|\"private_key_id\"\s*:|"
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----|\.iam\.gserviceaccount\.com)"
)
AZURE_CONN_RE = re.compile(
    r"(AccountKey=[A-Za-z0-9+/=]{20,}|SharedAccessKey=[A-Za-z0-9+/=]{20,}|"
    r"DefaultEndpointsProtocol=https;AccountName=)", re.IGNORECASE
)

# Prefixes that are only ever issued for a live account.
VENDOR_TOKEN_RES = (
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "a GitHub personal access token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub fine-grained token"),
    (re.compile(r"\bgh[osu]_[A-Za-z0-9]{20,}"), "a GitHub OAuth/app token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{15,}"), "a GitLab personal access token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "a Slack API token"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}"), "a SendGrid API key"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}"), "a Google API key"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "an npm publish token"),
    (re.compile(r"\bdop_v1_[a-f0-9]{40,}"), "a DigitalOcean token"),
    (re.compile(r"\bshpat_[a-f0-9]{30,}"), "a Shopify access token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\."), "a signed JWT"),
)

SECRET_KEY_RE = re.compile(
    r"(^|[._\-])(token|secret|password|passwd|pwd|apikey|api_key|accesskey|"
    r"privatekey|key|credential|credentials|auth|authorization|dsn)$",
    re.IGNORECASE,
)

NETWORK_CMD_RE = re.compile(
    r"(^|[\s;&|`(])(curl|wget|httpie|nc|telnet|ssh|scp|rsync)\s", re.IGNORECASE)

PATHLIKE_RE = re.compile(r"^([./~]|[A-Za-z]:\\)|\.(pem|key|json|p12|pfx|crt|cer|env)$")


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_live_secret(value: str) -> bool:
    """Shape test for an unrecognised but real-looking credential.

    Length, charset spread and entropy together. Each alone is useless:
    `supersecretpassword` is long, `a1b2` is mixed, and a base64 blob of a
    public cert is high-entropy. Requiring all three keeps developer-obvious
    values (`postgres`, `local-dev-password`) quiet.
    """
    v = value.strip().strip("\"'")
    if len(v) < 20 or len(v) > 500:
        return False
    if any(ch.isspace() for ch in v):
        return False
    if PATHLIKE_RE.search(v):
        return False
    if "://" in v:
        return False  # a URL; the URL detectors own it
    if is_placeholder(v):
        return False
    has_lower = any(c.islower() for c in v)
    has_upper = any(c.isupper() for c in v)
    has_digit = any(c.isdigit() for c in v)
    classes = sum((has_lower, has_upper, has_digit))
    if classes < 2:
        return False
    return entropy(v) >= 3.0


def safe_key(key: str) -> str:
    """The key name is printed verbatim -- so it must not BE a credential.

    Config files do map a live token on the left of the separator: an
    allowlist keyed by API key, a YAML mapping of `AKIA...: readonly`, a
    per-token rate-limit table. `ASSIGN_RE` accepts exactly the charset those
    prefixes use, so without this the full secret reaches the JSON, the
    stderr summary, the committed `.testing/env-safety.json` and any report
    built from them -- defeating rule 1 at the one place it is not applied.

    Only the unambiguous-prefix detectors are consulted. A shape heuristic
    here would redact ordinary CamelCase key names and make the report
    useless, and a key name that merely looks random is not a disclosure.
    """
    if not key or key == "(inline)":
        return key or "(inline)"
    if (AWS_KEY_RE.search(key) or STRIPE_LIVE_RE.search(key)
            or AZURE_CONN_RE.search(key) or GCP_SA_RE.search(key)
            or any(p.search(key) for p, _ in VENDOR_TOKEN_RES)):
        return f"(key redacted) {redact(key)}"
    return key


class Finding(dict):
    """Plain dict; subclassed only so construction reads in one place."""

    def __init__(self, severity: str, file: str, line: int, key: str,
                 detector: str, value: str, why: str) -> None:
        super().__init__(
            severity=severity,
            file=file,
            line=line,
            key=safe_key(key),
            detector=detector,
            redacted_preview=redact(value),
            why=why,
        )


def scan_value(key: str, value: str, *, file: str, line: int,
               context: str = "config", raw: str = "") -> list[Finding]:
    """All detectors for one key/value pair. `context` is "command" for
    shell lines (package.json scripts, CI run steps) and "config"
    otherwise, which decides whether a plain external host is a MEDIUM
    "not hermetic" finding or a CRITICAL "tests point at a live system"."""
    out: list[Finding] = []
    key = key or "(inline)"
    stripped = value.strip().strip("\"'").rstrip(",")
    # Key and trailing comment included deliberately: `BRAINTREE_ENV=production`
    # puts the vendor in the key, and `SMTP_HOST=... # sandbox` puts the
    # exemption in a comment split_pair has already removed.
    line_text = f"{key} {value} {raw}"

    def add(sev, detector, why, val=None):
        out.append(Finding(sev, file, line, key, detector, val if val is not None else stripped, why))

    # ---- CRITICAL: live payment credentials -----------------------------
    if not is_masked(stripped):
        m = STRIPE_LIVE_RE.search(stripped)
        if m:
            add("CRITICAL", "stripe_live_key",
                "this is a live-mode Stripe key; a test run using it can create "
                "real charges and refunds against a real account")
        if PAYPAL_LIVE_RE.search(stripped) and not SANDBOX_MARKER_RE.search(line_text):
            add("CRITICAL", "paypal_live_credential",
                "this points at PayPal's live API rather than the sandbox; "
                "payments made by a test would be real")
        if BRAINTREE_LIVE_RE.search(line_text):
            add("CRITICAL", "braintree_production",
                "Braintree is configured for the production environment; "
                "a test transaction would settle for real money")

    # ---- CRITICAL: databases --------------------------------------------
    if not is_masked(stripped):
        for scheme, rest in URL_RE.findall(stripped):
            engine = DB_SCHEMES.get(scheme.lower())
            if not engine:
                continue
            host = host_of(rest)
            if scheme.lower() == "mongodb+srv" or not is_safe_host(host):
                add("CRITICAL", "remote_database_url",
                    f"this is a {engine} connection string pointing at a "
                    "non-local server; a test that truncates or seeds tables "
                    "would do it to that database")
                break

    # ---- CRITICAL: real mail relays -------------------------------------
    if SMTP_HOST_RE.search(stripped) and not SANDBOX_MARKER_RE.search(line_text) \
            and not is_masked(stripped):
        add("CRITICAL", "live_smtp_host",
            "this is a real mail relay with no sandbox marker; a test that "
            "sends notifications would deliver them to real inboxes")

    # ---- CRITICAL / MEDIUM: hosts ---------------------------------------
    # Skipped when a more specific CRITICAL already named this line: a
    # Mongo Atlas URI should produce one finding that says "database", not
    # also a vaguer one that says "external host".
    if not is_masked(stripped) and not out:
        hosts, host_scan_truncated = real_hosts_in(
            stripped, bare=(context == "config"))
        if host_scan_truncated:
            # The tail past MAX_HOST_SCAN_CHARS was never matched against, so
            # "no production host here" is a statement about the head only. A
            # bound that silently shortens its own input turns a cost control
            # into a blind spot, which is the one thing this gate may never
            # do -- so the truncation is a finding, not a footnote. HIGH in a
            # config file for the same reason unread_finding() is HIGH: not
            # evidence of production, but not evidence of safety either.
            add("HIGH" if context == "config" else "MEDIUM",
                "host_scan_truncated",
                f"this value is longer than {MAX_HOST_SCAN_CHARS} characters, "
                "so only its first "
                f"{MAX_HOST_SCAN_CHARS} were checked for hostnames -- the rest "
                "was NOT examined and may name a production server")
        prod = [h for h in hosts if is_prod_host(h)]
        if prod:
            add("CRITICAL", "production_hostname",
                "this host is named for production; tests configured against "
                "it read and write live data")
        elif hosts and context == "config":
            add("CRITICAL", "external_hostname",
                "this points the suite at a real external host rather than a "
                "local or example one, so a test run leaves this machine and "
                "reaches whatever is there")
        elif hosts and context == "command":
            add("MEDIUM", "network_in_test_command",
                "a test command reaches a real host over the network, so the "
                "suite is not hermetic and can fail -- or act -- on whatever "
                "that host is today")

    # ---- HIGH: cloud credentials ----------------------------------------
    if AWS_KEY_RE.search(value) and not is_masked(stripped):
        add("HIGH", "aws_access_key",
            "an AWS access key id is present; anything a test does with it "
            "happens in a real AWS account")
    if GCP_SA_RE.search(value):
        add("HIGH", "gcp_service_account",
            "a Google service-account credential is present; it grants a test "
            "run whatever that service account can do in the live project")
    if AZURE_CONN_RE.search(value):
        add("HIGH", "azure_connection_string",
            "an Azure storage/service-bus connection string is present; a test "
            "could read or delete real blobs or queue messages")

    # ---- HIGH: vendor tokens and live-looking secrets -------------------
    if not is_masked(stripped):
        for pattern, label in VENDOR_TOKEN_RES:
            if pattern.search(stripped):
                add("HIGH", "vendor_api_token",
                    f"this looks like {label}, issued against a real account "
                    "rather than a test double")
                break

    # The generic shape test is the fallback. If a named detector already
    # identified this value, repeating it as "a long high-entropy string"
    # adds a line to the report and no information to the reader.
    already_named = any(SEVERITY_RANK[f["severity"]] >= SEVERITY_RANK["HIGH"] for f in out)
    if SECRET_KEY_RE.search(key) and not already_named and looks_live_secret(stripped):
        add("HIGH", "live_looking_secret",
            "a long, high-entropy value is assigned to a credential-shaped "
            "key, which is what a real secret looks like and what a "
            "placeholder does not")

    # ---- MEDIUM: network tooling in a command ---------------------------
    if context == "command" and NETWORK_CMD_RE.search(value):
        add("MEDIUM", "network_in_test_command",
            "the test command shells out to a network tool, so results depend "
            "on a live service rather than on the code under test")

    # One line, one finding per detector: a command can match both the
    # "reaches a real host" and the "shells out to curl" branch, and the
    # reader does not need to be told twice.
    deduped: list[Finding] = []
    seen = set()
    for f in out:
        if f["detector"] in seen:
            continue
        seen.add(f["detector"])
        deduped.append(f)
    return deduped


# --------------------------------------------------------------------------
# reading files
# --------------------------------------------------------------------------

ASSIGN_RE = re.compile(
    r"""^\s*(?:-\s+)?(?:export\s+)?["']?([A-Za-z_][A-Za-z0-9_.\-]{0,80})["']?\s*[:=]\s*(.*)$"""
)

COMMENT_RE = re.compile(r"^\s*(#|//|;|\*|/\*)")

ENV_BLOCK_RE = re.compile(r"^\s*(?:-\s+)?(env|environment|variables|with)\s*:\s*$",
                          re.IGNORECASE)

CMD_KEY_RE = re.compile(
    r"^(\s*)(?:-\s+)?(run|script|scripts|command|commands|before_script|"
    r"after_script|entrypoint|cmd|args)\s*:\s*(.*)$", re.IGNORECASE
)


# Byte-order marks, longest first so UTF-32 is not mistaken for UTF-16.
# A .env written by a Windows editor is routinely UTF-16, and UTF-16 text is
# half NUL bytes -- so the binary heuristic below used to throw it away and the
# gate returned SAFE on a file naming a production database. A NUL inside a
# file that opens with a UTF-16 BOM is encoding, not binary content.
# The codec names are the BOM-consuming ones ("utf-16", not "utf-16-le"), so
# the mark does not survive into the text as a stray ﻿ that would break
# the first key name on the first line.
BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def read_text(path: str, *, max_bytes: int = MAX_BYTES) -> tuple[str | None, str | None]:
    """(text, skip_reason). Exactly one of the two is None.

    A reason rather than a bare None, because "I could not read it" has to
    reach the report: this gate exists to stop a suite being aimed at
    production, and a .env it never opened is precisely where that lives.
    """
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return None, (f"larger than {max_bytes} bytes "
                          f"({max_bytes // (1024 * 1024)}MB ceiling) so it "
                          "was never read")
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"could not be read ({exc.strerror or exc.__class__.__name__})"

    for bom, encoding in BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(encoding, errors="replace"), None
            except (UnicodeDecodeError, LookupError):
                return None, f"declares a {encoding} BOM but does not decode"

    if b"\x00" in raw[:8000]:
        return None, "looks binary (NUL bytes, no text BOM) so it was not read"
    return raw.decode("utf-8", errors="replace"), None


def split_pair(line: str) -> tuple[str, str]:
    m = ASSIGN_RE.match(line)
    if not m:
        return "(inline)", line.strip()
    key, value = m.group(1), m.group(2).strip()
    # Trailing `# comment` only when space-separated, so a value containing
    # `#` (plenty of generated passwords do) survives intact.
    value = re.sub(r"\s+#.*$", "", value).strip()
    value = value.rstrip(",").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return key, value


# The only findings worth keeping in a .env.example: things that are a
# credential by their own shape, rather than things that are a target.
TEMPLATE_CREDENTIAL_DETECTORS = frozenset({
    "stripe_live_key", "paypal_live_credential", "braintree_production",
    "remote_database_url", "aws_access_key", "gcp_service_account",
    "azure_connection_string", "vendor_api_token", "live_looking_secret",
})


def scan_text(text: str, *, rel: str, kind: str) -> list[Finding]:
    findings: list[Finding] = []
    cmd_indent: int | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or COMMENT_RE.match(line):
            continue

        context = "config"
        if kind == "ci":
            m = CMD_KEY_RE.match(line)
            if ENV_BLOCK_RE.match(line):
                # An env: block inside a step is configuration even though it
                # sits under a run: -- and configuration is where a real
                # production target hides, so it must not be downgraded.
                cmd_indent = None
            elif m:
                cmd_indent = len(m.group(1))
                context = "command"
            elif cmd_indent is not None:
                indent = len(line) - len(line.lstrip())
                if indent > cmd_indent:
                    context = "command"
                else:
                    cmd_indent = None

        key, value = split_pair(line)
        findings.extend(scan_value(key, value, file=rel, line=lineno,
                                   context=context, raw=line))

    if kind == "env_template":
        # A template is never loaded by the app, so a hostname in it
        # configures nothing and flagging it is pure noise. A real
        # credential in it is still a real credential, so those survive.
        findings = [f for f in findings if f["detector"] in TEMPLATE_CREDENTIAL_DETECTORS]
        note = template_note(text, rel)
        if note:
            findings.append(note)
    return findings


def template_note(text: str, rel: str) -> Finding | None:
    """One informational line per template file, so the report shows the
    file was read and found to be exactly what it claims to be."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or COMMENT_RE.match(line):
            continue
        key, value = split_pair(line)
        if key != "(inline)" and is_placeholder(value):
            return Finding(
                "LOW", rel, lineno, key, "template_placeholder", value,
                "this is a template file holding placeholder values, which is "
                "correct -- noted only so the report shows it was checked",
            )
    return None


def scan_package_json(text: str, rel: str) -> list[Finding]:
    """Only the `scripts` block. The rest of package.json is dependency
    metadata full of registry and homepage URLs that no test ever calls."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []

    findings: list[Finding] = []
    for name, command in scripts.items():
        if not isinstance(command, str):
            continue
        m = re.search(r'"%s"\s*:' % re.escape(name), text)
        lineno = text.count("\n", 0, m.start()) + 1 if m else 1
        findings.extend(scan_value(name, command, file=rel, line=lineno,
                                   context="command"))
    return findings


# --------------------------------------------------------------------------
# driving the scan
# --------------------------------------------------------------------------

# A template is meant to be full of fake values, so one that cannot be read
# costs nothing -- it is noted, at LOW, and does not move the verdict. Every
# other kind here is a file a test run actually takes its target from.
INFORMATIONAL_KINDS = frozenset({"env_template"})


def unread_finding(rel: str, kind: str, reason: str) -> Finding:
    """A file the gate could not read, as a finding in its own right.

    HIGH, not CRITICAL: an unread file is not evidence of production, so it
    must not read as proof of one. HIGH is enough to make the verdict CAUTION
    and to trip `--fail-on high`, which is the honest shape -- "unknown",
    loudly, instead of "safe", quietly.
    """
    severity = "LOW" if kind in INFORMATIONAL_KINDS else "HIGH"
    return Finding(
        severity, rel, 1, "(whole file)", "unreadable_config", "",
        f"this file {reason}, so it was NOT checked -- it may or may not "
        "point the suite at production, and nothing here can tell you which",
    )


def iter_config_files(repo: str):
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            kind = classify(rel)
            if kind:
                yield full, rel, kind


def scan(repo: str, *, max_bytes: int = MAX_BYTES) -> dict:
    if not os.path.isdir(repo):
        raise NotADirectoryError(f"{repo} is not a directory")

    findings: list[Finding] = []
    scanned: list[str] = []
    skipped: list[dict] = []

    for full, rel, kind in iter_config_files(repo):
        text, reason = read_text(full, max_bytes=max_bytes)
        if text is None:
            # Unreadable is not clean. It goes in skipped_files AND produces a
            # finding of its own, because `--fail-on` only ever looks at
            # findings: before this, a .env the gate could not open still came
            # back SAFE, and "I could not read it" rendering as "it is safe" is
            # the single worst answer this script can give.
            skipped.append({"file": rel, "reason": reason or "unreadable"})
            findings.append(unread_finding(rel, kind, reason or "unreadable"))
            continue
        scanned.append(rel)
        if kind == "package_json":
            findings.extend(scan_package_json(text, rel))
        else:
            findings.extend(scan_text(text, rel=rel, kind=kind))

    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["line"],
                                 f["detector"]))

    counts = Counter(f["severity"] for f in findings)
    if counts.get("CRITICAL"):
        verdict = "UNSAFE"
    elif counts.get("HIGH") or counts.get("MEDIUM"):
        verdict = "CAUTION"
    else:
        verdict = "SAFE"

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": os.path.abspath(repo),
        "verdict": verdict,
        "counts": {s: counts.get(s, 0) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW")},
        "findings": [dict(f) for f in findings],
        "scanned_files": scanned,
        "skipped_files": skipped,
    }


def breaches(result: dict, threshold: str) -> bool:
    floor = SEVERITY_RANK[threshold.upper()]
    return any(SEVERITY_RANK[f["severity"]] >= floor for f in result["findings"])


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="env_safety_check.py",
        description="Refuse to run tests that are pointed at production.",
    )
    ap.add_argument("repo", nargs="?", default=".", help="repository path")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--fail-on", default="critical",
                    choices=["critical", "high", "medium"],
                    help="exit 1 when a finding at this severity or above exists "
                         "(default: critical)")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES,
                    help="never read a config file larger than this "
                         f"(default {MAX_BYTES}, i.e. 2MB); a file over the "
                         "ceiling is listed in skipped_files AND raises an "
                         "unreadable_config finding, never passed as clean")
    args = ap.parse_args(argv)

    try:
        result = scan(args.repo, max_bytes=args.max_bytes)
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(result, indent=2)
    if args.out:
        # Atomic: the gate runs in parallel with the other scripts under
        # .testing/, and a half-written safety report that still parses would
        # be read as a verdict. See scripts/atomic_io.py.
        try:
            write_text_atomic(args.out, payload + "\n")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    else:
        print(payload)

    c = result["counts"]
    print(f"{result['verdict']}: {c['CRITICAL']} critical, {c['HIGH']} high, "
          f"{c['MEDIUM']} medium, {c['LOW']} low "
          f"({len(result['scanned_files'])} files scanned, "
          f"{len(result['skipped_files'])} skipped unread)", file=sys.stderr)
    for f in result["findings"][:10]:
        if f["severity"] == "LOW":
            continue
        print(f"  {f['severity']:8} {f['file']}:{f['line']} {f['key']} "
              f"[{f['detector']}] {f['redacted_preview']}", file=sys.stderr)
    for s in result["skipped_files"]:
        print(f"  skipped {s['file']}: {s['reason']}", file=sys.stderr)

    return 1 if breaches(result, args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
