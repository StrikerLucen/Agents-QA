# Stack playbook — Rust

Reference for a testsquad agent working in a Rust repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

Rust's shape is unusual: the **compiler and clippy do work that is "testing" in
other languages**, mutation testing is genuinely good (`cargo-mutants`), and
**branch coverage is effectively unavailable**. Rank §9 first on any roadmap —
in Rust it is not a consolation prize, it is the main event.

---

## 1. Detection signals

```bash
ls Cargo.toml Cargo.lock rust-toolchain.toml rust-toolchain .cargo/config.toml 2>/dev/null
grep -n "^\[workspace\]" -A 20 Cargo.toml
cargo --version && rustc --version
cat rust-toolchain.toml 2>/dev/null     # pinned toolchain + components
```

| Marker | Conclusion |
|---|---|
| `[workspace]` in the root `Cargo.toml` | **workspace** — `cargo test` from the root only covers the default members. Use `--workspace` for everything, `-p <crate>` to scope |
| `members = [...]` / `default-members` | `default-members` is what a bare `cargo test` actually runs. **Read it**; a crate outside it is never tested by the default command |
| `rust-toolchain.toml` | pinned channel; `components = ["clippy","rustfmt","llvm-tools-preview"]` tells you what coverage/lint tooling is expected |
| `.cargo/config.toml` | custom `[alias]` entries (`cargo t`, `cargo xtask`) and `rustflags`. **Read the aliases — they are the repo's canonical commands** |
| `[features]` with `default = [...]` | a bare `cargo test` tests only the default feature set. Untested feature combinations are a real gap |
| `xtask/` directory | the repo's build/test automation lives there: `cargo xtask test` |
| `#![no_std]` in `src/lib.rs` | embedded; host `cargo test` may not work at all. Look for `defmt-test`, `embedded-test`, or a QEMU runner in `.cargo/config.toml` |
| `build.rs` | code generation; mutation and coverage tools can behave oddly against generated code |

### Test layout and framework

Rust has one runner in the toolchain (`cargo test`, i.e. libtest) plus the
`nextest` alternative harness. The attribute style barely varies; the **location**
of tests is what matters.

