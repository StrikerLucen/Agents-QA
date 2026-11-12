# Stack playbook — Swift

Reference for a testsquad agent working in a Swift repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

Swift's defining constraint is that **the toolchain is Apple's**: almost
everything beyond a pure SPM package requires macOS and Xcode, coverage comes
out in a proprietary container that needs conversion, and the simulator is a
flake source with no analogue in the other stacks. Rank §9 first on a roadmap —
Swift 6 strict concurrency is the largest single win available.

---

## 1. Detection signals

```bash
ls Package.swift Package.resolved *.xcodeproj *.xcworkspace Podfile Cartfile 2>/dev/null
ls Project.swift Workspace.swift project.yml Tuist/ 2>/dev/null     # generated projects
ls *.xctestplan **/*.xctestplan 2>/dev/null
swift --version && xcodebuild -version 2>/dev/null
cat .swift-version 2>/dev/null
```

| Marker | Conclusion |
|---|---|
| `Package.swift` and **no** `.xcodeproj` | **pure SPM package** — `swift build` / `swift test`. Works on Linux too (check `Package.swift` platforms) |
| `.xcworkspace` | **use the workspace, not the project** — `xcodebuild -workspace App.xcworkspace -scheme App` |
| `.xcodeproj` only | Xcode project — `xcodebuild -project App.xcodeproj -scheme App` |
| `Podfile` | CocoaPods — `pod install` first; the `.xcworkspace` is the entry point |
| `Project.swift` / `Workspace.swift` + `Tuist/` | **Tuist** — the `.xcodeproj` is generated. `tuist generate` before anything; **never edit the project file** |
| `project.yml` | **XcodeGen** — same: `xcodegen generate` first |
| `*.xctestplan` | **test plans** — the authoritative definition of which targets, configurations, environment variables and options a test run uses. **Read it; it overrides scheme settings** |
| `Package.swift` **and** `.xcodeproj` | both; the app consumes local SPM packages. Test the packages with `swift test` (fast) and the app with `xcodebuild` (slow) |
| `.xcconfig` files | build settings outside the project file — **read them for base URLs and flags** (§7) |

```bash
swift package describe --type json | jq '.targets[] | {name, type, path}'
xcodebuild -list -workspace App.xcworkspace 2>/dev/null   # schemes, configurations, targets
xcodebuild -showdestinations -scheme App -workspace App.xcworkspace 2>/dev/null
```

`xcodebuild -list` gives the **schemes** — and a scheme is what you actually
test. A target with no scheme cannot be run by `xcodebuild test`.

### Test framework — XCTest vs Swift Testing

```bash
grep -rln "import XCTest" --include='*.swift' . | head
grep -rln "import Testing" --include='*.swift' . | head
grep -rn "@Test\b\|#expect(\|#require(" --include='*.swift' . | head
grep -rn "func test[A-Z_]" --include='*.swift' . | head
grep -n "swift-tools-version" Package.swift
```

| Marker | Framework | Notes |
|---|---|---|
| `import XCTest` + `class XTests: XCTestCase` + `func testFoo()` | **XCTest** | the long-standing default. Method names **must** start with `test`; one instance per test method; `setUp`/`tearDown` |
| `import Testing` + `@Test func foo()` + `#expect(...)` | **Swift Testing** | the newer framework. Free functions or `struct` suites, `@Suite`, parameterised via `@Test(arguments:)`, `#expect`/`#require` macros, **parallel by default**, `async`/`throws` native |
| both in one repo | **normal and supported** — they run in the same process under `swift test` and under Xcode 16+ | write new tests in the dominant style of the file's directory |
| `XCTAssert*` with `async` and `await fulfillment(of:)` | modern XCTest | |
| `waitForExpectations(timeout:)` | older XCTest async style — a flake source (§6) | |
| `XCUIApplication()` | **UI tests** — a separate target, needs a simulator/device, the slowest and flakiest tier | |
| `assertSnapshot(` (`swift-snapshot-testing`) | snapshot tests — host-dependent, see §6 | |

Swift Testing is only available from Swift 6 / Xcode 16. If
`swift-tools-version` is below that, `import Testing` will not resolve — state
that rather than recommending a migration the toolchain cannot do.

Supporting packages:

```bash
grep -nE "swift-snapshot-testing|Quick|Nimble|Cuckoo|Mockingbird|OHHTTPStubs|Mocker|swift-custom-dump|ViewInspector|swift-dependencies|Difference" Package.swift Podfile *.xcodeproj/project.pbxproj 2>/dev/null
```

| Package | Means |
|---|---|
| `Quick` + `Nimble` | BDD (`describe`/`it`/`expect(...).to(equal(...))`) over XCTest |
| `swift-snapshot-testing` | `assertSnapshot(of:as:)`, snapshots in `__Snapshots__/` |
| `OHHTTPStubs` / `Mocker` / a custom `URLProtocol` | HTTP stubbing |
| `ViewInspector` | assert on SwiftUI view trees without a simulator |
| `swift-dependencies` / `Factory` / `Resolver` | DI container — how collaborators are faked |
| `Cuckoo` / `Mockingbird` | codegen mocking. **Both have had maintenance gaps** — *verify before recommending either*; the Swift norm is hand-written protocol conformances (§5) |

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. In Swift it is acute: an `xcodebuild test` of an app
target can take **minutes** (build + simulator boot + install + run), and a
cold `swift test` must compile the whole module graph.

