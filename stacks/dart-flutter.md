# Stack playbook — Dart / Flutter

Reference for a testsquad agent working in a Dart or Flutter repo. Run the
detection block first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

Dart's position is mixed and the playbook says so: the **runner, coverage and
analyzer are excellent**, and the **mutation-testing ecosystem is effectively
unavailable** (§4). Do not let a roadmap promise what Dart cannot deliver.

---

## 1. Detection signals

```bash
ls pubspec.yaml pubspec.lock analysis_options.yaml melos.yaml 2>/dev/null
grep -nE "^(name|environment|sdk|flutter):" -A 3 pubspec.yaml
grep -n "flutter:" pubspec.yaml                      # a `flutter:` SDK dep => Flutter package
flutter --version 2>/dev/null || dart --version
ls android/ ios/ web/ macos/ linux/ windows/ 2>/dev/null
```

| Marker | Conclusion |
|---|---|
| `pubspec.yaml` with `flutter: sdk: flutter` under `dependencies` | **Flutter package** — use `flutter test`, never `dart test` |
| `pubspec.yaml` with no Flutter dependency | **pure Dart package** — use `dart test` |
| `android/` + `ios/` directories | a Flutter **app** (not a package); has flavours, signing, `--dart-define`. See §7 |
| `melos.yaml` / `pubspec_overrides.yaml` | **monorepo** — `melos run test` is the canonical entry point; per-package commands need `melos exec` |
| `environment: sdk: '>=3.x'` | Dart 3 — sound null safety is mandatory, records/patterns available |
| `flutter: module:` in pubspec | add-to-app module; integration testing is hosted by the native app |
| `build.yaml` + `*.g.dart` / `*.freezed.dart` / `*.mocks.dart` | **code generation** (`build_runner`). Generated files must be excluded from coverage and lint, and **regenerated before tests** or the suite tests stale code |

### Test type — this is the decision that matters

```bash
ls test/ test_driver/ integration_test/ 2>/dev/null
find test -name '*_test.dart' | wc -l
grep -rln "testWidgets(" test/ | head
grep -rln "IntegrationTestWidgetsFlutterBinding" integration_test/ | head
grep -rln "goldenFileComparator\|matchesGoldenFile" test/ | head
```

| Location / marker | Kind | Runner | Cost |
|---|---|---|---|
| `test/**_test.dart` with `test(...)` only | **unit test** | `flutter test` / `dart test` | milliseconds. The bulk of the suite should be here |
| `test/**_test.dart` with `testWidgets(...)` | **widget test** | `flutter test` | tens of ms. Runs headless in a **fake** Flutter engine — no real rendering, no real platform channels, fake async clock |
| `integration_test/**_test.dart` | **integration test** | `flutter test integration_test` (headless) or `flutter drive` (on a device) | **seconds to minutes per test, on a real device or emulator.** The 10%-flake tier |
| `test_driver/` | legacy Flutter Driver | `flutter drive --driver=test_driver/x.dart` | superseded by `integration_test`; treat as a migration item |
| `matchesGoldenFile(...)` | **golden / screenshot test** | `flutter test --update-goldens` to regenerate | Font and renderer differences make goldens **host-dependent** — see §6 |

The widget-vs-integration distinction is the single most important thing to get
right: a widget test is cheap and deterministic; an integration test needs a
device, is slow, and is where Flutter flake lives. Put logic in unit tests,
rendering in widget tests, and reserve `integration_test/` for a handful of
end-to-end journeys.

### Supporting packages that change what idiomatic looks like

```bash
grep -nE "^\s+(mockito|mocktail|bloc_test|flutter_test|integration_test|golden_toolkit|alchemist|patrol|build_runner|very_good_analysis|flutter_lints|custom_lint|riverpod|bloc|provider|get_it|http_mock_adapter|nock|fake_async|clock|faker|test_cov_console|coverage)\s*:" pubspec.yaml
```

| Package | Means |
|---|---|
| `mockito` + `build_runner` | generated mocks in `*.mocks.dart` via `@GenerateMocks` / `@GenerateNiceMocks` |
| `mocktail` | **no codegen**, `class MockX extends Mock implements X {}` — now the more common choice |
| `bloc_test` | `blocTest(...)` for bloc/cubit state sequences |
| `fake_async` / `clock` | deterministic time — see §6 |
| `golden_toolkit` / `alchemist` | golden-test infrastructure with device/font control |
| `patrol` | native-interaction integration tests (permissions dialogs, notifications) |
| `http_mock_adapter` (dio) / `mockito` on `http.Client` | HTTP stubbing |
| `very_good_analysis` / `flutter_lints` / `lints` | the lint rule set in use — see §9 |

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. In Flutter it is worse than most: `flutter test` pays a
**per-invocation compile cost** (kernel compilation of the test and its imports)
before anything runs.