```bash
grep -rn "#\[cfg(test)\]" --include='*.rs' src/ | wc -l       # unit tests, in-module
ls tests/ benches/ examples/ 2>/dev/null                       # integration / bench / example targets
grep -rn "#\[tokio::test\]\|#\[async_std::test\]\|#\[actix_rt::test\]\|#\[test_log::test\]" --include='*.rs' . | head
grep -nE "^(proptest|quickcheck|rstest|insta|mockall|wiremock|serial_test|test-case|assert_cmd|predicates|tempfile|rusty-fork|criterion|trybuild|cargo-nextest|arbitrary|loom) " Cargo.toml
grep -n "\[dev-dependencies\]" -A 30 Cargo.toml
grep -rn "```" --include='*.rs' src/ | grep -c "" # doctests exist if code fences do
```

| Location | Target kind | What it can see |
|---|---|---|
| `src/**.rs` inside `#[cfg(test)] mod tests` | **unit tests** | private items of the module. Most Rust tests live here |
| `tests/*.rs` | **integration tests** | only the crate's **public** API. Each file is a separate binary and a separate process |
| `tests/common/mod.rs` | shared helper | must be `common/mod.rs`, not `common.rs`, or cargo compiles it as its own test binary |
| `benches/*.rs` | benchmarks (criterion) | not run by `cargo test` |
| `examples/*.rs` | examples | compiled by `cargo test`, not run |
| `///` / `//!` doc comments with ```` ``` ```` fences | **doctests** | run by `cargo test`, **not by `cargo nextest`** — see §2 |

| Dev-dependency | Means |
|---|---|
| `rstest` | parameterised tests / fixtures — `#[rstest]`, `#[case(...)]` |
| `insta` | snapshot tests; `cargo insta review`, snapshots in `snapshots/*.snap` |
| `mockall` | generated mocks — `#[automock]` / `mock!` |
| `wiremock` | async HTTP stubbing |
| `serial_test` | `#[serial]` — admission that some tests cannot run in parallel. **Read why** |
| `rusty-fork` | per-test process isolation |
| `proptest` / `quickcheck` / `arbitrary` | property-based testing (see §5) |
| `assert_cmd` + `predicates` | CLI integration tests |
| `trybuild` | compile-fail tests for macros |
| `loom` | exhaustive concurrency model checking — the serious answer to data races |

### `cargo test` vs `cargo nextest`

```bash
cargo nextest --version 2>/dev/null
ls .config/nextest.toml nextest.toml 2>/dev/null
grep -rn "nextest" .github/workflows/ Makefile justfile 2>/dev/null | head
```

| | `cargo test` | `cargo nextest` |
|---|---|---|
| Process model | one process per **test binary**, threads within it | **one process per test** |
| Shared-state leakage | yes, within a binary | eliminated — this is the big win |
| Doctests | **runs them** | **does not run them** — you must also run `cargo test --doc` |
| Flaky-test retries | none | `--retries N` (reports as FLAKY — do not use it to hide flake) |
| Per-test timeout | none | `slow-timeout` + `terminate-after` in config |
| Output on failure | interleaved | per-test, captured, readable |
| Filtering | substring | substring + `-E` filtersets (`test(foo) & package(bar)`) |

If `.config/nextest.toml` exists, nextest is the repo's canonical runner and
you must still run doctests separately or they silently stop being checked.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake.

**Rust has no import-graph test selector** — no `--findRelatedTests`, no
testmon. Scope by **crate** (workspace member), which is cheap and reliable
because cargo's incremental compilation makes untouched crates nearly free to
skip. Say so plainly rather than implying finer scoping exists.

```bash
# Changed workspace crates -> test only those packages. The practical default.
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.rs' 'Cargo.toml')
PKGS=$(for f in $CHANGED; do
         d=$(dirname "$f")
         while [ "$d" != "." ] && [ "$d" != "/" ]; do
           if [ -f "$d/Cargo.toml" ]; then
             grep -m1 '^name *= *' "$d/Cargo.toml" | sed 's/.*= *"\(.*\)"/\1/'; break
           fi
           d=$(dirname "$d")
         done
       done | sort -u)
[ -n "$PKGS" ] && cargo test $(printf -- '-p %s ' $PKGS)

# Reverse dependencies — the closest Rust gets to related-test selection
for p in $PKGS; do
  cargo metadata --no-deps --format-version 1 \
    | jq -r --arg p "$p" '.packages[] | select(.dependencies[]?.name == $p) | .name'
done | sort -u

cargo test -p acme-cart cart::add_item     # single crate, single module path
cargo test --workspace --all-features      # everything (CI, not hooks)
```

Make the hook fast and non-blocking:

```bash
cargo test -p acme-cart --lib            # unit tests only — skips integration binaries
cargo test -p acme-cart --lib --no-fail-fast
cargo check -p acme-cart                 # type-check only; ~5-10x faster than a test build
```

`cargo check` first, then `cargo test --lib` on the changed crate, is the
cheapest useful hook in Rust. A full `cargo test --workspace` in a PostToolUse
hook is unusable: a cold incremental build of a medium workspace is minutes.

**A filter that matches no tests exits 0.** Verify selection:

```bash
cargo test -p acme-cart cart::add_item -- --list | grep -q ": test" || echo "NO TEST MATCHED"
```

### Full suite / single file / single test

| Goal | `cargo test` | `cargo nextest` |
|---|---|---|
| Whole workspace | `cargo test --workspace` | `cargo nextest run --workspace` |
| One package | `cargo test -p acme-cart` | `cargo nextest run -p acme-cart` |
| Unit tests only | `cargo test --lib` | `cargo nextest run --lib` |
| One integration file (`tests/api.rs`) | `cargo test --test api` | `cargo nextest run --test api` |
| Doctests only | `cargo test --doc` | **n/a — use `cargo test --doc`** |
| By name substring | `cargo test add_item` | `cargo nextest run add_item` |
| Exact test | `cargo test -- --exact cart::tests::add_item` | `cargo nextest run -E 'test(=cart::tests::add_item)'` |
| Filterset | — | `cargo nextest run -E 'package(acme-cart) & test(cart::)'` |
| Include `#[ignore]`d | `cargo test -- --ignored` / `--include-ignored` | `cargo nextest run --run-ignored all` |
| Show stdout of passing tests | `cargo test -- --nocapture` | `cargo nextest run --no-capture` |
| List, don't run | `cargo test -- --list` | `cargo nextest list` |
| Serial | `cargo test -- --test-threads=1` | `cargo nextest run -j1` |
| Don't stop at first failure | `cargo test --no-fail-fast` | default; `--fail-fast` to opt in |
| All features / no features | `--all-features` / `--no-default-features` | same |
| Release-mode behaviour | `cargo test --release` | `cargo nextest run --release` |
| Machine-readable | `cargo test -- -Z unstable-options --format json` (nightly) | `cargo nextest run --message-format libtest-json` (*verify before relying on this*) |

Everything after `--` goes to **libtest**, not to cargo. `cargo test --release`
differs meaningfully: integer overflow panics in debug and wraps in release, and
`debug_assert!` disappears. **A test suite that only runs in debug has never
exercised release semantics** — state that as a finding.

Feature combinations are the quiet gap:

```bash
cargo test --no-default-features ; cargo test --all-features
cargo install cargo-hack && cargo hack test --each-feature --workspace
```

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **lcov**
(`lcov.info`). Both mainstream Rust coverage tools can emit it.

| Tool | Mechanism | Status 2026-11-12 | Verdict |
|---|---|---|---|
| **`cargo-llvm-cov`** | LLVM source-based instrumentation (`-C instrument-coverage`) | healthy, the de-facto standard | **use this** |
| **`cargo-tarpaulin`** | ptrace/LLVM backends, Linux-centric | maintained but less accurate; macOS/Windows support is weaker | fallback only |
| `grcov` | Mozilla's lcov/`gcov` post-processor | maintained | useful when CI already produces `.profraw` |
| `cargo-kcov` | kcov wrapper | obsolete | do not adopt |

```bash
cargo install cargo-llvm-cov
rustup component add llvm-tools-preview

# lcov — the format the normalizer reads
cargo llvm-cov --workspace --all-features --lcov --output-path lcov.info

# With nextest as the harness (recommended if the repo uses nextest)
cargo llvm-cov nextest --workspace --lcov --output-path lcov.info
# NOTE: this does NOT include doctests. Add them explicitly:
cargo llvm-cov --workspace --doctests --lcov --output-path lcov.info   # requires nightly

# Human views
cargo llvm-cov --workspace --summary-only
cargo llvm-cov --workspace --html          # target/llvm-cov/html/index.html
cargo llvm-cov --workspace --text          # per-region annotated source

# Cobertura is also parsed, if a CI tool wants it
cargo llvm-cov --workspace --cobertura --output-path coverage.xml
```

| Output | Format | Parsed as |
|---|---|---|
| `lcov.info` | lcov | `lcov` ✅ |
| `coverage.xml` (`--cobertura`) | Cobertura XML | `cobertura` ✅ |
| `--json` | llvm-cov export JSON | **not parsed** — it is not coverage.py JSON |
| `target/llvm-cov/html/` | HTML | humans only |

Tarpaulin, if that is what the repo has:

```bash
cargo install cargo-tarpaulin
cargo tarpaulin --workspace --out Lcov --output-dir .        # -> lcov.info
cargo tarpaulin --workspace --out Xml                        # -> cobertura.xml
```

Exclusions:

```bash
cargo llvm-cov --workspace --lcov --output-path lcov.info \
  --ignore-filename-regex '(tests?/|benches/|examples/|build\.rs|/target/)'
```

```toml
# Cargo.toml — exclude a crate entirely from the coverage workspace run
# (there is no per-function coverage pragma; use #[cfg(not(coverage))] guards
#  or the regex above)
```

### Branch coverage: Rust effectively does not have it

> **Treat Rust as having no usable branch coverage.** `cargo-llvm-cov` reports
> **line, region and function** coverage. LLVM *region* coverage is finer than
> line coverage and partially approximates branch behaviour, but it is **not**
> branch coverage and the lcov output does not carry `BRDA` records in a normal
> run — so `normalize_coverage.py` will report `branches_found: 0` and
> `branch_pct: null`, correctly.
>
> `cargo llvm-cov` has gained a `--branch` option backed by LLVM's branch
> coverage (`-Z coverage-options=branch`), but it is **nightly-only and
> unstable**. *Verify before relying on this* — the flag name and nightly
> requirement have moved between releases. Do not put it on a default roadmap.

Report this as **"branch coverage is not measured in Rust"** and emit `null`.
Never report 0% — 0% is a claim that every branch is untested, which is false
and will be read as a catastrophe.

The practical substitute: Rust's `match` exhaustiveness check already does what
branch coverage is for in most other languages. An unhandled enum variant is a
**compile error**, not an untested branch. Point at that in reports — it is a
genuine structural advantage, and it means the missing branch metric costs Rust
less than it would cost Python or Java.

Normalize (lcov from llvm-cov carries absolute paths):

```bash
python3 scripts/normalize_coverage.py lcov.info --out .testsquad/coverage.json \
  --strip-prefix "$(pwd)"
```

---

## 4. Mutation testing — cargo-mutants

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| **cargo-mutants v27.1.0** | **Healthy, actively maintained** | the default choice |
| `mutagen` | abandoned (nightly-only, last meaningful work years ago) | do not adopt |

```bash
cargo install --locked cargo-mutants
cargo mutants --version
```

### Diff-scoped invocation — the one to use

```bash
git diff origin/main... > /tmp/pr.diff
cargo mutants --in-diff /tmp/pr.diff
```

| Flag | What it does |
|---|---|
| `--in-diff /tmp/pr.diff` | mutate **only lines present in that diff**. The diff must be generated against the same tree you are testing |
| `-p acme-cart` / `--package` | restrict to a workspace member |
| `--file 'src/cart/**'` / `--exclude-file` | path globs |
| `--re` / `--exclude-re` | regex over the mutant description (`--exclude-re 'impl Display'`) |
| `-j N` / `--jobs` | parallel mutant runs; each gets its own build dir, so disk and RAM scale with N |
| `--timeout-multiplier 2.0` | relative to the baseline test time; prefer it over a fixed `--timeout` |
| `--baseline skip` | skip the unmutated baseline run (only when you already know it is green) |
| `--test-tool nextest` | use nextest as the harness — faster, and per-test isolation makes mutant verdicts more reliable |
| `--shard k/n` | split across CI runners |
| `--no-shuffle` | deterministic ordering, for reproducible reports |

```bash
# The full recommended CI/PR invocation
git diff origin/main... > /tmp/pr.diff
cargo mutants \
  --in-diff /tmp/pr.diff \
  --test-tool nextest \
  --timeout-multiplier 2.0 \
  -j 4 \
  --output .testsquad/mutants
```

Outputs land in `mutants.out/` (or `--output`):

| File | Contents |
|---|---|
| `missed.txt` | **surviving mutants — the findings** |
| `caught.txt` / `timeout.txt` | killed (timeouts count as caught; investigate if numerous) |
| `unviable.txt` | did not compile — the type system rejected the mutant. Ignore |
| `outcomes.json` | machine-readable; parse this |

```bash
jq -r '.outcomes[] | select(.summary=="MissedMutant") | "\(.scenario.Mutant.file):\(.scenario.Mutant.line) \(.scenario.Mutant.replacement)"' \
  .testsquad/mutants/outcomes.json
```

### cargo-mutants' documented blind spot — state it in every report

> **A test-file-only change runs no mutants at all.** `--in-diff` mutates
> *source* lines in the diff; if the diff touches only `tests/` or a
> `#[cfg(test)]` block, there are no mutable lines and the gate passes
> vacuously. **Someone deleting assertions passes a cargo-mutants diff gate.**
> cargo-mutants documents this and also warns explicitly that a change in file A
> can destroy the test coverage of file B without any mutant in the diff
> noticing.

Mitigations, both required:

1. A separate check that flags **net assertion deletions** in test files
   (`git diff -U0 origin/main... -- '*test*' | grep -c '^-.*assert'`) and treats
   a non-zero count as needing human review.
2. An occasional **full** `cargo mutants` run off the critical path (nightly,
   sharded), so (a)-class regressions are eventually caught.

Also note: `unviable` mutants are common in Rust and are *not* a problem. The
borrow checker and type system reject many mutations that would compile in C# or
Java. A high unviable count is Rust being Rust, not a misconfiguration.

### Suppression

```rust
#[mutants::skip]            // needs the `mutants` crate as a dependency
fn log_request(req: &Request) { tracing::info!(?req); }

#[cfg_attr(test, mutants::skip)]   // skip without a runtime dependency
```

```toml
# .cargo/mutants.toml
exclude_globs = ["src/bin/**", "src/generated/**", "build.rs", "**/tests/**"]
exclude_re = [
  "impl Debug", "impl Display", "fn fmt", "fn clone",
  "tracing::", "log::", "println!", "eprintln!", "metrics::",
]
timeout_multiplier = 2.0
test_tool = "nextest"
```

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**.

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste.
   cargo-mutants has no one-per-line flag; **post-filter `outcomes.json` to one
   surviving mutant per line and ≤7 per file.**
2. **Arid-node suppression** — never mutate: `tracing!`/`log!` macros, metrics
   emitters, `Debug`/`Display`/`fmt` impls, `Clone`/`Default`,
   `panic!`/`unreachable!`/`todo!` messages, trivial getters, pure delegation
   (`self.inner.foo()`), derive-generated code, `build.rs`. Via `exclude_re` /
   `exclude_globs` / `#[mutants::skip]`. **This step matters more than the
   choice of tool** — it is the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff. cargo-mutants has no
   project score to gate on anyway, which is the correct default.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **`tests/*.rs` integration targets** | The native seam: the crate's public API, one process per file | Each file is a separate binary — compile time scales with file count. Shared helpers must live in `tests/common/mod.rs` |
| **`wiremock`** (Rust crate) | Async HTTP stubbing against an outbound client | Spins a real local server per test; cheap. The idiomatic choice for `reqwest` clients |
| **`testcontainers-rs`** | Real Postgres/Redis/Kafka | Docker daemon required; 2–20s startup. Gate with `#[ignore]` + `--ignored` in CI, or a feature flag |
| **`sqlx::test`** | Per-test transactional Postgres with automatic rollback | Genuinely excellent if the repo uses sqlx. Needs a live DB and `DATABASE_URL` — see §7 |
| **`assert_cmd` + `predicates` + `tempfile`** | CLI binaries | The standard trio for `src/bin` crates. `Command::cargo_bin("acme")` |
| **`insta`** | Snapshot assertions | `cargo insta review` is a deliberate review step; snapshots still rot. Commit `*.snap`, never `*.snap.new` |
| **`trybuild`** | Compile-fail tests for proc macros | The only way to test macro error messages; stderr snapshots break on compiler version bumps |
| **`loom`** | Exhaustive interleaving check for lock-free/concurrent code | Slow and restricted to `loom`'s own sync primitives, but it is a **proof**, not a sample. The right tool for a data-race hypothesis |
| **`proptest` / `quickcheck`** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16). No head-to-head study shows LLM-written property tests beat LLM-written example tests under equal budget. `proptest` persists failures in `proptest-regressions/` — **commit that directory** or shrunk counterexamples are lost |
| **`cargo-fuzz` / `afl.rs`** | Coverage-guided fuzzing of parsers and `&[u8]` boundaries | Nightly required for `cargo-fuzz`. High value for any `from_bytes`/`parse` surface; corpus must be committed |

```rust
// wiremock — the shape to match. Inject the base URL; never hardcode it.
let server = MockServer::start().await;
Mock::given(method("GET")).and(path("/v1/cart"))
    .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items": []})))
    .mount(&server).await;
let client = CartClient::new(&server.uri());
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests. In
Rust, derive the shape from the **trait definition and its associated types**,
or from a `serde` struct — both are machine-checkable. `mockall`'s `#[automock]`
generates from the real trait, which is why it is preferable to a hand-written
stub whose signature can silently drift.

---

## 6. Flake taxonomy — Rust

> **There is no published per-cause flakiness breakdown for Rust** comparable
> to the Java (async 45% / concurrency 20% / order 12%), Python (order 59% /
> infrastructure 28%) or JavaScript (concurrency 21% / async 20% / OS 18% /
> network 13%) figures in RESEARCH §19. Do not invent one and do not reuse
> another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way.

### The one to check first

**`cargo test` runs tests in parallel threads *within the same process*, by
default**, with `--test-threads` defaulting to the core count. Every test in a
test binary shares one address space: process-global state — `static mut`,
`OnceLock`/`LazyLock`, `std::env::set_var`, the current directory, a global
logger/subscriber, a global registry, a fixed TCP port, a shared temp path — is
shared between concurrently running tests. **Shared-state races between tests
are the dominant Rust flake pattern**, and they are invisible in a serial run.

`std::env::set_var` deserves its own line: it is **`unsafe` as of the Rust 2024
edition** precisely because it is not thread-safe, and a test calling it races
with every other test in the binary that reads the environment.

```bash
# The diagnostic: does it pass serially?
cargo test -- --test-threads=1
cargo test -p acme-cart --lib -- --test-threads=1
# Green serially + red in parallel == shared state between tests. Not a timing bug.

# The structural fix, not a workaround: nextest runs one process per test.
cargo nextest run -p acme-cart
# If it is green under nextest and red under `cargo test`, you have confirmed
# in-process shared state.
```

### Other consensus causes, with their instruments

| Cause | Smell | Instrument |
|---|---|---|
| Async runtime scheduling | `#[tokio::test]` with `tokio::time::sleep` as synchronisation; `spawn`ed tasks not awaited | `#[tokio::test(flavor = "multi_thread", worker_threads = 1)]` to pin, `tokio::time::pause()` + `advance()` to make time deterministic, `tokio-console` to see stuck tasks |
| Data races in the code under test | intermittent wrong values, not panics | **ThreadSanitizer** (nightly): `RUSTFLAGS="-Zsanitizer=thread" cargo +nightly test -Zbuild-std --target x86_64-unknown-linux-gnu` |
| UB / aliasing in `unsafe` | passes, then miscompiles after a toolchain bump | **Miri**: `cargo +nightly miri test` — the single highest-value Rust-specific check if the crate has any `unsafe` |
| Lock-free / atomics ordering | reproduces once a month in CI | **`loom`** — exhaustive, not sampled |
| Port binding | `TcpListener::bind("127.0.0.1:8080")` | bind `:0` and read back `listener.local_addr()?.port()` |
| Filesystem | fixed `/tmp/test-foo` paths, tests sharing a dir | `tempfile::tempdir()` per test |
| Order dependency | — | **libtest does not shuffle.** `cargo nextest` does not shuffle either. There is **no first-party order-randomisation flag in Rust** — say so rather than inventing one. Approximate by running single tests in isolation and comparing |
| Time / timezone | `chrono::Local`, `SystemTime::now()` in an assertion | inject a clock trait; `TZ=` sweep |
| HashMap iteration | asserting iteration order of `HashMap`/`HashSet` | **`std::collections::HashMap` is randomly seeded per process by design** — never assert its order. Use `BTreeMap` or sort |

### Detection commands

```bash
# Serial vs parallel — the first and best diagnostic
cargo test -- --test-threads=1
cargo nextest run -j1

# Repeat to surface timing flake (retries OFF while diagnosing)
for i in $(seq 1 50); do cargo test -p acme-cart cart::add_item || break; done
cargo nextest run --retries 2    # CI only; a FLAKY result is a finding, not a pass

# Undefined behaviour and data races
cargo +nightly miri test -p acme-cart
RUSTFLAGS="-Zsanitizer=thread" cargo +nightly test -Zbuild-std --target x86_64-unknown-linux-gnu

# Release semantics differ (overflow panics vs wraps; debug_assert disappears)
cargo test --release

# Environment sweep, and single-core to starve the scheduler
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 cargo test ; TZ=UTC cargo test
taskset -c 0 cargo test          # Linux
```

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "thread::sleep\|tokio::time::sleep" --include='*.rs' . | head -30
grep -rn "SystemTime::now()\|Instant::now()\|Utc::now()\|Local::now()" --include='*.rs' src/ tests/ | head -20
grep -rn "set_var\|env::var" --include='*.rs' tests/ src/ | grep -i test | head -20
grep -rn "static mut\|OnceLock\|LazyLock\|lazy_static\|OnceCell" --include='*.rs' . | head -20
grep -rnE "127\.0\.0\.1:[0-9]{4,5}|0\.0\.0\.0:[0-9]{4,5}" --include='*.rs' . | head
grep -rn "/tmp/" --include='*.rs' . | head
grep -rn "#\[serial\]\|#\[ignore\]" --include='*.rs' . | head -20
grep -rn "rand::random\|thread_rng\|Uuid::new_v4" --include='*.rs' . | head -20
grep -rn "unwrap()\|expect(" --include='*.rs' tests/ | wc -l
```

Specific smells: `thread::sleep` as synchronisation (use a channel, a
`Notify`, or a deadline-bounded poll) · `Instant::now()`/`Utc::now()` in an
assertion (inject a clock) · `env::set_var` in any test (process-global; see
above) · a mutated `static`/`OnceLock` · hardcoded ports · fixed `/tmp` paths ·
unseeded `thread_rng()`/`Uuid::new_v4()` in an expected value · **asserting
`HashMap` iteration order** · `#[serial]` without a comment explaining what is
shared — it is a flake marker, and the shared resource it guards is a finding ·
`#[ignore]` on a test nobody has run in a year (count them: they are coverage
that silently stopped existing) · floating-point `assert_eq!` (use
`approx::assert_relative_eq!`).

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).
`cargo nextest --retries` turning a red into a FLAKY is **not** a pass.

---

## 7. Production-danger signals

```bash
grep -rnE "(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp|clickhouse)://" --include='*.rs' --include='*.toml' --include='*.env*' --include='*.yaml' . \
  | grep -vE "localhost|127\.0\.0\.1|::1|testcontainers|test"
ls .env .env.production config/production.toml config/prod.toml 2>/dev/null
grep -rn "dotenvy\|dotenv" --include='*.rs' --include='Cargo.toml' . | head
grep -rn "env::var(\"DATABASE_URL\")\|env!(\"DATABASE_URL\")" --include='*.rs' . | head
grep -rn "RUST_ENV\|APP_ENV\|ENVIRONMENT" --include='*.rs' --include='*.toml' --include='*.yml' . | head
grep -rn "sqlx::migrate!\|MIGRATOR.run" --include='*.rs' . | head
```

| Red flag | Why |
|---|---|
| `DATABASE_URL` read by `#[sqlx::test]` or a fixture with **no test-only fallback** | sqlx's test macro **creates and drops databases on the server `DATABASE_URL` points at**. Pointed at prod, it is destructive |
| `sqlx::migrate!().run(&pool)` in a test fixture with no DB-name guard | migrations against a real schema |
| `env!("DATABASE_URL")` (compile-time) in sqlx offline/online mode | the build itself talks to a database; check `.sqlx/` is committed and `SQLX_OFFLINE=true` is set in CI |
| `dotenvy::dotenv()` in a test or `build.rs` with no explicit path | loads the developer's real `.env` from an ancestor directory |
| A real host in `config/default.toml` with no `config/test.toml` override | the default config **is** the production config |
| `#[ignore]`d integration tests that read prod credentials from the environment | `--ignored` in a CI job runs them |
| A client whose base URL defaults to a production host when the env var is unset | a test hitting the live API |
| AWS/GCP SDK with no LocalStack/emulator endpoint override | real cloud calls and real charges |
| Stripe/Twilio/SendGrid keys matching `sk_live_` | live-money side effects |
| `--release` integration tests that start the real server on the real port | port conflicts plus real traffic |
| `cargo run` invoked from a test (`assert_cmd` aside) with inherited env | whatever the shell had |

Assert the guard, don't assume it:

```rust
// tests/common/mod.rs — call this from every integration test's setup
pub fn refuse_production() {
    if let Ok(url) = std::env::var("DATABASE_URL") {
        let safe = ["localhost", "127.0.0.1", "::1", "test"];
        assert!(
            safe.iter().any(|s| url.contains(s)),
            "refusing to run tests against {url}"
        );
    }
    if std::env::var("APP_ENV").map(|e| e.contains("prod")).unwrap_or(false) {
        panic!("refusing to run tests with APP_ENV=production");
    }
}
```

---

## 8. House conventions to match

| Convention | Rule |
|---|---|
| Unit tests | **In the same file as the code**, in a `#[cfg(test)] mod tests { use super::*; ... }` block at the bottom. This is the dominant Rust idiom — there is no separate unit-test directory |
| Integration tests | `tests/<area>.rs`, one binary per file, **public API only** |
| Shared integration helpers | `tests/common/mod.rs` (the `mod.rs` form is required), imported with `mod common;` |
| Test function naming | `fn add_item_to_empty_cart()` — snake_case, no `test_` prefix (the `#[test]` attribute already says it). Some repos use `test_`; **match the module you are editing** |
| Error assertions | `assert!(matches!(err, Error::NotFound { .. }))` or `assert_eq!(err.to_string(), "...")`. Prefer matching the variant over the message |
| Panics | `#[should_panic(expected = "substring")]` — always with `expected`, never bare |
| `Result`-returning tests | `fn parses() -> anyhow::Result<()> { ...; Ok(()) }` lets you use `?`. Common and preferred over `.unwrap()` chains |
| Async tests | `#[tokio::test]` / `#[tokio::test(flavor = "multi_thread")]` — match the runtime the crate uses |
| Parameterised | `rstest`'s `#[rstest] #[case(1, 2)]`, or a plain `for` over an array of tuples. Both are idiomatic |
| Fixtures / test data | `tests/fixtures/`, `tests/data/`, loaded via `include_str!("fixtures/order.json")` (compile-time, no IO) or `std::fs::read_to_string` |
| Snapshots | `snapshots/*.snap` beside the test (insta's default) or `tests/snapshots/` |
| Property-test regressions | `proptest-regressions/*.txt` — **commit these** |
| Fuzz corpus | `fuzz/corpus/<target>/` — commit the minimised corpus |
| Mocks | `mockall`'s `#[automock]` on a trait, or a hand-written struct implementing the trait. For a 1–2 method trait, hand-write it; generators pay off above ~5 methods |
| Builders | a `TestOrderBuilder` / `OrderFixture` with `Default` + `with_*` methods is the common factory idiom. `fake` crate for random data — **seed it** |
| Temp files | `tempfile::TempDir` / `NamedTempFile`, never a hardcoded path |
| Feature-gated tests | `#[cfg(feature = "integration")]` or `#[ignore]` + a CI job running `--ignored` |

```rust
// The in-module unit-test shape to match
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_item_to_empty_cart() -> anyhow::Result<()> {
        let cart = Cart::default();
        let got = cart.add(Item::new("sku-1", 2))?;
        assert_eq!(got.len(), 1);
        Ok(())
    }

    #[test]
    #[should_panic(expected = "quantity must be positive")]
    fn rejects_zero_quantity() { Item::new("sku-1", 0); }
}
```

---

## 9. Static analysis + type checking — rank first on every roadmap

In Rust this is not a consolation prize. The compiler proves memory safety,
`match` exhaustiveness and (with `#[must_use]`) unhandled results — categories
that other stacks need tests for. Clippy adds ~750 lints over that.

```bash
# Tier 0 — free, no install beyond rustup components
cargo check --workspace --all-targets --all-features
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings

# Clippy on a changed crate only, for hooks
cargo clippy -p acme-cart --all-targets -- -D warnings

# Machine-readable, for diff filtering
cargo clippy --workspace --all-targets --message-format=json > .testsquad/clippy.json
jq -r 'select(.reason=="compiler-message") | .message.spans[0].file_name + ":" + (.message.spans[0].line_start|tostring) + " " + .message.code.code' \
  .testsquad/clippy.json 2>/dev/null | sort -u

# DIFF-SCOPED lint — the RESEARCH §6 pattern (~70% fix rate scoped, ~0% as a backlog)
cargo clippy --workspace --message-format=short 2>&1 \
  | grep -Ff <(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.rs')

# Tier 1
cargo install cargo-deny && cargo deny check          # licences, advisories, duplicate deps
cargo install cargo-audit && cargo audit              # RUSTSEC advisories
cargo install cargo-udeps && cargo +nightly udeps     # unused dependencies
cargo install cargo-machete && cargo machete          # stable alternative to udeps
cargo doc --workspace --no-deps                       # broken intra-doc links are warnings

# Tier 2 — the high-value Rust-specific dynamic checks
cargo +nightly miri test -p acme-cart                 # UB in any `unsafe` block
cargo install cargo-semver-checks && cargo semver-checks   # public-API breakage, for libraries
cargo install cargo-hack && cargo hack check --each-feature --workspace
```

Clippy lint groups, in roadmap order:

| Group / lint | Catches | Why early |
|---|---|---|
| `clippy::correctness` (deny by default) | genuinely wrong code — e.g. `==` on floats, out-of-bounds indexing patterns | already on; make sure nobody has `allow`ed it |
| `clippy::suspicious` | almost certainly a bug | free |
| `-D warnings` on `--all-targets` | **includes `tests/` and `benches/`** — most repos lint only `--lib` and never see test-code problems | one flag |
| `clippy::unwrap_used` / `expect_used` in `src/` | panics in production paths | pairs with `#[allow]` inside `#[cfg(test)]` |
| `clippy::pedantic` | style + subtle correctness; noisy | tier 2, allow-by-exception |
| `clippy::nursery` | experimental; **expect false positives** | tier 3 at most |
| `#[must_use]` + `unused_must_use` | ignored `Result`s — Rust's #1 silent-bug source | compiler-level, free |
| `#[deny(missing_docs)]` | undocumented public API | only for libraries |
| `rust_2018_idioms`, `unsafe_code` | `#![deny(unsafe_code)]` at crate root is a strong, cheap claim | one line |

```toml
# Cargo.toml — workspace-wide lint config (Cargo 1.74+), preferable to
# crate-root attributes because it applies to every member
[workspace.lints.rust]
unsafe_code = "deny"
unused_must_use = "deny"

[workspace.lints.clippy]
correctness = { level = "deny", priority = -1 }
unwrap_used = "warn"
```

Config to read before trusting a clean run:

```bash
cat clippy.toml .clippy.toml rustfmt.toml .rustfmt.toml 2>/dev/null
grep -rn "#!\[allow(" --include='*.rs' src/ | head -20
grep -rn "#\[allow(" --include='*.rs' . | wc -l
grep -rn "allow(clippy::" --include='*.rs' . | sort -u | head -20
grep -rn "unsafe " --include='*.rs' src/ | wc -l
```

A crate-root `#![allow(clippy::all)]`, or a large and growing `#[allow(...)]`
count, means findings are being silenced rather than fixed — **report the count
and its trend, not a clean run.** Any `unsafe` block without a `// SAFETY:`
comment is a finding in its own right, and the strongest argument for putting
`cargo miri test` on the roadmap.
