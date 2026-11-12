# Stack playbook — PHP

Reference for a testsquad agent working in a PHP repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

PHP has **the best diff-scoped mutation gate of any ecosystem in this set** —
Infection's `--git-diff-lines` is line-level, not file-level. It is the one
stack where the RESEARCH §15 ratchet argument can be implemented exactly as
written. Rank §9 first on a roadmap and §4 second.

---

## 1. Detection signals

```bash
ls composer.json composer.lock phpunit.xml phpunit.xml.dist phpstan.neon psalm.xml 2>/dev/null
php -v && composer --version
jq -r '.require, .["require-dev"]' composer.json 2>/dev/null
jq -r '.scripts' composer.json 2>/dev/null         # the repo's canonical commands
jq -r '.autoload, .["autoload-dev"]' composer.json 2>/dev/null
```

| Marker | Conclusion |
|---|---|
| `composer.json` `scripts` block | **the repo's canonical entry points** — `composer test`, `composer lint`. Read before composing your own command |
| `autoload-dev.psr-4` | where test classes live and under which namespace. **This is the authoritative answer to "where do tests go"** |
| `"php": "^8.x"` in `require` | language version; affects available syntax and which tool versions work |
| `vendor/bin/` | all tools are run from here: `vendor/bin/phpunit`, never a global `phpunit` |
| `.php-version` / `Dockerfile` `FROM php:` | the real interpreter version in CI |

### Framework

```bash
ls artisan bin/console config/app.php config/bundles.php 2>/dev/null
jq -r '.require | keys[]' composer.json | grep -E "laravel|symfony|slim|laminas|cakephp|yii|wordpress|drupal|magento"
```

| Marker | Framework | Test base class |
|---|---|---|
| `artisan` + `config/app.php` | **Laravel** | `Tests\TestCase` extends `Illuminate\Foundation\Testing\TestCase` |
| `bin/console` + `config/bundles.php` | **Symfony** | `KernelTestCase` / `WebTestCase` |
| `symfony/framework-bundle`, no `bin/console` | Symfony components | plain `PHPUnit\Framework\TestCase` |
| `wp-config.php` / `wp-content/` | WordPress | `WP_UnitTestCase` — needs the WP test suite installed |
| `app/code/` + type `magento2-*` | Magento 2 | `Magento\TestFramework` |
| none of the above | plain PHP / a library | plain PHPUnit |

### Test framework — PHPUnit vs Pest

```bash
ls phpunit.xml phpunit.xml.dist tests/Pest.php 2>/dev/null
jq -r '.["require-dev"] | keys[]' composer.json | grep -E "phpunit|pestphp|codeception|behat|phpspec|paratest"
vendor/bin/phpunit --version 2>/dev/null
vendor/bin/pest --version 2>/dev/null
grep -rln "extends TestCase\|class .*Test extends" tests/ | head
grep -rln "^it(\|^test(\|^describe(" tests/ | head
```

| Marker | Framework | Note |
|---|---|---|
| `phpunit.xml` / `.dist` + classes `extends TestCase` | **PHPUnit** | the substrate. Versions 10/11/12 differ materially in config schema and annotations-vs-attributes |
| `tests/Pest.php` + `pestphp/pest` + `it('...', fn() => ...)` | **Pest** | a layer **on top of PHPUnit** — PHPUnit flags mostly still work, and `vendor/bin/pest` is the runner. Mixed PHPUnit + Pest files in one suite is normal and fine |
| `codeception/codeception` + `codeception.yml` | Codeception | its own suites (`unit`, `functional`, `acceptance`); `vendor/bin/codecept run` |
| `behat/behat` + `features/` | Behat | BDD acceptance; slow, usually legacy |
| `phpspec/phpspec` | phpspec | design-focused unit specs; declining |
| `brianium/paratest` | parallel PHPUnit | raises the stakes on shared state (see §6) |

PHPUnit version matters for the commands below:

```bash
vendor/bin/phpunit --version
grep -n "schemaLocation\|<phpunit" phpunit.xml phpunit.xml.dist 2>/dev/null
grep -rn "#\[Test\]\|#\[DataProvider\]\|#\[CoversClass\]" tests/ | head   # PHP 8 attributes
grep -rn "@test\|@dataProvider\|@covers" tests/ | head                     # legacy annotations
```

PHPUnit 10 changed the config schema and the result/event system; PHPUnit 11+
**deprecates doc-comment annotations in favour of PHP 8 attributes**
(`#[Test]`, `#[DataProvider]`, `#[CoversClass]`). Match whichever the repo uses —
mixing them in one file is where "the test silently does not run" comes from.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. A Laravel or Symfony suite pays framework-boot cost per
process, so scoping matters more here than in a plain library.

**PHP has no import-graph test selector** — no `--findRelatedTests`, no testmon.
There are three usable mechanisms, best first:

```bash
# 1. Changed test files themselves — always run these, always cheap
CHANGED_TESTS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'tests/**/*.php')
[ -n "$CHANGED_TESTS" ] && vendor/bin/phpunit $CHANGED_TESTS

# 2. Convention mapping: src/ -> tests/, Foo.php -> FooTest.php (PSR-4 enforces it)
MAPPED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'src/**/*.php' 'app/**/*.php' \
  | sed -e 's|^src/|tests/|' -e 's|^app/|tests/Unit/|' -e 's|\.php$|Test.php|' \
  | while read f; do [ -f "$f" ] && echo "$f"; done)
[ -n "$MAPPED" ] && vendor/bin/phpunit $MAPPED

# 3. Filter by class name derived from the diff
CLASSES=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.php' \
          | xargs -n1 basename 2>/dev/null | sed 's/\.php$//' | sort -u)
FILTER=$(printf '%sTest|' $CLASSES | sed 's/|$//')
[ -n "$FILTER" ] && vendor/bin/phpunit --filter "$FILTER"

# 4. Suite / directory scoping; 5. everything (CI, not hooks)
vendor/bin/phpunit --testsuite Unit          # names come from phpunit.xml
vendor/bin/phpunit tests/Unit/Cart
vendor/bin/phpunit
```

Pest equivalents: replace `vendor/bin/phpunit` with `vendor/bin/pest`. Pest also
has:

```bash
vendor/bin/pest --dirty          # only tests for files changed per git status — the closest
                                 # thing PHP has to change-scoping, and it is good
vendor/bin/pest --retry          # re-run only the previously failed tests
vendor/bin/pest --bail
vendor/bin/pest --parallel
```

`pest --dirty` is a genuine advantage of Pest over bare PHPUnit for hook use.
If the repo has Pest, use it.

Laravel / Symfony wrappers (prefer them — they set the right env):

```bash
php artisan test --filter CartTest           # Laravel; wraps PHPUnit/Pest, uses .env.testing
php artisan test --parallel                  # needs brianium/paratest
php bin/phpunit tests/Unit                   # Symfony; wraps PHPUnit, uses .env.test
```

Guard an empty selection — **PHPUnit exits 0 when no tests match a filter** in
some versions and non-zero in others, and `--filter` with an empty string runs
everything:

```bash
[ -z "$FILTER" ] && exit 0                   # guard FIRST; never pass an empty filter
vendor/bin/phpunit --filter "$FILTER"
```

### Full suite / single file / single test

| Goal | PHPUnit | Pest |
|---|---|---|
| Whole suite | `vendor/bin/phpunit` | `vendor/bin/pest` |
| One file | `vendor/bin/phpunit tests/Unit/CartTest.php` | `vendor/bin/pest tests/Unit/CartTest.php` |
| One method | `vendor/bin/phpunit --filter 'CartTest::testAddsItem'` | `vendor/bin/pest --filter 'adds item'` |
| By regex | `vendor/bin/phpunit --filter '/adds.*item/'` | `vendor/bin/pest --filter 'adds'` |
| One suite | `vendor/bin/phpunit --testsuite Unit` | `vendor/bin/pest --testsuite=Unit` |
| By group | `vendor/bin/phpunit --group slow` / `--exclude-group slow` | `vendor/bin/pest --group=slow` |
| Changed files only | — | `vendor/bin/pest --dirty` |
| Stop at first failure | `--stop-on-failure` / `--stop-on-error` | `--bail` |
| Re-run failures | `--cache-result` + `--order-by=defects` | `--retry` |
| List tests | `--list-tests` / `--list-suites` / `--list-groups` | `--list-tests` (*verify*) |
| JUnit XML for CI | `--log-junit results.xml` | `--log-junit results.xml` |
| Parallel | `vendor/bin/paratest -p4` | `vendor/bin/pest --parallel --processes=4` |
| Random order | `--order-by=random --random-order-seed=12345` | `--order-by=random` |
| Per-test time limit | `--enforce-time-limit` + `#[Small]`/`#[Medium]`/`#[Large]` (needs `php-invoker`) | same |

Confirm your filter selected something — `OK (0 tests, 0 assertions)` is a
green run that tested nothing, and a hook checking only the exit code calls it a
pass:

```bash
vendor/bin/phpunit --filter "$FILTER" --list-tests | grep -c " - " || echo "NO TEST MATCHED"
```

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **Cobertura XML**.
PHPUnit emits it natively from 9.4+. **Clover XML — the PHP default — is NOT
parsed**; do not recommend `--coverage-clover` unless something else converts it.

### A coverage driver is required

```bash
php -m | grep -iE "xdebug|pcov"
php -i | grep -E "xdebug.mode|pcov.enabled"
```

| Driver | Speed | Branch coverage | Use when |
|---|---|---|---|
| **PCOV** | fast (~1.2–2x) | **no — line coverage only** | CI coverage runs where you only need line data |
| **Xdebug** | slow (~2–10x) | **yes**, with `xdebug.mode=coverage` and path/branch collection enabled | when branch data is required, and for debugging |
| none | n/a | n/a | `--coverage-*` silently produces nothing useful and PHPUnit warns. **Check first** |

