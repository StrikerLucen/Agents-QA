# Stack playbook — Go

Reference for a testsquad agent working in a Go repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

Go is the stack where the toolchain is strongest and the **mutation-testing
ecosystem is effectively unavailable**. Section 4 says so plainly; do not let a
roadmap promise what Go cannot deliver.

---

## 1. Detection signals

```bash
ls go.mod go.sum go.work vendor/ Makefile Taskfile.yml 2>/dev/null
head -5 go.mod                       # module path + go directive (language version)
go env GOFLAGS GOWORK GOTOOLCHAIN
go version
```

| Marker | Conclusion |
|---|---|
| `go.mod` | Go modules (the only modern layout) |
| `go.work` | **multi-module workspace** — `./...` from the root may not cover every module; run per module or use `go work` awareness |
| `vendor/` | vendored deps; `go test` uses `-mod=vendor` automatically. `go mod tidy` will churn it |
| `go` directive in `go.mod` | the language version; features and vet checks vary by it |
| `Makefile` / `Taskfile.yml` with a `test` target | **the repo's canonical entry point** — read it before composing your own command; it likely sets build tags, `-race`, or coverage flags |
| `tools.go` with `//go:build tools` | the pinned dev tooling (lint, mockgen, etc.) — install from here, not from `latest` |

### Test framework

Go has one runner (`go test`) and several assertion/structure libraries layered
on it. The runner never changes; only the test body style does.

```bash
grep -rn "stretchr/testify\|onsi/ginkgo\|onsi/gomega\|google/go-cmp\|gotest.tools\|matryer/is\|smartystreets/goconvey" go.mod
grep -rln "testify/assert\|testify/require\|testify/suite" --include='*_test.go' . | head
grep -rln "ginkgo\." --include='*_test.go' . | head
grep -rln "cmp.Diff" --include='*_test.go' . | head
```

| Marker | Style | Note |
|---|---|---|
| no assertion lib, `if got != want { t.Errorf(...) }` | **stdlib `testing`** | the Go-idiomatic default; table-driven tests |
| `github.com/stretchr/testify` | **testify** | `assert` (continues) vs `require` (aborts). Also `suite.Suite` for setup/teardown |
| `github.com/onsi/ginkgo/v2` + `gomega` | **Ginkgo** | BDD, `Describe/Context/It`; needs `RunSpecs` in a `_suite_test.go`; often has its own CLI (`ginkgo`) |
| `github.com/google/go-cmp` | **cmp** | `cmp.Diff` for struct comparison — usually alongside stdlib style |
| `gotest.tools/v3` | gotest.tools assertions | |
| `_test.go` in package `foo` vs `foo_test` | internal vs external test package | external (`foo_test`) tests only the exported API — match what exists |

Table-driven subtests are the dominant Go idiom. Detect and match:

```bash
grep -rn "for _, tt := range\|for name, tc := range" --include='*_test.go' . | head
grep -rn "t.Run(" --include='*_test.go' . | wc -l
```

Also detect:

```bash
grep -rn "//go:build " --include='*_test.go' . | sort -u | head    # build tags gating tests
grep -rn "testing.Short()" --include='*_test.go' . | head          # -short support
grep -rln "golden\|\.golden" --include='*_test.go' . | head        # golden-file tests
ls testdata **/testdata 2>/dev/null                                 # the stdlib fixture convention
grep -rn "TestMain(" --include='*_test.go' . | head                 # package-level setup
```

Build tags matter: `//go:build integration` means those tests do **not** run
under a plain `go test ./...` and are invisible unless you pass
`-tags=integration`. A suite can look complete while half of it never runs.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. In Go, scoping is by
**package**, and it is cheap and reliable because the build cache makes
unchanged packages nearly free.

```bash
# Packages containing changed files — the standard scoped run
PKGS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.go' \
       | xargs -n1 dirname 2>/dev/null | sort -u | sed 's|^|./|')
[ -n "$PKGS" ] && go test $PKGS

# Include reverse dependencies (what else imports the changed packages).
# This is the closest Go gets to `jest --findRelatedTests`.
CHANGED_PKGS=$(echo "$PKGS" | xargs -r go list 2>/dev/null)
go list -deps=false -f '{{.ImportPath}} {{join .Imports " "}}' ./... \
  | awk -v want="$(echo $CHANGED_PKGS | tr ' ' '|')" '$0 ~ want {print $1}' \
  | sort -u | xargs -r go test

# Subtree scoping when the diff is confined to one area
go test ./pkg/...
go test ./internal/cart/...

# Everything (CI, not hooks)
go test ./...
```

