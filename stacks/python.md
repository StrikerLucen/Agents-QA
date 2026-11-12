# Stack playbook — Python

Reference for a testsquad agent working in a Python repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

---

## 1. Detection signals

```bash
ls pyproject.toml setup.py setup.cfg requirements*.txt Pipfile tox.ini noxfile.py 2>/dev/null
ls uv.lock poetry.lock pdm.lock Pipfile.lock 2>/dev/null
```

### Dependency / environment manager — decided by lockfile first

| Marker | Manager | Install | Run a command |
|---|---|---|---|
| `uv.lock` | **uv** | `uv sync --all-extras --dev` | `uv run <cmd>` |
| `poetry.lock` + `[tool.poetry]` in pyproject | **Poetry** | `poetry install --with dev` | `poetry run <cmd>` |
| `pdm.lock` + `[tool.pdm]` | **PDM** | `pdm install -G:all` | `pdm run <cmd>` |
| `Pipfile.lock` | **pipenv** | `pipenv install --dev` | `pipenv run <cmd>` |
| `requirements.txt` only | **pip** | `pip install -r requirements.txt -r requirements-dev.txt` | `python -m <cmd>` |
| `environment.yml` | **conda/mamba** | `conda env create -f environment.yml` | `conda run -n <env> <cmd>` |
| `[project]` in pyproject, no lock | PEP 621 + pip | `pip install -e '.[dev,test]'` | `python -m <cmd>` |

`tox.ini` or `noxfile.py` present ⇒ **the repo's canonical test entry point is
`tox` / `nox`, not bare pytest.** Read the envlist; CI runs those envs and they
may set env vars, install extras, or pin a Python version the bare command will
not reproduce.

Prefix every command below with the manager's runner (`uv run`, `poetry run`,
etc.). Bare `pytest` in a Poetry/uv project runs against the wrong interpreter.

### Test framework

```bash
grep -nE "pytest|unittest|nose|hypothesis|tox" pyproject.toml setup.cfg tox.ini requirements*.txt 2>/dev/null
ls pytest.ini conftest.py tests/conftest.py 2>/dev/null
grep -rn "import unittest" --include='test_*.py' --include='*_test.py' -l . 2>/dev/null | head
```

| Marker | Framework |
|---|---|
| `pytest.ini`, `[tool.pytest.ini_options]` in pyproject, `[pytest]` in setup.cfg/tox.ini, any `conftest.py` | **pytest** |
| `import unittest` + `class X(unittest.TestCase)` and no pytest config | **unittest** (pytest can still *run* these — prefer pytest as the runner) |
| `nose`/`nose2` | legacy; `nose` is dead (Python 3.10+ incompatible). Report as a migration item |
| `hypothesis` in deps | property-based tests already present — see §5 |
| `django` + `manage.py` | Django test runner *or* `pytest-django`; check for `DJANGO_SETTINGS_MODULE` in pytest config |

Mixed is common and fine: pytest runs `unittest.TestCase` classes natively.
Write new tests in the dominant style of the directory you are editing.

### Key pytest plugins to detect (they change command semantics)

```bash
python -m pytest --version   # prints the plugin list under "plugins:"
```

`pytest-xdist` (`-n`), `pytest-cov`, `pytest-randomly`, `pytest-asyncio` /
`anyio`, `pytest-django`, `pytest-mock` (`mocker` fixture), `pytest-timeout`,
`pytest-picked`, `pytest-testmon`, `freezegun` / `time-machine`,
`respx` / `responses` / `vcrpy`.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. Always scope.

Python has **no built-in import-graph test mapper** equivalent to
`jest --findRelatedTests`. Four options, best first:

