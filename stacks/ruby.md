# Stack playbook — Ruby

Reference for a testsquad agent working in a Ruby repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

Two things dominate Ruby: **almost every Ruby repo of consequence is a Rails
app**, so Rails-specific behaviour is not a sub-case, it is the main case; and
**Ruby has no static type checker by default**, so §9 is where the largest
untapped wins usually are.

---

## 1. Detection signals

```bash
ls Gemfile Gemfile.lock *.gemspec Rakefile .ruby-version .rubocop.yml 2>/dev/null
ls config/application.rb bin/rails config/environments 2>/dev/null   # Rails
ruby -v && bundle -v
grep -nE "^ruby |rails|rspec|minitest|sorbet|rbs|steep" Gemfile
```

| Marker | Conclusion |
|---|---|
| `config/application.rb` + `bin/rails` | **Rails app.** Everything in §2/§3/§6/§7 changes — use the Rails rows |
| `*.gemspec` + `lib/` and no `config/` | **a gem**, not an app. `rake test`/`rake spec` is the canonical entry; no DB, no fixtures |
| `config/environments/{development,test,production}.rb` | Rails environments — `RAILS_ENV` selects one. See §7 |
| `Gemfile.lock` | the authoritative versions. **Always `bundle exec`** — a bare `rspec` can resolve a different gem version |
| `.ruby-version` / `.tool-versions` | the expected interpreter. A mismatch produces confusing native-extension errors |
| `Rakefile` with a `task :default` | the repo's canonical command: read `rake -T` |
| `bin/setup`, `bin/ci`, `Makefile`, `Procfile.dev` | the repo's real entry points — read before composing your own |
| `app/` with `models/ controllers/ jobs/ mailers/` | Rails MVC layout; test directories mirror it |
| `engines/` or `components/` with their own `Gemfile` | a modular monolith — tests run per engine as well as at the root |
| `config/database.yml` | DB config per environment. `test:` section must exist and must not be prod |

### Test framework — RSpec vs Minitest

```bash
ls spec/spec_helper.rb spec/rails_helper.rb .rspec 2>/dev/null     # RSpec
ls test/test_helper.rb 2>/dev/null                                  # Minitest
grep -nE "rspec|minitest|test-unit|shoulda|cucumber|capybara|factory_bot|fixtures" Gemfile
find spec test -name '*_spec.rb' -o -name '*_test.rb' 2>/dev/null | head -20
```

| Marker | Framework | Canonical command |
|---|---|---|
| `spec/` + `.rspec` + `spec_helper.rb` | **RSpec** | `bundle exec rspec` |
| `spec/rails_helper.rb` | RSpec **+ rspec-rails** | `bundle exec rspec` |
| `test/` + `test_helper.rb` + `class X < ActiveSupport::TestCase` | **Minitest** (Rails default) | `bin/rails test` |
| `class X < Minitest::Test` and no Rails | plain Minitest | `bundle exec rake test` or `ruby -Itest test/x_test.rb` |
| `test/` + `Test::Unit` | legacy test-unit | treat as Minitest-compatible; flag as a migration item |
| both `spec/` and `test/` | **both** — common in long-lived Rails apps. Run both; write new tests in the directory the file under test is covered by |
| `features/*.feature` | Cucumber | slow, usually legacy. `bundle exec cucumber` |

Supporting gems that change what idiomatic looks like:

```bash
grep -nE "factory_bot|faker|ffaker|shoulda-matchers|rspec-mocks|mocha|webmock|vcr|timecop|rails-controller-testing|capybara|selenium|cuprite|database_cleaner|rspec-retry|simplecov|parallel_tests|knapsack|rswag|super_diff" Gemfile
```

| Gem | Means |
|---|---|
| `factory_bot` / `factory_bot_rails` | factories in `spec/factories/` or `test/factories/` — **not** Rails fixtures |
| Rails `fixtures` (`test/fixtures/*.yml`) | the Rails-native alternative; `fixtures :all` in `test_helper.rb` |
| `shoulda-matchers` | one-liner model matchers (`should validate_presence_of(:name)`) |
| `mocha` | an alternative to `rspec-mocks`/`minitest/mock`. Do not mix |
| `webmock` / `vcr` | HTTP stubbing / cassettes |
| `timecop` / `ActiveSupport::Testing::TimeHelpers` | time freezing |
| `database_cleaner` | present usually because transactional fixtures were insufficient — **read why** (see §6) |
| `rspec-retry` | **flake is being hidden.** A finding, not a fix |
| `parallel_tests` / `knapsack` | parallel suite; raises the stakes on shared state |
| `capybara` + `selenium-webdriver`/`cuprite` | system/feature tests — the 10%-flake tier |

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. A Rails suite's **boot time alone** (loading the whole
app environment) is seconds before a single test runs — scoping is not optional.

**Ruby has no built-in import-graph test selector.** The nearest things, best
first:

