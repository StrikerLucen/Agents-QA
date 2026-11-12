# Stack playbook — JavaScript / TypeScript

Reference for a testsquad agent working in a JS/TS repo. Run the detection block
first, record the answers, then use only the rows that match.

Everything here was verified against the sources in `docs/RESEARCH.md` on
**2026-11-12**. Where a version or flag may have moved since, the entry says
*verify before relying on this*.

---

## 1. Detection signals

Read these files, in this order. Stop as soon as the question is answered.

| Question | Read / run | Conclusion |
|---|---|---|
| Is this JS/TS at all? | `package.json` exists | yes |
| TypeScript? | `tsconfig.json` exists, or `devDependencies.typescript` | yes |
| ESM or CJS? | `package.json` → `"type"` field | `"module"` = ESM, absent/`"commonjs"` = CJS |
| Monorepo? | `pnpm-workspace.yaml`, `package.json` → `workspaces`, `turbo.json`, `nx.json`, `lerna.json` | run per-package, never at root |

### Package manager — decided by lockfile, never by preference

```bash
ls package-lock.json pnpm-lock.yaml yarn.lock bun.lock bun.lockb 2>/dev/null
```

| Lockfile | Manager | Install | Run a script | Run a binary |
|---|---|---|---|---|
| `package-lock.json` | npm | `npm ci` | `npm run test` | `npx <bin>` |
| `pnpm-lock.yaml` | pnpm | `pnpm install --frozen-lockfile` | `pnpm test` | `pnpm exec <bin>` |
| `yarn.lock` | Yarn | `yarn install --immutable` | `yarn test` | `yarn <bin>` |
| `bun.lock` / `bun.lockb` | Bun | `bun install --frozen-lockfile` | `bun run test` | `bunx <bin>` |

Also check `package.json` → `packageManager` (Corepack pin). If it disagrees with
the lockfile, the `packageManager` field wins — it is what CI enforces.

Yarn version matters: `cat .yarnrc.yml` present ⇒ Yarn Berry (2+). Absent and
`yarn.lock` begins with `# yarn lockfile v1` ⇒ Yarn Classic (`--immutable` is
Berry-only; Classic uses `--frozen-lockfile`).

### Test runner

```bash
grep -nE '"(jest|vitest|mocha|@jest/globals|node:test|ava|jasmine)"' package.json
ls jest.config.* vitest.config.* vite.config.* .mocharc.* ava.config.* 2>/dev/null
grep -rn "from 'node:test'\|require('node:test')" --include='*.test.*' -l . 2>/dev/null | head
```

| Marker | Runner |
|---|---|
| `jest.config.{js,ts,cjs,mjs,json}` or `package.json` → `"jest"` key | **Jest** |
| `vitest.config.*`, or `test:` block inside `vite.config.*`, or `vitest` in devDeps | **Vitest** |
| `.mocharc.{yml,json,js,cjs}` or `mocha` in devDeps | **Mocha** (+ assertion lib: `chai` / `node:assert`) |
| imports of `node:test` and no runner in devDeps | **node:test** (stdlib, Node ≥ 18; `--experimental-test-coverage` ≥ 20, stable ≥ 22) |
| `react-scripts test` in `scripts.test` | **CRA**-wrapped Jest — config is locked unless ejected/craco |

### Framework / bundler

| Marker | Framework | Consequence |
|---|---|---|
| `next.config.{js,mjs,ts}` | Next.js | server/client component split; `next/jest` or Vitest+`vite-tsconfig-paths` |
| `vite.config.*` without `next.config` | Vite SPA | Vitest is the native choice |
| `react-scripts` in deps | CRA | **unmaintained**; Jest version pinned by `react-scripts` |
| `remix.config.*` / `@remix-run/*` | Remix | |
| `nuxt.config.*` | Nuxt | `@nuxt/test-utils` |
| `angular.json` | Angular | Karma/Jasmine by default, increasingly Jest/Vitest via builders |
| `nest-cli.json` | NestJS | Jest preconfigured, `test:e2e` script present |
| `app.json` + `expo` in deps | Expo / React Native | `jest-expo` preset |

