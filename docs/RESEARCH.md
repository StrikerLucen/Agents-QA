# Research findings behind testsquad's design

Every non-obvious design decision in this project traces to a citation here.
Where the evidence is contested, both sides are recorded. Vendor-published
numbers are marked as such.

## The five numbers that drive the design

| Finding | Number | Source |
|---|---|---|
| False-positive rate above which developers stop trusting a tool | **10% "effective false positives"** — above it, Google auto-disables the check | [Sadowski et al., CACM 2018](https://cacm.acm.org/research/lessons-from-building-static-analysis-tools-at-google/) |
| Fix rate: findings shown at diff/review time vs. a batch list | **~70% vs. ~0%** | [Distefano et al., CACM 2019](https://cacm.acm.org/research/scaling-static-analyses-at-facebook/) |
| LLM tests that assert the bug, when the buggy implementation is in the prompt | **3.84% vs 0.46%** — an 8.4x increase | [Zhao et al., ISSTA 2026](https://arxiv.org/abs/2607.22883) |
| "Passing" validation evidence in LLM repair agents that proves nothing | **46%** of positive events are non-discriminating | [Xu & Wu](https://arxiv.org/abs/2607.28871) |
| Fault detection: mutation-guided vs coverage-guided test generation | **12–15% vs 2.0–2.4%** mutant kill rate | [Meta ACH, FSE 2025](https://arxiv.org/abs/2501.12862) |

## 1. Coverage is a floor detector, not a quality target

[Inozemtseva & Holmes, ICSE 2014](https://cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf)
(31,000 suites, 5 Java systems): once suite *size* is controlled for, the
correlation between coverage and effectiveness drops to low-to-moderate. Their
conclusion: coverage "should not be used as a quality target."

The asymmetry we rely on: **uncovered code is definitely untested (strong
signal); covered code may still be unverified (weak signal).**

[ISSTA 2026 replicability study](https://arxiv.org/abs/2607.22880) adds the
sharper point: coverage and mutation scores discriminate between models in
*regression* settings, but **stop being reliable indicators in bug-detection
settings** — exactly the case we care about.

Contested: Gopinath et al. argue coverage does predict effectiveness in the real
world. The safe synthesis is the floor/target distinction above.

**Design consequence:** we never report coverage as a headline score. We report
which high-risk code is untested.

## 2. Generate from the spec, never from the implementation

[Zhao, Zhou & Cohen, ISSTA 2026](https://arxiv.org/abs/2607.22883) — Defects4J
3.0, 318 focal methods, 11 frontier models:

- Buggy implementation in the prompt → **137.69 bug-asserting tests on average (3.84%)**
- Fixed implementation → **16.46 (0.46%)**
- Bug-exposing tests: **2.98% (buggy) vs 8.51% (fixed)**
- Mitigation that worked: derive a behavioral spec/docstring first, then prompt
  from the spec. Effective tests rose **104 → 187 (+79%)**

[Konstantinou et al.](https://arxiv.org/html/2410.21136v1) confirms the mechanism:
given buggy code plus a *correct* assertion, LLM accuracy drops 8–9pp; given
buggy code plus a *bug-matching* assertion, it accepts it **79.6–84.2%** of the
time. The model sides with the implementation.

This is structural, not promptable-away. Diffblue — which sells test generation —
markets the same behaviour as a feature: its tests describe observed behaviour,
so a bug becomes an asserted invariant.

**Design consequence:** the oracle must come from an independent source — a
docstring, type signature, issue text, spec, or a mutant to kill. Where none
exists, the test is labelled a **characterization test**, not a correctness test.

## 3. An LLM judging test quality is near chance

- [Konstantinou et al.](https://arxiv.org/html/2410.21136v1): LLM assertion
  *classification* accuracy **40.8–46.3%**, while *generation* succeeds 89–94%.
  Generating is easy; judging is not.
- [RM-RF](https://arxiv.org/html/2601.13097): a zero-shot prompted LLM judge
  scores **0.53–0.59 F1** on predicting whether a test compiles, adds coverage,
  or adds mutation kills. Fine-tuned, it reaches 0.69.
- LLM-as-judge agreement is overstated by **33–41pp** once corrected for chance.

**Design consequence:** the QA gatekeeper's verdict must rest on **execution**,
not opinion. Its core check is differential execution: run the test against
mutated and unmutated code; identical results mean the test discriminates
nothing. [Xu & Wu](https://arxiv.org/abs/2607.28871) found **46%** of passing
validation evidence in repair agents fails exactly this check, and **23.8%** of
rollouts closed with nothing but such evidence.

## 4. Mutation beats coverage as the acceptance signal — affordably

Meta superseded its own coverage-filtered TestGen-LLM with mutation-guided
[ACH](https://arxiv.org/abs/2501.12862):

- ACH: **12–15%** mutant kill rate; TestGen-LLM on the same classes: **2.0–2.4%**
- 10,795 Android Kotlin classes → 9,095 mutants → 571 tests, **73% engineer acceptance**
- Equivalent-mutant detection: LLM alone 0.79 precision / 0.47 recall; **with cheap
  static pre-processing, 0.95 / 0.96**

Crucially ACH does **not** run a full mutation sweep. An LLM generates a *few
realistic, targeted* mutants from a plain-English description of the fault class.
That is what makes it affordable per-task.

[MIST-RL](https://www.alphaxiv.org/abs/2603.01409): reward **marginal** kills per
test, not total — removing incremental feedback drops mutation score 74.0% → 65.1%.
Otherwise you get redundant near-duplicate tests.

## 5. The TestGen-LLM funnel: expect to discard most candidates

[Alshahwan et al., FSE 2024](https://arxiv.org/abs/2402.09171), Instagram Reels/Stories:

| Stage | Survivors |
|---|---|
| Built | 75% |
| Built **and** passed reliably | 57% |
| Built, passed **and** increased coverage | **25%** |

Per-trial success at scale was far lower: **5.0%** (Facebook), **3.5%** (Instagram).
Of what survived, **73%** was accepted by engineers; unsupervised on a large
unfamiliar surface, acceptance was **51%**.

Their flakiness filter is the whole algorithm: **a test that does not pass 5 of 5
runs is discarded.**

Other lessons they report: judge **per test case, not per class** (engineers
rejected class-level claims); LLMs **self-plagiarize**, emitting near-identical
tests; "TODO: add assertion" scaffolds are their own reject category; unit tests
that leak into other classes signal missing mocks and should be flagged to a
reviewer rather than auto-rejected.

**Contradiction worth acting on:** [YATE](https://arxiv.org/abs/2507.18316) shows
*repairing* failed candidates beats discarding them — **+32.1% lines covered and
+21.8% mutants killed**. A broken test still encodes a valid intent.

## 6. Timing beats accuracy

[Distefano et al., CACM 2019](https://cacm.acm.org/research/scaling-static-analyses-at-facebook/):
Infer deployed as a batch bug list achieved a **~0% fix rate**. The same analyzer,
same bugs, delivered as comments on the diff under review: **~70%**.

[Lewis et al., ICSE 2013](https://research.google/pubs/does-bug-prediction-support-human-developers-findings-from-a-google-case-study/)
is the cautionary half: Google's bug prediction ranked files correctly and
produced **no measurable change in developer behaviour**. Their conclusion — a
finding must be *actionable*, its reasoning *obvious*, and it must appear *in the
workflow*. A correct ranking nobody acts on is a failed feature.

**Design consequence:** the PR comment is the primary surface; the dashboard is
secondary. Findings are scoped to the diff. Pre-existing issues go to a separate
non-blocking backlog — a wall of legacy findings on first install is the fastest
route to being ignored permanently.

## 7. Trust is a measurable quantity

Google's operating rule: a code-review check must stay under **10% effective
false positives** or it is disabled until fixed. Their definition is the useful
part — **an issue is an effective false positive if the developer took no
positive action after seeing it.** A technically-correct finding nobody acts on
counts as a false positive; a technically-incorrect one someone fixes anyway does not.

Corroborating from security operations: industry false-positive rates of 46–80%,
**~63% of alerts unaddressed**, **71% of analysts reporting burnout**. Mature
teams target <10–15% — converging independently on Google's number.

Google SRE's alerting philosophy supplies the budget discipline: **≤2 incidents
requiring intervention per 12-hour shift**, alert on **symptoms not causes**, and
"you'll be tempted to add a lot of stuff; resist."

## 8. Risk: what actually predicts defects

- [Nagappan & Ball, ICSE 2005](https://www.microsoft.com/en-us/research/publication/use-of-relative-code-churn-measures-to-predict-system-defect-density/):
  **absolute churn is a poor predictor; churn normalized by size is a strong one.**
  `relative_churn = churned_LOC / total_LOC` discriminated fault-prone binaries
  with **89.0% accuracy** on Windows Server 2003.
- [Rahman & Devanbu, ICSE 2013](https://dl.acm.org/doi/10.5555/2486788.2486846)
  and a [722k-commit replication](https://arxiv.org/abs/2008.09569): process
  metrics (churn, recency, authors) beat product metrics (LOC, complexity). The
  argument against complexity-only models is **stasis** — code metrics barely
  change between releases, so they flag the same files forever.
- Google's time-weighted risk (from FixCache, implemented in
  [bugspots](https://github.com/igrigorik/bugspots)): for each bug-fixing commit
  touching a file, add `1 / (1 + e^(-12t + 12))` where `t` is the normalized
  timestamp. Recent fixes dominate; old ones decay. ~20 lines of code, and it beat
  fancier algorithms on developers' own judgments.
- [CodeScene "Code Red"](https://arxiv.org/abs/2203.04374) (vendor study, 39
  codebases, 30,737 files): low-health files carry **15x more defects** and
  **124% more development time**. Direction well-supported; exact multipliers are
  vendor-flavoured.

**Design consequence:** likelihood is computed from `git log` (relative churn +
time-weighted fix recency), with complexity as a tie-breaker and an explanation.
Impact is supplied by humans. The two are **never collapsed into one number** —
[Bach's objection](https://www.satisfice.com/download/heuristic-risk-based-testing)
is that multiplying two invented ordinals yields fake precision people stop
arguing with.

## 9. Flakiness

[Google](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html):
**~1.5% of all test runs are flaky**; **~16% of their 4.2M tests** have shown
flakiness at some point. Every test added to chase a coverage number adds
expected flake, which subtracts from the trustworthiness of the whole report.

Industry practice converges on a per-test **status lifecycle** (healthy → flaky →
quarantined → broken) so the headline number is computed only from trusted tests,
and on ranking flakes by **cost** (PR-impact, CI minutes wasted) rather than count.

## 10. Reporting structure

- **ReportPortal's "To Investigate" bucket**: the report distinguishes triaged
  from untriaged, and carries human triage decisions forward to matching failures
  in the next run. The reading list is only the untriaged items.
- **Currents' error-signature grouping**: report length is O(distinct problems),
  not O(failing tests).
- **Allure's Categories**: classify every failure as product defect / test defect
  / infrastructure *before* counting. Never show one undifferentiated "failed" count.
- **Session-based test management** ([Bach](https://www.satisfice.com/download/session-based-test-management))
  separates **bugs** (problems in the product) from **issues** (problems with the
  testing itself). This maps exactly onto reproduced vs. unreproduced.
- [Bettenburg et al., TSE 2010](https://thomas-zimmermann.com/publications/files/zimmermann-tse-2010.pdf)
  (466 developers): most valued are **steps to reproduce, stack traces, test
  cases** — and these are precisely what reporters supply least. A **wrong repro
  delays a fix more than a missing one**.
- Charter format from Hendrickson's *Explore It!*:
  **Explore (target) with (resources) to discover (information).**

## 11. Benchmark contamination — how we evaluate honestly

Benchmark choice changes results by ~7x:

| Benchmark | Status |
|---|---|
| HumanEval-Java, TestEval | **Saturated/leaked.** ULT measures TestEval pass@5 at 91.79% vs 12.57% on uncontaminated functions |
| Defects4J | Highest memorization rate of any bug benchmark |
| SWT-Bench **Lite** | Near-saturated |
| **ULT / UnLeakedTestbench** | Purpose-built contamination-free — **use this** |
| **SWT-Bench Verified** | The honest signal — best systems still fail >1/3 of cases |

The cliff in one study: [Siddiq et al.](https://arxiv.org/html/2305.00418v3)
measured GPT-3.5 at **69.1% line coverage on HumanEval-Java and 1.3% on SF110**
(real projects). EvoSuite scored 27.5% on the same real projects.

**Design consequence:** our own evals run on seeded fixtures with known answers,
and any public benchmarking uses ULT and SWT-Bench Verified only.

## 12. Multi-agent orchestration: the cost knee

- Cost **bottoms out at ~4–6 subagents and rises after**. A 4-agent system can
  burn 10x a single agent; past ~6 hand-offs, roughly 4x the cost for half the
  success rate.
- Under *equal token budgets*, single-agent beat sequential multi-agent on
  multi-hop reasoning (0.418–0.427 vs 0.379–0.389).
- Known failure modes (MAST taxonomy): ambiguous delegation, tool-invocation
  error, **context pollution**, conflicting sub-agent outputs, premature action.
- A researcher given 4 dimensions and ~20 fetches exhausted its budget and
  returned **zero salvageable output** — give each specialist one dimension and a
  hard tool-call budget.
- **Subagents cannot ask for approval mid-task.** Investigation belongs in
  subagents; approval-gated writes belong in the parent.

**Design consequence:** the squad has 8 roles but the orchestrator **selects 2–4
per run** based on the diff. Workers are read-only where possible; writes stay
with the parent.

## 13. What already exists (so we don't rebuild it)

| Project | Stars | Relevance |
|---|---|---|
| [wshobson/agents](https://github.com/wshobson/agents) | 42.4k | Already ships `unit-testing`, `tdd-workflows`, `debugging-toolkit`, `agent-teams`, and `ship-mate` (orchestrate → … → qa → review, max-2-iteration QA loop) |
| [testland/qa](https://github.com/testland/qa) | 13 | 78 QA plugins / 402 components / 73 agents, CI-validated with evals. Closest competitor. **No commands, no orchestration, no onboarding** |
| [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) | 26.5k | 100+ persona agents — the anti-pattern: résumé-style capability lists, fake JSON "protocols", no tool scoping |
| [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) | 47.0k | 317 plugins. **No "testing squad" plugin exists** — the slot is open |

**Saturated, do not build:** a generic `test-automator` / `qa-expert` agent, a
standalone debugger agent, a memory backend, a per-framework skill library.

**Genuinely missing:** a plugin that profiles a repo and writes *tailored agents
and rules into it*. That is this project's differentiator.

### Prior art worth reading before writing any agent prompt
- [`code-modernization/test-engineer.md`](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-modernization/agents/test-engineer.md) — "the legacy code is the oracle"; "a comparison that cannot run is a failure, never a skip"; prove the tests can fail
- [`pr-review-toolkit/pr-test-analyzer.md`](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/pr-review-toolkit/agents/pr-test-analyzer.md) — 1–10 criticality, "the specific regression this prevents", explicitly anti-100%-coverage
- [`testland/qa` test-code-critic](https://github.com/testland/qa/blob/main/plugins/qa-test-review/agents/test-code-critic.md) — Bash-scoped tool grants; conventions live in a *skill*, the agent only enforces them
- [`obra/superpowers` fix-loop](https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/references/fix-loop.md) — bounded loop, resume-then-escalate, "cannot verify" as a first-class verdict

## 14. What the commercial tools actually do

Most "AI testing" products are browser E2E record-and-replay with ML locator
healing — not test generation (Testim, Mabl, Functionize, Autify, Momentic,
QA Wolf). Genuine code-level generation: Qodo Gen, Early AI, Tusk, Keploy, and
Antithesis. Four mechanisms are worth copying.

**"AI at build time, determinism at run time"** — Octomind's stated principle.
The LLM writes the test, then leaves the loop forever. *A test whose pass/fail
depends on a model call is not a test.* The same applies to our gatekeeper: it
runs at PR time and emits a verdict; it never sits inside the suite.

Octomind is also the cautionary tale — it [wound down in June 2026](https://rywalker.com/research/octomind),
the first funded AI-QA-agent casualty, squeezed between managed services above
and Playwright's own native AI tooling below.

**Two-phase generation: behaviour list → test code.** Qodo Gen enumerates
*behaviours* in natural language ("what should this do under X"), lets the user
accept/reject/edit that list, and only then synthesizes code. Autify's Genesis
does test *design* from specs as a separate upstream artifact. The convergence
across independent vendors is itself the signal, and it dovetails with the ISSTA
finding in §2: the behaviour list **is** the independent oracle.

It also solves the §3 problem. An LLM judging test code scores near chance; an
LLM judging *"are these the assertions a careful engineer would make?"* is a far
more tractable question. **Judge the intent, verify the code by execution.**

**Derive mocks from reality, not from the model.** Tusk Drift records real
production traces (including outbound DB and third-party calls) and replays them
with dependencies mocked from the recording. Keploy does the same at the network
layer via eBPF. The point for us: letting an LLM invent what a dependency returns
is *the* root cause of tautological tests — the test then asserts exactly what
the mock was configured to say. Derive the mock shape from the type signature or
a recorded trace instead.

**Schema-driven negative-case enumeration.** Keploy derives edge cases
mechanically from the type schema — boundary, type-violation, missing-required,
ordering — and uses the LLM only to fill in plausible values. Deterministic
enumeration plus LLM realization beats asking the LLM "what edge cases exist?"

**Smaller borrowings:**
- **Never surface a test that has not been executed at least once** (Early AI).
- **Tri-state assertion classification** — meaningful / tautological (asserts what
  the mock was told to return) / noise-sensitive (asserts on formatting,
  ordering, timestamps that will flake). Applitools' insight generalized: a naive
  exact comparison produces so many false positives the signal is worthless, so
  you need a tuned tolerance model. Tri-state is far more useful than pass/fail.
- **Cache the verdict**, keyed on a hash of the test plus the code under test;
  re-run the LLM only when that hash changes (Momentic's resolve-once-and-cache).
  Keeps per-CI-run cost near zero.
- **Prioritize by production usage** where trace data exists — weight toward
  functions that are actually hot, not uniformly toward uncovered ones.
- **Generate the regression test from the issue or the fixing commit**, not from
  the function body (Mabl's Jira→scenario input). This is another independent
  oracle source.

Unverifiable vendor claims to ignore: Functionize's "99.97% element recognition",
mabl's "~30% of failures auto-healed", Tusk's "69% merge rate", Applitools'
"99.9999% accuracy". All self-reported, no published methodology.

## 15. How Google made mutation testing affordable — the suppression numbers

Petrović & Ivanković et al., [TSE 2021](https://arxiv.org/abs/2102.11378) —
16,935,148 mutants across 776,740 changelists in 10 languages:

| Measure | Value |
|---|---|
| Mutants ever shown to a developer | **12.5%** of those generated |
| Median mutants per changelist, naive | **820** |
| Median after arid-node suppression + one-per-line | **7** |
| Median actually surfaced | **2** per changelist (99th pct: 43) |
| Hard cap | **≤7 mutants per file** |
| Developer-reported usefulness | **15% → 89%** over six years |

Two mechanisms do the work:

1. **One mutant per line**, chosen at random among applicable operators. In
   **>90%** of lines with multiple mutants, all mutants share the same fate, so
   generating more is nearly pure waste.
2. **Arid-node suppression** — AST nodes whose mutation is almost never
   informative: logging, metrics, debug/assert statements, trivial getters, pure
   delegation, `toString`, string constants in error messages. Google needed 100+
   hand-written rules to reach 89% usefulness, grown from developer feedback on
   66,798 mutants, **plus fuzzy name matching covering 200+ function families** —
   which is why a name-pattern matcher earns its place beside the explicit rules.
   The rule breakdown: 1 heuristic prevents uncompilable mutants, **13 prevent
   equivalent mutants**, 16 prevent "unproductive but killable" ones, 2 prevent
   redundant ones. Preventing an equivalent mutant is cheaper than detecting one. **This step is the difference between 820 and 7 — it matters
   more than the choice of tool.**

[ICSE 2021](https://arxiv.org/abs/2103.07189) (14.73M mutants, 6 years) shows it
changes behaviour: developers exposed to mutants write more tests (Spearman
r=0.9), mutant survivability *fell* over time (r=−0.50), and — the key number —
**70% of 1,502 high-priority real bugs were coupled to at least one mutant that
would have been reported during code review.**

**The ratchet argument** (Infection's design, PHP): gate on **covered-MSI of the
diff**, never on project score. If every merged change clears ≥80% on its own
changed lines, the project converges to ≥80% **without ever running a
full-project mutation run.** That is what makes this affordable, and it is the
answer to "mutation is too slow."

### Per-tool diff scoping — verified flags
| Language | Tool | Diff-scoped invocation |
|---|---|---|
| PHP | Infection | `--git-diff-lines --git-diff-base=origin/main` — **line-level, the strictest** |
| Rust | cargo-mutants | `--in-diff /tmp/pr.diff` |
| C# | Stryker.NET | `--since:origin/main --with-baseline` — full score, partial run |
| JS/TS | StrykerJS | `--incremental` (**not** `--since`; that is Stryker.NET only — the docs conflate these). Reuses ~94% of prior results |
| Java | PIT | `historyInputLocation` — **experimental**, can produce false "still killed". Reliable git scoping needs commercial arcmutate |

### Tool maintenance, verified 2026-11-12
Healthy: StrykerJS (v10.1.1), Stryker.NET (5.1.0), mutmut (3.8.0), cosmic-ray
(v8.9.0), PIT (1.30.2), Infection (0.35.10), cargo-mutants (v27.1.0), Mull (0.38.0).

**Qodo Cover-Agent is abandoned.** The README carries *"This repository is no longer
maintained"*, dated **2025-06-15**; last release v0.3.10, 2025-05-21; ~5.7k stars, AGPL-3.0.
There is no canonical successor fork. The parent company raised a **$70M Series B on
30 March 2026** — [Qodo's own announcement](https://www.qodo.ai/blog/qodo-70m-series-b-shift-to-artificial-wisdom/)
is the primary source ("Today, we are thrilled to announce that Qodo has raised $70
million in Series B funding, bringing total capital raised to $120 million"; led by
Qumra Capital), corroborated independently by
[TechCrunch](https://www.techcrunch.com/2026/03/30/qodo-bets-on-code-verification-as-ai-coding-scales-raises-70m/),
[Globes](https://en.globes.co.il/en/article-1001538970) and
[SiliconANGLE](https://siliconangle.com/2026/03/30/ai-generated-code-verification-startup-qodo-raises-70m/)
— and pivoted to agentic code review, so popularity and funding say nothing about whether
a tool is maintained. Read it as a reference implementation; do not depend on it.
*Both halves re-verified against the repository README and the primary announcement on
2026-11-12.*

**Dead or dying — do not adopt:** `mutatest` (Python, last release 2022),
`go-mutesting` (last commit 2021), and `gremlins` (Go, last commit 2026-03-30,
semi-dormant). **Go is the weakest ecosystem by a wide margin** and our Go
playbook must say so rather than pretend otherwise.

### Blind spots to state out loud
A diff-scoped mutation gate is **a ratchet on new code, not an audit**. It cannot
see: (a) a change in file A destroying coverage of file B (cargo-mutants warns of
this explicitly); (b) deleted assertions in test files — test-only changes run no
mutants at all, so someone removing assertions passes the gate; (c) dependency,
config or fixture changes flipping mutant status (PIT calls this "unproven").
Mitigation: keep an occasional full run off the critical path.

## 16. Property-based testing: real, but oversold

The honest numbers. [PBT-GPT](https://arxiv.org/abs/2307.04346): best config
(GPT-4, two-stage) produced **54.6% valid, 41.7% valid-and-sound** tests with
**only 20.5% property coverage**. [Agentic PBT](https://arxiv.org/abs/2510.09907)
(Anthropic, 984 reports over 100+ Python packages): **56% valid bugs, 32% worth
reporting** — but the agent's own confidence ranking works, lifting the top 21
reports to **86% valid**. Real merged fixes in NumPy, HuggingFace Tokenizers, AWS
Lambda Powertools.

**There is no head-to-head study showing LLM-written property tests beat
LLM-written example tests** under the same budget. PBT-Bench's closest comparison
gives PBT-guided prompting ~10pp more bug recall, and *negative* for some frontier
models. Evaluation is almost entirely Python + Hypothesis.

Google **archived OSS-Fuzz-Gen on 2026-11-10** — the flagship "LLM writes your
fuzz harness" project is read-only, which should temper claims this is productised.

**The durable pattern across property testing, metamorphic testing and fuzzing:**
the LLM proposes the *relation, property or harness*; a deterministic engine
(fuzzer, shrinker, mutation engine) does the search; an LLM or human triages the
result. **Use LLMs for oracle authoring and result triage, never as the oracle.**

**Design consequence:** property-based testing stays an *assess*-tier offering the
advisor can recommend where the stack supports it well (Python/Hypothesis,
JS/fast-check), with its real hit rate stated. It is not a default.

## 17. Flakiness: reruns alone cannot work, and the math says so

**The compounding argument, which drives our whole design.** If a generator emits
1,000 tests each with a 0.1% flake rate, **~63% of PRs will hit at least one
flaky failure** (1 − 0.999¹⁰⁰⁰). To keep PR disruption under 5% across 1,000
tests you need a per-test flake rate below **~5×10⁻⁵**.

Reruns cannot get you there. Detection power is `1 − (1−p)ⁿ`:

| Flake rate | 10 runs | 20 runs | 100 runs |
|---|---|---|---|
| 10% | 65% | 87% | ~100% |
| 5% | 40% | 64% | 99.4% |
| 0.1% | 1% | 2% | 9.5% |

**Therefore the primary gate must be free and static; reruns are secondary.**
(FlakeFlagger needed **10,000 reruns per test** to label ground truth — that is
the cost argument against reruns as a primary oracle.)

### Tier 0 — zero-rerun checks, always run
- **DeFlaker's differential coverage** ([ICSE 2018](https://cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf)):
  if a test fails but its coverage **doesn't intersect the changed code**, the
  test is at fault, not the code. 1,874 flaky tests found across 26 projects at a
  **1.5% false-alarm rate**, with no reruns at all.
- **Static flake smells in the generated test before it ever runs**: unseeded
  `random`/`uuid4`/`Math.random`; `datetime.now()`/`Date.now()` in assertions;
  `sleep` or fixed timeouts; real network or DNS; reliance on dict/set/map
  iteration order; `os.listdir`/`glob` order; locale- or timezone-dependent
  formatting; float equality; assertions on unordered collection order.
- **Size heuristic** ([Google, 4.2M tests](https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html)):
  flake rate by test size is **small 0.5%, medium 1.6%, large 14%**, and the
  relationship with binary size is linear. Meta's floors: **<1% for unit,
  ~10% for some E2E frameworks** — so thresholds must be per-framework, never global.

### Tier 1 — burn-in, three-way rather than N identical
GitHub's [18x flaky-build reduction](https://github.blog/engineering/engineering-principles/reducing-flaky-builds-by-18x/)
came from splitting the retries by *cause class*: naive same-condition retries
caught only **25%** of flaky failures; retrying (1) in the same process,
(2) with a simulated time shift, and (3) on a different host caught **~90%**.
Same budget, far more signal — and it names the cause instead of just flagging.

Add **order randomization** (iDFlakies / `pytest-randomly` / NonDex): run the new
test in isolation, first, last, and in shuffled orders with printed seeds. A flip
that appears only under reordering is a shared-state leak, which is a different
bug from timing flake. Plus an environment sweep over `TZ`, `LANG` (the
Turkish-i case catches case-folding bugs), hash seed, and a clock near midnight,
month-end, Feb 29 or a DST transition.

**Any pass/fail disagreement across identical same-commit runs means reject.**
That is the universal industry definition (Google, CircleCI, Buildkite, Datadog).

### Tier 2 — steady-state scoring and quarantine
Start with Buildkite's **transition count ÷ window** — identical to Apple's flip
rate, O(1), no inference, published thresholds (1 transition in 5 = 0.2). Apple's
entropy score `−Σ p(i)log₂p(i)` is a good second signal. Graduate to a
Beta-binomial posterior and quarantine on the **credible-interval lower bound**,
so a test failing 1 of 2 runs isn't quarantined. Meta's
[PFS](https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/)
refinement — splitting `pb` (bad world state) from `pf` (the test's own
flakiness) — is what stops an infra outage poisoning every test's score.

Starting thresholds from published defaults: **≥10 observations** before
classifying (BuildPulse), **30% failure rate** over a rolling **7-day** window to
quarantine, auto-unquarantine after **30 days clean** (Datadog).

**Quarantine never means delete.** Google found that when a stable test turned
flaky and the cause could be traced, it was **a real production bug 1 in 6 times
(~17%)**. Every vendor keeps running quarantined tests and collecting data.

**Validate the policy against Apple's benchmark**: [ICSE-SEIP 2020](https://2020.icse-conferences.org/details/icse-2020-Software-Engineering-in-Practice/2/Modeling-and-Ranking-Flaky-Tests-at-Apple)
achieved a **44% flakiness reduction at <1% loss in fault detection**. If our gate
rejects tests that would have caught real bugs, it is over-tuned.

### Context numbers worth remembering
- **84% of pass→fail transitions at Google are flakes, not real bugs** (ICSE-SEIP 2017).
- **~1.5% of all Google test runs** are flaky; **~16% of their 4.2M tests** have flaked.
- **Visibility alone** took Spotify from **6% to 4%** flakiness in two months, with
  no other intervention — the cheapest win in the entire literature.
- Spotify's **Odeneye grid** (tests × runs, coloured by result): a failure
  *column* is an infra problem, scattered dots are per-test flakiness. Stops us
  scoring one outage as 400 flaky tests.
- **A PASS is strong evidence; a FAIL is weak evidence.** Both Google's
  [Flake Aware Culprit Finding](https://hackthology.com/pdfs/icst-2023.pdf) and
  Meta's PFS are built on this asymmetry: the oracle may answer FAIL when it
  should have said PASS, but never the reverse.

## 18. Test distribution: shape follows architecture

Cited by `agents/automation-engineer.md`. Recorded here because an agent citing a
consensus it cannot point at is how this project loses its own standard.

The 2026 position is that **"which shape is right" is the wrong question.** The three
shapes each encode an assumption about where defects live:

| Shape | Source | Bulk of tests | Fits |
|---|---|---|---|
| **Pyramid** | Mike Cohn (2009); [Fowler](https://martinfowler.com/articles/practical-test-pyramid.html) | Unit | Backend-heavy, logic-dense code with real units |
| **Trophy** | [Kent C. Dodds](https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications) | Integration, on a base of **static analysis and types** | Frontends, where units are mostly glue |
| **Honeycomb** | [Spotify](https://engineering.atspotify.com/2018/01/testing-of-microservices) | Integration and contract, thin unit layer | Microservices, where bugs live in the contracts |

The durable principles from the pyramid survive all three: test at different
granularities, and the higher you go the fewer you need.

Google's own framing is explicitly anti-dogma —
[*"Pyramid or Crab? Find a testing strategy that fits"*](https://web.dev/articles/ta-strategies).
*Software Engineering at Google*
([Ch. 11](https://abseil.io/resources/swe-book/html/ch11.html)) gives a ratio rather
than a shape: roughly **80% small / 15% medium / 5% large**, presented as a starting
heuristic to adjust from, alongside an explicit warning against coverage mandates.

What changed by 2026: containers and ephemeral environments made integration tests cheap
and fast, contract testing matured, and flake economics made large E2E suites expensive
to keep green (flake rate by size: **small 0.5%, medium 1.6%, large 14%**).

**Design consequence:** testsquad never prescribes a shape. It starts from where this
repo's production defects actually originate — `known-issues.md`, bug-fix commits in the
risk map — and places each test at the **lowest level that can catch that class of
defect**. The right distribution is derived per repo, not imposed.

## 19. Flake causes differ by language — do not reuse one taxonomy

Cited by every stack playbook and by `/testsquad:health`. A shared checklist would be
wrong in two stacks out of three.

### Java — the canonical study
[Luo, Hariri, Eloussi & Marinov, "An Empirical Analysis of Flaky Tests", FSE 2014](https://dl.acm.org/doi/pdf/10.1145/2635868.2635920).
201 flaky-test-fixing commits across 51 Apache projects; **161 were classifiable**, and
every percentage below is out of 161 — commonly misquoted as 201.

| Cause | Share |
|---|---|
| **Async wait** | **45%** (74/161 — the paper rounds down from 46.0%) |
| **Concurrency** | 20% |
| **Test order dependency** | 12% |
| Resource leak | ~6% |
| Network | ~5% |
| Time (timezone/clock) | ~4% |
| IO | ~3% |
| Randomness | ~2% |

Those first three are **77% of all flaky-test fixes**. How they were actually fixed:
**54%** of async-wait flakes by waiting on a real condition instead of `sleep`; **31%**
of concurrency flakes by adding synchronization; **74%** of order-dependency flakes by
cleaning shared state between tests.

### Python — a different distribution entirely
[Gruber et al., "An Empirical Study of Flaky Tests in Python", ICST 2021](https://arxiv.org/abs/2101.09077),
7,571 flaky tests:

| Cause | Share |
|---|---|
| **Order dependency** | **59%** |
| **Test-infrastructure problems** | **28%** (a category absent from Luo's taxonomy) |
| Network, randomness, remainder | ~13% |

**Async wait is almost absent — 3 instances.** A Java-derived checklist applied to a
Python repo would hunt for the wrong thing entirely.

### JavaScript — order dependency nearly vanishes
[Hashemi et al., "An Empirical Study of Flaky Tests in JavaScript", ICSME 2022](https://arxiv.org/abs/2207.01047):
concurrency 21%, async wait 20%, OS 18%, network 13% — **70% between them**.
**Test-order dependency is nearly nonexistent**, almost certainly because Jest isolates
each file in its own process.

### Go — no published breakdown
We could not find one, and the playbook says so rather than inventing numbers.
Practitioner consensus names goroutine leaks and `t.Parallel()` misuse; `-race` and
`-shuffle=on` are the available instruments.

### Order dependency specifically
[iDFlakies (ICST 2019)](https://mir.cs.illinois.edu/marinov/publications/LamETAL19iDFlakies.pdf)
runs the suite in the original order and in several randomized orders. A test that fails
under a shuffled order but passes consistently in isolation or in the original order is
**order-dependent**; one that flips within the *same* order is not. Of 422 flaky tests
it detected, **~50% were order-dependent**. NonDex is the orthogonal tool for Java: it
randomizes under-specified API behaviour such as `HashMap` iteration order — precisely
the assumption a generated test is most likely to bake in.

Full landscape: [Parry, Kapfhammer, Hilton & McMinn, "A Survey of Flaky Tests", TOSEM 2021](https://dl.acm.org/doi/10.1145/3476105).

## 20. Mutation operators: which five, and how to weight them

Cited by `skills/mutate/SKILL.md` and `scripts/arid_nodes.py`, which uses these figures to
choose and weight the operators it emits. Recorded here because an engine whose behaviour
is driven by unsourced numbers is exactly what this project exists not to be.

From Petrović & Ivanković et al.,
[Practical Mutation Testing at Scale, TSE 2021](https://arxiv.org/abs/2102.11378)
(16.9M mutants, 776,740 changelists, 10 languages). Google uses **five operators only**:

| Operator | Share of mutants | Survive | Judged productive |
|---|---|---|---|
| **SBR** statement block removal | 68% | 12.6% | — |
| **UOI** unary operator insertion | 18.5% | 9.5% | **74.5% (lowest)** |
| **LCR** logical connector replacement | 7.7% | 16.3% | — |
| **ROR** relational operator replacement | 4.0% | 14.7% | **84.1% (highest)** |
| **AOR** arithmetic operator replacement | 1.8% | 13.5% | — |

Also from that paper: **~87% of mutant runs kill the mutant**, so only ~13% survive to be
candidates for reporting; and **context-based selection beats uniform random by >40% on
survivability and ~50% on productivity.**

**Why not a larger operator set.** Gopinath et al.,
[On the Limits of Mutation Reduction Strategies, ICSE 2016](https://rahul.gopinath.org/resources/icse2016/gopinath2016on.pdf):
the maximum achievable improvement of **any** reduction strategy over plain random
sampling is about **13%**. Earlier work from the same group
([ISSRE 2015](https://stairs.ics.uci.edu/papers/2015/How_hard_does_mutation_analysis_have_to_be_anyway.pdf))
found a fixed sample of ~1,000 mutants estimates the full-suite score to within about
**±7%**, roughly independent of program size, and that operator-based selection is **not**
superior to uniform random sampling.

**Design consequence:** ship exactly these five, weight selection toward ROR and away from
UOI, and spend the engineering effort on *suppression and diff-scoping* rather than on
exotic operators — the ceiling there is 13%, while suppression is the difference between
820 and 7 mutants.

Two cautions on applying the table:
- The weighting is spent at **operator-choice** time. Ranking survivors by productivity
  again at cap time collapses the mix — a large file then surfaces seven ROR mutants and
  never an SBR one, which is the opposite of the measured distribution.
- **The ≤7 cap is per file and the 99th-percentile figure of 43 is per changelist.** The
  denominators differ; they are not comparable numbers.

## 21. Multi-agent orchestration: the cost knee

Cited by `skills/test/SKILL.md` for the "select 2–4 specialists" rule. These are the
weakest-sourced numbers in this document, and they are labelled as such.

- Cost bottoms out at roughly **4–6 subagents** and rises after; a four-agent system can
  burn **~10x** a single agent, and past ~6 hand-offs roughly **4x the cost for half the
  success rate** — [industry write-up](https://readysolutions.ai/guides/subagent-orchestration-in-production/),
  not a peer-reviewed measurement.
- Under **equal token budgets**, single-agent beat sequential multi-agent on multi-hop
  reasoning (**0.418–0.427 vs 0.379–0.389**) — [OrchestraBench](https://arxiv.org/pdf/2608.05263).
- Failure taxonomy — ambiguous delegation, tool-invocation error, context pollution,
  conflicting sub-agent outputs, premature action — [MAST](https://arxiv.org/pdf/2607.28802).
- A researcher given four dimensions and ~20 fetches exhausted its budget and returned
  **zero salvageable output**, which is why each specialist gets one dimension and a hard
  tool-call budget.

**Treat the cost figures as directional.** They justify "select a few agents, not all of
them"; they do not justify a precise optimum. The squad has **eight roles**: seven agent
files in `agents/`, plus the orchestrator, which is a skill (`skills/test/`) rather than a
subagent because it must be able to ask the user questions that subagents cannot.