```bash
# The command to use. XDEBUG_MODE=coverage must be set for the process.
XDEBUG_MODE=coverage vendor/bin/phpunit \
  --coverage-cobertura=coverage.xml --coverage-text --coverage-html=coverage-html

# PCOV instead (faster, no branch data)
php -d pcov.enabled=1 -d pcov.directory=src vendor/bin/phpunit --coverage-cobertura=coverage.xml

# Laravel / Pest wrappers
XDEBUG_MODE=coverage php artisan test --coverage --coverage-cobertura=coverage.xml
XDEBUG_MODE=coverage vendor/bin/pest --coverage --coverage-cobertura=coverage.xml
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage.xml` (`--coverage-cobertura`) | Cobertura XML | `cobertura` ✅ |
| `clover.xml` (`--coverage-clover`) | Clover XML | **NOT parsed** ❌ |
| `--coverage-php` | serialised PHP object | not parsed; used for merging |
| `--coverage-crap4j` | Crap4J XML | not parsed |
| `coverage-html/` | HTML | humans only |

If CI already produces Clover and you cannot change it, produce Cobertura **as
well** — both flags can be given in one run:

```bash
XDEBUG_MODE=coverage vendor/bin/phpunit --coverage-clover=clover.xml --coverage-cobertura=coverage.xml
```

### Branch coverage

**PHP can measure branch coverage, but only with Xdebug, and only when path
coverage is enabled.** PCOV cannot do it at all.

```xml
<!-- phpunit.xml (PHPUnit 10+) -->
<source>
  <include><directory>src</directory></include>
  <exclude><directory>src/Migrations</directory><file>src/Kernel.php</file></exclude>
</source>
<coverage pathCoverage="true" includeUncoveredFiles="true" ignoreDeprecatedCodeUnits="true"/>
```

```bash
grep -c 'condition-coverage' coverage.xml      # branch data present?
```

**Verify before relying on this**: whether `pathCoverage="true"` results in
per-line `condition-coverage` attributes in the Cobertura report depends on the
PHPUnit and php-code-coverage versions. Run the grep above once. If it returns
0, branch data did not reach the file, and the honest report is
`branch_pct: null` — **"branch coverage was not measured"**, never 0%. Same
answer whenever PCOV was the driver: `null`, not zero.

Normalize:

```bash
python3 scripts/normalize_coverage.py coverage.xml \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

Parallel (paratest) writes per-process coverage that must be merged:

```bash
XDEBUG_MODE=coverage vendor/bin/paratest -p4 --coverage-cobertura=coverage.xml
```

Do not gate on a project-wide `--coverage-text` threshold or
`<coverage><report>` minimum: it fails on legacy debt the current change did not
cause. Compute the diff figure from the normalized JSON — and note that **§4 is
a strictly better gate in PHP** and you already have it.

---

## 4. Mutation testing — Infection, the best diff gate in any ecosystem

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| **Infection 0.35.10** | **Very healthy** | the default and only choice |
| `humbug` | superseded by Infection (same lineage) | do not adopt |
| `mutagenesis`, assorted forks | unmaintained | do not adopt |

```bash
composer require --dev infection/infection
vendor/bin/infection --version
```

### Diff-scoped invocation — the one to use

```bash
XDEBUG_MODE=coverage vendor/bin/infection \
  --git-diff-lines \
  --git-diff-base=origin/main \
  --min-covered-msi=80 \
  --threads=4 \
  --show-mutations \
  --logger-json=.testsquad/infection.json
```

| Flag | What it does |
|---|---|
| **`--git-diff-lines`** | mutate **only the lines added or changed in the diff** — not merely the changed files. **Line-level, the strictest diff scoping of any mutation tool in this playbook set** |
| `--git-diff-base=origin/main` | the ref to diff against |
| `--git-diff-filter=AM` | which diff statuses to consider |
| `--min-covered-msi=80` | **fail below 80% covered-MSI**. This is the gate |
| `--min-msi=N` | fail below N% raw MSI — **weaker; prefer covered-MSI** (see below) |
| `--threads=N` / `--threads=max` | parallelism |
| `--only-covered` | skip mutants in uncovered code (they are a coverage finding, not a mutation one) |
| `--filter=src/Cart/` | path filter, if you need it on top of the diff |
| `--show-mutations` | print the escaped mutants' diffs — the actual findings |
| `--logger-json=` / `--logger-html=` | machine- and human-readable reports |
| `--initial-tests-php-options='-d zend_extension=xdebug.so'` | force the coverage driver |
| `--coverage=build/coverage` | reuse an existing coverage run instead of re-running the suite |

**MSI vs covered-MSI.** MSI = killed ÷ *all* mutants, so uncovered code drags
it down and it conflates "untested" with "weakly tested". **covered-MSI** =
killed ÷ mutants *in code the tests actually execute* — "where my tests do run,
do they assert anything?", the question mutation testing exists to answer.
**Gate on covered-MSI**; report MSI separately as a coverage signal.

### The ratchet argument — RESEARCH §15, implemented exactly

> If **every merged change clears ≥80% covered-MSI on its own changed lines**,
> then the project's assertion quality converges to ≥80% **without ever running
> a full-project mutation run.** Each PR ratchets its own diff; nothing is ever
> re-litigated; the legacy backlog is never a blocker. The expensive thing
> (a full sweep) becomes unnecessary rather than merely deferred.

This is why PHP is the strongest stack here: `--git-diff-lines` makes the
per-PR run proportional to the size of the diff, not the size of the project.
A ten-line change costs seconds. **This is the gate to put on a PHP roadmap,
ahead of any coverage threshold.**

Caveat to state alongside it: the ratchet converges **changed** lines. Code
nobody ever touches is never improved, by design. That is a feature (it is where
risk is lowest) but it must be said, not implied.

```json
// infection.json5
{
  "source": { "directories": ["src"], "excludes": ["Migrations", "Kernel.php", "*/Generated/*"] },
  "logs": { "text": "build/infection.log", "json": ".testsquad/infection.json" },
  "mutators": {
    "@default": true,
    "global-ignore": ["*::__toString", "*::jsonSerialize", "*::getId", "*Logger*", "*::log*"],
    "IncrementInteger": false, "Concat": false, "CastString": false
  },
  "minCoveredMsi": 80,
  "timeout": 10
}
```

```bash
jq -r '.escaped[] | "\(.mutator.originalFilePath):\(.mutator.originalStartLine) \(.mutator.mutatorName)"' \
  .testsquad/infection.json