### DOM environment

`jsdom` or `happy-dom` in devDeps, or `testEnvironment: 'jsdom'` in Jest config,
or `environment: 'jsdom'` in Vitest config. Absent ⇒ node environment; a DOM test
will fail with `document is not defined`, which is a config gap and not a bug.

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. Always scope.

```bash
# Jest — pass source files, Jest maps them to dependent tests via its graph
npx jest --findRelatedTests $(git diff --name-only --diff-filter=ACMR HEAD -- '*.ts' '*.tsx' '*.js' '*.jsx') --passWithNoTests

# Jest, staged only (what a pre-commit hook should do)
npx jest --findRelatedTests $(git diff --cached --name-only --diff-filter=ACMR) --passWithNoTests

# Jest, everything changed vs. the base branch (what a PR hook should do)
npx jest --findRelatedTests $(git diff --name-only --diff-filter=ACMR origin/main...HEAD) --passWithNoTests

# Jest, git-aware alternatives (no file list needed)
npx jest --onlyChanged          # vs. last commit
npx jest --changedSince=origin/main

# Vitest — note: subcommand `related`, not a flag
npx vitest related --run $(git diff --name-only --diff-filter=ACMR origin/main...HEAD)
npx vitest --changed origin/main --run

# Mocha — no dependency graph. Approximate by path convention:
npx mocha $(git diff --name-only --diff-filter=ACMR origin/main...HEAD | sed 's|^src/|test/|; s|\.js$|.test.js|' | xargs ls 2>/dev/null)

# node:test — same limitation, same approach
node --test $(git diff --name-only --diff-filter=ACMR origin/main...HEAD | sed 's|^src/|test/|; s|\.js$|.test.js|' | xargs ls 2>/dev/null)
```

`--passWithNoTests` is mandatory in hooks: without it, a diff that touches only
non-test-mapped files exits non-zero and blocks the edit.

Mocha and node:test have **no file-to-test dependency graph**. Do not claim
related-test scoping for them; say so and fall back to path mapping or a
directory-scoped run.

### Full suite / single file / single test

| | Jest | Vitest | Mocha | node:test |
|---|---|---|---|---|
| Full suite | `npx jest` | `npx vitest run` | `npx mocha` | `node --test` |
| Watch | `npx jest --watch` | `npx vitest` | `npx mocha --watch` | `node --test --watch` |
| One file | `npx jest path/to/f.test.ts` | `npx vitest run path/to/f.test.ts` | `npx mocha path/to/f.test.js` | `node --test path/to/f.test.js` |
| One test by name | `npx jest -t "regex of test name"` | `npx vitest run -t "name"` | `npx mocha --grep "name"` | `node --test --test-name-pattern "name"` |
| One describe block | `npx jest -t "Describe name"` | `npx vitest run -t "Describe"` | `npx mocha --grep "Describe"` | `--test-name-pattern` matches full path |
| Bail on first failure | `--bail` | `--bail=1` | `--bail` | *(no equivalent; verify before relying on this)* |
| Serial (diagnostic) | `--runInBand` | `--pool=forks --poolOptions.forks.singleFork` | serial by default | `--test-concurrency=1` |
| Fail on `.only` | `--ci` + lint rule | `--allowOnly=false` (default in `run` under CI) | `--forbid-only` | lint rule only |

`vitest run` is non-negotiable in CI and hooks: bare `vitest` enters watch mode
and hangs the hook forever.

Prefer the repo's own `scripts.test` wrapper when one exists — it may set
`NODE_OPTIONS`, a config path, or env the raw binary does not.

---

## 3. Coverage

Target formats that `scripts/normalize_coverage.py` parses: **lcov**
(`lcov.info`) for every JS/TS runner. It auto-detects lcov from `SF:`/`DA:` lines
or a `.info` extension.

Line-only coverage hides untested branches. Always request branch data.

### Jest (coverage via babel-plugin-istanbul / V8)