```bash
# 1. Changed spec/test files themselves — always run these, always cheap
CHANGED_SPECS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD \
                -- 'spec/**/*_spec.rb' 'test/**/*_test.rb')
[ -n "$CHANGED_SPECS" ] && bundle exec rspec $CHANGED_SPECS
[ -n "$CHANGED_SPECS" ] && bin/rails test $CHANGED_SPECS

# 2. Convention mapping: app/ -> spec/ or test/. Works because Rails enforces layout.
MAPPED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'app/**/*.rb' 'lib/**/*.rb' \
  | sed -e 's|^app/|spec/|' -e 's|^lib/|spec/lib/|' -e 's|\.rb$|_spec.rb|' \
  | while read f; do [ -f "$f" ] && echo "$f"; done)
[ -n "$MAPPED" ] && bundle exec rspec $MAPPED

# Minitest equivalent
MAPPED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'app/**/*.rb' 'lib/**/*.rb' \
  | sed -e 's|^app/|test/|' -e 's|^lib/|test/lib/|' -e 's|\.rb$|_test.rb|' \
  | while read f; do [ -f "$f" ] && echo "$f"; done)
[ -n "$MAPPED" ] && bin/rails test $MAPPED

# 3. Directory scoping when the diff is confined to one area
bundle exec rspec spec/models/cart_spec.rb
bundle exec rspec spec/models            # fastest tier; no HTTP, no browser
bin/rails test test/models

# 4. --only-failures / --next-failure — not change scoping, but the right
#    second command after a failure and nearly free.
bundle exec rspec --only-failures        # needs `config.example_status_persistence_file_path`
bundle exec rspec --next-failure         # one failure at a time
bin/rails test --failfast

# 5. Everything (CI, not hooks)
bundle exec rspec
bin/rails test && bin/rails test:system
```

**`bin/rails test` does NOT run system tests.** `bin/rails test` covers
`test/` except `test/system`; `bin/rails test:all` includes them. A green
`bin/rails test` says nothing about the system suite — state that in any report.

Flags that make a hook survivable:

```bash
bundle exec rspec spec/models --tag '~slow' --tag '~js'
bundle exec rspec --fail-fast
bin/rails test test/models -f           # fail fast
bundle exec spring rspec spec/models    # if spring is in the Gemfile: preloaded app
```

An empty selection must not fail the hook. `rspec` with no files exits 0 after
running the whole suite — which is exactly the disaster you are avoiding:

```bash
[ -z "$SELECTION" ] && exit 0            # guard FIRST; never pass an empty list to rspec
bundle exec rspec $SELECTION
```

### Full suite / single file / single test

| Goal | RSpec | Minitest / Rails |
|---|---|---|
| Whole suite | `bundle exec rspec` | `bin/rails test` (+ `test:system`) |
| One file | `bundle exec rspec spec/models/cart_spec.rb` | `bin/rails test test/models/cart_test.rb` |
| One example by line | `bundle exec rspec spec/models/cart_spec.rb:42` | `bin/rails test test/models/cart_test.rb:42` |
| By name | `bundle exec rspec -e "adds an item"` | `bin/rails test -n "/adds an item/"` |
| By tag / exclude tag | `-t focus` / `-t ~slow` | `bin/rails test -n "/.../"`; tags via `TestProf` or custom |
| Directory | `bundle exec rspec spec/models` | `bin/rails test test/models` |
| Fail fast | `--fail-fast` / `--fail-fast=3` | `-f` |
| Only previous failures | `--only-failures` | — |
| Seed / order | `--seed 12345` / `--order random` / `--order defined` | `TESTOPTS="--seed=12345"` / `bin/rails test --seed 12345` |
| Slowest examples | `--profile 20` | `-v` + timing, or `minitest-reporters` |
| JUnit XML for CI | `-f RspecJunitFormatter -o results.xml` (gem `rspec_junit_formatter`) | `minitest-reporters` JUnit reporter |
| Parallel | `bundle exec parallel_rspec spec/` (gem `parallel_tests`) | `bin/rails test` is parallel by default (`parallelize(workers: :number_of_processors)`) |
| System/browser tests | `bundle exec rspec spec/system` | `bin/rails test:system` |

Confirm the selection matched something. **`0 examples, 0 failures` exits 0** in
RSpec, so a hook checking only the exit code reports success for a run that
tested nothing:

```bash
bundle exec rspec $SELECTION 2>&1 | grep -q "0 examples" && echo "NOTHING RAN"
```

---

## 3. Coverage

SimpleCov is the only real option. The normalizer parses **Cobertura XML** and
**lcov**; SimpleCov needs a formatter gem for either — its default HTML and
`.resultset.json` are **not** parseable.

```ruby
# Gemfile
group :test do
  gem "simplecov", require: false
  gem "simplecov-cobertura", require: false   # -> coverage/coverage.xml  (parsed)
  # or
  gem "simplecov-lcov", require: false        # -> coverage/lcov/*.lcov   (parsed)
end
```