**Dart has no import-graph test selector** — no `--findRelatedTests`, no
testmon, no `--dirty`. Say so plainly. Scope by **path**, which works well
because Dart's convention mirrors `lib/` into `test/` precisely.

```bash
# 1. Changed test files themselves — always run these, always cheap
CHANGED_TESTS=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'test/**/*_test.dart')
[ -n "$CHANGED_TESTS" ] && flutter test $CHANGED_TESTS

# 2. Convention mapping: lib/ -> test/, foo.dart -> foo_test.dart
MAPPED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'lib/**/*.dart' \
  | sed -e 's|^lib/|test/|' -e 's|\.dart$|_test.dart|' \
  | while read f; do [ -f "$f" ] && echo "$f"; done)
[ -n "$MAPPED" ] && flutter test $MAPPED

# 3. Directory scoping when the diff is confined to one area
flutter test test/features/cart
flutter test test/unit                       # if the repo splits unit/widget

# 4. Everything (CI, not hooks) — and note integration tests are NOT included
flutter test
flutter test integration_test                # separate, explicit, slow
```

Melos monorepo:

```bash
melos exec --scope="acme_cart" -- flutter test
melos exec --dir-exists=test -- flutter test
# Changed packages only:
melos exec --diff=origin/main -- flutter test      # *verify the flag name for your melos version*
```

Flags that make a hook survivable:

```bash
flutter test $FILES --no-pub                 # skip the pub resolution step
flutter test $FILES --concurrency=4          # default is cores/2; raise or lower deliberately
flutter test $FILES --exclude-tags=golden    # goldens are host-dependent; keep them out of hooks
flutter test $FILES --reporter=compact
```

**Guard an empty selection.** `flutter test` with no arguments runs the *whole
suite* — exactly the disaster you are avoiding:

```bash
[ -z "$FILES" ] && exit 0                    # guard FIRST, before the command
flutter test $FILES
```

`flutter test` on a path matching no tests exits non-zero with
"No test files were found" — acceptable in a hook only if you have already
guarded the empty case.

### Full suite / single file / single test

| Goal | Flutter | Pure Dart |
|---|---|---|
| Whole suite | `flutter test` | `dart test` |
| One file | `flutter test test/cart_test.dart` | `dart test test/cart_test.dart` |
| One test by exact name | `flutter test --plain-name 'adds an item'` | `dart test -N 'adds an item'` |
| By regex | `flutter test --name 'adds.*item'` | `dart test -n 'adds.*item'` |
| One file + one test | `flutter test test/cart_test.dart --plain-name 'adds an item'` | same with `dart test` |
| By tag | `flutter test --tags golden` / `--exclude-tags slow` | `dart test -t golden` / `-x slow` |
| Directory | `flutter test test/features` | `dart test test/features` |
| Integration tests | `flutter test integration_test` or `flutter drive --driver=... --target=...` | n/a |
| Platform selection | — | `dart test -p vm,chrome` |
| Concurrency | `--concurrency=N` (`-j`) | `-j N` |
| Random order | `--test-randomize-ordering-seed=random` / `=12345` | `--test-randomize-ordering-seed=12345` |
| Machine-readable | `--reporter=json > .testsquad/test.json` | `--reporter=json` |
| Expanded output | `--reporter=expanded` | `--reporter=expanded` |
| Update goldens | `flutter test --update-goldens` | n/a |
| Per-test timeout | `--timeout=30s`, or `@Timeout(Duration(seconds: 30))` in the file | same |
| Fail fast | — (**no `--bail`**; the Dart test runner has no fail-fast flag. *Verify before claiming otherwise*) | same |
| Run on a device | `flutter test -d <deviceId> integration_test/app_test.dart` | n/a |

**Nested group names matter for `--plain-name`:** `group('Cart', () { test('adds an item', ...) })`
produces the full name `Cart adds an item`. `--plain-name 'adds an item'` still
matches as a substring; `--name` is a regex over the full name. A filter that
matches nothing exits non-zero with "No tests match", which is the behaviour you
want — but confirm it rather than assuming:

```bash
flutter test --plain-name 'adds an item' 2>&1 | grep -q "No tests match" && echo "NO TEST MATCHED"
```

Code generation is a pre-step, not an optional one:

```bash
dart run build_runner build --delete-conflicting-outputs
# Stale *.g.dart / *.mocks.dart means the suite is testing yesterday's model.
# In CI, run this and then `git diff --exit-code` to prove generated code is current.
```

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **lcov**. Flutter
emits it natively.