```bash
npx jest --coverage \
  --coverageReporters=lcov --coverageReporters=json-summary --coverageReporters=text-summary \
  --coverageDirectory=coverage
```

Output: `coverage/lcov.info` (lcov, carries `BRDA:` branch records),
`coverage/coverage-summary.json`, `coverage/lcov-report/index.html`.

Branch coverage: istanbul instrumentation records branches by default — the
`BRDA:` lines are present without an extra flag. Enforce a floor:

```bash
npx jest --coverage --coverageThreshold='{"global":{"branches":70,"lines":80}}'
```

Beware `coverageProvider: 'v8'` in Jest config: V8 coverage reports **range**
coverage and its branch numbers differ materially from istanbul's. If a report
looks implausibly high, check this field.

### Vitest

```bash
npx vitest run --coverage \
  --coverage.provider=v8 \
  --coverage.reporter=lcov --coverage.reporter=json-summary --coverage.reporter=text
```

Output: `coverage/lcov.info`. Requires `@vitest/coverage-v8` (or
`@vitest/coverage-istanbul`) installed — Vitest errors out and offers to install
it, which will hang a non-interactive hook. Install it explicitly first.

For branch numbers that match Jest/istanbul semantics:

```bash
npm i -D @vitest/coverage-istanbul
npx vitest run --coverage --coverage.provider=istanbul --coverage.reporter=lcov
```

Thresholds: `--coverage.thresholds.branches=70 --coverage.thresholds.lines=80`.

### Mocha (via nyc / c8)

```bash
npx nyc --reporter=lcov --reporter=text-summary \
  --branches 70 --lines 80 \
  npx mocha
```

Output: `coverage/lcov.info`. `nyc` is istanbul-based (branch-aware). `c8` is the
V8-based equivalent: `npx c8 --reporter=lcov npx mocha`.

### node:test

```bash
node --test --experimental-test-coverage \
  --test-reporter=lcov --test-reporter-destination=coverage/lcov.info \
  --test-reporter=spec --test-reporter-destination=stdout
```

Output: `coverage/lcov.info`. Node's built-in coverage is **statement/branch via
V8**; its branch granularity is coarser than istanbul's. On Node < 22 this flag
is experimental and its output shape has changed between minors — verify before
relying on this.

### Normalize

```bash
python3 scripts/normalize_coverage.py coverage/lcov.info \
  --out .testsquad/coverage.json --strip-prefix "$(pwd)"
```

Monorepos emit absolute paths from several packages; `--strip-prefix` is what
makes them joinable with `git diff --name-only`.

---

## 4. Mutation testing

**Tool: StrykerJS v10.x — healthy as of 2026-11-12** (v10.1.1 verified). This is
one of the two best-maintained mutation ecosystems anywhere; use it.

```bash
npm i -D @stryker-mutator/core
# plus exactly one runner plugin:
npm i -D @stryker-mutator/jest-runner      # Jest
npm i -D @stryker-mutator/vitest-runner    # Vitest
npm i -D @stryker-mutator/mocha-runner     # Mocha
```

### The flag everyone gets wrong

> StrykerJS has **`--incremental`**. It does **NOT** have `--since`.
> `--since` is **Stryker.NET only**. Blog posts and even some docs pages
> conflate the two. If you pass `--since` to StrykerJS it is an unknown option.

```bash
# Diff-scoped run — the only affordable invocation
npx stryker run --incremental --incrementalFile reports/stryker-incremental.json
```

`--incremental` works by diffing source + test files against the state recorded
in the incremental file and re-running mutants only where something changed.
StrykerJS reuses **~94%** of prior results (RESEARCH §15).

**The cache file must be persisted in CI or incremental mode degrades to a full
run every time.** GitHub Actions:

```yaml
- uses: actions/cache@v4
  with:
    path: reports/stryker-incremental.json
    key: stryker-incremental-${{ github.sha }}
    restore-keys: stryker-incremental-
- run: npx stryker run --incremental --incrementalFile reports/stryker-incremental.json
```