**Swift has no import-graph test selector** — no `--findRelatedTests`, no
testmon. Scope by **test target and test name**, derived from the diff by
convention. Say so plainly.

```bash
# 1. Changed test files -> their class/suite names.
#    SPM --filter is a regex over "TestTarget.TestClass/testMethod".
SUITES=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'Tests/**/*.swift' \
         | xargs -n1 basename 2>/dev/null | sed 's/\.swift$//' | sort -u)
# 2. Plus changed sources mapped by convention (Foo.swift -> FooTests)
SUITES="$SUITES $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'Sources/**/*.swift' \
         | xargs -n1 basename 2>/dev/null | sed 's/\.swift$/Tests/' | sort -u)"
FILTER=$(printf '%s|' $SUITES | sed 's/|$//')
[ -n "$FILTER" ] && swift test --filter "$FILTER"

# 3. One test target — the cheapest meaningful scope in a multi-target package
swift test --filter CartTests

# 4. Xcode projects: -only-testing is the scoping flag (-skip-testing is the inverse)
xcodebuild test -workspace App.xcworkspace -scheme App \
  -destination 'platform=iOS Simulator,name=iPhone 16,OS=latest' \
  -only-testing:AppTests/CartTests -skip-testing:AppUITests

# 5. Everything (CI, not hooks)
swift test
```

Flags that make a hook survivable:

```bash
swift test --filter CartTests --parallel
swift test --skip-build                       # only if a build already ran this session
swift build --build-tests && swift test --skip-build --filter CartTests

# Xcode: split build and run so the hook does not rebuild every time
xcodebuild build-for-testing -workspace App.xcworkspace -scheme App \
  -destination '...' -derivedDataPath .build/dd
xcodebuild test-without-building -workspace App.xcworkspace -scheme App \
  -destination '...' -derivedDataPath .build/dd -only-testing:AppTests/CartTests
```

`build-for-testing` + `test-without-building` is **the** pattern for Swift in
CI and in hooks. It is the difference between a 4-minute and a 20-second
iteration, and it is what makes `-only-testing` worth using at all.

Guard the empty case — `swift test` with no `--filter` runs everything:

```bash
[ -z "$FILTER" ] && exit 0                    # guard FIRST
swift test --filter "$FILTER"
```