```bash
flutter test --coverage                      # -> coverage/lcov.info
flutter test --coverage --coverage-path=.testsquad/lcov.info

# Pure Dart
dart test --coverage=coverage                # -> coverage/test/**.vm.json (NOT lcov)
dart pub global activate coverage
dart pub global run coverage:format_coverage \
  --lcov --in=coverage --out=coverage/lcov.info \
  --report-on=lib --packages=.dart_tool/package_config.json
```

| Output | Format | Parsed as |
|---|---|---|
| `coverage/lcov.info` | lcov | `lcov` ✅ |
| `coverage/test/**.vm.json` (raw `dart test --coverage`) | VM coverage JSON | **not parsed** — must go through `format_coverage` |
| `genhtml` output | HTML | humans only |

```bash
# Human view
genhtml coverage/lcov.info -o coverage/html && open coverage/html/index.html
```

### The "uncovered files are invisible" trap — check this first

`flutter test --coverage` only records files that were **imported by a test**.
A `lib/` file no test imports does not appear in `lcov.info` at all, so it is
not counted as 0% — it is counted as nothing, and the headline percentage is
inflated, sometimes enormously.

```bash
# How many lib/ files are missing from the report entirely?
find lib -name '*.dart' ! -name '*.g.dart' ! -name '*.freezed.dart' | sed 's|^|SF:|' | sort > /tmp/all.txt
grep '^SF:' coverage/lcov.info | sed 's|.*/lib/|SF:lib/|' | sort -u > /tmp/covered.txt
comm -23 /tmp/all.txt /tmp/covered.txt | head -40      # files with NO coverage record at all
```

Report that count explicitly. "82% coverage over the 40% of files any test
imports" is the honest sentence; "82% coverage" alone is misleading. The usual
remedy is a generated `test/coverage_helper_test.dart` that imports every
library — mention it as an option, and note it inflates nothing (an imported
but unexercised file correctly shows 0%).

Exclusions — generated code otherwise dominates the numbers:

```bash
# lcov is usually available; otherwise filter the file directly
lcov --remove coverage/lcov.info \
  '*/generated/*' '*.g.dart' '*.freezed.dart' '*.mocks.dart' '*/l10n/*' \
  -o coverage/lcov.info --ignore-errors unused
```

### Branch coverage

**Treat Dart/Flutter as having effectively no branch coverage.**

The Dart VM gained a `--branch-coverage` capability, surfaced as
`dart test --coverage=... ` plus `format_coverage --branch-coverage`, and
Flutter added a `flutter test --branch-coverage` flag. **Verify before relying
on this** — availability depends on the SDK version, it does not work on every
platform (notably it is a VM feature, so web/JS-compiled tests do not get it),
and whether `BRDA` records actually reach `lcov.info` must be checked, not
assumed:

```bash
flutter test --coverage --branch-coverage 2>/dev/null
grep -c '^BRDA:' coverage/lcov.info || echo "no branch records emitted"
```

If that grep returns 0 — which is the common case — `normalize_coverage.py` will
report `branches_found: 0` and `branch_pct: null`, **correctly**. Report it as
**"branch coverage was not measured"**, never as 0%. A `null` here is the tool
being honest, not a gap in the test suite.

Normalize:

```bash
python3 scripts/normalize_coverage.py coverage/lcov.info \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

Do not gate on a project-wide coverage threshold; it fails on legacy debt the
current change did not cause. Compute the diff figure from the normalized JSON.

---

## 4. Mutation testing — effectively unavailable in Dart

**State this plainly in any report or roadmap. Do not pretend otherwise.**

| Option | Status, verified 2026-11-12 | Verdict |
|---|---|---|
| A maintained, widely-adopted, diff-scopeable mutation tool for Dart | **does not exist** | — |
| `mutation_test` (pub.dev) | a niche, rules-file-driven text/regex mutator. Not AST-aware, **no git-diff scoping**, small user base. *Verify its current maintenance status and feature set before adopting* | evaluate only if a team insists |
| Anything else on pub.dev tagged "mutation" | hobby projects and abandoned experiments | do not adopt |

> **Dart has no equivalent of Stryker.NET, Infection, mutant, cargo-mutants,
> mutmut or PIT.** Every other stack in this playbook set except Go has a
> healthy mutation tool with a diff gate; Dart does not.
>
> **Recommendation: do not make mutation testing a gate in a Dart or Flutter
> repo.** If a roadmap item promises a Dart mutation score, that item is
> unachievable with maintained tooling and should be struck.

### What to do instead in Dart

Redirect the budget to the things Dart *is* good at. These produce more signal
per minute here than a fragile mutation run would.

1. **`dart analyze` with strict modes and a strong lint set** (§9). Dart's
   analyzer is genuinely excellent and sound null safety removes an entire bug
   class statically. This is the highest-value item on any Dart roadmap.
2. **Hand-written mutants for the specific change under review.** Flip a
   comparison, invert a boolean, change a `+` to a `-`, delete a null check —
   and verify the suite fails. That is the mutation idea applied manually, and
   it is what Meta's ACH does anyway: a few *targeted, realistic* mutants, not a
   full sweep (RESEARCH §4). It is cheap, needs no tooling, and is the right
   thing to put on a Dart roadmap in place of a mutation gate.
3. **Assertion-quality review on the diff.** Count assertions per test, flag
   tests whose only assertion is `expect(result, isNotNull)` or
   `expect(find.byType(X), findsOneWidget)` with no state assertion — the
   Flutter-specific tautological test.
4. **Coverage of `lib/` file *presence*** (the §3 trap) — in Dart this finds
   more untested code than mutation testing would, because whole files commonly
   escape the report.

### Google's affordability rules — for reference and for the manual approach

Google generated 16.9M mutants and showed developers **12.5%**. Median per
changelist: **820 → 7**. Median surfaced: **2**. Hard cap: **≤7 mutants per
file** (RESEARCH §15).

1. **One mutant per changed line.** In **>90%** of lines with multiple
   applicable mutants all share the same fate, so more is pure waste. Cap at
   **≤7 per file**.
2. **Arid-node suppression** — never mutate: `debugPrint`/`log`/`logger` calls,
   analytics and crash-reporting emitters, `toString`/`==`/`hashCode`
   (especially `freezed`-generated), trivial getters, pure delegation, exception
   *message* strings, `*.g.dart` / `*.freezed.dart` / `*.mocks.dart`, generated
   l10n. **This step matters more than the choice of tool** — it is the 820 → 7.
3. **Never gate on a mutation score.** Gate on the diff — and in Dart, gate on
   nothing mutation-related at all.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **`testWidgets` + `WidgetTester`** (`flutter_test`) | **The default.** Pump a widget tree, tap, enter text, assert via `Finder`s | Headless, deterministic, milliseconds. The best value in the stack. Does **not** execute real platform channels or real rendering |
| **`integration_test` package** | Real app on a real device/emulator, real platform channels | **Seconds to minutes per test; needs a device.** The 10%-flake tier. Keep to a handful of journeys |
| **`patrol`** | Integration tests that must touch **native** UI — permission dialogs, notifications, system settings | The only practical option for those flows. Adds its own native harness and build complexity |
| **`flutter drive`** | Driving `integration_test` from the host (screenshots, device farms) | Needed for Firebase Test Lab / Browserstack. Slower and more moving parts than `flutter test integration_test` |
| **Golden tests** (`matchesGoldenFile`) | Pixel-level regression of a widget | **Host-dependent**: fonts, text shaping, renderer version and platform all change the bytes. Must be generated on one canonical platform (a Docker image or a single CI OS) or they will flake forever. See §6 |
| **`golden_toolkit` / `alchemist`** | Goldens with controlled device sizes and real fonts | Reduces, does not eliminate, the host-dependence problem |
| **`mocktail` / `mockito`** | Faking collaborators | `mocktail` needs no codegen and is the lower-friction choice. `mockito`'s `@GenerateNiceMocks` is type-checked against the real class, which is safer |
| **`http_mock_adapter`** (dio) / a fake `http.Client` | HTTP stubbing | `MockClient((request) async => Response(...))` from `package:http/testing.dart` is built in and needs no package |
| **`bloc_test`** | Asserting a bloc/cubit's emitted state sequence | Excellent for state machines; asserts the sequence, so it is sensitive to incidental ordering |
| **`fake_async` / `clock`** | Deterministic time and timers | See §6. `FakeAsync` is what makes `pumpAndSettle` deterministic |
| **`sqflite_common_ffi` / `drift` in-memory** | A real local database in tests | Fast and faithful enough for SQL assertions |
| **`shared_preferences` `setMockInitialValues`** | Plugin state without a device | The pattern for every plugin: `TestDefaultBinaryMessengerBinding...setMockMethodCallHandler` |
| **`glados` / `fast_check`-style PBT** | Property-based testing | Dart's PBT ecosystem is **markedly weaker** than Python's or JS's. **Assess-tier at most** (RESEARCH §16); *verify maintenance before recommending any Dart PBT package* |

```dart
// The widget-test shape to match
testWidgets('adds an item to an empty cart', (tester) async {
  await tester.pumpWidget(MaterialApp(home: CartPage(repo: FakeCartRepo())));
  await tester.tap(find.byKey(const Key('add-item')));
  await tester.pump();                     // one frame
  expect(find.text('1 item'), findsOneWidget);
});
```

**Platform channels do not exist in a widget test.** Any plugin call throws
`MissingPluginException` unless mocked. That is why plugin-heavy code must be
behind an interface — and it is the single most common reason a widget test is
"impossible to write" for a given widget.

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests. In
Dart, derive the shape from the **abstract class / interface** and from the
`fromJson` of the real model — both are compiler-checked. `mockito`'s generated
mocks fail to compile when the real interface changes; a hand-written
`mocktail` fake does not, so pair `mocktail` with an `implements` clause (which
the analyzer does check) and never with `dynamic`.

---

## 6. Flake taxonomy — Dart / Flutter

> **There is no published per-cause flakiness breakdown for Dart or Flutter**
> comparable to the Java (async 45% / concurrency 20% / order 12%), Python
> (order 59% / infrastructure 28%) or JavaScript (concurrency 21% / async 20% /
> OS 18% / network 13%) figures in RESEARCH §19. Do not invent one and do not
> reuse another language's percentages.

What follows is **practitioner consensus, not measurement.** Label it that way.

### Structural note that shapes everything

Dart is **single-threaded per isolate**, and the Dart test runner **runs each
test file in its own isolate** (and `flutter test` runs files concurrently
across isolates by default, `--concurrency` = cores/2). That means:

- **Cross-*file* state leakage is largely prevented by construction** —
  closer to JavaScript/Jest than to Python or Ruby.
- **Cross-*test* leakage within one file is entirely possible**: top-level
  variables, `static` fields, singletons (`GetIt`), `SharedPreferences` mock
  values, a global `HttpOverrides`, a registered plugin handler, and anything
  set in `setUpAll`.

So the dominant pattern to look for is **in-file shared state plus async/timing
in widget tests**, not cross-file order dependency.

### The one to check first: pumping and async

`testWidgets` runs in a **fake-async zone with a fake clock**. Nothing advances
unless you advance it. The classic failures:

| Mistake | Symptom | Fix |
|---|---|---|
| `await tester.tap(...)` then asserting with no `pump` | the frame never rebuilt; finder finds the old widget | `await tester.pump()` after every state change |
| `pumpAndSettle()` with an **infinite** animation (a spinner, a repeating `AnimationController`) | **times out after 10 minutes** or throws "pumpAndSettle timed out" | use `pump(Duration(...))` a fixed number of times, not `pumpAndSettle` |
| `pumpAndSettle()` waiting on a real `Future` (network, `Timer`) | the fake clock never advances the real future | inject a fake; or `tester.runAsync(() async { ... })` for genuinely real async |
| `await Future.delayed(...)` inside a widget test | hangs or completes at the wrong time | `fakeAsync((async) { ... async.elapse(...); })` |
| Pending timers at test end | "A Timer is still pending even after the widget tree was disposed" | cancel timers in `dispose`; this is a **real memory-leak finding**, not a test problem |
| `tester.pump()` count assumptions | passes locally, fails under load | assert on state/finders, never on frame counts |

```bash
grep -rn "pumpAndSettle" test/ | wc -l
grep -rn "Future.delayed\|Timer(" test/ lib/ | head -20
grep -rn "runAsync" test/ | head
```

### Other consensus causes, with their instruments

| Cause | Smell | Instrument |
|---|---|---|
| **Golden-test host dependence** | goldens pass locally, fail in CI with a 0.3% pixel diff | Fonts are the usual culprit: the test engine uses **Ahem**-style placeholder fonts unless real fonts are loaded. Generate goldens on **one** canonical platform (a pinned Docker image), never on developer machines. `--update-goldens` in CI only, deliberately |
| Time | `DateTime.now()` in logic under test | `package:clock` + `withClock(Clock.fixed(...), () {...})`; `fakeAsync` for timers |
| Randomness | unseeded `Random()`, `Uuid().v4()` in an expected value | `Random(42)`; inject an id generator |
| Order within a file | `setUpAll` creating shared mutable state; a singleton not reset | `setUp`/`tearDown` per test; `GetIt.I.reset()` in `tearDown` |
| Order across files | rare (isolate per file), but **`--test-randomize-ordering-seed` still finds in-file order bugs** | see below |
| Integration-test device flake | emulator cold start, animations, keyboard, permission dialogs | disable animations, use `patrol` for native dialogs, retry at the **job** level and report the retry as a finding |
| Platform channels | `MissingPluginException` intermittently | register mock handlers in `setUp`, unregister in `tearDown` |
| Concurrency across files | tests sharing a port, a temp dir, or the same `SharedPreferences` file path | `--concurrency=1` to diagnose |
| Float | `expect(total, 0.3)` | `closeTo(0.3, 0.0001)` |

### Detection commands

```bash
# Order randomisation — the available instrument. The seed is printed and replayable.
# It randomises order WITHIN a file and across files; because each file is its own
# isolate, in-file order is where the bugs are.
flutter test --test-randomize-ordering-seed=random
flutter test --test-randomize-ordering-seed=12345