Two flags that make hooks safe:

```bash
go test $PKGS -count=1      # defeat the test cache when you need a real run
go test $PKGS -short        # skip tests guarded by testing.Short()
```

`go test` **caches successful results** keyed on the package, its inputs and the
environment. A re-run with no changes prints `(cached)` and costs nothing — this
is why package-scoped runs are viable in a synchronous hook where a JVM or
Python suite would not be. Do not routinely pass `-count=1`; it throws the cache
away.

An empty `$PKGS` must not fail the hook — guard with `[ -n "$PKGS" ]`.

### Full suite / single file / single test

| Goal | Command |
|---|---|
| Whole module | `go test ./...` |
| One package | `go test ./internal/cart` |
| One test function | `go test ./internal/cart -run '^TestAddItem$'` |
| One subtest | `go test ./internal/cart -run '^TestAddItem$/^empty_cart$'` |
| Pattern (regex, anchored by `^...$`) | `go test ./... -run 'TestCart.*'` |
| Verbose, per-test output | `go test -v ./internal/cart` |
| Machine-readable | `go test -json ./... > .testsquad/gotest.json` |
| Fail fast | `go test -failfast ./...` |
| With race detector | `go test -race ./...` |
| Timeout (default 10m) | `go test -timeout 60s ./...` |
| Repeat for flake hunting | `go test -run '^TestX$' -count=50 ./pkg` |
| Parallelism | `-p N` (packages in parallel) / `-parallel N` (tests within a package) |
| Build tags | `go test -tags=integration ./...` |
| Benchmarks | `go test -bench=. -benchmem -run='^$' ./pkg` |
| Fuzzing | `go test -fuzz=FuzzParse -fuzztime=30s ./pkg` |
| Does it even build? | `go build ./... && go vet ./...` |

**Subtest names**: spaces in `t.Run("empty cart", ...)` become underscores in the
`-run` pattern (`empty_cart`). Getting this wrong silently runs nothing and
exits 0 — always confirm with `-v` that the expected test actually ran.

A `go test` that matched no tests exits **0**, not an error. Verify selection
explicitly:

```bash
go test -v -run '^TestAddItem$' ./internal/cart | grep -q "^=== RUN" || echo "NO TEST MATCHED"
```

Ginkgo repos: `ginkgo -r`, `ginkgo --focus="adds item"`, `ginkgo --randomize-all`,
`ginkgo -p` (parallel). `go test ./...` also works and is preferable in hooks
because it needs no extra binary.

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **Go cover profile**
(`coverage.out`), detected by its `mode:` first line.

```bash
# Whole module, cross-package attribution. -coverpkg is essential: without it,
# a test in package A that exercises package B credits nothing to B.
go test ./... -coverprofile=coverage.out -covermode=atomic -coverpkg=./...

# Scoped to the changed packages
go test $PKGS -coverprofile=coverage.out -covermode=atomic -coverpkg=./...

# Human views
go tool cover -func=coverage.out            # per-function percentages + total
go tool cover -html=coverage.out -o coverage.html
go tool cover -func=coverage.out | tail -1  # the single total line
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage.out` | Go cover profile | `gocover` |
| `coverage.html` | HTML | humans only |

`-covermode` values:

| Mode | Meaning | When |
|---|---|---|
| `set` | was the block executed (default) | fastest; fine without `-race` |
| `count` | how many times | hot-path analysis |
| `atomic` | count, race-safe | **required with `-race`** — use it as the default |

### Branch coverage: Go does not have it

> **Go's cover tool measures statement/block coverage only. There is no branch
> coverage flag.** The profile records basic blocks (`file:startLine.startCol,
> endLine.endCol numStatements count`), which is why
> `normalize_coverage.py` reports `branches_found: 0` and `branch_pct: null` for
> Go — correctly.