**A `--filter` that matches nothing is an error in SPM** ("No matching test
cases were run") and exits non-zero. In a hook, treat that specific message as
success only after you have already guarded the empty-filter case:

```bash
out=$(swift test --filter "$FILTER" 2>&1); rc=$?
echo "$out" | grep -q "No matching test cases" && rc=0
echo "$out"; exit $rc
```

### Full suite / single file / single test

| Goal | SPM (`swift test`) | Xcode (`xcodebuild test`) |
|---|---|---|
| Whole suite | `swift test` | `xcodebuild test -workspace W -scheme S -destination D` |
| One test target | `swift test --filter CartTests` | `-only-testing:AppTests` |
| One class / suite | `swift test --filter CartTests.CartServiceTests` | `-only-testing:AppTests/CartServiceTests` |
| One test method | `swift test --filter 'CartTests.CartServiceTests/testAddsItem'` | `-only-testing:AppTests/CartServiceTests/testAddsItem` |
| By regex | `swift test --filter 'Cart.*Tests'` | — (`-only-testing` is exact) |
| Skip something | `swift test --skip CartUITests` | `-skip-testing:AppUITests` |
| List tests | `swift test --list-tests` | `xcodebuild test -enumerate-tests` (*verify flag availability for your Xcode*) |
| Parallel | `swift test --parallel` (`--num-workers N`) | `-parallel-testing-enabled YES -maximum-concurrent-test-simulator-destinations N` |
| Serial | `swift test` (default is serial for XCTest) · Swift Testing is **parallel by default** — see §6 | `-parallel-testing-enabled NO` |
| Release configuration | `swift test -c release` | `-configuration Release` |
| Repeat / until failure | — | `-test-iterations 50 -run-tests-until-failure` |
| Randomise order | — (**no first-party flag**; see §6) | `-test-timeouts-enabled`… order randomisation is a **test-plan** option (`testExecutionOrdering = "random"`), not a CLI flag. *Verify for your Xcode version* |
| Result bundle | — | `-resultBundlePath .testsquad/Result.xcresult` |
| Machine-readable | `swift test --xunit-output .testsquad/tests.xml` | parse the `.xcresult` (see §3) or pipe through `xcbeautify`/`xcpretty` |
| Test plan | — | `-testPlan Unit` |

`-destination` is mandatory for anything iOS (`xcrun simctl list devices
available` to enumerate; `'platform=macOS'` and `'generic/platform=iOS'` also
valid). **Pin a specific OS version in CI** rather than `OS=latest` — `latest`
changes when the runner image updates and silently breaks a suite with no code
change.

Raw `xcodebuild` output is unreadable, so CI pipes it: `xcodebuild test ... 2>&1
| xcbeautify`. **Forgetting `set -o pipefail` makes a failing suite exit 0** —
check for it in any CI script you read.

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **lcov**. Swift does
not emit it directly — both paths need a conversion step.

### SPM path (the clean one)

```bash
swift test --enable-code-coverage
BIN=$(swift build --show-bin-path)
PROFDATA="$BIN/codecov/default.profdata"
XCTEST=$(ls -d "$BIN"/*.xctest 2>/dev/null | head -1)
# macOS: point llvm-cov at the executable INSIDE the bundle.
# Linux: the binary is the test executable itself, not a .xctest bundle.
EXEC="$XCTEST/Contents/MacOS/$(basename "$XCTEST" .xctest)"

xcrun llvm-cov export "$EXEC" -instr-profile "$PROFDATA" -format=lcov \
  -ignore-filename-regex='(Tests/|\.build/|Package\.swift)' > lcov.info

xcrun llvm-cov report "$EXEC" -instr-profile "$PROFDATA"      # human summary
```

### Xcode path

```bash
xcodebuild test \
  -workspace App.xcworkspace -scheme App \
  -destination 'platform=iOS Simulator,name=iPhone 16,OS=18.0' \
  -enableCodeCoverage YES \
  -resultBundlePath .testsquad/Result.xcresult

# Apple's own reader — JSON, but NOT a format the normalizer parses
xcrun xccov view --report --json .testsquad/Result.xcresult > coverage-xccov.json
xcrun xccov view --report .testsquad/Result.xcresult          # human summary
```

`.xcresult` and `xccov` JSON are **not** parseable by
`normalize_coverage.py`. You need a converter — `xcresultparser` emits both
formats:

```bash
brew install a7ex/homebrew-formulae/xcresultparser    # *verify the current tap/name*
xcresultparser -o cobertura .testsquad/Result.xcresult > coverage.xml
xcresultparser -o lcov .testsquad/Result.xcresult > lcov.info
```

**Verify before relying on this**: it is third-party, and the `.xcresult` format
changed across Xcode versions (Xcode 16 moved to a new result-bundle API;
`xcrun xcresulttool get --legacy` is required for some older invocations).
Confirm it runs against your Xcode before putting it in CI. If no converter is
available, the honest answer is that coverage could not be normalised — not a
fabricated number.

The alternative that avoids the problem entirely: **extract the logic into SPM
packages and measure coverage there with `swift test --enable-code-coverage`.**
That is a real architectural recommendation, not a workaround, and it is worth
putting on a roadmap.

| Output | Format | Parsed as |
|---|---|---|
| `lcov.info` (from `llvm-cov export -format=lcov`) | lcov | `lcov` ✅ |
| `coverage.xml` (from `xcresultparser -o cobertura`) | Cobertura XML | `cobertura` ✅ |
| `.xcresult` bundle | Apple result bundle | **not parsed** |
| `xccov --json` output | Apple JSON | **not parsed** |
| `*.profdata` / `*.profraw` | LLVM profile | intermediate; must be exported |

### Branch coverage

**Treat Swift as having no usable branch coverage.**

`llvm-cov` can compute branch coverage (`-show-branch-summary`,
`--show-branches=count`) when the binary was built with branch-coverage
instrumentation, but **Swift's `--enable-code-coverage` does not enable it**,
and `llvm-cov export -format=lcov` from a standard Swift build emits no `BRDA`
records. Apple's `xccov` reports line coverage only, with no branch concept at
all.

```bash
grep -c '^BRDA:' lcov.info || echo "no branch records — expected for Swift"
```

That grep returning 0 is the normal case. `normalize_coverage.py` will report
`branches_found: 0` and `branch_pct: null`, **correctly**. Report it as
**"branch coverage is not available in Swift"**, never as 0% — 0% claims every
branch is untested, which is false and will be read as a catastrophe.

The partial substitute, like Rust: Swift's `switch` exhaustiveness over an enum
is a **compile error** when a case is unhandled, and `if let`/`guard let` make
the nil branch explicit. Point at that in reports; it means the missing branch
metric costs Swift less than it would cost Python or PHP.

Normalize:

```bash
python3 scripts/normalize_coverage.py lcov.info \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

---

## 4. Mutation testing — muter

| Tool | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| **`muter`** (muter-mutation-testing/muter) | **Healthy** | the only real option |
| anything else for Swift | hobby or abandoned | do not adopt |

```bash
brew install muter-mutation-testing/formulae/muter    # *verify the current tap name*
muter --version
muter init                                            # generates muter.conf.yml
```

```yaml
# muter.conf.yml
executable: /usr/bin/xcodebuild        # or /usr/bin/swift, with arguments: ["test"]
arguments: ["test", "-workspace", "App.xcworkspace", "-scheme", "App",
            "-destination", "platform=iOS Simulator,name=iPhone 16,OS=18.0"]
excludeFileList: ["**/Generated/**", "**/*.generated.swift", "AppDelegate.swift", "SceneDelegate.swift"]
mutateFilesInDirectory: "Sources"
```

### Diff-scoped invocation

```bash
# Derive the file list from the diff — this is how you scope muter to a PR
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'Sources/**/*.swift' | tr '\n' ',')
[ -n "$CHANGED" ] && muter run --files-to-mutate "$CHANGED" --output-json .testsquad/muter.json
muter run --skip-coverage          # faster start; worse mutant selection
```

> **Honest statement of muter's diff scoping:** `--files-to-mutate` is
> **file-level**, not line-level. Unlike Infection's `--git-diff-lines` or
> cargo-mutants' `--in-diff`, muter has no git-diff flag and no concept of
> "lines in this PR". You must build the file list yourself, and a one-line
> change to a 500-line file mutates the whole file. **Verify the exact flag
> spelling and whether a newer muter release has added diff support before
> relying on this** — the CLI has changed across versions.

Because of that, and because every mutant costs a **full `xcodebuild test` run
on a simulator**, a muter run on an iOS app is expensive in a way no other
stack in this set is. Mitigations, in order:

1. Run muter against **SPM package targets** (`executable: /usr/bin/swift`,
   `arguments: ["test"]`) rather than the app — no simulator, far faster.
2. Scope to a **handful of changed files**, never the whole project.
3. Run it **nightly or on demand**, not on every PR.

Reading results: muter prints surviving mutants with file, line and the
mutation operator applied (`RelationalOperatorReplacement`,
`RemoveSideEffects`, `ChangeLogicalConnector`, `SwapTernary`). **A surviving
mutant is the finding.**

```bash
jq -r '.fileReports[] | .path as $p | .appliedOperators[] | select(.testSuiteOutcome=="passed") | "\($p):\(.mutationPoint.position.line) \(.mutationPoint.mutationOperatorId)"' \
  .testsquad/muter.json