Narrow the blast radius further by passing the changed files as mutate globs:

```bash
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'src/**/*.ts' | paste -sd, -)
npx stryker run --incremental --incrementalFile reports/stryker-incremental.json --mutate "$CHANGED"
```

Useful flags: `--concurrency=4` (default is CPU-bound and will starve CI),
`--reporters=json,clear-text` → `reports/mutation/mutation.json`,
`--coverageAnalysis=perTest` (large speedup; requires a runner that reports
per-test coverage — Jest and Vitest do).

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants and showed developers **12.5%** of them. Median
per changelist went from **820 → 7** through two mechanisms, and only **2** were
actually surfaced. Hard cap: **≤7 mutants per file**.

1. **One mutant per changed line.** In >90% of lines with multiple applicable
   mutants, all mutants share the same fate — generating more is waste.
2. **Arid-node suppression.** Never mutate: `console.*`, logger calls, metrics
   emitters, `assert`/`invariant`, trivial getters, pure delegation wrappers,
   `toString`, string literals inside `Error(...)` messages. In Stryker, express
   this with `// Stryker disable next-line all` comments or a `mutate` glob that
   excludes `**/logger.ts`, `**/telemetry/**`, `**/*.stories.tsx`.
3. **Gate on covered-MSI of the diff, never on project score.** If every merged
   change clears its threshold on its own changed lines, the project converges
   without ever running a full-project sweep.

```bash
npx stryker run --incremental --mutate "$CHANGED" --thresholds.break 80
```

### Blind spots to state in any report

A diff-scoped gate is a ratchet on new code, not an audit. It cannot see:
(a) a change in file A destroying coverage of file B; (b) **deleted assertions in
test files** — a test-only change runs no mutants at all, so removing assertions
passes the gate; (c) config/fixture changes flipping mutant status. Keep an
occasional full run off the critical path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **Playwright** | Default for browser E2E. Multi-browser, auto-waiting, trace viewer, parallel by default | Browser download is large — hundreds of megabytes, cache it in CI; CI run minutes; still the 10%-flake tier per Meta's floors (RESEARCH §17) |
| **Cypress** | Existing investment, strong DX | Single-tab/single-origin model, slower parallelism, paid dashboard for sharding |
| **MSW** | Mocking HTTP at the network layer in unit + component tests | Handlers drift from the real API unless generated from a schema/OpenAPI spec |
| **Testcontainers (node)** | Real Postgres/Redis/Kafka in integration tests | Needs a Docker daemon; container startup costs seconds, not milliseconds — time it in your own suite; unusable in sandboxed CI without privileges |
| **supertest** | HTTP assertions against an Express/Koa/Nest app without binding a port | In-process only — proves nothing about the deployed network path |
| **Testing Library** (`@testing-library/react` etc.) | Component tests via the DOM | jsdom is not a browser; layout, scroll, real focus and CSS behave differently |

```bash
# Playwright
npm i -D @playwright/test && npx playwright install --with-deps chromium
npx playwright test                              # full
npx playwright test tests/checkout.spec.ts       # one file
npx playwright test -g "adds item to cart"       # by title
npx playwright test --project=chromium --workers=2 --reporter=line
npx playwright test --last-failed                # rerun only prior failures
npx playwright show-trace trace.zip
```

**Derive mocks from reality, not from the model** (RESEARCH §14). An LLM
inventing what a dependency returns is *the* root cause of tautological tests —
the test then asserts exactly what the mock was told to say. Derive mock shapes
from the TypeScript type, an OpenAPI schema, or a recorded trace.

---

## 6. Flake taxonomy — JavaScript

Per-language distributions differ sharply; a shared checklist would be wrong.
For JavaScript the reported breakdown is:

| Cause | Share |
|---|---|
| Concurrency | **21%** |
| Async wait | **20%** |
| OS / environment | **18%** |
| Network | **13%** |
| **Order dependency** | **nearly nonexistent** |