```ruby
# spec/spec_helper.rb (or test/test_helper.rb) — MUST be the very first thing
# loaded, before the app, or coverage misses everything required at boot.
require "simplecov"
require "simplecov-cobertura"

SimpleCov.start "rails" do
  enable_coverage :branch          # <-- BRANCH COVERAGE. Off by default.
  primary_coverage :branch         # make branch the reported headline figure
  formatter SimpleCov::Formatter::CoberturaFormatter
  add_filter %w[/spec/ /test/ /config/ /db/ /vendor/ /bin/]
  add_group "Services", "app/services"
  track_files "{app,lib}/**/*.rb"  # count never-loaded files as 0%, not as absent
end
```

```bash
COVERAGE=1 bundle exec rspec
COVERAGE=1 bin/rails test
ls coverage/                        # coverage.xml, index.html, .resultset.json
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage/coverage.xml` (simplecov-cobertura) | Cobertura XML | `cobertura` ✅ |
| `coverage/lcov/*.lcov` (simplecov-lcov) | lcov | `lcov` ✅ |
| `coverage/.resultset.json` | SimpleCov internal | **not parsed** |
| `coverage/index.html` | HTML | humans only |

### Branch coverage

**Ruby has real branch coverage** (MRI 2.5+ `Coverage` with `branches: true`),
exposed by SimpleCov as `enable_coverage :branch`. It is **off by default** — if
nobody set it, branch data does not exist and the honest report is `null`
("not measured"), never 0%.

Two caveats to state rather than gloss:

- `enable_coverage :branch` must be set **before** `SimpleCov.start` finishes
  booting and before the app is required. Set after, it silently records nothing.
- Whether `simplecov-cobertura` writes per-line `condition-coverage` attributes
  (the thing the normalizer reads for branch data) depends on the formatter
  version. **Verify before relying on this**: run it once, then
  `grep -c 'condition-coverage' coverage/coverage.xml`. If the count is 0, branch
  data did not survive the formatter and you must report `branch_pct: null` and
  say the formatter dropped it — do not report 0%.

```bash
grep -c 'condition-coverage' coverage/coverage.xml || echo "no branch data in the XML"
```