```bash
# 1. pytest-testmon — real dependency tracking via coverage. Closest to findRelatedTests.
pip install pytest-testmon
pytest --testmon                 # runs only tests affected by changes since the last run
pytest --testmon-noselect        # update the DB without selecting (seed it in CI)
# Requires .testmondata to be persisted; cold cache = full run.

# 2. pytest-picked — tests in files touched per git status. Fast, no DB, coarse.
pip install pytest-picked
pytest --picked                  # unstaged + untracked
pytest --picked=first            # changed tests first, then everything else
pytest --picked --mode=branch    # vs. the parent branch

# 3. --lf / --ff — last-failed. Not change-scoping, but the right second command
#    after a failure and nearly free.
pytest --lf                      # only tests that failed last run
pytest --ff                      # failures first, then the rest
pytest --lf --last-failed-no-failures=none   # exit 5 instead of running everything

# 4. Path heuristic — map src -> tests by convention. Always available.
pytest $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.py' \
  | sed 's|^src/||; s|^||' | sed 's|\([^/]*\)\.py$|test_\1.py|' \
  | while read f; do find tests -name "$(basename "$f")" 2>/dev/null; done | sort -u)
```

Plus always run the directly-changed test files:

```bash
CHANGED_TESTS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'test_*.py' '*_test.py' 'tests/**/*.py')
[ -n "$CHANGED_TESTS" ] && pytest $CHANGED_TESTS
```