```

Statuses: `escaped` (**the finding** — a real assertion gap), `killed` (fine),
`notCovered` (a coverage gap; report separately), `timeout` (counted as killed),
`syntaxError` (Infection's problem, ignore).

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**.

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste. Infection
   has no one-per-line flag; `--git-diff-lines` narrows the line set, then
   **post-filter `infection.json` to one escaped mutant per line, ≤7 per file.**
2. **Arid-node suppression** — never mutate: PSR-3 `logger->*` and Monolog
   calls, metrics emitters, `__toString`/`jsonSerialize`, trivial
   getters/setters, pure delegation, exception *message* strings, Doctrine
   migrations, `*/Generated/*`, framework boilerplate (`Kernel.php`, service
   providers). Via `source.excludes`, `mutators.global-ignore` and
   `#[CodeCoverageIgnore]`. **This step matters more than the choice of tool** —
   it is the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff: `--min-covered-msi`
   with `--git-diff-lines`. Do **not** set a project-wide `--min-msi` in CI.

Blind spots to state out loud: (a) a change in file A destroying coverage of
file B is invisible; (b) **a test-only change mutates nothing**, so deleting
assertions passes the gate; (c) config/fixture changes flip mutant status
silently. Mitigation: an occasional full run off the critical path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **Laravel HTTP tests** (`$this->getJson('/api/cart')`) | **The default integration seam in Laravel.** Full kernel, middleware, routing, real DB | Excellent value. `RefreshDatabase` trait wraps each test in a transaction |
| **Symfony `WebTestCase`** (`$client->request(...)`) | Same for Symfony: real kernel, real container, DomCrawler assertions | Excellent value. `KernelTestCase` for service-level tests without HTTP |
| **Laravel `RefreshDatabase` / `DatabaseTransactions`** | Per-test DB isolation | `RefreshDatabase` migrates once then wraps in transactions — fast. `DatabaseMigrations` re-migrates **every test** and is the usual reason a Laravel suite takes 20 minutes |
| **Symfony `dama/doctrine-test-bundle`** | Transactional rollback per test for Doctrine | The Symfony equivalent of `RefreshDatabase`; a large, cheap speedup |
| **SQLite `:memory:`** as the test DB | Fast local runs | **Different SQL dialect from MySQL/Postgres** — JSON columns, `ENUM`, full-text, locking and migration DDL all differ. A green SQLite suite does not prove the production query works |
| **Testcontainers for PHP** | Real MySQL/Postgres/Redis | Docker daemon; 2–20s startup. Many PHP teams use a docker-compose service instead |
| **Guzzle `MockHandler`** / **`Http::fake()`** (Laravel) | Faking outbound HTTP | Cheap and precise. Pair with `Http::preventStrayRequests()` so a real call is an error |
| **`php-vcr`** | Record/replay cassettes | Cassettes rot silently. *Verify maintenance before adopting* |
| **WireMock / MockServer (container)** | Contract-style stubbing | Another process; worth it for multi-service contracts |
| **Laravel Dusk / Symfony Panther / Codeception WebDriver** | Browser E2E | **The 10%-flake tier.** ChromeDriver version drift is a constant maintenance cost |
| **Behat** | Gherkin acceptance tests | Slow, usually legacy. Rarely worth starting new |
| **PBT libraries (`eris` etc.)** | Property-based testing | PHP's PBT ecosystem is **markedly weaker** than Python's or JS's. **Assess-tier at most** (RESEARCH §16); *verify maintenance before recommending any PHP PBT library* |

```php
// Laravel — the shape to match
Http::preventStrayRequests();                     // a real outbound call now fails the test
Http::fake(['api.stripe.com/*' => Http::response(['id' => 'ch_1'], 200)]);

// Symfony — service-level, no HTTP
self::bootKernel();
$svc = static::getContainer()->get(CartService::class);
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests. In
PHP, derive the shape from the **interface or the typed DTO** — PHPUnit's
`createMock(PaymentGateway::class)` is type-checked against the real class, and
`createStub()`/`createMock()` on an **interface** is strictly safer than
`getMockBuilder()->setMethods()` on a concrete class, which can silently stub
away real behaviour. Prophecy (`phpspec/prophecy`) is also type-aware.

**Never mock what you do not own without a contract test** — a mocked Guzzle
response that matches nothing the real API returns is the canonical PHP
tautological test.

---

## 6. Flake taxonomy — PHP

> **There is no published per-cause flakiness breakdown for PHP** comparable to
> the Java (async 45% / concurrency 20% / order 12%), Python (order 59% /
> infrastructure 28%) or JavaScript (concurrency 21% / async 20% / OS 18% /
> network 13%) figures in RESEARCH §19. Do not invent one and do not reuse
> another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way.

### Structural note that shapes everything

PHP's request-per-process model makes classic concurrency flake rare: there are
no threads and a plain PHPUnit run is sequential in one process. **What remains
is overwhelmingly shared-state and environment flake**, not async flake. Expect
order dependency and global state to dominate — but say "expect", not a number.

What persists across tests in one PHPUnit process: `static` properties,
singletons and service-locator state, `$GLOBALS`, `$_SERVER`/`$_ENV`/`$_SESSION`,
`putenv()`, `date_default_timezone_set()`, `setlocale()`, `ini_set()`,
registered autoloaders and stream wrappers, the booted Laravel/Symfony
container, Eloquent `$booted`/global scopes, and any file written to disk.

### The ones to check first

**Test order dependency.** PHPUnit runs in file order by default and never
shuffles unless told to.

```bash
# Turn shuffling ON and record the seed — the highest-yield PHP flake detector
vendor/bin/phpunit --order-by=random --random-order-seed=12345
vendor/bin/phpunit --order-by=default        # confirm order is the cause
vendor/bin/phpunit --order-by=reverse        # cheap, deterministic, effective
vendor/bin/phpunit --order-by=defects        # previously-failing first
```

```xml
<!-- phpunit.xml — pin it so every run shuffles -->
<phpunit executionOrder="random" resolveDependencies="true" ...>
```

**Process isolation as the diagnostic.** `vendor/bin/phpunit --process-isolation`
runs one process per test — slow, but conclusive: green under isolation and red
otherwise **proves** in-process shared state. Diagnose with it; do not leave it
on. `#[RunTestsInSeparateProcesses]` / `#[PreserveGlobalState(false)]` on a
class, and `@backupGlobals` / `@backupStaticAttributes` (off by default in
modern PHPUnit), are all admissions that someone already hit this — read why and
report the count.

```bash
grep -rn "backupGlobals\|backupStaticAttributes\|RunInSeparateProcess\|PreserveGlobalState" tests/ phpunit.xml | head
grep -rn "static " src/ | grep -v "static function\|static::" | head -20
```

**Parallel execution** (paratest / `pest --parallel` / `artisan test --parallel`)
turns latent shared-*external*-state into real flake: a shared test database, a
fixed Redis DB index, a fixed port, a shared `storage/` dir, a shared S3 bucket.
Laravel creates per-worker databases (`your_db_test_1`, `_2`, …) — anything
**not** the database is still shared. Check for per-worker setup:
`grep -rn "TEST_TOKEN\|ParallelTesting::" tests/`

### Other consensus causes, with their instruments

| Cause | Smell | Instrument |
|---|---|---|
| Time | `time()`/`now()` in an assertion; tests failing at midnight, month end, DST | Laravel `Carbon::setTestNow()` / `$this->travelTo()`; Symfony `ClockMock`; `symfony/clock` `MockClock` |
| Timezone / locale | `date()` output asserted; `setlocale` leaking | pin `date.timezone=UTC` in `phpunit.xml` `<php><ini>`; sweep with `TZ=` |
| Float / money | `assertEquals(0.3, $total)` | `assertEqualsWithDelta($expected, $actual, 0.0001)`; use integer cents |
| Randomness | unseeded `Faker`, `random_int`, `Str::uuid()` in an expected value | seed Faker; freeze UUIDs with `Str::createUuidsUsing()` |
| DB row ordering | `assertEquals([$a,$b], User::all()->toArray())` with no `ORDER BY` | **the DB does not guarantee order without `ORDER BY`** — add one, or compare as sets |
| Filesystem | tests writing to `storage/` or a fixed `/tmp` path | `sys_get_temp_dir()` + `uniqid()`, or `vfsStream` |
| Browser (Dusk/Panther) | fixed `sleep`, `waitFor` missing | use the framework's `waitFor*` helpers; never `sleep` |
| Queues / events | asserting on a job that ran asynchronously | `Queue::fake()`, `Event::fake()`, `Bus::fake()` |

### Detection commands

```bash
# Order — five seeds, then isolate
for s in 1 2 3 4 5; do vendor/bin/phpunit --order-by=random --random-order-seed=$s || echo "FAILED seed=$s"; done
vendor/bin/phpunit --filter CartTest --process-isolation      # proves shared state

# Repeat one test to surface timing flake; serialise parallel runs
for i in $(seq 1 50); do vendor/bin/phpunit --filter 'CartTest::testAddsItem' || break; done
vendor/bin/paratest -p1

# Environment sweep + the CI-only memory limit
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 vendor/bin/phpunit
TZ=UTC vendor/bin/phpunit
vendor/bin/phpunit -d memory_limit=256M
```

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "sleep(\|usleep(" tests/ | head -30
grep -rn "time()\|date(\|new DateTime()\|Carbon::now()\|now()" tests/ | head -20
grep -rn "rand(\|mt_rand\|random_int\|uniqid\|Str::uuid\|faker" tests/ | head -20
grep -rn "static ::\|self::\$\|static \$" tests/ src/ | head -20
grep -rn "putenv\|\$_ENV\[\|\$_SERVER\[\|\$GLOBALS" tests/ | head -20
grep -rn "file_put_contents\|fopen\|/tmp/" tests/ | head -20
grep -rnE "localhost:[0-9]{4,5}|127\.0\.0\.1:[0-9]{4,5}" tests/ | head
grep -rn "markTestSkipped\|markTestIncomplete\|#\[Group\('skip" tests/ | wc -l
grep -rn "assertTrue(true)\|expectNotToPerformAssertions\|assertNotNull(\$" tests/ | head -20
```

Specific smells: `sleep()` as synchronisation · `time()`/`now()` in an
assertion · unseeded Faker/`uniqid()` in an expected value · `static` properties
mutated by tests · `putenv`/`$_SERVER` mutation without restoration · fixed
`/tmp` paths · hardcoded ports · **`markTestSkipped` counts** (tests that
silently stopped existing — report the trend) · `assertTrue(true)` and
`expectNotToPerformAssertions()` (**a test that cannot fail** — tautological,
and a finding) · tests with zero assertions.

```xml
<!-- phpunit.xml — make PHPUnit itself report tautological tests -->
<phpunit beStrictAboutTestsThatDoNotTestAnything="true"
         beStrictAboutOutputDuringTests="true"
         beStrictAboutChangesToGlobalState="true"
         failOnRisky="true"
         failOnWarning="true" ...>
```

`beStrictAboutChangesToGlobalState="true"` is the single most useful flake-
prevention setting in PHPUnit and is off by default. Recommend it early.

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

```bash
ls .env .env.production .env.testing .env.test .env.local 2>/dev/null
grep -rn "APP_ENV\|APP_DEBUG\|DB_CONNECTION\|DB_DATABASE\|DB_HOST" .env* phpunit.xml phpunit.xml.dist 2>/dev/null | head -30
grep -rnE "(mysql|pgsql|postgres(ql)?|mongodb(\+srv)?|redis)://" .env* config/ --include='*' 2>/dev/null \
  | grep -vE "localhost|127\.0\.0\.1|::1|sqlite|test" | head
ls bootstrap/cache/config.php bootstrap/cache/routes-v7.php var/cache/prod 2>/dev/null
grep -rn "RefreshDatabase\|DatabaseMigrations\|migrate:fresh\|doctrine:schema:drop" tests/ | head
grep -rn "MAIL_MAILER\|QUEUE_CONNECTION\|CACHE_DRIVER\|FILESYSTEM_DISK" .env* phpunit.xml 2>/dev/null
```

| Red flag | Why |
|---|---|
| **`.env.production` present and `.env` is a symlink/copy of it** | Laravel loads `.env` at boot; a test run then uses production credentials |
| **`bootstrap/cache/config.php` exists** (Laravel config cache) | **`php artisan config:cache` bakes the resolved config — including production values — into a PHP file that takes precedence over `.env` and `.env.testing` entirely.** A test run against a production-cached config reads the prod database no matter what `.env.testing` says. **`php artisan config:clear` before any test run** |
| `var/cache/prod/` present and `APP_ENV=prod` (Symfony) | the prod container is compiled and may be the one booted |
| `APP_ENV=production` or `APP_ENV=prod` anywhere a test command can see it — CI env, `Dockerfile`, `Makefile`, a `composer script` | the suite boots production config |
| `phpunit.xml` `<php><env name="DB_DATABASE" value="..."/>` pointing at a non-test database | **`RefreshDatabase` / `migrate:fresh` will drop and recreate it** |
| `DB_*` variables exported in the CI environment | **environment variables beat `.env` in Laravel** — the `.env.testing` safety net does not apply |
| `RefreshDatabase` / `DatabaseMigrations` / `migrate:fresh` with no DB-name guard | **destroys whatever database is configured** |
| Symfony `doctrine:schema:drop --force` in a test bootstrap | same |
| `MAIL_MAILER` not `array`/`log` in the test env | **real emails to real customers** |
| `QUEUE_CONNECTION` not `sync`/`null` in the test env | tests enqueue real background jobs |
| `CACHE_DRIVER=redis` pointing at the production Redis | tests poison the production cache |
| `FILESYSTEM_DISK=s3` with no fake | real uploads; `Storage::fake('s3')` is the fix |
| `Http::preventStrayRequests()` absent (Laravel) / no outbound HTTP block | any test can hit any real API |
| Stripe/Twilio/SendGrid keys matching `sk_live_`/`SG.` in `.env` reachable from tests | live-money side effects |
| `APP_KEY` from production in a test env | decryptable production payloads in test logs |

Assert the guard, don't assume it:

```php
// tests/TestCase.php (Laravel) or tests/bootstrap.php (Symfony)
$env = $_ENV['APP_ENV'] ?? getenv('APP_ENV') ?: '';
if (str_contains(strtolower($env), 'prod')) { fwrite(STDERR, "Refusing: APP_ENV={$env}\n"); exit(3); }

$db = config('database.connections.'.config('database.default').'.database') ?? '';
$safe = ['test', ':memory:', 'localhost', '127.0.0.1'];
if ($db !== '' && !array_filter($safe, fn($s) => str_contains((string) $db, $s))) {
    fwrite(STDERR, "Refusing to run tests against database {$db}\n"); exit(3);
}
if (file_exists(base_path('bootstrap/cache/config.php'))) {
    fwrite(STDERR, "Refusing: config cache present; run `php artisan config:clear`\n"); exit(3);
}
```

The config-cache check is PHP-specific and worth making loudly: it is the one
production-danger mechanism in this stack that is **invisible in every config
file you would think to read.**

---

## 8. House conventions to match

```bash
jq -r '.["autoload-dev"]' composer.json
ls tests/
grep -n "<testsuite" -A 3 phpunit.xml phpunit.xml.dist 2>/dev/null
ls tests/Pest.php database/factories tests/Factories 2>/dev/null
```

| Convention | Rule |
|---|---|
| Test location | `tests/`, mapped by `autoload-dev.psr-4` (commonly `"Tests\\": "tests/"` in Laravel, `"App\\Tests\\": "tests/"` in Symfony). **The PSR-4 map is authoritative** |
| Suite split | `tests/Unit/` and `tests/Feature/` (Laravel); `tests/Unit/`, `tests/Integration/`, `tests/Functional/` (Symfony/libraries). Suite names are defined in `phpunit.xml` `<testsuite>` |
| Mirror | `src/Cart/CartService.php` → `tests/Unit/Cart/CartServiceTest.php`; `app/Services/X.php` → `tests/Unit/Services/XTest.php` |
| File naming | `<Subject>Test.php` — the `Test` suffix is required by the default `suffix="Test.php"` in `phpunit.xml`. A file named otherwise **is silently never run** |
| Class naming | `final class CartServiceTest extends TestCase` |
| Method naming | `public function testAddsItemToEmptyCart(): void` (prefix style) or `#[Test] public function it_adds_item_to_empty_cart(): void` (attribute style). **Match the file you are editing** |
| Pest naming | `it('adds an item to an empty cart', function () { ... });` / `test('...')` |
| Data providers | `#[DataProvider('cartCases')]` + `public static function cartCases(): array` — **the provider must be `static` in PHPUnit 10+**, a very common silent breakage |
| Setup/teardown | `protected function setUp(): void { parent::setUp(); ... }` — **the `parent::setUp()` call is mandatory** in framework test cases; omitting it produces baffling failures |
| Pest hooks | `beforeEach()`, `afterEach()` in the file or in `tests/Pest.php` |
| Global test config | `tests/Pest.php` (`uses(TestCase::class)->in('Feature')`), `tests/bootstrap.php`, `phpunit.xml` `<php>` block |
| Factories | Laravel: `database/factories/CartFactory.php` (`Cart::factory()->create()` / `->make()` — **`make()` does not touch the DB and is much faster**). Symfony: `zenstruck/foundry` factories in `tests/Factory/` |
| Fixtures | Doctrine fixtures in `src/DataFixtures/`; static files in `tests/Fixtures/`, `tests/fixtures/`, `tests/_data/` |
| Mocking idiom | PHPUnit `$this->createMock(PaymentGateway::class)` / `createStub()`; `Mockery::mock(PaymentGateway::class)` (common in Laravel, needs `Mockery::close()` in teardown — the `MockeryPHPUnitIntegration` trait does it); Prophecy in older Symfony. **Never mix Mockery and PHPUnit mocks in one test** |
| Laravel fakes | `Http::fake()`, `Queue::fake()`, `Event::fake()`, `Mail::fake()`, `Storage::fake()`, `Bus::fake()` — prefer these over mocking the facade |
| Assertions | `self::assertSame()` over `assertEquals()` (strict comparison; `assertEquals` does loose typing and will pass `'1' == 1`). Laravel: `$response->assertOk()->assertJsonPath(...)` |
| Groups | `#[Group('slow')]` / `@group slow` — reuse existing groups; read `phpunit.xml` for `<exclude>` |

---

## 9. Static analysis + type checking — rank first on every roadmap

PHP's static analysers are unusually good, and since PHP's own type system is
partial, they carry more of the load than in a compiled language. These are the
cheapest wins in the stack.

```bash
# Tier 0 — free, no install beyond composer
composer validate --strict && composer audit          # config sanity + known CVEs
vendor/bin/phpunit --list-tests > /dev/null           # does the suite even load?

# Tier 1 — a static analyser. PICK ONE; do not run both.
vendor/bin/phpstan analyse --memory-limit=1G --error-format=json > .testsquad/phpstan.json
vendor/bin/psalm --output-format=json > .testsquad/psalm.json
vendor/bin/psalm --taint-analysis                    # security data-flow — Psalm's edge

# DIFF-SCOPED — the RESEARCH §6 pattern (~70% fix rate scoped, ~0% as a backlog)
vendor/bin/phpstan analyse $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.php' | tr '\n' ' ')
vendor/bin/psalm --diff $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.php' | tr '\n' ' ')

# Tier 1 — style, autofixable, stop arguing about it
vendor/bin/php-cs-fixer fix --dry-run --diff
vendor/bin/phpcbf --standard=PSR12 src               # autofix (phpcs to check)
vendor/bin/pint --test ; vendor/bin/pint --dirty     # Laravel wrapper; --dirty = changed only

# Tier 2
vendor/bin/rector process --dry-run                   # automated upgrades + dead code
vendor/bin/phpmd src text cleancode,codesize,unusedcode
vendor/bin/deptrac analyse                            # architecture/layer boundaries
```

### PHPStan vs Psalm

| | PHPStan | Psalm |
|---|---|---|
| Levels | 0–10 (`max`); ratchet one level at a time | `errorLevel` 1–8 (1 strictest); plus per-issue config |
| Baseline | `phpstan-baseline.neon` (`--generate-baseline`) | `psalm-baseline.xml` (`--set-baseline`) |
| Framework support | **Laravel: `larastan` is excellent and near-mandatory.** Symfony: `phpstan-symfony` | `psalm-plugin-symfony`, `psalm-plugin-laravel` (weaker) |
| Distinctive strength | ecosystem, extension availability, Laravel support | **taint analysis** (security data-flow), stricter generics, `@psalm-*` annotations |
| Recommendation | **default for Laravel and most repos** | choose for security-sensitive code or where it is already in use |

**Do not introduce both.** Pick the one the repo already has; if neither, pick
PHPStan (+ larastan for Laravel).

The ratchet: `vendor/bin/phpstan analyse --generate-baseline` freezes the legacy
backlog, then raise the level one notch at a time, regenerating the baseline and
requiring that new code adds no new entries.

```neon
# phpstan.neon
includes:
    - vendor/nunomaduro/larastan/extension.neon
    - vendor/phpstan/phpstan-phpunit/extension.neon
parameters:
    level: 6
    paths: [src, app, tests]
    checkMissingIterableValueType: true
    treatPhpDocTypesAsCertain: false
```

**Include `tests/` in the analysed paths.** Most repos analyse only `src/`, so
the test code — where mock shapes silently diverge from the real object — is
never checked. `phpstan/phpstan-phpunit` specifically understands
`createMock()` return types, which is how you catch a mock that no longer
matches its interface.

Checks worth enabling, in roadmap order:

| Check | Catches | Why early |
|---|---|---|
| `declare(strict_types=1)` at the top of every file | silent type juggling (`'1' == 1`, `"abc" == 0`) | the single highest-value one-line change in PHP. Count how many files lack it |
| PHPStan level 5–6 | undefined methods/properties, wrong argument types, impossible conditions | the real-bug band; levels 0–4 are mostly noise reduction |
| `phpstan/phpstan-phpunit` | wrong mock types, `assertInstanceOf` that can never hold | **targets the §5 tautology cause** |
| `phpstan/phpstan-strict-rules` | loose comparisons, unneeded casts, boolean-ish conditions | tier 2 |
| `larastan` | Eloquent magic (dynamic properties, relation return types) resolved rather than ignored | Laravel-specific and large |
| PHPStan levels 7–10 | mixed types, missing iterable value types | tier 3; large effort, diminishing returns |
| `php-cs-fixer` / `pint` / `phpcs` PSR-12 | formatting | autofix it, then stop discussing it |
| `rector` `deadCode` set | unreachable code, unused private methods | one-time cleanup, big diff |
| `composer audit` | known CVEs | free, run in CI |

Config to read before trusting any clean run:

```bash
cat phpstan.neon phpstan.neon.dist psalm.xml 2>/dev/null | head -60
wc -l phpstan-baseline.neon psalm-baseline.xml 2>/dev/null   # the silenced backlog
grep -rn "@phpstan-ignore\|@psalm-suppress\|phpcs:ignore\|@phpstan-ignore-line" src/ app/ tests/ | wc -l
grep -rn "declare(strict_types=1)" src/ app/ | wc -l
find src app -name '*.php' 2>/dev/null | wc -l       # compare with the line above
grep -n "excludePaths\|ignoreErrors" -A 10 phpstan.neon 2>/dev/null
```

A large or growing baseline, `@phpstan-ignore`/`@psalm-suppress` count, or a
low `declare(strict_types=1)` ratio means findings are being silenced rather
than fixed. **Report the count and its trend, not a clean run.** Check
specifically whether `excludePaths` drops `tests/` — if so, the test code is
unanalysed, and that is where the mock-shape drift lives.