Report this as "branch coverage is not available in Go", never as 0%. A `null`
branch percentage in a Go report is the tool being honest, not a gap.

The practical substitute: a block with multiple conditions (`if a && b`) is one
statement. To expose the hidden branch, split the condition or write a
table-driven case per combination and check the per-function numbers from
`go tool cover -func`.

Normalize:

```bash
python3 scripts/normalize_coverage.py coverage.out --out .testsquad/coverage.json \
  --strip-prefix "$(go list -m)"
```

Go cover profiles use **import paths**, not filesystem paths
(`github.com/acme/app/internal/cart/service.go`). `--strip-prefix` with the
module path converts them to repo-relative paths so they join with
`git diff --name-only`. In a `go.work` workspace, strip each module path
separately.

Go also gives no per-line detail for uncovered code — the normalizer records the
**start line of each uncovered block**. Treat `uncovered_lines` as block
starts, not an exhaustive line list, and say so in any report derived from it.

Integration-test coverage of a built binary (Go 1.20+):

```bash
go build -cover -o ./bin/app ./cmd/app
GOCOVERDIR=/tmp/covdata ./bin/app ...          # run the binary / e2e suite
go tool covdata textfmt -i=/tmp/covdata -o=coverage.out
```

This is how you attribute E2E coverage back to source, and it emits the same
`coverage.out` format the normalizer reads.

---

## 4. Mutation testing — effectively unavailable in Go

**State this plainly in any report or roadmap. Do not pretend otherwise.**

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| `go-mutesting` | **Dead — last commit 2021** | do not adopt |
| `gremlins` | **Semi-dormant — last commit 2026-03-30** | use with caution, at your own risk |
| `ooze`, `mutesting` forks, assorted others | unmaintained hobby projects | do not adopt |

> **Go is the weakest mutation-testing ecosystem by a wide margin.** There is no
> Go equivalent of StrykerJS, PIT, mutmut or cargo-mutants. Every other stack in
> this playbook set has a healthy, diff-scopeable mutation tool; Go does not.
>
> **Recommendation: do not make mutation testing a gate in a Go repo.** If a
> roadmap item promises a Go mutation score, that item is unachievable with
> maintained tooling and should be struck.

### If a team insists on trying anyway

`gremlins` is the only option worth evaluating, and only with eyes open — it is
semi-dormant, so a Go release that changes the AST or toolchain can break it
with no fix forthcoming.

```bash
# Verify the install path and current release before relying on this.
go install github.com/go-gremlins/gremlins/cmd/gremlins@latest

gremlins unleash ./...                 # full run
gremlins unleash --dry-run ./...       # list mutants without executing
gremlins unleash --tags=integration ./...
gremlins unleash --workers=4 ./...
```

gremlins has a `--diff`-style coverage-driven selection in some versions;
**verify before relying on this** — the flag surface has changed between
releases and the documentation lags. Compose diff scoping by passing only the
changed packages:

```bash
PKGS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.go' \
       | xargs -n1 dirname | sort -u | sed 's|^|./|')
gremlins unleash $PKGS
```

Expect: no incremental cache, no history file, no supported CI integration, and
no upstream to report bugs to with any expectation of a fix.

### What to do instead in Go

The affordability rules below are what make mutation testing viable elsewhere;
since Go cannot deliver it, redirect the budget to the things Go *is* excellent
at — they produce more signal per minute here than a fragile mutation run would.

1. **`go test -race` on every changed package.** This is Go's highest-signal
   dynamic check and has no analogue in the other stacks. It finds real
   concurrency bugs, not hypothetical ones.
2. **Native fuzzing** (`go test -fuzz`) on parsers, decoders and any
   `[]byte`/`string` boundary. A deterministic engine doing the search is
   exactly the durable pattern from RESEARCH §16.
3. **`go vet` + `staticcheck`** as the gate (see §9).
4. **Hand-written mutants for the specific change under review.** Flip a
   comparison, invert a boolean, drop an error check — and verify the suite
   fails. That is the mutation idea applied manually, and it is what Meta's ACH
   does anyway: a few *targeted, realistic* mutants, not a full sweep
   (RESEARCH §4).

### Google's affordability rules — for reference and for the manual approach

