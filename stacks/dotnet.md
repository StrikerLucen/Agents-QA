# Stack playbook — .NET / C#

Reference for a testsquad agent working in a .NET repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

.NET is one of the two strongest stacks in this set: a first-class diff-scoped
mutation tool (Stryker.NET), Cobertura coverage with real branch data, and a
compiler that already does most of the static analysis. Rank §9 first on any
roadmap and §4 second.

---

## 1. Detection signals

```bash
ls *.sln *.slnx global.json Directory.Build.props Directory.Packages.props nuget.config 2>/dev/null
find . -name '*.csproj' -o -name '*.fsproj' -o -name '*.vbproj' | grep -v obj/ | head -40
dotnet --info                       # SDK + runtimes actually installed
cat global.json 2>/dev/null         # SDK pin — a mismatch fails every command
```

| Marker | Conclusion |
|---|---|
| `*.sln` / `*.slnx` | solution; `dotnet test MySln.sln` builds everything. Slow — prefer per-project |
| `global.json` | **SDK is pinned.** If that SDK is not installed, every `dotnet` command fails with a confusing error. Check first |
| `Directory.Build.props` | repo-wide MSBuild settings — `TreatWarningsAsErrors`, `Nullable`, `LangVersion`, analyzers. **Read it; it defines the rules you must not break** |
| `Directory.Packages.props` + `ManagePackageVersionsCentrally` | Central Package Management — do not add a `Version=` to a `PackageReference` |
| `<TargetFrameworks>` (plural) | multi-targeted; `dotnet test` runs the suite **once per TFM**. Use `-f net8.0` to pick one |
| `packages.config` / `<Project ToolsVersion=` | **legacy .NET Framework project.** `dotnet test` may not work; you need `vstest.console.exe` / `msbuild` on Windows |
| `*.fsproj` | F# — test frameworks below still apply (xUnit/NUnit/Expecto) |

### Test framework

```bash
grep -rn "PackageReference Include=\"\(xunit\|NUnit\|MSTest\|Expecto\|TUnit\)" --include='*.csproj' --include='*.props' . | sort -u
grep -rln "\[Fact\]\|\[Theory\]" --include='*.cs' . | head
grep -rln "\[Test\]\|\[TestFixture\]\|\[TestCase" --include='*.cs' . | head
grep -rln "\[TestMethod\]\|\[TestClass\]" --include='*.cs' . | head
grep -rn "Microsoft.NET.Test.Sdk\|Microsoft.Testing.Platform" --include='*.csproj' . | head
```

| Marker | Framework | Notes |
|---|---|---|
| `xunit` / `xunit.v3` + `[Fact]` / `[Theory]` | **xUnit** | dominant in new code. New class instance per test. **Parallelises by collection** — see §6 |
| `NUnit` + `[Test]` / `[TestCase]` | **NUnit** | one fixture instance reused across tests by default → shared state. `[Parallelizable]` is opt-in |
| `MSTest` + `[TestMethod]` | **MSTest** | common in older/enterprise repos. Parallelism opt-in via assembly attribute |
| `Expecto` | F# Expecto | runs as a console app, not via `dotnet test` adapter by default |
| `Microsoft.NET.Test.Sdk` | VSTest adapter (classic) | `dotnet test` works |
| `Microsoft.Testing.Platform` / `<EnableMSTestRunner>` / `<UseMicrosoftTestingPlatformRunner>` | **MTP** — the newer runner | **flag syntax differs from VSTest**: `--filter` vs `--treenode-filter`, and `dotnet run --project ... -- <args>`. Read `dotnet test -- --help` in the repo. *Verify before relying on this* |

Assertion/mocking libraries change what idiomatic looks like:

```bash
grep -rn "PackageReference Include=\"\(FluentAssertions\|Shouldly\|AwesomeAssertions\|Moq\|NSubstitute\|FakeItEasy\|AutoFixture\|Bogus\|Verify\|Testcontainers\|WireMock.Net\|Respawn\|Microsoft.AspNetCore.Mvc.Testing\|EntityFrameworkCore.InMemory\|Snapshooter\)" --include='*.csproj' . | sort -u
```

Note: **FluentAssertions changed to a paid licence at v8** for commercial use;
many repos pinned to 7.x or moved to `AwesomeAssertions` (the MIT fork) or
`Shouldly`. If you see an 8.x pin in a commercial repo, flag it as a licensing
finding, not a test finding. *Verify current licence terms before advising.*

### `dotnet test` vs `vstest`