Parallel runs (Rails' default parallel testing, or `parallel_tests`) write one
result set per worker and must be merged:

```ruby
# test_helper.rb — Rails parallel testing
parallelize_setup    { |worker| SimpleCov.command_name "worker-#{worker}" }
parallelize_teardown { |_|      SimpleCov.result }
SimpleCov.at_exit { SimpleCov.result.format! }
```

Without this, coverage reflects **one worker only** and will look catastrophic
for no reason. Check for it before reporting a low Rails coverage number.

Normalize:

```bash
python3 scripts/normalize_coverage.py coverage/coverage.xml \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

Do not gate on `SimpleCov.minimum_coverage 80` project-wide: it fails on legacy
debt the current change did not cause. `minimum_coverage_by_file` is slightly
better but still a project gate. Compute the diff figure from the normalized
JSON. (`undercover` is the gem that does diff-scoped coverage natively —
*verify its current maintenance before recommending it.*)

---

## 4. Mutation testing — mutant

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| **`mutant`** (mbj/mutant, ~2.2k stars) | **Actively maintained** | the only serious option |
| `mutant-rspec` / `mutant-minitest` | integration gems, same repo | pick the one matching your framework |
| `mutest` | a fork of an old mutant | unmaintained; do not adopt |
| `mutant-license` | **required for commercial/closed-source use** | see below |

> **Licensing, state it before recommending:** mutant is free for open-source
> projects; **commercial use requires a paid licence** (the `mutant-license`
> gem, resolved from a private source). A closed-source repo cannot simply
> `bundle add mutant` and run. Raise this as the first question, not a footnote.
> *Verify current licence terms before advising a purchase.*

```ruby
# Gemfile
group :test do
  gem "mutant-rspec"      # or "mutant-minitest"
  # gem "mutant-license", source: "https://oss:<token>@gem.mutant.dev"  # commercial
end
```

### Diff-scoped invocation — the one to use

```bash
# mutant's incremental mode: only subjects touched since a ref — the diff gate
bundle exec mutant run --since origin/main --integration rspec --fail-fast

# Explicit subject scoping (the reliable form)
bundle exec mutant run --include lib --require acme --integration rspec 'Acme::Cart#add_item'
```

| Flag | What it does |
|---|---|
| `--since <ref>` | **restrict subjects to code changed since that ref** — the diff gate |
| `--integration rspec` / `minitest` | which runner executes the mutants |
| `--include <dir>` / `--require <file>` | load path and bootstrap — mutant must be able to `require` your code without Rails if possible |
| `--fail-fast` | stop on the first alive mutant |
| `--jobs N` | parallel workers (forks) |
| `--zombie` | run in an isolated namespace; needed for some Rails setups |
| `'Acme::Cart#add_item'` | subject expression: class, `#instance_method`, `.class_method`, or a namespace prefix |

Config file:

```yaml
# config/mutant.yml
requires: ["./config/environment"]   # Rails; slow boot, usually unavoidable
integration: { name: rspec }
includes: ["lib", "app"]
matcher:
  subjects: ["Acme::Cart*"]
  ignore: ["Acme::Cart#to_s", "Acme::Cart#inspect"]
```

Reading results: mutant prints `alive` mutants with a coloured diff of the
source change it made. **`alive` is the finding**; `killed` is fine;
`timeout` is counted as killed but a large count means the subject is too slow
to mutate usefully.

### Mutant's two hard constraints — know them before promising a roadmap item

1. **Mutant demands near-complete coverage of the subject.** It aborts with
   "Mutation analysis is not possible, subject is not covered" when the selected
   tests do not exercise a subject. This is by design and it is strict: mutant
   is harder to adopt incrementally than Infection or Stryker.
2. **Rails boot cost is paid per worker.** Mutant forks a process per mutant; a
   Rails app with a 5s boot makes even a small run expensive. Scope aggressively
   to plain-Ruby service objects in `app/services/` or `lib/` first — they are
   where mutation testing pays, and where mutant runs fastest.

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**.

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste. Mutant has
   no one-per-line flag; **post-filter the alive list to one mutant per line and
   ≤7 per file.**
2. **Arid-node suppression** — never mutate: `Rails.logger`/`logger.*` calls,
   StatsD/Datadog emitters, `to_s`/`inspect`/`as_json`, trivial `attr_reader`
   getters, pure delegation (`delegate :x, to: :y`), exception *message*
   strings, `db/migrate/`, `db/schema.rb`, generated code. Via `matcher.ignore`
   in `config/mutant.yml`. **This step matters more than the choice of tool** —
   it is the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff: `--since origin/main`.

Blind spots: (a) a change in file A destroying coverage of file B is invisible;
(b) **a test-only change mutates nothing**, so deleting assertions passes the
gate; (c) fixture/factory changes flip mutant status silently. Mitigation: an
occasional full run off the critical path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **Rails request specs** (`spec/requests/`, `ActionDispatch::IntegrationTest`) | **The default integration seam.** Full middleware stack, routing, real controller, real DB — no browser | By far the best value in Rails. Prefer these over controller specs (deprecated) and over system specs wherever JS is not involved |
| **Rails system tests / RSpec system specs** (`spec/system/`) | Real browser, real JS | **The 10%-flake tier.** Driver downloads, Capybara waits, screenshot debugging. Keep the count small and the assertions coarse |
| **`rack-test` driver** (Capybara, no JS) | Feature flows without a browser | Fast and stable, but silently skips everything JS-driven — a test can "pass" a flow the user cannot complete |
| **`cuprite` (CDP) vs `selenium-webdriver`** | system-test driver | cuprite is faster with no webdriver binary to drift; selenium covers more browsers |
| **`webmock`** | Block **all** real HTTP in tests | `WebMock.disable_net_connect!(allow_localhost: true)` in `spec_helper` is the single highest-value line in a Ruby test suite. Add it if missing |
| **`vcr`** | Record/replay a real API once | **Cassettes rot silently** — a stale cassette is a test passing against an API that changed. Set `record: :none` in CI and re-record deliberately |
| **Testcontainers (ruby gem)** | Real Postgres/Redis/Kafka | Docker daemon; container startup costs seconds, not milliseconds — time it in your own suite. Less common in Ruby than elsewhere; most teams run real local services instead |
| **`database_cleaner`** | Truncation when transactions cannot be used (JS system tests) | Far slower. Its presence usually means someone hit the system-test transaction problem — see §6 |
| **`factory_bot`** | Object graphs for tests | `create` hits the DB; `build`/`build_stubbed` do not. **`build_stubbed` avoids the database entirely** — usually the cheapest large win in a Rails suite. Confirm it on yours with `bundle exec rspec --profile` before and after |
| **Rails fixtures** (`test/fixtures/*.yml`) | Static shared data, loaded once per suite in a transaction | Very fast; but shared mutable data across the whole suite, and the classic source of cross-test coupling |
| **`rswag` / `committee`** | Assert responses against an OpenAPI schema | Real contract value if a schema already exists |
| **`shoulda-matchers` model specs** | Validations/associations | Nearly free, but **asserts the declaration, not the behaviour** — a textbook tautological test. Do not count them as coverage of business logic |
| **`rantly` / `propcheck`** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16), and Ruby's PBT ecosystem is markedly weaker than Python's or JS's. *Verify maintenance before recommending.* |

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests. Ruby
makes this worse than any other stack here: `double("Thing", foo: 1)` will
happily answer a method the real object does not have. Use **verifying doubles
exclusively**:

```ruby
# spec/spec_helper.rb — make this the default, repo-wide
RSpec.configure do |config|
  config.mock_with :rspec do |mocks|
    mocks.verify_partial_doubles = true     # partial doubles check the real method
  end
end
```

```ruby
instance_double(PaymentGateway, charge: Result.ok)   # verifies signature + existence
class_double(PaymentGateway).as_stubbed_const
# NOT: double(charge: ...)  -- unverified, will outlive the real method it mimics
```

`verify_partial_doubles = true` and `instance_double` over `double` are the two
highest-value changes to most Ruby suites. An unverified `double` is a test that
keeps passing after the method it stubs is renamed or deleted.

---

## 6. Flake taxonomy — Ruby

> **There is no published per-cause flakiness breakdown for Ruby** comparable
> to the Java (async 45% / concurrency 20% / order 12%), Python (order 59% /
> infrastructure 28%) or JavaScript (concurrency 21% / async 20% / OS 18% /
> network 13%) figures in RESEARCH §19. Do not invent one and do not reuse
> another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way.
That said, Ruby's structure strongly resembles Python's: **one process, one
loaded object space, tests mutating shared global state**, so expect order
dependency to dominate — but say "expect", not "59%".

### The two to check first

**Order dependency.** RSpec defaults to `--order defined` unless configured
otherwise, and many suites never shuffle, so order bugs accumulate invisibly for
years. Ruby makes shared state easy: class-level `@@` variables and `@ivars` on
the class, memoised constants, `ENV` mutation, `Rails.cache`,
`ActiveRecord` class attributes, `Time.zone`, a monkeypatch applied in one spec,
a `before(:all)` block's leftovers, FactoryBot sequence counters, and anything
`Singleton`.

```bash
# Turn shuffling ON and record the seed. This is the single highest-yield
# Ruby flake detector.
bundle exec rspec --order random
bundle exec rspec --order random:12345     # reproduce exactly
bundle exec rspec --seed 12345
bundle exec rspec --order defined          # confirm order is the cause

# Minitest / Rails — shuffled by default; pin the seed to reproduce
bin/rails test --seed 12345
TESTOPTS="--seed=12345" bin/rails test

# Pin it in config so every run shuffles
# spec/spec_helper.rb:
#   config.order = :random
#   Kernel.srand config.seed
```

```bash
# Bisect the polluter mechanically
gem install rspec-bisect 2>/dev/null   # or:
bundle exec rspec --bisect --seed 12345
# rspec --bisect reduces a failing ordered run to the minimal pair of examples
# that reproduce it. It is slow and it is the correct tool. Use it.

# Does it pass alone?
bundle exec rspec spec/models/cart_spec.rb:42
```

**Rails transactional fixtures and their limits.** `use_transactional_tests`
(Minitest) / `config.use_transactional_fixtures = true` (RSpec) wraps each test
in a DB transaction and rolls it back — clean, fast, and the reason most Rails
suites are not order-dependent at the DB layer. It **breaks in three places**:

1. **System/JS tests**: the browser runs in a separate process with its own DB
   connection and cannot see the uncommitted transaction. Rails handles this for
   its own system tests by sharing the connection, but any custom server thread
   or external process does not. This is why `database_cleaner` with a
   `truncation` strategy exists in so many Gemfiles.
2. **Code that commits explicitly** or uses `after_commit` callbacks — these do
   not fire inside a never-committed transaction unless `test_after_commit`-style
   support is enabled (modern Rails handles this; older apps do not).
3. **Multiple databases / connection pools** — a second connection sees none of
   the transaction's data.

Everything *outside* the DB is **not** rolled back: `Rails.cache`,
`ActiveJob` enqueued jobs, uploaded files, `ENV`, memoised class state, Redis,
Elasticsearch. Those are the leaks to hunt.

```bash
grep -rn "use_transactional_fixtures\|use_transactional_tests\|DatabaseCleaner" spec/ test/ | head
grep -rn "before(:all)\|before(:context)\|setup do" spec/ test/ | head -20   # NOT rolled back
grep -rn "Rails.cache\|REDIS\|\$redis" spec/ test/ | head
```

`before(:all)` / `before(:context)` data is created **outside** the per-example
transaction and persists across examples — it is RSpec's single most common
order-dependency source. Prefer `let`/`before(:each)`.

### Other consensus causes, with their instruments