Google generated 16.9M mutants and showed developers **12.5%**; median per
changelist went **820 → 7**; median surfaced **2**; hard cap **≤7 mutants per
file** (RESEARCH §15).

1. **One mutant per changed line.** In >90% of lines with multiple applicable
   mutants, all share the same fate.
2. **Arid-node suppression** — never mutate: `log.*` / `slog.*` / `zap` calls,
   metrics emitters, `panic`/`fmt.Errorf` message strings, trivial getters,
   pure delegation, `String()` methods, generated code
   (`// Code generated ... DO NOT EDIT.`). This step matters more than the
   choice of tool.
3. **Gate on the diff, never on a project score.**

---

## 5. E2E / integration options

Go's integration story is unusually good because the stdlib covers most of it.

| Tool | Use when | Honest cost |
|---|---|---|
| **`net/http/httptest`** | The idiomatic default. `httptest.NewServer` for a real local HTTP server; `httptest.NewRecorder` for handler unit tests | Essentially free. No reason not to use it |
| **`testing/fstest.MapFS`** | Filesystem fakes without touching disk | Only if the code takes an `fs.FS` — refactor to accept one |
| **`testdata/` + golden files** | Snapshot-style assertions, stdlib convention | Golden files rot; always provide an `-update` flag to regenerate |
| **Testcontainers-Go** | Real Postgres/Redis/Kafka in integration tests | Docker daemon required; container startup costs seconds, not milliseconds — time it in your own suite. Gate behind `//go:build integration` so unit runs stay fast |
| **`dockertest`** | Older alternative to Testcontainers | Prefer Testcontainers for new work |
| **`sqlmock`** | Asserting the exact SQL a repository emits | Asserts the query string, not the behaviour — brittle, and a textbook tautological test. Prefer a real DB via Testcontainers |
| **`httptest` + an interface seam** | Mocking an outbound third-party HTTP call | The Go way: define a narrow interface, inject a fake. No mocking framework needed |
| **WireMock** (container) | Contract-style stubbing across services | Another process; worth it only for multi-service contracts |
| **`go test -fuzz`** | Native coverage-guided fuzzing of parsers/decoders | Genuinely excellent and built in. Corpus lives in `testdata/fuzz/` and must be committed |
| **`gotestsum`** | Nicer output + JUnit XML for CI | `gotestsum --junitfile report.xml -- ./... -race` |
| **`rapid` / `gopter`** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16). Go's native fuzzer covers most of the same ground with better tooling |

```bash
# httptest — the pattern to match
srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(200); _, _ = w.Write([]byte(`{"ok":true}`))
}))
defer srv.Close()
client := NewClient(srv.URL)   // inject the URL — do not hardcode it in the client
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests.
In Go, derive the fake from the **interface declared at the consumer** — Go's
implicit interfaces make this cheap, and `mockgen`/`moq` generate from the real
interface rather than from imagination:

```bash
go install go.uber.org/mock/mockgen@latest     # verify current install path
mockgen -source=internal/cart/repo.go -destination=internal/cart/mock_repo_test.go -package=cart
go install github.com/matryer/moq@latest
moq -out repo_moq_test.go . Repository
```

Prefer a hand-written fake struct for a 2-method interface; generators pay off
above ~5 methods.

---

## 6. Flake taxonomy — Go

> **There is no published per-cause flakiness breakdown for Go** comparable to
> the Java (async 45% / concurrency 20% / order 12%), Python (order 59% /
> infrastructure 28%) or JavaScript (concurrency 21% / async 20% / OS 18% /
> network 13%) figures. Do not invent one and do not reuse another language's
> percentages.

What is well attested from practice rather than from a published distribution:
the two dominant Go flake causes are **goroutine leaks** and **`t.Parallel()`
misuse**. Treat those as the first two hypotheses, and say that the ranking is
practitioner consensus, not measured data.

### The two to check first

**Goroutine leaks** — a goroutine still running after the test returns keeps
touching shared state, a `t.Log` after test end panics with
`Log in goroutine after Test has completed`, and a leaked goroutine writing to a
closed channel or a reused port flakes a *later* test.

```bash
go get go.uber.org/goleak
```

```go
func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }
// or per-test:
defer goleak.VerifyNone(t)
```

**`t.Parallel()` misuse** — the classic forms:
- `t.Parallel()` in subtests that share a loop variable (pre-Go-1.22 capture
  semantics; still a bug if the variable is a shared pointer).
- `t.Parallel()` plus a `defer` cleanup in the parent: the parent returns before
  the parallel subtests run, so the cleanup fires too early. Use `t.Cleanup()`.
- `t.Parallel()` plus a shared fixture (a package-level DB, a fixed port, a
  `testdata` file written by the test).
- `t.Setenv()` in a parallel test — Go panics on this, by design.

```bash
grep -rn "t.Parallel()" --include='*_test.go' . | wc -l
grep -rn -B3 "t.Parallel()" --include='*_test.go' . | grep -n "defer \|os.Setenv\|:= range" | head
```

### Detection commands

```bash
# -race: Go's single best flake detector. Run it on every changed package.
go test -race $PKGS
go test -race -count=5 ./internal/cart