# Serialise to rule out cross-file resource contention; isolate the suspect
flutter test --concurrency=1
flutter test test/cart_test.dart --plain-name 'adds an item'

# Repeat to surface timing flake
for i in $(seq 1 50); do flutter test test/cart_test.dart --plain-name 'adds an item' || break; done

# Are goldens the problem? Then sweep the environment.
flutter test --exclude-tags=golden ; flutter test --tags=golden
TZ=Pacific/Kiritimati flutter test ; TZ=UTC flutter test

# Integration tests on a device
flutter test integration_test -d <deviceId>
```

### Static flake smells — Tier 0, before the test ever runs

```bash
grep -rn "pumpAndSettle" test/ | head -30
grep -rn "Future.delayed\|sleep(\|Timer(" test/ | head -20
grep -rn "DateTime.now()\|DateTime.parse('now'\|Stopwatch" test/ lib/ | head -20
grep -rn "Random()\|Uuid()" test/ lib/ | head -20
grep -rn "setUpAll\|tearDownAll" test/ | head -20
grep -rn "GetIt\|getIt\." test/ | grep -v "reset" | head -20
grep -rn "matchesGoldenFile" test/ | wc -l
grep -rn "skip:\s*true\|skip:\s*'" test/ | wc -l
grep -rn "expect(.*isNotNull)\|expect(.*isNotEmpty)" test/ | wc -l
grep -rn "findsWidgets\b" test/ | head          # looser than findsOneWidget
grep -rnE "localhost:[0-9]{4,5}|http://|https://" test/ | head
```

Specific smells: `pumpAndSettle` on anything that might animate forever ·
`Future.delayed` in a widget test · `DateTime.now()` in an assertion · unseeded
`Random()`/`Uuid()` · `setUpAll` creating mutable state · a `GetIt` registration
never reset · **golden tests not pinned to a canonical host** · `skip:` counts
(tests that silently stopped existing — count and report the trend) ·
`expect(x, isNotNull)` as the only assertion (**a test that cannot meaningfully
fail** — the Flutter tautological test) · `findsWidgets` where
`findsOneWidget` was meant · real URLs in test code.

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).
A CI job that retries a flaky integration test until it passes is hiding the
finding, not fixing it — report the retry configuration itself.

---

## 7. Production-danger signals

Flutter's production-danger surface is unusual: the danger is less "the test
wipes the production database" and more "**the test, or the build the test
validates, is wired to production backends, real analytics, and real push
notifications**", plus the build-flavour machinery that decides which.

```bash
grep -rn "dart-define\|--dart-define-from-file" . --include='*.sh' --include='*.yaml' --include='*.yml' --include='Makefile' --include='*.json' 2>/dev/null | head -20
ls android/app/src/*/  ios/Flutter/*.xcconfig ios/Runner/Firebase 2>/dev/null
grep -rn "flavorDimensions\|productFlavors" android/app/build.gradle* 2>/dev/null
ls lib/firebase_options*.dart android/app/google-services.json ios/Runner/GoogleService-Info.plist 2>/dev/null
grep -rn "String.fromEnvironment\|bool.fromEnvironment\|int.fromEnvironment" lib/ | head -20
grep -rn "kReleaseMode\|kDebugMode\|kProfileMode" lib/ | head
ls .env .env.production .env.prod assets/.env* 2>/dev/null
grep -rn "flutter_dotenv\|dotenv.load" lib/ test/ pubspec.yaml | head
grep -rnE "https://[a-z0-9.-]+\.(com|io|net|app)" lib/ --include='*.dart' | grep -viE "localhost|example|127\.0\.0\.1|10\.0\.2\.2" | head -20
```

| Red flag | Why |
|---|---|
| **`--dart-define` prod values baked into the test or CI test command** (e.g. `--dart-define=API_URL=https://api.acme.com`) | integration tests hit the production API — real orders, real payments, real push |
| `--dart-define-from-file=env/prod.json` in a test job | same, one level less visible |
| `String.fromEnvironment('API_URL', defaultValue: 'https://api.acme.com')` | **the default is the production URL** — any build that forgets the define is a production build |
| `google-services.json` / `GoogleService-Info.plist` for the **production** Firebase project present in the default flavour directory | tests write to production Firestore/Analytics/Crashlytics |
| `firebase_options.dart` with only one (production) option set | `DefaultFirebaseOptions.currentPlatform` resolves to prod in every flavour |
| `productFlavors` in `build.gradle` where the **default/first flavour is prod** | `flutter test integration_test` with no `--flavor` runs the production flavour |
| Xcode **Release** scheme or a prod `.xcconfig` used by the integration-test job | the test validates — and talks to — the production configuration |
| `flutter_dotenv` loading `.env` with no explicit test override, and `.env` is the production file | production credentials inside the test process |
| `kReleaseMode` guards around the *only* safety check | debug builds skip the guard; release builds (which is what a store build is) do not |
| A real analytics/crash SDK initialised in `main()` with no test no-op | integration tests pollute production analytics and crash dashboards |
| Hardcoded production hosts in `lib/` with no injection seam | no test can avoid them |
| `http` calls in widget tests with no `HttpOverrides` | widget tests **can** make real network calls; Flutter's test binding returns a 400 for `HttpClient` by default, but a `dio`/`http` client with a custom adapter bypasses that |
| Real device integration tests signed with the **production** signing config | a tester's device registers as a real install |

Assert the guard, don't assume it:

```dart
// test/flutter_test_config.dart — runs before every test file in the package
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  const apiUrl = String.fromEnvironment('API_URL', defaultValue: 'http://localhost');
  const env    = String.fromEnvironment('APP_ENV', defaultValue: 'test');
  if (env.contains('prod') || apiUrl.contains('api.acme.com')) {
    throw StateError('Refusing to run tests against $apiUrl (APP_ENV=$env)');
  }
  await testMain();
}
```

`test/flutter_test_config.dart` is the Flutter-specific hook for this — it is
picked up automatically for every test file in the package and is the right
place for a global guard, global font loading for goldens, and
`HttpOverrides.global` blocking.

---

## 8. House conventions to match

```bash
ls test/ integration_test/
find test -type d | head -20
ls test/helpers test/fixtures test/mocks test/support 2>/dev/null
cat test/flutter_test_config.dart 2>/dev/null
grep -rn "@GenerateMocks\|@GenerateNiceMocks" test/ | head
```

| Convention | Rule |
|---|---|
| Test location | **`test/` mirrors `lib/`** exactly: `lib/features/cart/cart_service.dart` → `test/features/cart/cart_service_test.dart`. This is near-universal in Dart and the analyzer/tooling assumes it |
| File naming | `<subject>_test.dart`. **A file not ending in `_test.dart` is silently never run** — check for near-misses (`cart_tests.dart`, `test_cart.dart`) |
| Integration tests | `integration_test/<journey>_test.dart`, never in `test/` |
| Test helpers | `test/helpers/`, `test/support/` — **must not end in `_test.dart`** or the runner tries to execute them and fails on "no tests" |
| Generated mocks | `test/**/*.mocks.dart` (mockito), produced by `@GenerateNiceMocks([MockSpec<CartRepo>()])` in the test file + `build_runner`. **Commit them or regenerate in CI — do not leave CI guessing** |
| Hand-written fakes | `test/mocks/`, `test/fakes/` — `class MockCartRepo extends Mock implements CartRepo {}` (mocktail) |
| Fixtures | `test/fixtures/*.json`, loaded with `File('test/fixtures/cart.json').readAsStringSync()`. For asset-bundle fixtures use `rootBundle` + `TestDefaultBinaryMessengerBinding` |
| Goldens | `test/goldens/` or `test/**/goldens/` — `matchesGoldenFile('goldens/cart_page.png')`. Commit the PNGs |
| Global test setup | `test/flutter_test_config.dart` (package-wide, automatic) |
| Group naming | `group('CartService', () { group('addItem', () { test('adds an item to an empty cart', ...) }) })` — the full name is the concatenation, which is what `--name` matches |
| Test naming | lowercase sentence: `'returns an error when the cart is full'`. Not `'test adds item'` |
| Setup/teardown | `setUp`/`tearDown` per test; **`setUpAll`/`tearDownAll` only for genuinely immutable shared setup** (see §6) |
| Widget keys | `Key('add-item')` / `ValueKey` on anything a test needs to find. `find.byKey` is stable; `find.text` breaks on copy changes and localisation |
| Assertion idiom | `expect(actual, matcher)` — **matcher second**. `expect(find.text('1 item'), findsOneWidget)`, `expect(() => f(), throwsA(isA<CartFullException>()))` |
| Mocking idiom | `mocktail`: `when(() => repo.load()).thenAnswer((_) async => [])`, `verify(() => repo.save(any())).called(1)`, and **`registerFallbackValue`** for any custom type used with `any()`. `mockito`: `when(repo.load()).thenAnswer(...)` (no closure) |
| Bloc tests | `blocTest<CartBloc, CartState>('...', build: ..., act: ..., expect: () => [...])` |
| Lint config | `analysis_options.yaml` at the package root; a separate `test/analysis_options.yaml` may relax rules for tests — **read it; relaxed test lints are where mock-shape drift hides** |

---

## 9. Static analysis + type checking — rank first on every roadmap

Dart's analyzer is the strongest asset in this stack, and §4 means it also has
to carry weight mutation testing carries elsewhere. **Rank it first and invest
in it.**

```bash
# Tier 0 — free, no install
dart analyze                                  # exit 1 on any error; warnings per config
dart analyze --fatal-infos --fatal-warnings   # the CI form
dart format --output=none --set-exit-if-changed .
dart pub outdated
dart pub deps
flutter pub get && dart run build_runner build --delete-conflicting-outputs
git diff --exit-code -- '*.g.dart' '*.freezed.dart'   # proves generated code is current