| Situation | Command |
|---|---|
| SDK-style project, any modern TFM | `dotnet test` — always prefer this |
| Pre-built binaries, CI reusing artifacts | `dotnet vstest path/to/Tests.dll` (no build step) |
| .NET Framework `packages.config` project | `vstest.console.exe` or `msbuild /t:VSTest` — Windows only |
| MTP-enabled project | `dotnet test` still works; extra args go after `--` |

`dotnet vstest` does **not** restore or build. If the DLL is stale you are
testing yesterday's code and nothing will tell you.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake.

**.NET has no `--findRelatedTests` equivalent.** There is no supported
import-graph test selector in the SDK. Scope by **project** — that is the
granularity the ecosystem actually gives you, and it is usually enough because
test projects map 1:1 to source projects by convention.

```bash
# 1. Changed source projects -> their sibling test projects (the practical default)
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.cs')
PROJS=$(for f in $CHANGED; do
          d=$(dirname "$f")
          while [ "$d" != "." ] && [ "$d" != "/" ]; do
            p=$(ls "$d"/*.csproj 2>/dev/null | head -1); [ -n "$p" ] && echo "$p" && break
            d=$(dirname "$d")
          done
        done | sort -u)
for p in $PROJS; do
  base=$(basename "$p" .csproj)
  grep -q "IsTestProject\|Microsoft.NET.Test.Sdk" "$p" && echo "$p"
  find . -name "${base}.Tests.csproj" -o -name "${base}.UnitTests.csproj" | grep -v obj/
done | sort -u | while read t; do dotnet test "$t" --no-restore; done

# 2. Changed test files -> run just those classes by name filter
CLASSES=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*Tests.cs' '*Test.cs' \
          | xargs -n1 basename 2>/dev/null | sed 's/\.cs$//' | sort -u)
FILTER=$(printf 'FullyQualifiedName~%s|' $CLASSES | sed 's/|$//')
[ -n "$FILTER" ] && dotnet test --filter "$FILTER" --no-restore

# 3. Subtree scoping when the diff is confined
dotnet test tests/Acme.Cart.Tests/Acme.Cart.Tests.csproj

# 4. Everything (CI, not hooks)
dotnet test
```

Flags that make a hook survivable:

```bash
dotnet test --no-restore --no-build    # only if a build already ran this session
dotnet build && dotnet test --no-build # explicit two-phase; faster than two builds
dotnet test --filter "Category!=Integration&Category!=Slow"
dotnet test -m:1                        # serialise MSBuild — lower peak memory
```

**An empty filter matches nothing and `dotnet test` exits 1** with
"No test matches the given testcase filter". Guard it:

```bash
[ -z "$FILTER" ] && exit 0
dotnet test --filter "$FILTER"; rc=$?
# Treat "no tests matched" as success in a hook:
[ $rc -ne 0 ] && dotnet test --filter "$FILTER" 2>&1 | grep -q "No test matches" && rc=0
exit $rc
```

### Full suite / single file / single test

| Goal | Command |
|---|---|
| Whole solution | `dotnet test` (from the dir with the `.sln`) |
| One project | `dotnet test tests/Acme.Cart.Tests` |
| One class | `dotnet test --filter "FullyQualifiedName~Acme.Cart.Tests.CartTests"` |
| One test method | `dotnet test --filter "FullyQualifiedName=Acme.Cart.Tests.CartTests.AddsItem"` |
| By display name | `dotnet test --filter "DisplayName~adds item"` |
| By trait/category | `dotnet test --filter "Category=Unit"` (MSTest/NUnit) · `--filter "Trait=Fast"` or `"Category=Fast"` for xUnit `[Trait("Category","Fast")]` |
| Boolean filters | `--filter "Category=Unit&Priority=1"`, `|` for or, `!=` for not |
| One TFM of a multi-target project | `dotnet test -f net8.0` |
| Verbose | `dotnet test -v normal` / `--logger "console;verbosity=detailed"` |
| TRX for CI | `dotnet test --logger "trx;LogFileName=results.trx"` |
| JUnit XML | `dotnet test --logger "junit;LogFilePath=results.xml"` (needs `JunitXml.TestLogger`) |
| List without running | `dotnet test --list-tests` |
| Fail fast | no native flag; xUnit v3 has `-stopOnFail`, NUnit has `--stoponerror` via `-- NUnit.StopOnError=true` |
| Timeout per test | xUnit `[Fact(Timeout=5000)]` (needs collection parallelism off), NUnit `[Timeout(5000)]` |
| Blame hangs/crashes | `dotnet test --blame-hang --blame-hang-timeout 5m --blame-crash` |
| Repeat for flake hunting | NUnit `[Repeat(50)]`; otherwise loop in shell |