```

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file**. In Swift these rules are not an optimisation — they are the only thing
that makes a run finish.

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste. muter has
   no one-per-line flag; **post-filter the JSON to one surviving mutant per
   line and ≤7 per file.**
2. **Arid-node suppression** — never mutate: `os_log`/`Logger`/`print`/
   `NSLog` calls, analytics and crash-reporting emitters, `description`/
   `debugDescription`/`CustomStringConvertible`, `==`/`hash` boilerplate,
   trivial computed getters, pure delegation, `fatalError`/`assertionFailure`
   *messages*, `AppDelegate`/`SceneDelegate`, SwiftUI `body` layout-only code,
   generated files (SwiftGen, Sourcery, protobuf). Express via
   `excludeFileList`. **This step matters more than the choice of tool** — it is
   the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff; do not set a
   project-wide muter threshold in CI.

Blind spots: (a) a change in file A destroying coverage of file B is invisible;
(b) **a test-only change mutates nothing**, so deleting assertions passes the
gate; (c) config/fixture changes flip mutant status silently. Mitigation: an
occasional full run off the critical path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **SPM package tests** (`swift test`) | **The best value in the stack.** No simulator, no Xcode, fast, parallelisable, coverage works cleanly | Requires the logic to live in a package. Pushing code out of the app target into SPM modules is the highest-leverage architectural change for testability in Swift |
| **XCTest unit tests in an app target** | Logic that cannot be extracted yet | Needs a simulator boot and app launch per test *bundle*, not per test — but that is still seconds of fixed cost |
| **`URLProtocol` subclass** | Intercepting all `URLSession` traffic | **Built in, no dependency.** Register a stub `URLProtocol` on a `URLSessionConfiguration.ephemeral`. The idiomatic Swift way to fake HTTP |
| **`XCUITest`** (`XCUIApplication`) | Real app, real UI, real taps | **The slowest and flakiest tier by a wide margin.** Separate process, no access to app internals, accessibility-identifier-driven. Keep to a handful of journeys |
| **`ViewInspector`** | Asserting on a SwiftUI view hierarchy without a simulator | Much cheaper than XCUITest for view-logic assertions. Relies on runtime reflection of SwiftUI internals, so it can break on OS updates |
| **`swift-snapshot-testing`** | Image or text snapshots of views | Image snapshots are **host-dependent** (OS version, device, scale, fonts) — pin one simulator model and OS or they flake forever. **Text/`.dump` snapshots are far more stable** and usually the better choice |
| **In-memory Core Data / SQLite** | Persistence tests | `NSPersistentContainer` with a `/dev/null` store URL; fast and faithful |
| **`swift-dependencies` / protocol injection** | Swapping collaborators | Swift has **no runtime mocking** — no method swizzling story that is safe in Swift, no `unittest.mock`. Every fake is a hand-written type conforming to a protocol. This is a constraint, and it means **the code must be written against protocols or it cannot be tested at all** |
| **`swift-testing` parameterised tests** | Table-driven cases | `@Test(arguments: [...])` — each argument is reported as a separate test, which is better than a loop inside one test |
| **`SwiftCheck`** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16), and Swift's PBT ecosystem is markedly weaker than Python's or JS's. *Verify maintenance before recommending it.* |

```swift
// URLProtocol stub — the idiomatic, dependency-free HTTP fake.
// Subclass URLProtocol, return true from canInit, and in startLoading call a
// static handler closure, then client?.urlProtocol(didReceive:/didLoad:) and
// urlProtocolDidFinishLoading. Then:
let config = URLSessionConfiguration.ephemeral
config.protocolClasses = [StubURLProtocol.self]
let session = URLSession(configuration: config)   // inject this; never URLSession.shared
```

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests.
Swift makes this easier to get right than most stacks: a hand-written fake
**must** conform to the real protocol, and the compiler rejects it when the
protocol changes. Derive response shapes from the real `Codable` struct and its
`JSONDecoder` — decode a recorded fixture rather than hand-building a model
instance, so a schema change breaks the test rather than being invented around.

---

## 6. Flake taxonomy — Swift

> **There is no published per-cause flakiness breakdown for Swift** comparable
> to the Java (async 45% / concurrency 20% / order 12%), Python (order 59% /
> infrastructure 28%) or JavaScript (concurrency 21% / async 20% / OS 18% /
> network 13%) figures in RESEARCH §19. Do not invent one and do not reuse
> another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way.

### The two to check first

**`XCTestExpectation` and asynchronous waiting.** This is the Swift analogue of
Java's "async wait" category and, by consensus, the dominant XCTest flake.

| Mistake | Symptom | Fix |
|---|---|---|
| `waitForExpectations(timeout: 0.5)` with a real network/disk call | passes on a fast machine, times out in CI | raise the timeout **and** remove the real I/O; a timeout is a symptom, not a fix |
| `expectation.fulfill()` called twice | "API violation - multiple calls to fulfill()" — a hard failure, intermittently | set `expectedFulfillmentCount`, or guard the call site |
| `fulfill()` never called on an error path | always times out when the dependency misbehaves | fulfil in a `defer` or in every branch |
| `DispatchQueue.main.asyncAfter` as synchronisation | timing-dependent by construction | inject a scheduler/clock |
| mixing `async/await` tests with expectations | the test returns before the Task runs | use `async` test methods and `await`, or `await fulfillment(of: [exp], timeout: 1)` |
| `XCTAssert` inside an escaping closure that never runs | **the test passes having asserted nothing** — a tautological test | assert after the wait, not inside the callback |

```bash
grep -rn "waitForExpectations\|XCTestExpectation\|expectation(description" --include='*.swift' . | wc -l
grep -rn "asyncAfter\|Thread.sleep\|sleep(" --include='*Tests*.swift' . | head -20
grep -rn "timeout: 0\.\|timeout: 1)" --include='*.swift' . | head -20
```

**Simulator flake.** It has no analogue in the other stacks in this set and it
is a genuine, recurring cost:

- A cold simulator boot can exceed the test timeout.
- A simulator left in a bad state from a previous run fails every subsequent test.
- OS keyboard/autocorrect/permission dialogs intercept XCUITest taps.
- Animations make XCUITest element queries race.
- `OS=latest` silently changes when the CI image updates.

```bash
xcrun simctl shutdown all && xcrun simctl erase all     # nuclear reset — do this in CI
xcrun simctl boot "iPhone 16" && xcrun simctl bootstatus "iPhone 16" -b   # wait for ready
defaults write com.apple.iphonesimulator ConnectHardwareKeyboard 0        # keyboard flake
```

```swift
// XCUITest: pin language/locale and disable animations via launch arguments
app.launchArguments += ["-UITestMode", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
app.launchEnvironment["UITEST_DISABLE_ANIMATIONS"] = "1"
```

### Other consensus causes, with their instruments

| Cause | Smell | Instrument |
|---|---|---|
| **Swift Testing is parallel by default** | a `@Test` suite sharing a singleton, a file, `UserDefaults`, or a fixed port | `.serialized` trait on the `@Suite`; or make the state per-test. **XCTest is serial per class by default — migrating a suite to Swift Testing can introduce flake that was never there** |
| `swift test --parallel` | the same, for XCTest | drop `--parallel` to diagnose |
| Shared `UserDefaults` / Keychain | state surviving between tests and between runs | `UserDefaults(suiteName:)` per test + `removePersistentDomain(forName:)` in teardown |
| `static let` singletons | shared across every test in the process | inject instead; reset in `setUp` |
| Date / timezone | `Date()` in an assertion; tests failing at month end or under a different locale | inject a `Date` provider or a `Clock`; `Calendar(identifier:)` with an explicit `timeZone` and `locale` |
| Locale / language | `String` comparison, number and date formatting | `-AppleLanguages (en)` launch args; sweep with different `-AppleLocale` |
| Float | `XCTAssertEqual(total, 0.3)` | `XCTAssertEqual(total, 0.3, accuracy: 0.0001)` |
| Dictionary / Set order | asserting the order of `Dictionary` keys | **Swift `Dictionary` and `Set` have unspecified order and it varies per run** (hash seeding) — sort, or compare as sets |
| Snapshot host dependence | image snapshots differing by a few pixels | pin one simulator + OS; prefer text snapshots |
| Memory / retain cycles | a test passing alone, failing in a suite, after an object leaks | `addTeardownBlock { [weak obj] in XCTAssertNil(obj) }` |
| Data races | intermittent wrong values under `--parallel` | **Thread Sanitizer** (`-enableThreadSanitizer YES` / `swift test --sanitize=thread`) — the single highest-signal dynamic check in Swift |

### Detection commands

```bash
# Repeat until failure — Xcode's native flake hunter, and it is good
xcodebuild test -workspace App.xcworkspace -scheme App -destination '...' \
  -only-testing:AppTests/CartTests/testAddsItem -test-iterations 50 -run-tests-until-failure

# Serialise to rule out parallelism
swift test                                   # (no --parallel)
xcodebuild test ... -parallel-testing-enabled NO

# Sanitizers — the highest-signal dynamic checks in Swift
swift test --sanitize=thread
xcodebuild test ... -enableThreadSanitizer YES -enableAddressSanitizer YES

# Clean-room simulator, then environment sweep
xcrun simctl shutdown all && xcrun simctl erase all && xcodebuild test ...
TZ=Pacific/Kiritimati swift test ; TZ=UTC swift test
xcodebuild test ... -testLanguage tr -testRegion TR

# Order randomisation: there is NO first-party CLI flag. It is a TEST PLAN
# option (`testExecutionOrdering = "random"` in the .xctestplan) for Xcode runs,
# and SPM has no equivalent at all. *Verify for your Xcode version.*
grep -n "testExecutionOrdering\|randomExecutionOrdering" *.xctestplan **/*.xctestplan 2>/dev/null
```

The absence of a `--shuffle` flag in `swift test` is a real gap — say so rather
than inventing one. For SPM packages the practical substitute is running
suspect tests in isolation and comparing, plus `--parallel` / serial
comparison.

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "Thread.sleep\|sleep(\|usleep(\|asyncAfter" --include='*.swift' . | head -30
grep -rn "Date()\|Date.now\|Calendar.current\|TimeZone.current" --include='*Tests*.swift' . | head -20
grep -rn "UUID()\|Int.random\|arc4random\|\.shuffled()" --include='*Tests*.swift' . | head -20
grep -rn "static var\|static let" --include='*.swift' Sources/ | head -20
grep -rn "UserDefaults.standard" --include='*.swift' . | head -20
grep -rnE "localhost:[0-9]{4,5}|https?://" --include='*Tests*.swift' . | head
grep -rn "XCTSkip\|XCTExpectFailure\|\.disabled(" --include='*.swift' . | wc -l
grep -rn "XCTAssertNotNil\|XCTAssertTrue(true)" --include='*.swift' . | head -20
grep -rn "try!" --include='*Tests*.swift' . | wc -l
grep -rn "retry\|rerun" .github/workflows/ fastlane/ 2>/dev/null | head
```

Specific smells: `Thread.sleep`/`asyncAfter` as synchronisation · `Date()` in an
assertion · unseeded `UUID()`/`Int.random` in an expected value · `static var`
mutable state · `UserDefaults.standard` in a test · real URLs ·
**`XCTSkip`/`XCTExpectFailure`/`.disabled()` counts** (tests that silently
stopped existing — report the trend) · `XCTAssertNotNil` as the only assertion
(**a test that cannot meaningfully fail**) · `try!` in a test (a crash that
kills the whole bundle, so every later test "fails" too) · a CI config that
retries failures without reporting it — **hiding the finding is itself one.**

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).
`-retry-tests-on-failure` turning red into green is **not** a pass.

---

## 7. Production-danger signals

Swift's danger surface is configuration-shaped: **which scheme, which build
configuration, which `.xcconfig`, which `Info.plist`, which entitlements** —
and those live in places a code grep does not reach.

```bash
xcodebuild -list -workspace App.xcworkspace 2>/dev/null     # schemes + configurations
grep -rn "buildConfiguration" *.xcodeproj/xcshareddata/xcschemes/*.xcscheme 2>/dev/null
cat *.xctestplan **/*.xctestplan 2>/dev/null | head -60
ls *.xcconfig Configs/*.xcconfig 2>/dev/null
grep -rnE "API_URL|BASE_URL|_HOST|ENDPOINT" *.xcconfig Configs/ Sources/ --include='*.swift' 2>/dev/null | head -20
ls **/GoogleService-Info.plist 2>/dev/null
grep -rn "aps-environment" **/*.entitlements 2>/dev/null
grep -rn "#if DEBUG\|#if RELEASE\|DEBUG ==" --include='*.swift' Sources/ | head -20
grep -rnE "https://[a-z0-9.-]+\.(com|io|net|app)" --include='*.swift' Sources/ | grep -viE "localhost|example|127\.0\.0\.1" | head -20
```

| Red flag | Why |
|---|---|
| **The test action's build configuration is `Release`** in the scheme or test plan | the suite exercises the production configuration — production base URL, production Firebase, analytics on, `assert`s compiled out |
| A scheme whose **Test action** inherits the Run action's arguments, including prod `-launchArguments` | invisible unless you open the `.xcscheme` |
| `.xctestplan` with `environmentVariableEntries` setting prod values | **test plans override scheme settings and are easy to miss** |
| A production base URL as the **default** in code (`let base = ProcessInfo...["API_URL"] ?? "https://api.acme.com"`) | any run that forgets the override is a production run |
| Production `GoogleService-Info.plist` in the default target membership | tests write to production Firestore / Analytics / Crashlytics |
| `aps-environment: production` in the entitlements used by a test build | real push notifications |
| `#if DEBUG` guarding the **only** safety check | a Release-configuration test run skips it |
| `assert`/`precondition` used as the only validation | **`assert` is compiled out in Release**; a Release test run silently loses them |
| An `.xcconfig` with real credentials not in `.gitignore` | secrets in the repo, loaded by every build |
| XCUITest launch arguments pointing at a prod server | an end-to-end test placing real orders |
| A Keychain access group shared with the production app on a device | test runs reading/writing real user credentials |
| `UserDefaults.standard` in tests on a **device** (not simulator) | pollutes the real app's state |
| Stripe/Twilio/analytics keys matching a live prefix in any plist or xcconfig | live-money side effects |
| CI running UI tests against a **staging that writes to prod** data store | the usual real-world variant |

Assert the guard, don't assume it:

```swift
// Put this in every test target — it fails fast on the scheme/test-plan mistake
func testEnvironmentIsNotProduction() throws {
    let base = AppConfig.baseURL.absoluteString
    let safe = ["localhost", "127.0.0.1", "test", "staging-sandbox"]
    XCTAssertTrue(safe.contains { base.contains($0) }, "Refusing to run against \(base)")
    #if !DEBUG
    XCTFail("Tests are running in a non-DEBUG configuration")
    #endif
}
```

Make this test part of every test target. It is the one check that catches the
scheme/test-plan/xcconfig class of mistake, which no amount of source grepping
reliably finds.

---

## 8. House conventions to match

```bash
ls Tests/ **/Tests/ 2>/dev/null
swift package dump-package | jq -r '.targets[] | select(.type=="test") | "\(.name) -> \(.path // "Tests/\(.name)")"'
find . -name '__Snapshots__' -o -name 'Fixtures' -o -name 'Resources' | grep -i test | head
```

| Convention | Rule |
|---|---|
| SPM test location | **`Tests/<Target>Tests/`** — SPM's default convention, declared in `Package.swift` as `.testTarget(name: "CartTests", dependencies: ["Cart"])`. The directory **must** match the target name or you must set `path:` |
| Xcode test location | a `<App>Tests/` group mirroring the app's group structure, plus a separate `<App>UITests/` target |
| File naming | `CartService.swift` → `CartServiceTests.swift` |
| Class naming | `final class CartServiceTests: XCTestCase` — **`final`** is conventional (and faster) |
| XCTest method naming | **must** start with `test`: `func testAddsItemToEmptyCart()`. A method named `addsItem()` is **silently never run** — grep for near-misses |
| Alternative naming | `func test_addItem_whenCartIsEmpty_addsTheItem()` (underscored, given/when/then) is common. **Read 5 neighbouring tests and match** |
| Swift Testing naming | `@Test("adds an item to an empty cart") func addsItem() async throws` — the display name is the string, so the function name can be short |
| Suites | `@Suite("CartService") struct CartServiceTests { ... }`, nested `@Suite` for grouping. `.serialized` trait where state is shared |
| Setup / teardown | XCTest: `override func setUpWithError() throws` / `tearDownWithError()` — **call `super`**. `addTeardownBlock { }` for per-test cleanup. Swift Testing: `init()` / `deinit` on the suite `struct` |
| Assertions | XCTest: `XCTAssertEqual(actual, expected)` — **actual first**, the opposite of xUnit.net. `XCTUnwrap` instead of `!`. Swift Testing: `#expect(actual == expected)` and `try #require(optional)` |
| Error assertions | `XCTAssertThrowsError(try f()) { XCTAssertEqual($0 as? CartError, .full) }`; Swift Testing `#expect(throws: CartError.full) { try f() }` |
| Async | `func testX() async throws` + `await`. Avoid `waitForExpectations` in new code |
| Fixtures / resources | `Tests/CartTests/Resources/` declared as `resources: [.process("Resources")]` in `Package.swift`, read via **`Bundle.module`**. **A fixture not declared in `Package.swift` is not in the bundle** — the single most common "file not found" in SPM tests |
| Snapshots | `__Snapshots__/<TestClass>/<testName>.png` (swift-snapshot-testing's default), beside the test file. Commit them |
| Mocks / fakes | `Tests/CartTests/Mocks/MockCartRepo.swift` — a hand-written `final class MockCartRepo: CartRepoProtocol` recording calls and returning canned values. **This is the Swift norm; there is no runtime mocking** |
| Builders | `static func fixture(...)` extensions on the model in a test-only file: `extension Order { static func fixture(id: String = "1") -> Order { ... } }`. Very common and worth matching |
| Accessibility identifiers | XCUITest finds elements by `accessibilityIdentifier` — set them in the app, never match on user-visible text (it breaks on copy changes and localisation) |
| Test plans | `.xctestplan` files define which targets, configurations and options a CI job uses — add new targets there or they never run |

---

## 9. Static analysis + type checking — rank first on every roadmap

Swift's compiler is the type checker and it is strong. The uncaptured value is
in **strict concurrency** (Swift 6's data-race safety), the linters, and
turning warnings into errors.

```bash
# Tier 0 — free, no install
swift build -Xswiftc -warnings-as-errors
swift build 2>&1 | grep -c "warning:"            # the warning debt counter
xcodebuild build -workspace App.xcworkspace -scheme App \
  SWIFT_TREAT_WARNINGS_AS_ERRORS=YES GCC_TREAT_WARNINGS_AS_ERRORS=YES

# Formatting — swift-format ships with the toolchain from Swift 6
swift format lint --recursive --strict Sources Tests   # --in-place to fix

# DIFF-SCOPED — the RESEARCH §6 pattern (~70% fix rate scoped, ~0% as a backlog)
git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.swift' | xargs -r swift format lint --strict

# Tier 1 — SwiftLint, near-universal in Swift repos
brew install swiftlint
swiftlint lint --strict --reporter json > .testsquad/swiftlint.json
swiftlint lint --quiet $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.swift' | tr '\n' ' ')
swiftlint --fix                                   # autocorrect the mechanical rules
swiftlint analyze --compiler-log-path .build/build.log   # whole-module rules (unused imports/declarations)

# Tier 2
periphery scan --workspace App.xcworkspace --schemes App    # dead code — large, real findings
swift package diagnose-api-breaking-changes origin/main     # public API breakage (libraries)
```

### Strict concurrency — the largest single win

```bash
# What mode is the repo actually in?
grep -rn "swiftLanguageMode\|swiftLanguageVersion\|StrictConcurrency\|SWIFT_STRICT_CONCURRENCY" \
  Package.swift *.xcodeproj/project.pbxproj *.xcconfig 2>/dev/null
grep -rn "@unchecked Sendable\|nonisolated(unsafe)\|@preconcurrency" --include='*.swift' . | wc -l
```

```swift
// Package.swift — the ratchet: warnings first, then Swift 6 language mode (errors)
.target(name: "Cart", swiftSettings: [
    .enableUpcomingFeature("StrictConcurrency"),   // step 1
    // .swiftLanguageMode(.v6),                    // step 2
])
// Xcode equivalent, in an .xcconfig:
// SWIFT_STRICT_CONCURRENCY = complete   (minimal | targeted | complete)
```

Why it ranks first: strict concurrency turns an entire class of **data-race
bugs — exactly the §6 flake category with no good dynamic detector — into
compile errors.** It is the Swift equivalent of adopting nullable reference
types in C#: large, mechanical, and it removes bugs that tests find only
intermittently. Sequence it as `minimal` → `targeted` → `complete`, one module
at a time.

Count the escape hatches as the debt ledger — `@unchecked Sendable`,
`nonisolated(unsafe)`, `@preconcurrency import`. Each is a place the compiler was told to stop checking. A growing count means
the migration is being faked, not done — **report the count and its trend.**

SwiftLint rules worth enabling, in roadmap order:

| Rule | Catches | Why early |
|---|---|---|
| `force_unwrapping`, `force_try`, `force_cast` | `!`, `try!`, `as!` — the Swift crash trio | the real-bug family; **especially in tests**, where `try!` destroys the whole test bundle (§6) |
| `implicitly_unwrapped_optional` | `var x: Foo!` — a deferred crash | pairs with the above |
| `unused_import`, `unused_declaration` (needs `swiftlint analyze`) | dead code and stale dependencies | cheap, large diffs |
| `empty_xctest_method` | **an XCTest method with no body — a test that cannot fail** | directly targets the tautological-test class |
| `xctfail_message`, `single_test_class`, `test_case_accessibility` | XCTest-specific correctness | free, and tests are usually unlinted |
| `discouraged_optional_boolean`, `optional_enum_case_matching` | subtle logic errors | tier 2 |
| `todo` / `fixme` | counts as a debt ledger | report the number, do not fail on it |
| `line_length`, `type_body_length`, `file_length` | style; argue once then autoformat | last |

```yaml
# .swiftlint.yml
included: [Sources, Tests]
excluded: [.build, Generated, "**/*.generated.swift"]
opt_in_rules:
  - force_unwrapping
  - empty_xctest_method
  - unused_import
  - explicit_init
analyzer_rules:
  - unused_import
  - unused_declaration
```

**Include `Tests` in `included:`.** Most repos lint only `Sources`, so the test
code — where `try!`, empty test methods and stale mocks live — is never
checked.

Config to read before trusting any clean run:

```bash
cat .swiftlint.yml 2>/dev/null
grep -n "disabled_rules\|excluded" -A 20 .swiftlint.yml 2>/dev/null
grep -rn "swiftlint:disable" --include='*.swift' . | wc -l
grep -rn "// swiftlint:disable all\|swiftlint:disable:this" --include='*.swift' . | head -20
swift build 2>&1 | grep "warning:" | sed 's/.*warning: //' | sort | uniq -c | sort -rn | head -20
```

A large or growing `swiftlint:disable` count, a long `disabled_rules` list, or
an `excluded:` that drops `Tests` means findings are being silenced rather than
fixed. **Report the count and its trend, not a clean lint.**