# DIFF-SCOPED — the RESEARCH §6 pattern (~70% fix rate scoped, ~0% as a backlog)
dart analyze $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.dart' | tr '\n' ' ')
dart format --output=none --set-exit-if-changed $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.dart' | tr '\n' ' ')

# Machine-readable
dart analyze --format=machine > .testsquad/analyze.txt

# Tier 1
flutter pub run custom_lint                   # if custom_lint / riverpod_lint is configured
dart pub global activate dart_code_metrics_presets 2>/dev/null
# NOTE: `dart_code_metrics` was relicensed/commercialised as DCM. *Verify current
# licensing and maintenance before recommending it* — the free OSS package name
# is no longer the maintained product.
```

### The strict modes — the biggest single win

```yaml
# analysis_options.yaml
include: package:very_good_analysis/analysis_options.yaml   # or flutter_lints / lints

analyzer:
  language:
    strict-casts: true        # no implicit downcasts from dynamic
    strict-raw-types: true    # no bare List/Map/Future without type arguments
    strict-inference: true    # no silent inference to dynamic
  errors:
    missing_required_param: error
    missing_return: error
    invalid_annotation_target: ignore
    todo: ignore
  exclude:
    - "**/*.g.dart"
    - "**/*.freezed.dart"
    - "**/*.mocks.dart"
    - "lib/generated/**"