Order dependency is almost absent because Jest and Vitest give each test *file*
its own worker process. Shared state cannot leak across files; it can still leak
*within* a file. Do not spend budget hunting order dependency in JS — spend it on
async and concurrency.

### Detection commands

```bash
# Serial execution — if the suite passes here and fails in parallel, it is
# a concurrency/shared-resource problem (ports, temp files, a shared DB).
npx jest --runInBand
npx vitest run --pool=forks --poolOptions.forks.singleFork

# Randomize within-file order (the only place order flake lives in JS)
npx jest --randomize            # Jest 29.5+; verify availability in older Jest
npx vitest run --sequence.shuffle --sequence.seed=12345

# Repeat a single test to surface timing flake
npx vitest run -t "name" --repeat=20
npx jest -t "name" --runTestsByPath path/to/f.test.ts   # wrap in a shell loop for N

# Environment sweep (OS/locale is 18% here — the second-biggest bucket)
TZ=Pacific/Kiritimati LANG=tr_TR.UTF-8 npx vitest run
TZ=UTC npx vitest run
```

### Static flake smells — check before the test ever runs (Tier 0)

`Math.random()` unseeded · `Date.now()` / `new Date()` in an assertion ·
`crypto.randomUUID()` in expected values · `setTimeout`-based waiting ·
`await page.waitForTimeout(n)` in Playwright · real network or DNS ·
`Object.keys()` iteration-order assumptions · `fs.readdir` ordering ·
`toLocaleString` without an explicit locale · float equality (`toBe(0.1+0.2)`) ·
asserting on the order of an unordered collection · `expect.any()` masking a
real assertion.

Fix the first three with fake timers and seeded randomness:

```js
vi.useFakeTimers(); vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
jest.useFakeTimers().setSystemTime(new Date('2026-01-01T00:00:00Z'));
```

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

Grep for these before running anything. A test suite that reaches production is
the worst outcome this plugin can produce.

```bash
grep -rnE "NODE_ENV\s*=\s*['\"]?production" --include='*.json' --include='*.ts' --include='*.js' --include='*.yml' .
grep -rnE "(postgres|mysql|mongodb(\+srv)?|redis|amqp)://" --include='*.ts' --include='*.js' --include='*.env*' . | grep -vE "localhost|127\.0\.0\.1|::1|test|docker"
grep -rn "dotenv" --include='*.ts' --include='*.js' jest.setup.* vitest.setup.* test/ tests/ src/setupTests.* 2>/dev/null
ls -la .env .env.production .env.prod 2>/dev/null
grep -rnE "(prod|production)\.(api|internal)|\.amazonaws\.com|\.rds\.amazonaws" --include='*.ts' --include='*.js' .
```

| Red flag | Why |
|---|---|
| `require('dotenv').config()` in `jest.setup.js` / `vitest.setup.ts` with no `path:` override | loads the developer's real `.env`, which often holds prod credentials |
| `.env` not in `.gitignore`, or `.env.production` present | one `NODE_ENV` flip from live |
| `process.env.DATABASE_URL` used without a test-specific default | CI secret injection points it at staging or prod |
| Playwright `baseURL` pointing at a real domain | E2E writes to the live system |
| `nock`/`msw` disabled or `.enableNetConnect()` in setup | tests make real outbound calls |
| Stripe/Twilio/SendGrid keys matching `sk_live_`, `AC…` without `test` | live-money side effects |
| A seed/migration script run by `globalSetup` with no DB-name guard | can truncate a real database |

Assert the guard, don't assume it:

```js
// vitest.setup.ts / jest.setup.js
if (!/test|localhost|127\.0\.0\.1/.test(process.env.DATABASE_URL ?? '')) {
  throw new Error(`Refusing to run tests against ${process.env.DATABASE_URL}`);
}
```

---

## 8. House conventions to match

Detect the repo's own convention and copy it exactly; do not impose one.

```bash
# Where do tests live?
find . -path ./node_modules -prune -o -name '*.test.*' -print -o -name '*.spec.*' -print 2>/dev/null | head -30
ls __tests__ test tests src/__tests__ 2>/dev/null
# What does the config say?
grep -nE "testMatch|testRegex|include|spec" jest.config.* vitest.config.* vite.config.* .mocharc.* 2>/dev/null
```