# -shuffle=on: randomize the order of tests within a package. The seed is
# printed; replay it to reproduce.
go test -shuffle=on ./...
go test -shuffle=1762934401234567890 ./internal/cart

# Repeat to surface timing flake
go test -run '^TestX$' -count=100 ./internal/cart
go test -run '^TestX$' -count=100 -race ./internal/cart

# Isolate: does it pass alone, serially?
go test -run '^TestX$' -p 1 -parallel 1 ./internal/cart

# Starve the scheduler — surfaces races that hide on a fast machine
GOMAXPROCS=1 go test -race ./...
GOMAXPROCS=1 go test -count=20 -run '^TestX$' ./pkg

# Environment sweep
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 go test ./...
TZ=UTC go test ./...

# Leak detection without goleak, as a cheap first pass
go test -race -run '^TestX$' -v ./pkg 2>&1 | grep -i "after Test has completed"
```

`-race` costs a large multiple of both runtime and memory — the Go race
detector's own documentation gives the upstream figures, and
`go test -count=1 ./pkg` with and without `-race` gives yours. Run it on changed
packages in hooks and on everything in CI — not the reverse.

### Static flake smells — check before the test ever runs (Tier 0)

```bash
grep -rn "time.Sleep" --include='*_test.go' . | head -40
grep -rn "time.Now()\|time.Since(" --include='*_test.go' . | head -20
grep -rn "rand\.\|uuid.New()" --include='*_test.go' . | head -20
grep -rn "for .* := range .*map\[" --include='*_test.go' . | head -20
grep -rnE ":[0-9]{4,5}\"" --include='*_test.go' . | head          # hardcoded ports
grep -rn "os.Setenv\|os.Getenv" --include='*_test.go' . | head
grep -rn "http.Get\|http.Post\|net.Dial" --include='*_test.go' . | head  # real network
```

Specific smells: `time.Sleep` used as synchronization (use a channel, a
`sync.WaitGroup`, or `require.Eventually`) · `time.Now()` in an assertion
(inject a clock) · unseeded `math/rand` (Go 1.20+ auto-seeds the global source —
which makes it *less* reproducible, so seed explicitly with
`rand.New(rand.NewSource(1))`) · `uuid.New()` in an expected value ·
**range over a map in an assertion — Go randomizes map iteration order by
design**, so sort keys or compare with `cmp.Diff` on the map itself ·
hardcoded ports (use `:0` and read back the assigned port) · `os.Setenv`
without `t.Setenv` (which restores automatically, but panics under
`t.Parallel()`) · writing into `testdata/` during a test · real network calls ·
unbuffered channels with no timeout (a deadlock becomes a 10-minute test
timeout) · `reflect.DeepEqual` on structs with time fields.

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

```bash
grep -rnE "(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp|clickhouse)://" --include='*.go' --include='*.yaml' --include='*.yml' --include='*.env*' . \
  | grep -vE "localhost|127\.0\.0\.1|::1|testcontainers|test"