| Cause | Smell | Instrument |
|---|---|---|
| Capybara async waits | `sleep 2`, `find(...).click` immediately after a JS action | `Capybara.default_max_wait_time`, use `have_content`/`have_selector` matchers (which retry) instead of `page.text` (which does not) |
| Time | `Time.now` in an assertion, tests that fail at midnight / month end / DST | `freeze_time` / `travel_to` (ActiveSupport) or `Timecop.freeze`; **always with a block or an `after { travel_back }`** |
| Randomness | unseeded `Faker`, `SecureRandom` in an expected value | `Faker::Config.random = Random.new(42)`; `srand` from the RSpec seed |
| Parallel workers | Rails parallel tests + a shared external resource (Redis DB 0, a fixed port, a shared S3 bucket) | give each worker its own index: `ENV["TEST_ENV_NUMBER"]` |
| Float / BigDecimal | `expect(total).to eq 0.3` | `be_within(0.001).of(0.3)` |
| Hash/array ordering | asserting `User.all` order without an `ORDER BY` | **Postgres does not guarantee row order without `ORDER BY`** — add one, or use `match_array` |
| Eager loading | passes in test (lazy autoload), fails in production (eager load) | `config.eager_load = true` in the test environment at least in CI |

### Detection commands

```bash
# Five seeds, then bisect the failing one
for i in 1 2 3 4 5; do bundle exec rspec --order random:$i || echo "FAILED seed=$i"; done
bundle exec rspec --bisect --seed <failing seed>
bin/rails test --seed $RANDOM

# Repeat one example to surface timing flake; serialise Rails parallel testing
for i in $(seq 1 50); do bundle exec rspec spec/models/cart_spec.rb:42 || break; done
PARALLEL_WORKERS=1 bin/rails test

# Environment sweep
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 bundle exec rspec
TZ=UTC bundle exec rspec

# Confirm no real network is possible
grep -rn "disable_net_connect" spec/ test/ || echo "WebMock is NOT blocking real HTTP"
```

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "sleep " spec/ test/ | head -30
grep -rn "Time.now\|Date.today\|DateTime.now\|Time.current" spec/ test/ | head -20
grep -rn "SecureRandom\|rand(\|Faker::" spec/ test/ | head -20
grep -rn "before(:all)\|before(:context)" spec/ | head -20
grep -rn "@@\|class_variable" app/ lib/ | head -20
grep -rn "ENV\[" spec/ test/ | head -20
grep -rn "rspec-retry\|retry:\|flaky" spec/ Gemfile | head
grep -rn "double(" spec/ | grep -v "instance_double\|class_double\|object_double" | wc -l
grep -rn "allow_any_instance_of" spec/ | wc -l
grep -rn "xit \|skip \|pending " spec/ test/ | wc -l
```

Specific smells: `sleep` as synchronisation · `Time.now` in an assertion ·
unseeded `Faker`/`SecureRandom` · `before(:all)` creating records · class
variables mutated by tests · `ENV[]=` without restoration (use
`ClimateControl` or `stub_const`) · **unverified `double(...)`** (count it; a
large count is a report-worthy finding) · `allow_any_instance_of` (it stubs
globally and is order-sensitive) · `xit`/`skip` counts (tests that silently
stopped existing) · **`rspec-retry` or a CI `retry` block — flake being hidden,
and a finding in its own right.**

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).
A test that only passes with `rspec-retry` is not a passing test.

---

## 7. Production-danger signals

```bash
grep -rn "RAILS_ENV\|RACK_ENV" . --include='*.rb' --include='*.yml' --include='*.yaml' --include='Dockerfile' --include='Makefile' --include='*.sh' | grep -i prod | head
cat config/database.yml 2>/dev/null
grep -rnE "(postgres(ql)?|mysql2?|mongodb(\+srv)?|redis)://" config/ .env* --include='*' 2>/dev/null \
  | grep -vE "localhost|127\.0\.0\.1|::1|test" | head
ls .env .env.production config/credentials/production.key config/master.key 2>/dev/null
grep -rn "dotenv\|Dotenv.load" config/ spec/ test/ Gemfile | head
grep -rn "ActiveJob\|perform_later\|Sidekiq" config/environments/test.rb | head
grep -rn "delivery_method\|perform_deliveries" config/environments/test.rb | head
grep -rn "db:drop\|db:reset\|db:schema:load\|truncate" spec/ test/ lib/tasks/ Rakefile | head
```

| Red flag | Why |
|---|---|
| **`RAILS_ENV=production`** set anywhere a test command can see it — CI env, `Dockerfile`, `Makefile`, `bin/` script, or a developer's shell | the suite boots the production environment: real DB, real cache, real mailer, eager loading of production config |
| `config/database.yml` `test:` section pointing at a non-test database name | **`bin/rails test` runs `db:test:prepare`, which will load the schema into — i.e. wipe — whatever that points at** |
| `DATABASE_URL` set in the environment | **it overrides `database.yml` entirely, including the `test:` section.** This is the number-one way a Rails suite destroys a real database |
| `ActiveRecord::Migration.maintain_test_schema!` with a prod `DATABASE_URL` | silently migrates the wrong database |
| `config/master.key` / `config/credentials/production.key` readable by the test env | production secrets in a test process |
| `Dotenv.load` in `config/application.rb` or a spec helper with no explicit file | loads the developer's real `.env` |
| `config.action_mailer.delivery_method` not `:test` in `config/environments/test.rb` | **real emails sent to real customers** |
| `ActiveJob::Base.queue_adapter` not `:test`/`:inline` in the test env, or Sidekiq not in fake mode | tests enqueue real background jobs onto the real queue |
| `config.active_storage.service` not `:test` | real uploads to real S3 |
| `WebMock.disable_net_connect!` absent | any test can hit any real API |
| VCR with `record: :new_episodes` in CI | silently records against the live API |
| Stripe/Twilio/SendGrid keys matching `sk_live_`/`SG.` in test config | live-money side effects |
| A rake task invoked by a test that calls `db:drop`/`db:reset` | destroys the target database |

```ruby
# spec/spec_helper.rb / test/test_helper.rb — assert the guard, don't assume it
abort "Refusing: RAILS_ENV=#{ENV['RAILS_ENV']}" if ENV["RAILS_ENV"].to_s.include?("prod")