linter:
  rules:
    avoid_dynamic_calls: true
    unawaited_futures: true          # a dropped Future is a silent async bug
    always_declare_return_types: true
    prefer_const_constructors: true
    use_build_context_synchronously: true
    avoid_print: true
```

`strict-casts`, `strict-raw-types` and `strict-inference` are **off by default**
and are where most of the uncaptured value is. They close the `dynamic` holes
that sound null safety does not — a `dynamic` from a `jsonDecode` flows
unchecked through the whole codebase otherwise.

```bash
# How much `dynamic` is actually in play?
grep -rn "dynamic " lib/ | wc -l
grep -rn "as " lib/ --include='*.dart' | wc -l
grep -rn "jsonDecode\|json\[" lib/ | head -20     # the usual dynamic entry point
grep -rn "// ignore:" lib/ test/ | wc -l
grep -rn "// ignore_for_file:" lib/ test/ | wc -l
```

Lint rule sets, in roadmap order:

| Set / rule | Catches | Why early |
|---|---|---|
| `package:flutter_lints` | the Flutter baseline; almost always already present | zero-cost floor |
| `package:very_good_analysis` | a much stricter superset (~200 rules) | the realistic target for a repo willing to invest |
| `strict-casts` / `strict-raw-types` / `strict-inference` | implicit `dynamic` — the main soundness hole left after null safety | **the highest-value change in this section** |
| `unawaited_futures` | a `Future` created and dropped — a silently swallowed error, and a flake source | directly targets §6 |
| `use_build_context_synchronously` | `BuildContext` used after an `await` — a real crash class | Flutter-specific, high value |
| `avoid_dynamic_calls` | method calls on `dynamic` that fail only at runtime | pairs with strict-casts |
| `missing_required_param`, `missing_return` as **errors** | compile-adjacent bugs demoted to warnings by default | one config line |
| `custom_lint` + `riverpod_lint` / `bloc_lint` | state-management misuse the analyzer cannot see | only if the repo uses that state solution |
| `avoid_print`, `prefer_const_constructors` | noise and rebuild performance | autofixable (`dart fix --apply`) |

```bash
dart fix --dry-run          # what the analyzer can fix automatically
dart fix --apply            # apply it — a large, safe, one-time diff
```

Config to read before trusting any clean run:

```bash
cat analysis_options.yaml
cat test/analysis_options.yaml 2>/dev/null
grep -n "exclude:" -A 10 analysis_options.yaml
grep -rn "// ignore:" lib/ | awk -F'ignore: ' '{print $2}' | sort | uniq -c | sort -rn | head -20
grep -rn "// ignore_for_file:" lib/ test/ | head -20
```

A large or growing `// ignore:` / `// ignore_for_file:` count means findings are
being silenced rather than fixed — **report the count and its trend, not a clean
analyze.** Check specifically whether `exclude:` drops `test/` or whether a
`test/analysis_options.yaml` relaxes the rules: if so, the test code is
unanalysed, which is exactly where a `dynamic` mock whose shape no longer
matches the real class goes unnoticed.