grep -rn "os.Getenv(\"DATABASE_URL\"\|os.Getenv(\"DSN\"" --include='*_test.go' . 
grep -rnE "godotenv|joho/godotenv" --include='*.go' .
grep -rnE "\.amazonaws\.com|\.rds\.|prod\.|production" --include='*.go' --include='*.yaml' --include='*.yml' . | head -30
ls .env .env.production config/production.yaml config/prod.yaml 2>/dev/null
grep -rn "ENV\|APP_ENV\|GO_ENV" --include='*.yaml' --include='*.yml' .github/workflows/ 2>/dev/null
```

| Red flag | Why |
|---|---|
| `os.Getenv("DATABASE_URL")` in a test or `TestMain` with **no test-only fallback** | CI secrets or the developer's shell point it at staging/prod |
| `godotenv.Load()` in `TestMain` or a test helper with no explicit path | loads the developer's real `.env` |
| A real host in `config/config.yaml` and no `config/test.yaml` override | the default config is the production config |
| `AWS_ENDPOINT_URL` / `S3_ENDPOINT` unset, no LocalStack, real `aws-sdk-go` client in tests | real AWS calls and real charges |
| A migration runner (`golang-migrate`, `goose`, `atlas`) invoked by `TestMain` without a DB-name guard | **`migrate down` / `goose reset` on a real database destroys it** |
| `//go:build integration` tests that read prod credentials from the environment | the tag hides them from review, not from CI |
| `httptest` not used — the client's base URL defaults to a production host | a test hitting the live API |
| Stripe/Twilio/SendGrid keys matching `sk_live_` | live-money side effects |
| A `main()` smoke test that starts the real server on the real port | port conflicts plus real traffic |

Assert the guard, don't assume it:

```go
func TestMain(m *testing.M) {
    dsn := os.Getenv("DATABASE_URL")
    if dsn != "" && !strings.ContainsAny(dsn, "") &&
        !(strings.Contains(dsn, "localhost") || strings.Contains(dsn, "127.0.0.1") ||
          strings.Contains(dsn, "test")) {
        fmt.Fprintf(os.Stderr, "refusing to run tests against %s\n", dsn)
        os.Exit(3)
    }
    os.Exit(m.Run())
}
```

---

## 8. House conventions to match

Go's conventions are enforced by the toolchain, so there is less variance here
than in any other stack — but match the repo anyway.

| Convention | Rule |
|---|---|
| Test file location | **Same directory and same package as the code.** `internal/cart/service.go` → `internal/cart/service_test.go`. There is no `tests/` directory in idiomatic Go |
| Test package | `package cart` (internal — can touch unexported identifiers) or `package cart_test` (external — exported API only). Check which the directory uses and match it |
| File naming | `<subject>_test.go`. `export_test.go` is the conventional way to expose internals to an external test package |
| Function naming | `TestXxx(t *testing.T)`, `BenchmarkXxx(b *testing.B)`, `FuzzXxx(f *testing.F)`, `ExampleXxx()` |
| Subtest naming | `t.Run("adds item to empty cart", ...)` — spaces become underscores in `-run` |
| Fixtures | **`testdata/`** — the toolchain ignores this directory for builds. Golden files go in `testdata/golden/` |
| Golden-file update | an `-update` flag: `var update = flag.Bool("update", false, "update golden files")`, run with `go test ./pkg -update` |
| Package setup/teardown | `TestMain(m *testing.M)` — one per package |
| Per-test cleanup | `t.Cleanup(func(){...})`, not `defer`, when `t.Parallel()` is in play |
| Helpers | mark them `t.Helper()` so failures report the caller's line |
| Shared test utilities | an `internal/testutil` package, or a `*_test.go` helper file in the same package |
| Build tags | `//go:build integration` on the first line, blank line after — gate anything needing Docker/network |
| Mocks | hand-written fakes for small interfaces; `mockgen`/`moq`-generated `*_mock_test.go` for larger ones. The interface is declared at the **consumer**, not the producer |

Table-driven test, the shape to match:

```go
func TestAddItem(t *testing.T) {
    tests := map[string]struct {
        cart    Cart
        item    Item
        want    Cart
        wantErr error
    }{
        "empty cart":     {/* ... */},
        "duplicate item": {/* ... */},
    }
    for name, tc := range tests {
        t.Run(name, func(t *testing.T) {
            t.Parallel()
            got, err := tc.cart.Add(tc.item)
            if !errors.Is(err, tc.wantErr) {
                t.Fatalf("err = %v, want %v", err, tc.wantErr)
            }
            if diff := cmp.Diff(tc.want, got); diff != "" {
                t.Errorf("mismatch (-want +got):\n%s", diff)
            }
        })
    }
}
```