`--filter` operators: `=` exact, `!=` not, `~` contains, `!~` not-contains.
`FullyQualifiedName~Foo` is the one to reach for by default; use the others only
when a substring match is ambiguous.

Verify your filter selected something before trusting a green run:

```bash
dotnet test --filter "$FILTER" --list-tests | grep -c "    " || echo "NO TEST MATCHED"
```

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **Cobertura XML**.
Coverlet emits it natively and it carries real `condition-coverage` data, so
.NET gets genuine branch coverage.

```bash
# The standard path — coverlet.collector is already a dependency in the
# default test project template.
dotnet test --collect:"XPlat Code Coverage"
# -> TestResults/<guid>/coverage.cobertura.xml

# Pin the output location so you can find it deterministically
dotnet test --collect:"XPlat Code Coverage" \
  --results-directory ./TestResults \
  -- DataCollectionRunSettings.DataCollectors.DataCollector.Configuration.Format=cobertura

# MSBuild integration (needs the coverlet.msbuild package, not the collector)
dotnet test /p:CollectCoverage=true \
            /p:CoverletOutputFormat=cobertura \
            /p:CoverletOutput=./TestResults/coverage.cobertura.xml \
            /p:Threshold=0 \
            /p:ExcludeByAttribute="GeneratedCodeAttribute%2cCompilerGeneratedAttribute%2cExcludeFromCodeCoverageAttribute"
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage.cobertura.xml` | Cobertura XML | `cobertura` ✅ |
| `coverage.opencover.xml` | OpenCover XML | **not parsed** — convert or use cobertura |
| `coverage.json` (coverlet's own) | coverlet intermediate | **not parsed** — it is not coverage.py JSON |
| `*.coverage` (`--collect:"Code Coverage"`, no XPlat) | binary Microsoft format | **not parsed**; needs `dotnet-coverage merge -f cobertura` |
| `lcov.info` (`CoverletOutputFormat=lcov`) | lcov | `lcov` ✅ — also fine |

If a repo already uses the binary collector, convert:
`dotnet tool install -g dotnet-coverage` then
`dotnet-coverage collect -f cobertura -o coverage.cobertura.xml "dotnet test"`
(or `dotnet-coverage merge -f cobertura -o merged.cobertura.xml "**/*.coverage"`).

### Branch coverage

**.NET has real branch coverage and it is on by default.** Coverlet reports
line, branch and method coverage without any extra flag, and writes
`branch-rate` plus per-line `condition-coverage="75% (3/4)"` into the Cobertura
XML — which is exactly what the normalizer reads. No `--branch` equivalent is
needed; if a Cobertura file from a .NET run has no `condition-coverage`
attributes, the report was produced by something other than coverlet and you
should say branch data was **not measured** (`null`), never 0%.

Exclusions — set them or the numbers are noise:

```xml
<!-- Directory.Build.props or the test csproj -->
<PropertyGroup>
  <ExcludeByAttribute>GeneratedCodeAttribute,CompilerGeneratedAttribute,ExcludeFromCodeCoverageAttribute</ExcludeByAttribute>
  <ExcludeByFile>**/Migrations/*.cs,**/*.Designer.cs,**/*.g.cs</ExcludeByFile>
  <Include>[Acme.*]*</Include>
  <Exclude>[*.Tests]*,[*]*.Program</Exclude>
</PropertyGroup>
```

The same `<Format>`/`<ExcludeByAttribute>` keys work inside a
`.runsettings` `<DataCollector friendlyName="XPlat code coverage"><Configuration>`
block — preferred when CI already passes `--settings coverage.runsettings`.

Normalize (Cobertura `<sources>` entries are absolute — strip the repo root):

```bash
python3 scripts/normalize_coverage.py TestResults/*/coverage.cobertura.xml \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

Multiple test projects write multiple Cobertura files. Merge first:

```bash
dotnet tool install -g dotnet-reportgenerator-globaltool
reportgenerator -reports:"TestResults/**/coverage.cobertura.xml" \
  -targetdir:"./TestResults/merged" -reporttypes:"Cobertura;TextSummary"
```

Do not gate on `/p:Threshold=80` project-wide: it fails on legacy debt the
current change did not cause. Compute the diff figure from the normalized JSON.

---

## 4. Mutation testing — Stryker.NET

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| **Stryker.NET 5.1.0** | **Very healthy** | the default and only choice |
| `VisualMutator`, `Ninjaturtles` | abandoned | do not adopt |

**Stryker.NET has real git-diff scoping — `--since`.** This is the flag
StrykerJS does *not* have (StrykerJS uses `--incremental`, which is a result
cache, not a diff scope). Blog posts and even some docs conflate the two.
`--since` is .NET-only; do not carry it to a JS repo, and do not expect
`--incremental` semantics from it.

```bash
dotnet tool install -g dotnet-stryker     # or: dotnet new tool-manifest && dotnet tool install dotnet-stryker
dotnet stryker --version
```

### Diff-scoped invocation — the one to use

```bash
# Only mutate code changed since origin/main, and report a FULL project score by
# reusing the baseline results for everything untouched.
dotnet stryker \
  --project Acme.Cart.csproj \
  --since:origin/main \
  --with-baseline:origin/main \
  --reporter json --reporter cleartext \
  --output .testsquad/stryker
```

| Flag | What it does |
|---|---|
| `--since:origin/main` | mutate **only** files changed vs that ref. Without it you mutate the whole project |
| `--with-baseline:origin/main` | load the previous run's results and merge, so the reported score covers the whole project while only the diff is executed |
| `--since:<ref> --mutate "!**/Migrations/**"` | diff scope minus noise |
| `--project <csproj>` | required when a test project references several source projects |
| `--test-project <csproj>` | restrict which test project runs the mutants |
| `--concurrency N` | default is cores−1; lower it in CI to avoid thrash |
| `--break-at N` | fail the run below N% mutation score — **do not use**, see below |
| `--mutation-level Basic\|Standard\|Advanced\|Complete` | mutant volume; `Basic` is the affordable tier |

`--with-baseline` needs somewhere to persist the baseline: set
`baseline.provider` to `Disk` (local) or `AzureFileStorage`/`Dashboard` (CI).
On a cold cache the first run is full-project — budget for that once.

```json
// stryker-config.json next to the test project
{ "stryker-config": {
    "project": "Acme.Cart.csproj",
    "mutation-level": "Basic",
    "reporters": ["json", "cleartext"],
    "mutate": ["!**/Migrations/**", "!**/*.Designer.cs", "!**/*.g.cs", "!**/Program.cs"],
    "ignore-methods": ["*Log*", "*Logger*", "Console.Write*", "ToString", "GetHashCode", "*Dispose*"],
    "ignore-mutations": ["string", "Regex"],
    "baseline": { "provider": "Disk" },
    "concurrency": 4 } }
```

`ignore-methods` and `ignore-mutations` are Stryker's arid-node suppression.
**Configure them before the first real run** — they are the difference between
a usable report and 800 mutants nobody reads.

```bash
jq '.files | to_entries[] | {f:.key, survived:[.value.mutants[]|select(.status=="Survived")]|length}' \
  .testsquad/stryker/reports/mutation-report.json
```

Statuses: `Killed` (good), `Survived` (**the finding** — a real test gap),
`NoCoverage` (not executed at all — a coverage gap, report separately),
`Timeout` (counted as killed), `CompileError` (ignore, Stryker's problem).

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**.

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste. Stryker
   has no one-per-line flag: use `--mutation-level Basic`, then **post-filter
   the JSON to one surviving mutant per line and ≤7 per file.**
2. **Arid-node suppression** — never mutate: `ILogger`/`Serilog`/`NLog` calls,
   metrics emitters, `Console.Write*`, exception *message* strings,
   `ToString`/`GetHashCode`/`Equals`, trivial auto-property getters, pure
   delegation, `Dispose`, `[GeneratedCode]`/`*.g.cs`/`*.Designer.cs`, EF
   migrations. Via `ignore-methods`, `ignore-mutations`, `mutate` exclusions.
   **This step matters more than the choice of tool** — it is the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff. Do not set
   `--break-at` against the whole project.

Blind spots to state out loud: (a) a change in file A destroying coverage of
file B is invisible; (b) **a test-only change runs no mutants at all**, so
deleting assertions passes the gate; (c) config/fixture changes flip mutant
status silently. Mitigation: an occasional full run off the critical path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **`WebApplicationFactory<TProgram>`** (`Microsoft.AspNetCore.Mvc.Testing`) | The idiomatic default for ASP.NET Core. Boots the real DI container and pipeline in-process | Needs `Program` to be reachable (`public partial class Program {}` or `InternalsVisibleTo`). Proves nothing about the deployed network path, TLS, or reverse proxy |
| **Testcontainers for .NET** | Real SQL Server/Postgres/Redis/Kafka/Azurite | Docker daemon required; 2–30s startup (SQL Server is the slow one). Gate behind a `[Trait("Category","Integration")]` |
| **`Respawn`** | Reset a real DB between integration tests without re-migrating | Far faster than drop/create. Must be told which tables to skip (`__EFMigrationsHistory`) |
| **EF Core `InMemory` provider** | — | **Avoid.** Microsoft explicitly discourages it: no relational constraints, no transactions, different query translation. A test passes here and fails on SQL. Use SQLite in-memory or a container |
| **SQLite in-memory** (`DataSource=:memory:`) | Lighter than a container, keeps relational semantics | Connection must stay open or the DB vanishes. Still not your production dialect |
| **WireMock.Net** | Stubbing an outbound third-party HTTP API | Another listener; worth it for contract-style stubs |
| **`HttpMessageHandler` fake + `IHttpClientFactory`** | Mocking one outbound call | The cheap, framework-free way. Prefer it for single calls |
| **Playwright for .NET** | Browser E2E | Browser download is large — hundreds of megabytes, cache it in CI; the 10%-flake tier |
| **Verify / Snapshooter** | Approval/snapshot assertions | Snapshots rot; require a deliberate `*.received.*` → `*.verified.*` review step, and commit the verified files |
| **FsCheck / CsCheck** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16). No study shows LLM-written property tests beat example tests under equal budget |

```csharp
// WebApplicationFactory — the shape to match: swap the DB registration, keep the rest
_client = factory.WithWebHostBuilder(b => b.ConfigureServices(s => {
    s.RemoveAll<DbContextOptions<AppDb>>();
    s.AddDbContext<AppDb>(o => o.UseNpgsql(_pg.GetConnectionString()));
})).CreateClient();
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an
LLM invent what a dependency returns is *the* root cause of tautological tests.
In .NET, derive the shape from the **interface and its DTO types** — the
compiler already tells you the contract. `Moq`/`NSubstitute` set up against a
real interface; never hand-roll a JSON blob you imagined.

Moq note: the 4.20.x `SponsorLink` episode pushed many repos to
**NSubstitute** or **FakeItEasy**. Match what the repo uses; do not introduce a
second mocking library.

---

## 6. Flake taxonomy — .NET

> **There is no published per-cause flakiness breakdown for C#/.NET**
> comparable to the Java (async 45% / concurrency 20% / order 12%), Python
> (order 59% / infrastructure 28%) or JavaScript (concurrency 21% / async 20% /
> OS 18% / network 13%) figures in RESEARCH §19. Do not invent one and do not
> reuse another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way
in any report.

### The ones to check first

**Test-framework parallelism semantics.** This is the .NET-specific hazard and
differs per framework:

| Framework | Default | Consequence |
|---|---|---|
| **xUnit** | tests in **different collections run in parallel**; tests in the same collection are serial. A new class instance per test | Two test *classes* touching the same static field, file, port or DB row race by default. Fix by putting them in one `[Collection("Db")]`, not by sprinkling locks |
| **NUnit** | serial by default; **one fixture instance shared by all tests in it** | Shared instance fields leak between tests. `[Parallelizable(ParallelScope.All)]` turns on the races |
| **MSTest** | serial by default; opt in with `[assembly: Parallelize(Workers=0, Scope=ExecutionScope.MethodLevel)]` | Method-level parallelism in a class with instance state is the classic foot-gun |

```bash
# What parallelism is actually configured?
grep -rn "CollectionBehavior\|DisableTestParallelization\|MaxParallelThreads" --include='*.json' --include='*.cs' .
cat **/xunit.runner.json 2>/dev/null
grep -rn "\[Collection(" --include='*.cs' . | head
grep -rn "Parallelizable\|LevelOfParallelism" --include='*.cs' . | head
grep -rn "assembly: Parallelize\|DoNotParallelize" --include='*.cs' . | head
```

**`async void` test methods** — the test completes before the assertion runs,
and an exception goes to the synchronization context, not the runner. A test
that cannot fail is worse than no test.

```bash
grep -rn "async void" --include='*Test*.cs' --include='*Spec*.cs' . | head
```

**`.Result` / `.Wait()` / `.GetAwaiter().GetResult()` in tests** — deadlocks on
any context-capturing code, intermittently.

```bash
grep -rnE "\.Result\b|\.Wait\(\)|GetAwaiter\(\)\.GetResult\(\)" --include='*Test*.cs' . | head -20
```

**`IClassFixture` / `ICollectionFixture` mutated by a test** — xUnit's shared
fixtures are the .NET analogue of a session-scoped pytest fixture, and the same
order-dependency bug.

**`DateTime.Now`, `TimeZoneInfo.Local`, `CultureInfo.CurrentCulture`** —
decimal separators and date parsing differ per machine. .NET on Linux uses ICU
and differs from Windows NLS for string comparison.

### Detection commands

```bash
# Serialise everything — if it goes green, parallelism is the cause
dotnet test -- xUnit.MaxParallelThreads=1 xUnit.ParallelizeAssembly=false
dotnet test -- NUnit.NumberOfTestWorkers=1
dotnet test --filter "..." -m:1

# Randomise order: NUnit has it (NUnit.RandomSeed=12345). xUnit has NO built-in
# shuffle flag — implement ITestCaseOrderer/ITestCollectionOrderer with a seeded
# shuffle, or accept that order dependency is undetectable. *Verify before
# relying on any third-party shuffle package being maintained.*
dotnet test -- NUnit.RandomSeed=12345

# Repeat to surface timing flake
for i in $(seq 1 50); do dotnet test --filter "FullyQualifiedName~CartTests.AddsItem" --no-build || break; done
# NUnit natively: [Repeat(50)] / [Retry(3)] — Retry HIDES flake; report it as a finding

# Hangs and crashes
dotnet test --blame-hang --blame-hang-timeout 2m --blame-crash
# -> Sequence.xml naming the test that hung

# Environment sweep
TZ=Pacific/Kiritimati DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=0 LANG=tr-TR dotnet test
TZ=UTC dotnet test
DOTNET_TieredPGO=0 DOTNET_TieredCompilation=0 dotnet test   # timing-sensitive cases
```

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "Thread.Sleep\|Task.Delay" --include='*Test*.cs' . | head -30
grep -rn "DateTime.Now\|DateTime.Today\|DateTimeOffset.Now" --include='*Test*.cs' . | head -20
grep -rn "new Random()\|Guid.NewGuid()" --include='*Test*.cs' . | head -20
grep -rn "static " --include='*Test*.cs' . | grep -v "static class\|const" | head -20
grep -rnE ":[0-9]{4,5}\"" --include='*Test*.cs' . | head          # hardcoded ports
grep -rn "Environment.SetEnvironmentVariable" --include='*Test*.cs' . | head
grep -rn "HttpClient(" --include='*Test*.cs' . | head             # real network
grep -rn "\[Retry(\|\[Repeat(" --include='*.cs' . | head          # flake being hidden
```

Specific smells: `Thread.Sleep` as synchronisation (use a `TaskCompletionSource`
or a deadline-bounded poll) · `DateTime.Now` in an assertion (inject
`TimeProvider`; .NET 8+ has `FakeTimeProvider`) · unseeded `new Random()` ·
`Guid.NewGuid()` in an expected value · `static` mutable fields in a test class ·
**asserted `Dictionary`/`HashSet` enumeration order** (unspecified — order it) ·
hardcoded ports (bind `:0`) · `CurrentCulture`-dependent `ToString()`/`Parse` ·
`[Retry]`, which **hides** flake and is a finding, not a fix.

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

```bash
ls appsettings*.json **/appsettings*.json 2>/dev/null
grep -rn "ASPNETCORE_ENVIRONMENT\|DOTNET_ENVIRONMENT" --include='*.json' --include='*.yml' --include='*.yaml' --include='*.cs' --include='*.props' --include='*.runsettings' . | head -20
grep -rnE "(Server=|Data Source=|Host=)[^\"]*" --include='appsettings*.json' . | grep -viE "localhost|127\.0\.0\.1|\(localdb\)|:memory:|test" | head
grep -rn "ConnectionStrings" --include='*Test*.cs' --include='appsettings*.json' . | head -20
grep -rn "Migrate()\|EnsureDeleted()\|EnsureCreated()" --include='*Test*.cs' . | head
grep -rn "AddUserSecrets\|AddAzureKeyVault\|AddEnvironmentVariables" --include='*Test*.cs' --include='*Startup*.cs' . | head
```

| Red flag | Why |
|---|---|
| `appsettings.Production.json` loaded in a test — or `ASPNETCORE_ENVIRONMENT=Production` set in CI, a `.runsettings`, or a `launchSettings.json` profile the test harness reuses | the suite runs against production config: real DB, real cache, real mail |
| `WebApplicationFactory` without `UseEnvironment("Testing")` | it inherits `ASPNETCORE_ENVIRONMENT` from the host — which on a dev machine or a CI runner may be anything |
| A real `Server=`/`Host=` in `appsettings.json` and no `appsettings.Testing.json` override | the default config **is** the production config |
| `ctx.Database.EnsureDeleted()` or `Migrate()` in a fixture with no DB-name guard | **destroys whatever database the connection string points at** |
| `Respawn` checkpoint reset without a DB-name guard | same: it truncates every table it is pointed at |
| `AddUserSecrets<T>()` or `AddAzureKeyVault` in a test host | pulls the developer's or the pipeline's real credentials |
| Azure/AWS SDK clients with no Azurite/LocalStack endpoint override | real cloud calls and real charges |
| `SendGrid`/`Stripe`/`Twilio` keys matching `SG.`/`sk_live_` | live-money and real-email side effects |
| Hangfire/Quartz/MassTransit not switched to an in-memory transport in test config | tests enqueue real background jobs onto the real broker |
| A `.runsettings` `<EnvironmentVariables>` block setting prod values | invisible in the test code itself |

Assert the guard, don't assume it:

```csharp
// A module initializer or an xUnit assembly fixture — fails fast, before any test
var env = Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT") ?? "";
if (env.Contains("Prod", StringComparison.OrdinalIgnoreCase))
    throw new InvalidOperationException($"Refusing: ASPNETCORE_ENVIRONMENT={env}");

var cs = Environment.GetEnvironmentVariable("ConnectionStrings__Default") ?? "";
string[] safe = ["localhost", "127.0.0.1", "(localdb)", ":memory:", "test"];
if (cs.Length > 0 && !safe.Any(s => cs.Contains(s, StringComparison.OrdinalIgnoreCase)))
    throw new InvalidOperationException($"Refusing to run tests against {cs}");
```

---

## 8. House conventions to match

```bash
find . -name '*.csproj' | grep -v obj/ | grep -iE "test|spec" 
grep -rn "IsTestProject\|IsPackable" --include='*.csproj' . | head
ls tests test src/*.Tests 2>/dev/null
```

| Convention | Rule |
|---|---|
| Test project location | A top-level `tests/` directory mirroring `src/` is the common modern layout; `src/Acme.Cart.Tests/` beside the code also occurs. **Match the repo** |
| Test project naming | `<Project>.Tests` (unit), `<Project>.IntegrationTests`, `<Project>.FunctionalTests`. One test project per source project |
| File naming | `CartService.cs` → `CartServiceTests.cs`, mirroring the source folder structure inside the test project |
| Class naming | `CartServiceTests`; nested `public class AddItem { ... }` classes for grouping per method is a common xUnit style |
| Method naming | `MethodName_Scenario_ExpectedResult` (dominant), or `Should_add_item_when_cart_is_empty`. **Read 5 neighbouring tests and match** |
| Arrange/Act/Assert | explicit `// Arrange` / `// Act` / `// Assert` comments are conventional in many .NET repos — match if present |
| Setup / teardown | xUnit: constructor + `IDisposable`/`IAsyncLifetime` (**no** `[SetUp]`). NUnit: `[SetUp]`/`[TearDown]`/`[OneTimeSetUp]`. MSTest: `[TestInitialize]`/`[ClassInitialize]` |
| Shared state | xUnit `IClassFixture<T>` (per class) / `ICollectionFixture<T>` + `[Collection]` (per collection) |
| Parameterised | xUnit `[Theory]` + `[InlineData]` / `[MemberData]` / `[ClassData]`; NUnit `[TestCase]` / `[TestCaseSource]`; MSTest `[DataRow]` |
| Mocks | `Moq` (`new Mock<IRepo>()`, `.Setup(...).Returns(...)`, `.Verify(...)`) or `NSubstitute` (`Substitute.For<IRepo>()`, `.Returns(...)`, `.Received()`). **Never mix both in one repo** |
| Fixtures / factories | `tests/Acme.Cart.Tests/Fixtures/`, `TestData/`, `Builders/`. `AutoFixture` (`fixture.Create<Order>()`) and `Bogus` (`new Faker<Order>()`) are the usual generators — **seed Bogus** (`Randomizer.Seed = new Random(1)`) |
| Static test data files | `TestData/*.json` with `<None Include="TestData\**" CopyToOutputDirectory="PreserveNewest" />` — a missing copy directive is a classic "works in IDE, fails in CI" |
| Assertions | `Assert.Equal(expected, actual)` (xUnit, **expected first**) vs `Assert.AreEqual(expected, actual)` (NUnit/MSTest) vs `actual.Should().Be(expected)` (FluentAssertions/Shouldly). Match the directory |
| `InternalsVisibleTo` | `<InternalsVisibleTo Include="Acme.Cart.Tests" />` in the source csproj if tests touch `internal` members |

---

## 9. Static analysis + type checking — rank first on every roadmap

The C# compiler is the type checker, and Roslyn analyzers ship with the SDK.
These are the cheapest wins in the stack.

```bash
# Tier 0 — free, no install
dotnet build -warnaserror
dotnet format --verify-no-changes            # fails if anything is unformatted
dotnet format --verify-no-changes --include $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.cs' | tr '\n' ' ')
dotnet build /p:EnforceCodeStyleInBuild=true # turn IDExxxx style rules into build diagnostics
dotnet list package --vulnerable --include-transitive
dotnet list package --deprecated
dotnet list package --outdated

# Tier 1 — turn the built-in analyzers up
#   in Directory.Build.props:
#   <AnalysisMode>All</AnalysisMode>
#   <AnalysisLevel>latest-recommended</AnalysisLevel>
#   <EnableNETAnalyzers>true</EnableNETAnalyzers>
#   <Nullable>enable</Nullable>
#   <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
dotnet build -v n 2>&1 | grep -E "warning (CA|IDE|CS)[0-9]+" | sort | uniq -c | sort -rn | head -30

# Tier 2 — third-party analyzers, as PackageReference with PrivateAssets=all
#   StyleCop.Analyzers, SonarAnalyzer.CSharp, Roslynator.Analyzers,
#   Meziantou.Analyzer, xunit.analyzers (ships with xunit),
#   Microsoft.VisualStudio.Threading.Analyzers (async/await correctness)
```

**Nullable reference types is the single highest-value item on a .NET testing
roadmap.** It is a static null-safety check over the whole codebase, it is free,
and the warnings it produces correlate directly with the `NullReferenceException`
class of production bugs.

```bash
# How much of the repo is actually null-checked?
grep -rn "<Nullable>" --include='*.csproj' --include='*.props' . 
grep -rn "#nullable disable" --include='*.cs' . | wc -l       # the real debt counter
grep -rn "!\s*;\|!\.\|null!" --include='*.cs' . | wc -l       # null-forgiving operator usage
```

Adoption ratchet for a legacy repo: `<Nullable>warnings</Nullable>` repo-wide →
`<Nullable>enable</Nullable>` with `#nullable disable` at the top of unconverted
files (drive that count down) →
`<WarningsAsErrors>CS8600;CS8602;CS8603;CS8618;CS8625</WarningsAsErrors>` →
`<TreatWarningsAsErrors>true</TreatWarningsAsErrors>` with an explicit `<NoWarn>`.

Analyzer rule families worth enabling, in roadmap order:

| Rule family | Catches | Why early |
|---|---|---|
| `CS86xx` (nullable) | dereference of a possibly-null value, uninitialised non-nullable field | the real-bug family; free |
| `CA2007`, `VSTHRD` (threading analyzers) | missing `ConfigureAwait`, `async void`, sync-over-async `.Result` | **directly targets the §6 flake causes** |
| `xunit.analyzers` (`xUnit1xxx`/`xUnit2xxx`) | `async void` tests, `Assert.Equal` argument order, unused `[Theory]` data, `[Fact]` with parameters | ships free with xUnit; catches silently-not-running tests |
| `CA1062`, `CA2000`, `CA2016` | unvalidated public arguments, undisposed `IDisposable`, missing `CancellationToken` flow | resource-leak flake |
| `CA1416` | platform-compatibility (Windows-only API on a Linux runner) | a classic CI-only failure |
| `IDE0005`, `IDE0055` | unused usings, formatting — fixed by `dotnet format` | zero-thought wins |
| `Meziantou.Analyzer` MA0004/MA0040 | missing `ConfigureAwait`, missing `CancellationToken` | tier 2 |

Config to read before trusting any clean run:

```bash
cat .editorconfig 2>/dev/null | grep -nE "^\[|dotnet_diagnostic|severity"
grep -rn "NoWarn\|WarningsNotAsErrors\|ExcludeFromCodeCoverage" --include='*.csproj' --include='*.props' . | head -20
grep -rn "#pragma warning disable" --include='*.cs' . | wc -l
grep -rn "SuppressMessage" --include='*.cs' . | wc -l
```

A large or growing `#pragma warning disable` / `[SuppressMessage]` /
`[ExcludeFromCodeCoverage]` count means findings are being silenced rather than
fixed. **Report the count and its trend, not a clean build.**
`[ExcludeFromCodeCoverage]` on a class full of logic is a coverage number that
means nothing — call it out explicitly.

Diff-scoped lint, the RESEARCH §6 pattern (findings scoped to the diff get ~70%
fix rates; the same findings as a batch backlog get ~0%):

```bash
dotnet format --verify-no-changes --include $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.cs' | tr '\n' ' ')
dotnet build -v n 2>&1 | grep -E "warning" \
  | grep -Ff <(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.cs')
```