| Convention | Looks like |
|---|---|
| Colocated (Vitest/Next default) | `src/lib/cart.ts` + `src/lib/cart.test.ts` |
| `__tests__` directory (Jest/CRA default) | `src/lib/__tests__/cart.test.ts` |
| Mirrored tree (Mocha, Node services) | `src/lib/cart.js` + `test/lib/cart.test.js` |
| `.spec.` vs `.test.` | Angular/Nest/Cypress lean `.spec.`; Jest/Vitest lean `.test.` — match what exists |

| Concern | Usual location |
|---|---|
| Test setup | `jest.setup.js`, `vitest.setup.ts`, `src/setupTests.ts`, wired via `setupFilesAfterEach` / `setupFiles` |
| Manual mocks | `__mocks__/` adjacent to the module; `__mocks__/<pkg>.ts` at root for node_modules |
| Fixtures | `__fixtures__/`, `test/fixtures/`, `src/**/fixtures/` |
| Factories | `test/factories/`, `src/test-utils/`, often `@faker-js/faker` + a `build*()` function |
| MSW handlers | `src/mocks/handlers.ts` + `src/mocks/server.ts` (node) / `browser.ts` |
| Custom render | `src/test-utils.tsx` re-exporting Testing Library with providers wrapped |
| E2E | `e2e/`, `tests/e2e/`, `cypress/e2e/`, `playwright/` |

Mocking idiom by runner: Jest `jest.mock()` / `jest.spyOn()`; Vitest
`vi.mock()` / `vi.spyOn()` (hoisted the same way, but factories must not
reference outer variables — use `vi.hoisted()`); Mocha has no built-in mocking,
so look for `sinon`; node:test has `mock.method()` / `mock.fn()`.

---

## 9. Static analysis + type checking

Cheapest wins, highest signal-to-noise. Rank these first on any roadmap — they
are free, deterministic, and run in seconds.

```bash
# Type check — no emit, whole project. The single highest-value command in a TS repo.
npx tsc --noEmit
npx tsc --noEmit -p tsconfig.test.json        # when tests have their own config
npx tsc --noEmit --strict                     # preview the strict-mode debt

# Lint
npx eslint . --max-warnings=0
npx eslint $(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.ts' '*.tsx' '*.js') --max-warnings=0
npx eslint . --format=json -o .testsquad/eslint.json

# Formatting (never a finding worth reporting — fix it silently)
npx prettier --check .

# Faster alternatives, if already present in the repo — do not introduce them unasked
npx biome check .
npx oxlint .

# Dead code / unused exports
npx knip
npx ts-prune
```

Config to check before trusting `tsc --noEmit` output:

```bash
grep -nE '"(strict|noImplicitAny|strictNullChecks|skipLibCheck|exclude|include)"' tsconfig.json
```

`"strict": false` means the type checker is finding a fraction of what it could;
say so in the report rather than reporting a clean run. `skipLibCheck: true` is
normal and not a finding. If `exclude` drops the test directory, tests are
untyped — that is a real finding.

Test-specific lint plugins worth enabling, in roadmap order:

| Plugin | Catches |
|---|---|
| `eslint-plugin-jest` / `eslint-plugin-vitest` | `.only` left in, `expect` outside a test, identical titles, missing assertions |
| `eslint-plugin-testing-library` | `await`-less async queries, direct node access, `container.querySelector` |
| `eslint-plugin-jest-dom` | `expect(el.textContent).toBe(x)` instead of `toHaveTextContent` |
| `eslint-plugin-playwright` | `waitForTimeout`, conditional `expect`, missing `await` on assertions |
| `@typescript-eslint/no-floating-promises` | unawaited promises — a direct cause of the 20% async-wait flake bucket |

`no-floating-promises` requires type-aware linting (`parserOptions.project`),
which is slower but is the rule most likely to find a real flake.