Guard against an empty selection in hooks — pytest exits **5** ("no tests
collected"), which a naive hook reads as failure:

```bash
pytest $SELECTION; rc=$?; [ $rc -eq 5 ] && rc=0; exit $rc
```

### Full suite / single file / single test

| | pytest | unittest |
|---|---|---|
| Full suite | `pytest` | `python -m unittest discover -s tests -p 'test_*.py'` |
| One file | `pytest tests/test_cart.py` | `python -m unittest tests.test_cart` |
| One class | `pytest tests/test_cart.py::TestCart` | `python -m unittest tests.test_cart.TestCart` |
| One test | `pytest tests/test_cart.py::TestCart::test_adds_item` | `python -m unittest tests.test_cart.TestCart.test_adds_item` |
| By name substring | `pytest -k "adds_item and not slow"` | `python -m unittest -k adds_item` |
| By marker | `pytest -m "not slow"` | — |
| Stop at first failure | `pytest -x` / `--maxfail=3` | `python -m unittest -f` |
| Parallel | `pytest -n auto` (xdist) | — |
| Quiet, no capture | `pytest -q -s` | `python -m unittest -b` (buffer) |
| Show slowest | `pytest --durations=20` | — |
| Collect only (no run) | `pytest --collect-only -q` | — |

Django: `python manage.py test` or, with pytest-django,
`pytest --ds=myproject.settings.test --reuse-db` (`--create-db` to force a fresh
one). `--reuse-db` is what makes a Django suite usable in a hook.

---

## 3. Coverage

Two formats `scripts/normalize_coverage.py` parses for Python: **coverage.py
JSON** (`coverage.json`, native, preferred) and **Cobertura XML**
(`coverage.xml`). Both are auto-detected.

Line-only coverage hides untested branches. `--branch` is not optional here —
coverage.py does **not** measure branches by default.

```bash
pip install pytest-cov            # or: pip install coverage

# pytest-cov — branch coverage ON, both report formats
pytest --cov=src --cov-branch \
       --cov-report=json:coverage.json \
       --cov-report=xml:coverage.xml \
       --cov-report=term-missing

# coverage.py directly (works for unittest, Django, anything)
coverage run --branch --source=src -m pytest
coverage run --branch --source=src -m unittest discover
coverage json -o coverage.json
coverage xml -o coverage.xml
coverage report --show-missing --skip-covered
coverage html            # htmlcov/index.html, for humans only
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage.json` | coverage.py native JSON | `coveragepy` |
| `coverage.xml` | Cobertura XML | `cobertura` |
| `htmlcov/` | HTML | not parsed — humans only |
| `.coverage` | SQLite DB | intermediate; must be converted |

The JSON path carries `summary.num_branches` / `summary.covered_branches` only
when `--cov-branch` (or `branch = True`) was set. Without it the normalizer
reports `branch_pct: null` — which is correct, and should be reported as "branch
coverage was not measured", never as 0%.

Pin it in config so every run is branch-aware:

```toml
# pyproject.toml
[tool.coverage.run]
branch = true
source = ["src"]
parallel = true          # required when using pytest-xdist
omit = ["*/migrations/*", "*/__main__.py"]

[tool.coverage.report]
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError", "\\.\\.\\."]
```

With `-n auto` (xdist) or multiple processes you must combine before reporting:

```bash
coverage combine && coverage json -o coverage.json
```

Normalize:

```bash
python3 scripts/normalize_coverage.py coverage.json --out .testsquad/coverage.json
```

Threshold on the diff, not the project: `--cov-fail-under` is a project-wide
gate and will fail on legacy debt the current change did not cause. Compute the
diff figure from the normalized JSON instead.

---

## 4. Mutation testing

### Tool choice

| Tool | Status 2026-11-12 | Use it when |
|---|---|---|
| **mutmut 3.x** (3.8.0 verified) | **Healthy. Fastest.** pytest-only, worker pooling | Default choice for a pytest repo |
| **cosmic-ray** (v8.9.0 verified) | **Healthy.** Framework-agnostic, distributed workers, SQLite session DB | unittest repos, or when you need resumable distributed runs |
| **mutatest** | **DEAD — last release 2022.** Do not adopt. | never |

Say the mutatest status out loud if a repo already depends on it; it is a
migration item, not a working tool.

### mutmut — diff-scoped invocation

```bash
pip install mutmut
```

```bash
# Scope to changed files. mutmut 3.x takes paths to mutate.
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'src/**/*.py')
mutmut run --paths-to-mutate "$(echo "$CHANGED" | paste -sd, -)"

mutmut results            # summary of survivors
mutmut show <id>          # the surviving mutant's diff
mutmut browse             # TUI
mutmut results --all      # full listing
```

mutmut 3 changed its CLI and config surface materially from 2.x (config moved
into `[tool.mutmut]` in `pyproject.toml`, `paths_to_mutate` key). **If the repo
pins mutmut 2.x, the 3.x flags above do not apply — read
`mutmut --help` in the repo's own environment before composing a command.**

```toml
# pyproject.toml
[tool.mutmut]
paths_to_mutate = ["src/"]
tests_dir = ["tests/"]
```

### cosmic-ray — diff-scoped invocation

```bash
pip install cosmic-ray
```

```toml
# cr.toml
[cosmic-ray]
module-path = "src/cart.py"       # narrow this to the changed module
timeout = 30.0
excluded-modules = ["*/migrations/*", "*/logging*"]
test-command = "pytest -x -q tests/test_cart.py"

[cosmic-ray.distributor]
name = "local"
```

```bash
cosmic-ray init cr.toml session.sqlite
cosmic-ray --verbosity=INFO exec cr.toml session.sqlite
cr-report session.sqlite
cr-rate session.sqlite --fail-over 20      # fail if >20% survive
cr-html session.sqlite > mutation.html
```

cosmic-ray has **no native git-diff flag**; scope it by generating `cr.toml`
per changed module from the diff. That is the supported pattern, not a
workaround.

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**.

1. **One mutant per changed line**, chosen at random among applicable operators.
   In >90% of lines with multiple mutants, all mutants share the same fate.
2. **Arid-node suppression** — never mutate: `logging.*` / `logger.*` calls,
   metrics emitters, `assert` statements, `__repr__` / `__str__`, trivial
   `@property` getters, pure delegation, string literals in exception messages,
   `if TYPE_CHECKING:` blocks, `...` bodies in Protocols/stubs. Express via
   `exclude`/`excluded-modules` and `# pragma: no mutate` comments.
3. **Gate on covered-MSI of the diff, never project score.** Every merged change
   clearing its own threshold converges the project without a full sweep.

Blind spots to state in any report: a diff-scoped gate cannot see a change in
module A destroying coverage of module B; it runs **no mutants at all for a
test-only change**, so deleted assertions pass the gate; and fixture/config
changes can flip mutant status silently.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **pytest fixtures** (`conftest.py`) | The native integration seam — DB sessions, app clients, temp dirs | Fixture scope (`session` vs `function`) is the #1 source of Python's order-dependency flake (see §6) |
| **Testcontainers-Python** | Real Postgres/Redis/Kafka/LocalStack | Docker daemon required; 2–20s startup per container; unusable in sandboxed CI |
| **pytest-postgresql / pytest-mysql** | Lighter than containers, local binary | Needs the server installed on the runner |
| **responses / respx / vcrpy** | HTTP mocking (requests / httpx / cassettes) | Cassettes rot silently; a stale cassette is a test that passes against an API that changed |
| **WireMock** (via `wiremock` py client or a container) | Contract-style stubbing of a third-party HTTP API | Another process to manage; worth it only for multi-service contracts |
| **Playwright for Python** | Browser E2E | Browser download ~1GB; 10%-flake tier per Meta's floors |
| **`httpx.AsyncClient(app=...)` / Django `Client` / Flask `test_client`** | In-process HTTP assertions | Proves nothing about the deployed network path |
| **Hypothesis** | Property-based testing | **Assess-tier, not a default** — see below |

Hypothesis is the best-supported PBT ecosystem anywhere, but the honest numbers
(RESEARCH §16): LLM-written property tests measured **54.6% valid / 41.7%
valid-and-sound with 20.5% property coverage**; an agentic study over 100+ Python
packages found **56% valid bugs, 32% worth reporting**, rising to 86% valid for
the agent's own top-ranked 21. There is **no head-to-head study showing
LLM-written property tests beat LLM-written example tests** under equal budget.
Recommend it where it fits; never default to it.

```bash
pip install hypothesis
pytest --hypothesis-show-statistics
pytest --hypothesis-seed=12345        # reproduce a reported failure
# .hypothesis/examples/ holds the failure database — persist it in CI or
# shrunk counterexamples are lost between runs.
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests.
Derive mock shapes from the type annotation, a Pydantic model, an OpenAPI
schema, or a recorded cassette.

---

## 6. Flake taxonomy — Python

Per-language distributions differ sharply; a shared checklist would be wrong.
For Python the reported breakdown is:

| Cause | Share |
|---|---|
| **Order dependency** | **59%** |
| Test infrastructure | **28%** |
| Async wait | **almost absent** |

This is the inverse of Java and of JavaScript. Python's dominant flake is
**shared state leaking between tests in the same process** — module-level
globals, `session`-scoped fixtures mutated by a test, a monkeypatch never undone,
an import-time singleton, a cached `lru_cache`, a Django DB left dirty, a
`faker`/`factory_boy` sequence counter. Spend your flake budget here first.

### Detection commands

```bash
pip install pytest-randomly

# pytest-randomly shuffles test order AND reseeds random/faker/factory_boy/numpy
# per test. This is the single highest-yield flake detector in Python.
pytest -p randomly                       # shuffled, random seed printed at the top
pytest -p randomly --randomly-seed=12345 # reproduce an exact order
pytest --randomly-seed=last              # replay the previous run's seed
pytest -p no:randomly                    # disable, to confirm order is the cause

# Isolate the suspect: does it pass alone?
pytest tests/test_cart.py::test_adds_item          # alone
pytest tests/ -k "test_adds_item or test_other"    # with a suspected polluter

# Bisect the polluter mechanically
pip install pytest-random-order
pytest -p no:randomly --random-order --random-order-bucket=global

# Confirm it is in-process state, not the DB: separate processes per test
pytest --forked                 # pytest-forked
pytest -n auto --dist=loadfile  # xdist, one file per worker — masks cross-file pollution

# Environment sweep (test-infrastructure is 28%)
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 PYTHONHASHSEED=random pytest
PYTHONHASHSEED=0 pytest
```

`PYTHONHASHSEED` matters specifically: set/dict iteration order depends on it,
and CPython randomizes it per process by default. A test that only passes with a
fixed seed has an ordering assumption.

### Static flake smells — check before the test ever runs (Tier 0)

Unseeded `random` / `uuid.uuid4()` / `secrets` · `datetime.now()` /
`date.today()` in an assertion · `time.sleep()` · real network or DNS ·
`os.listdir()` / `glob.glob()` order assumptions · `set` / `dict` iteration
order in an assertion · locale- or timezone-dependent `strftime` · float
equality (use `pytest.approx`) · `session`- or `module`-scoped mutable fixtures ·
`monkeypatch` not used (manual patching without teardown) · a module-level
constant mutated inside a test · `@lru_cache` not cleared between tests.

Fixes:

```python
freezer = freeze_time("2026-01-01")        # freezegun
with time_machine.travel("2026-01-01"):    # time-machine (faster)
random.seed(0); Faker.seed(0)
monkeypatch.setenv("X", "y")               # auto-reverted
@pytest.fixture  # function scope by default — keep it that way unless proven costly
```

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

```bash
grep -rnE "DJANGO_SETTINGS_MODULE" --include='*.py' --include='*.cfg' --include='*.ini' --include='*.toml' --include='*.yml' . 
grep -rnE "(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://" --include='*.py' --include='*.env*' --include='*.ini' . \
  | grep -vE "localhost|127\.0\.0\.1|::1|test|sqlite|docker"
grep -rn "load_dotenv\|dotenv_values" --include='*.py' conftest.py tests/ 2>/dev/null
grep -rnE "DEBUG\s*=\s*False|ENV\s*=\s*['\"]prod" --include='*.py' .
ls -la .env .env.production settings/production.py config/production.py 2>/dev/null
```

| Red flag | Why |
|---|---|
| `DJANGO_SETTINGS_MODULE=myproject.settings.production` anywhere in pytest config, tox, CI, or a `conftest.py` | the suite runs against production settings — real DB, real cache, real email backend |
| `pytest.ini` / `pyproject.toml` with `DJANGO_SETTINGS_MODULE` not ending in `test`/`local`/`dev` | same, more quietly |
| A real DB URL in a `conftest.py` fixture default | `engine = create_engine(os.getenv("DATABASE_URL", "<prod url>"))` — the fallback is the danger |
| `load_dotenv()` in `conftest.py` with no `dotenv_path=` override | loads the developer's real `.env` |
| `--reuse-db` pointed at a non-test database name | Django/pytest-django will migrate and truncate it |
| Alembic/Django migrations run by a session fixture with no DB-name guard | can destroy a real schema |
| Celery `task_always_eager` **not** set in test settings | tests enqueue real background jobs |
| `EMAIL_BACKEND` not `locmem`, `DEFAULT_FILE_STORAGE` pointing at S3 | real emails, real uploads |
| `boto3` without `moto`/`localstack`, no `AWS_ENDPOINT_URL` override | real AWS calls |
| Stripe/Twilio/SendGrid keys matching `sk_live_` | live-money side effects |

Assert the guard, don't assume it:

```python
# conftest.py
import os, pytest

@pytest.fixture(scope="session", autouse=True)
def _refuse_production():
    url = os.environ.get("DATABASE_URL", "")
    if url and not any(t in url for t in ("test", "localhost", "127.0.0.1", "sqlite")):
        pytest.exit(f"Refusing to run tests against {url}", returncode=3)
    settings_mod = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if "prod" in settings_mod:
        pytest.exit(f"Refusing: DJANGO_SETTINGS_MODULE={settings_mod}", returncode=3)
```

---

## 8. House conventions to match

```bash
ls tests test src/tests 2>/dev/null
find . -name 'test_*.py' -o -name '*_test.py' | grep -v '.venv\|site-packages' | head -30
find . -name 'conftest.py' | grep -v '.venv' 
grep -nE "testpaths|python_files|python_classes|python_functions|addopts|markers" \
     pyproject.toml pytest.ini setup.cfg tox.ini 2>/dev/null
```

| Convention | Looks like |
|---|---|
| Top-level `tests/` mirroring `src/` (most common) | `src/cart/service.py` → `tests/cart/test_service.py` |
| Package-adjacent | `mypkg/cart/service.py` → `mypkg/cart/tests/test_service.py` |
| Flat `tests/` | `tests/test_service.py` |
| `test_*.py` vs `*_test.py` | read `python_files` in config; default is both `test_*.py` and `*_test.py` |

| Concern | Usual location |
|---|---|
| Shared fixtures | `conftest.py` — nearest-directory wins; a root `conftest.py` applies everywhere |
| Factories | `tests/factories.py`, `tests/factories/`, usually `factory_boy` `DjangoModelFactory` / `Factory` |
| Fake data | `faker` via `factory.Faker(...)` — must be seeded (see §6) |
| Static fixtures | `tests/fixtures/`, `tests/data/`, loaded via a `tmp_path`/`pathlib` helper |
| Cassettes | `tests/cassettes/` (vcrpy), `tests/__snapshots__/` (syrupy) |
| Markers | declared in `[tool.pytest.ini_options] markers = [...]` — reuse existing marks (`slow`, `integration`, `e2e`), do not invent new ones |
| Custom plugins | `tests/plugins/`, registered via `pytest_plugins` in root `conftest.py` |

Mocking idiom: `unittest.mock.patch` (decorator or context manager) is the
baseline; `pytest-mock`'s `mocker` fixture if it is in deps (auto-undone);
`monkeypatch` for env vars and attributes (auto-undone — prefer it).
Patch **where the name is looked up**, not where it is defined:
`patch("myapp.service.requests.get")`, not `patch("requests.get")`.

---

## 9. Static analysis + type checking

Cheapest wins, highest signal-to-noise. Rank these first on any roadmap.

```bash
# Lint + autofix — ruff is the fastest and almost always already present
pip install ruff
ruff check .
ruff check --diff .                       # what --fix would change
ruff check $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.py')
ruff check . --output-format=json > .testsquad/ruff.json
ruff format --check .

# Type checking — pick whichever the repo already configures; do not introduce both
mypy src/ --ignore-missing-imports
mypy --strict src/                        # preview the strict-mode debt
pyright                                   # reads [tool.pyright] in pyproject
pyright --outputjson > .testsquad/pyright.json

# Security / correctness, roadmap tier 2
bandit -r src/ -f json -o .testsquad/bandit.json
pip-audit
```

Config to read before trusting the output:

```bash
grep -nE "\[tool\.(mypy|ruff|pyright)\]" -A 25 pyproject.toml
grep -nE "ignore_errors|ignore_missing_imports|follow_imports|disable_error_code|exclude|typeCheckingMode" \
     pyproject.toml mypy.ini setup.cfg 2>/dev/null
```

`ignore_errors = true` for a module, `follow_imports = skip`, or
`typeCheckingMode = "off"` means the type checker is finding a fraction of what
it could — report that rather than reporting a clean run. If `exclude` drops
`tests/`, the tests are unchecked, which is a real finding (and the usual reason
a mock's shape silently diverges from the real object).

Ruff rule sets worth enabling, in roadmap order:

| Ruff selector | Catches |
|---|---|
| `PT` (flake8-pytest-style) | wrong fixture decorators, `pytest.raises` without `match=`, parametrize misuse |
| `ASYNC` | blocking calls inside async functions |
| `S` (bandit) | `assert` in production code, hardcoded secrets, `subprocess(shell=True)` |
| `DTZ` (flake8-datetimez) | naive `datetime.now()` — directly feeds the timezone-flake bucket |
| `RUF015`, `B` (bugbear) | mutable default args, `B008` function-call defaults — a classic shared-state flake source |
| `ARG` | unused fixture arguments, which usually mean a fixture is silently not applied |

For `unittest`-heavy repos, `pytest --collect-only -q | wc -l` versus
`python -m unittest discover -v 2>&1 | grep -c '^test'` is a cheap check that
pytest is actually collecting everything the legacy runner did; a mismatch means
tests are silently not running.