db = ActiveRecord::Base.connection_db_config.database.to_s
abort "Refusing to run tests against #{db}" unless db =~ /test/i || db.include?(":memory:")

require "webmock/rspec"
WebMock.disable_net_connect!(allow_localhost: true)
```

`ActiveRecord::Base.connection_db_config.database` is the honest check: it
reflects what Rails actually resolved, including a `DATABASE_URL` override that
`database.yml` cannot show you.

---

## 8. House conventions to match

```bash
ls spec test
find spec test -maxdepth 1 -type d 2>/dev/null
cat .rspec 2>/dev/null
ls spec/factories test/factories test/fixtures 2>/dev/null
grep -rn "Dir\[.*support.*\].each" spec/rails_helper.rb spec/spec_helper.rb 2>/dev/null
```

| Convention | Rule |
|---|---|
| RSpec layout | `spec/` **mirrors `app/`**: `app/models/cart.rb` → `spec/models/cart_spec.rb`; `app/services/x.rb` → `spec/services/x_spec.rb`; `lib/y.rb` → `spec/lib/y_spec.rb` |
| RSpec test types | `spec/models/`, `spec/requests/` (preferred over `spec/controllers/`), `spec/system/`, `spec/jobs/`, `spec/mailers/`, `spec/services/`, `spec/helpers/`, `spec/views/` |
| Minitest layout | `test/models/`, `test/controllers/`, `test/integration/`, `test/system/`, `test/jobs/`, `test/mailers/` |
| File naming | `<subject>_spec.rb` (RSpec) / `<subject>_test.rb` (Minitest) |
| Example naming | `describe Cart do` / `describe "#add_item" do` / `context "when the cart is empty" do` / `it "adds the item" do`. **`describe` for the subject, `context` for the condition — "when/with/without"** |
| Minitest naming | `test "adds the item to an empty cart" do ... end` (Rails macro) or `def test_adds_the_item` |
| Shared setup | `let`/`let!` (lazy/eager) and `before(:each)`. **Avoid `before(:all)`** (see §6) |
| Shared examples | `spec/support/shared_examples/`, `it_behaves_like "a billable resource"` |
| Support files | `spec/support/**/*.rb`, auto-loaded by an explicit `Dir[...].each { require }` in `rails_helper.rb`. **Check it is uncommented** — the Rails generator ships it commented out, and a repo with `spec/support/` that is never loaded is a real finding |
| Factories | `spec/factories/<plural>.rb` or `test/factories/`. `FactoryBot.define do factory :cart do ... end end` |
| Factory usage | `build_stubbed` > `build` > `create`. Default to the cheapest that works |
| Rails fixtures | `test/fixtures/*.yml`, enabled by `fixtures :all` in `test_helper.rb` |
| Static data | `spec/fixtures/files/`, `test/fixtures/files/` — accessed via `file_fixture("x.json")` |
| Cassettes | `spec/cassettes/` (VCR), `spec/vcr_cassettes/` |
| Mocking idiom | `instance_double` / `class_double` / `object_double` with `verify_partial_doubles = true`; `allow(x).to receive(:y).and_return(z)`; `expect(x).to have_received(:y)` (spies, preferred over `expect(...).to receive` before the fact). Minitest: `Minitest::Mock` or `mocha`. **Never mix `mocha` and `rspec-mocks`** |
| Matchers | `expect(x).to eq y`. Custom matchers in `spec/support/matchers/` |
| Tags | `:slow`, `:js`, `:integration` — reuse the existing ones; read `.rspec` and `rails_helper.rb` for `filter_run_excluding` |

---

## 9. Static analysis + type checking — rank first on every roadmap

Ruby has **no type checker by default**, so this section carries more weight
here than in any other stack in this set. RuboCop is near-universal; gradual
typing is where the uncaptured value is.

```bash
# Tier 0 — RuboCop, almost always already in the Gemfile
bundle exec rubocop
bundle exec rubocop --parallel
bundle exec rubocop -a            # safe autocorrect
bundle exec rubocop -A            # unsafe autocorrect — review every hunk
bundle exec rubocop --format json --out .testsquad/rubocop.json

# DIFF-SCOPED — the RESEARCH §6 pattern (~70% fix rate scoped, ~0% as a backlog)
bundle exec rubocop --force-exclusion $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.rb' | tr '\n' ' ')
# Or, if the repo uses the gem:
bundle exec rubocop-git origin/main

# Generate a TODO so the legacy backlog stops blocking new work
bundle exec rubocop --auto-gen-config      # -> .rubocop_todo.yml

# Tier 1 — security and dependency
bundle exec brakeman -q -f json -o .testsquad/brakeman.json   # Rails security scanner
bundle exec bundler-audit check --update                      # gem CVEs
bundle exec erb_lint app/views                                 # if erb_lint is present

# Tier 2 — dead code and complexity
bundle exec rubycritic app lib
```

RuboCop extensions worth enabling, in roadmap order:

| Gem / department | Catches | Why early |
|---|---|---|
| `rubocop-rspec` | `let!` misuse, `before(:all)`, `described_class` drift, multiple expectations per example, **`RSpec/AnyInstance`** and **`RSpec/VerifiedDoubles`** | **directly targets the §6 flake and §5 tautology causes** — the single highest-value extension for a test repo |
| `rubocop-rails` | `Rails/SaveBang`, `Rails/TimeZone` (**`Time.now` vs `Time.current` — a direct flake source**), `Rails/OutputSafety`, `Rails/SkipsModelValidations` | cheap and Rails-specific |
| `rubocop-performance` | N+1-adjacent and allocation issues | tier 2 |
| `Lint/*` department | genuinely wrong code: shadowed variables, useless assignment, duplicate methods, **`Lint/UselessAssignment` in specs** | zero false positives in practice |
| `Lint/Debugger` | leftover `binding.pry` / `debugger` / `byebug` | will hang CI |
| `rubocop-factory_bot` | `FactoryBot/CreateList`, `FactoryBot/ExcessiveCreateList` | targets the slowest part of most suites |
| `rubocop-capybara` | `Capybara/CurrentPathExpectation`, `Capybara/VisibilityMatcher` | targets system-test flake |
| `Style/*` | formatting and idiom | last; autocorrect it and stop discussing it |

### Type checking — the real roadmap item

Ruby has two options; **introduce one, never both.**

| Option | What it is | Honest cost |
|---|---|---|
| **Sorbet** (`sorbet`, `sorbet-runtime`, `tapioca`) | Gradual static types via `sig` blocks + runtime checks. `# typed: false/true/strict/strong` per file | Real, mature, used at scale. But: `sig` blocks are intrusive, `sorbet-runtime` adds method-call overhead, and generating RBI for gems (`tapioca`) is an ongoing chore. Adoption is a quarter, not a week |
| **RBS + Steep** | Types in **separate `.rbs` files**, no source changes. Official Ruby-core direction | Less intrusive, no runtime cost, ships with Ruby. Weaker tooling, slower type checker, and inference (`rbs prototype`/`typeprof`) produces types you must then curate |

```bash
# Sorbet
bundle exec srb tc                                  # type-check
bundle exec tapioca init && bundle exec tapioca gems
bundle exec spoom bump --from false --to true       # ratchet files up a sigil level
grep -rn "# typed:" app/ lib/ | awk '{print $NF}' | sort | uniq -c   # the debt ledger

# RBS + Steep
bundle exec rbs prototype rb app/models/cart.rb > sig/cart.rbs
bundle exec steep check
bundle exec typeprof app/services/pricing.rb
```

The honest recommendation: **type checking is the highest-ceiling item on a
Ruby roadmap and the slowest to land.** Sequence it after RuboCop + `rubocop-rspec`
+ verifying doubles + WebMock, which are days of work with immediate returns.

Config to read before trusting any clean run:

```bash
cat .rubocop.yml 2>/dev/null | head -60
wc -l .rubocop_todo.yml 2>/dev/null          # the size of the silenced backlog
grep -c "Exclude" .rubocop.yml .rubocop_todo.yml 2>/dev/null
grep -rn "rubocop:disable" app/ lib/ spec/ | wc -l
grep -rn "# typed: false\|# typed: ignore" app/ lib/ | wc -l
grep -nE "AllCops:" -A 15 .rubocop.yml 2>/dev/null
```

A large or growing `.rubocop_todo.yml`, `rubocop:disable` count, or
`# typed: false` count means findings are being silenced rather than fixed.
**Report the count and its trend, not a clean run.** Check specifically whether
`AllCops: Exclude` drops `spec/` or `test/` — if it does, the test code is
unlinted, which is where `rubocop-rspec`'s findings would have been.