Assertion idiom: stdlib `if got != want { t.Errorf("got %v, want %v", got, want) }`
plus `cmp.Diff` for structs; testify `require.NoError(t, err)` /
`assert.Equal(t, want, got)` where testify is already a dependency. **Match the
directory**; mixing testify and stdlib style in one package is the most common
convention violation an agent introduces.

---

## 9. Static analysis + type checking

Go's compiler is the type checker, and `go vet` ships with it. These are the
cheapest wins in any stack and should rank first on every Go roadmap.

```bash
# Tier 0 — free, always run, no install
go build ./...
go vet ./...
gofmt -l .                       # lists unformatted files; empty output = clean
test -z "$(gofmt -l .)"          # exit non-zero if anything is unformatted
go mod tidy -diff                # fails if go.mod/go.sum are stale (Go 1.23+; verify)

# Vet on just the changed packages, for hooks
go vet $PKGS

# Tier 1 — staticcheck: the highest-value Go linter by a wide margin
go install honnef.co/go/tools/cmd/staticcheck@latest
staticcheck ./...
staticcheck -f json ./... > .testsquad/staticcheck.json

# Tier 1 — golangci-lint: the aggregator most repos already configure
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
golangci-lint run
golangci-lint run --new-from-rev=origin/main      # DIFF-SCOPED — use this in CI
golangci-lint run --out-format=json > .testsquad/golangci.json
golangci-lint run $PKGS

# Tier 2
go install golang.org/x/vuln/cmd/govulncheck@latest
govulncheck ./...                                  # known CVEs in your dep graph
go install github.com/securego/gosec/v2/cmd/gosec@latest
gosec -fmt=json -out=.testsquad/gosec.json ./...
go install golang.org/x/tools/cmd/deadcode@latest
deadcode ./...
```

`golangci-lint run --new-from-rev=origin/main` is the direct implementation of
RESEARCH §6: findings scoped to the diff get ~70% fix rates; the same findings
as a batch list get ~0%. Use it rather than dumping the full legacy backlog on
first install.

Linters worth enabling, in roadmap order:

| Linter | Catches | Why early |
|---|---|---|
| `go vet` | printf-arg mismatches, lost struct tags, unreachable code, **`copylocks`**, **`loopclosure`** | free, zero false positives by policy |
| `staticcheck` (SA*) | real bugs: impossible conditions, wrong `sync` usage, leaked tickers, ineffective appends | highest signal per minute |
| `errcheck` | **unchecked error returns** — Go's #1 real-bug source | |
| `govet`'s `lostcancel` + `contextcheck` | `context.WithCancel` whose cancel is never called — a goroutine leak | feeds §6 directly |
| `bodyclose` | unclosed `http.Response.Body` — connection and goroutine leak | |
| `rowserrcheck`, `sqlclosecheck` | leaked DB rows | |
| `paralleltest` | missing or misused `t.Parallel()` | directly targets the Go flake pattern in §6 |
| `tparallel` | `t.Parallel()` in a parent whose cleanup is `defer`red | same |
| `thelper` | test helpers missing `t.Helper()` | cheap, improves every failure message |
| `testifylint` | `assert` where `require` is needed, wrong arg order (`assert.Equal(t, got, want)` reversed) | only for testify repos |
| `noctx`, `errorlint`, `nilerr` | context-less requests, `errors.Is`/`As` misuse | tier 2 |

Config to read before trusting the output:

```bash
cat .golangci.yml .golangci.yaml .golangci.toml 2>/dev/null
grep -nE "^\s*(disable|enable|skip-dirs|skip-files|exclude)" .golangci.y*ml 2>/dev/null
grep -rn "//nolint" --include='*.go' . | wc -l
grep -rn "^//go:generate\|Code generated .* DO NOT EDIT" --include='*.go' . | wc -l
```

A large or growing `//nolint` count means findings are being silenced rather
than fixed — report the count and its trend, not a clean run. Generated files
(`DO NOT EDIT`) should be excluded from both lint and coverage targets; if they
are not, the numbers are distorted and that distortion should be stated.
