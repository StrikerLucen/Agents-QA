# Stack playbook — JVM (Java / Kotlin / Groovy / Scala)

Reference for a testsquad agent working in a JVM repo. Run the detection block
first, record the answers, then use only the rows that match.

Verified against the sources in `docs/RESEARCH.md` on **2026-11-12**. Where a
version or flag may have moved since, the entry says *verify before relying on
this*.

---

## 1. Detection signals

```bash
ls pom.xml build.gradle build.gradle.kts settings.gradle settings.gradle.kts \
   gradlew mvnw build.sbt 2>/dev/null
```

### Build tool

| Marker | Build tool | Wrapper (always prefer it) |
|---|---|---|
| `pom.xml` | **Maven** | `./mvnw` if present, else `mvn` |
| `build.gradle` (Groovy DSL) | **Gradle** | `./gradlew` |
| `build.gradle.kts` (Kotlin DSL) | **Gradle** | `./gradlew` |
| `settings.gradle{,.kts}` with `include(...)` | Gradle **multi-module** | address modules as `:app:test` |
| `pom.xml` with `<modules>` | Maven **multi-module** | `-pl <module> -am` |
| `build.sbt` | sbt (Scala) | `./sbt` or `sbt` |
| `BUILD.bazel` | Bazel | out of scope here; use the repo's own targets |

Always use the wrapper. `./gradlew` and `./mvnw` pin the build-tool version;
the system `gradle`/`mvn` will silently use a different one and may fail or
behave differently.

```bash
./gradlew --version ; ./mvnw --version
java -version 2>&1          # toolchain mismatch is the #1 "works on my machine"
cat .sdkmanrc .java-version 2>/dev/null
grep -nE "sourceCompatibility|targetCompatibility|jvmToolchain|<maven.compiler" \
     build.gradle* pom.xml 2>/dev/null
```

### Language

| Marker | Language |
|---|---|
| `src/main/java` | Java |
| `src/main/kotlin`, `kotlin("jvm")` plugin, `*.kt` | Kotlin |
| `src/main/groovy`, `*.groovy` | Groovy |
| `src/main/scala`, `build.sbt` | Scala |
| `grails-app/` | Grails (Groovy, Spock-by-default) |

### Test framework

```bash
grep -nE "junit-jupiter|junit:junit|spock-core|kotest|testng|scalatest|mockito|assertj|hamcrest" \
     pom.xml build.gradle build.gradle.kts 2>/dev/null
grep -rln "org.junit.jupiter" src/test | head
grep -rln "^import org.junit.Test\|org.junit.Assert" src/test | head
grep -rln "extends Specification" src/test | head
grep -rln "FunSpec\|StringSpec\|ShouldSpec\|io.kotest" src/test | head
```

| Marker | Framework | Note |
|---|---|---|
| `org.junit.jupiter:junit-jupiter`, imports `org.junit.jupiter.api.Test` | **JUnit 5 (Jupiter)** | needs `test { useJUnitPlatform() }` in Gradle |
| `junit:junit:4.x`, imports `org.junit.Test` / `org.junit.Assert` | **JUnit 4** | may run on JUnit 5 via `junit-vintage-engine` |
| both present | **mixed** — vintage engine in play | write new tests in Jupiter |
| `org.spockframework:spock-core`, `class X extends Specification` | **Spock** (Groovy) | runs on the JUnit platform; `given/when/then` blocks |
| `io.kotest:kotest-runner-junit5` | **Kotest** (Kotlin) | spec styles vary per file — match the file you edit |
| `org.testng:testng` | TestNG | `useTestNG()` in Gradle |
| `org.scalatest::scalatest` | ScalaTest | `sbt test` |

**A common and costly misdetection:** a Gradle build with JUnit 5 dependencies
but no `useJUnitPlatform()` silently runs **zero** Jupiter tests and reports
BUILD SUCCESSFUL. Check for it explicitly:

```bash
grep -n "useJUnitPlatform\|useTestNG\|useJUnit()" build.gradle build.gradle.kts
```

### Framework / stack

| Marker | Stack | Test consequence |
|---|---|---|
| `spring-boot-starter-parent` / `org.springframework.boot` plugin | Spring Boot | `@SpringBootTest` (slow, full context) vs `@WebMvcTest`/`@DataJpaTest` (slices, fast) |
| `quarkus-maven-plugin` / `io.quarkus` | Quarkus | `@QuarkusTest`, continuous testing mode |
| `io.micronaut` | Micronaut | `@MicronautTest` |
| `grails-app/` | Grails | Spock integration/unit specs, `grails test-app` |
| `android` plugin / `app/build.gradle` with `com.android.application` | Android | `testDebugUnitTest` (JVM) vs `connectedAndroidTest` (device) — very different costs |

---

## 2. Test commands

### Run only tests related to changed files — use this in hooks

A full suite in a synchronous hook blocks every edit. This is the single
most-reported mistake. JVM suites are the slowest of the four stacks here; a
`mvn test` on a mid-sized Spring project is minutes. Always scope.

The JVM has **no built-in import-graph test selector** equivalent to
`jest --findRelatedTests`. Scope by class name and module.

```bash
# --- Maven: -Dtest= selects by class/method. Comma-separated, wildcards allowed.
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- '*.java' '*.kt' '*.groovy')
CLASSES=$(echo "$CHANGED" | xargs -n1 basename 2>/dev/null | sed 's/\.\(java\|kt\|groovy\)$//' | sort -u)
PATTERN=$(echo "$CLASSES" | sed 's/$/Test,&Tests,&Spec,&IT/' | paste -sd, -)

./mvnw test -Dtest="$PATTERN" -DfailIfNoSpecifiedTests=false

# `-DfailIfNoSpecifiedTests=false` is MANDATORY in a hook: without it, a diff
# whose classes have no matching test class fails the build.

# Single class / single method / pattern
./mvnw test -Dtest=CartServiceTest
./mvnw test -Dtest='CartServiceTest#addsItem'
./mvnw test -Dtest='CartServiceTest#adds*'
./mvnw test -Dtest='Cart*Test,Order*Test'

# Multi-module: restrict to the modules the diff touched, plus their deps
./mvnw test -pl payments,orders -am -Dtest="$PATTERN" -DfailIfNoSpecifiedTests=false

# --- Gradle: --tests selects by fully-qualified or wildcard class/method
./gradlew test --tests '*CartServiceTest'
./gradlew test --tests '*CartServiceTest.addsItem'
./gradlew test --tests '*Cart*' --tests '*Order*'
./gradlew :payments:test --tests '*CartServiceTest'

# Gradle, generated from the diff
ARGS=$(echo "$CLASSES" | sed "s/^/--tests '*/; s/$/*'/" | paste -sd' ' -)
eval ./gradlew test $ARGS

# Gradle is incremental by default: a `./gradlew test` with no changes is
# UP-TO-DATE and nearly free. Never pass --rerun-tasks in a hook.
```

Also always run the directly-changed test classes themselves.

### Full suite / useful flags

| | Maven | Gradle |
|---|---|---|
| Full unit suite | `./mvnw test` | `./gradlew test` |
| Full build + tests | `./mvnw verify` | `./gradlew build` |
| Integration tests (Failsafe `*IT`) | `./mvnw verify -DskipTests=false` | `./gradlew integrationTest` (if the task exists) |
| Skip tests | `-DskipTests` (compiles them) / `-Dmaven.test.skip=true` (does not) | `-x test` |
| Offline | `-o` | `--offline` |
| Parallel | `-T 1C`, plus surefire `parallel`/`forkCount` | `--parallel`, plus `test { maxParallelForks = N }` |
| Serial (diagnostic) | `-Dsurefire.forkCount=1 -Dsurefire.parallel=none` | `test { maxParallelForks = 1; forkEvery = 0 }` |
| Fail fast | `-ff` | `--fail-fast` |
| Quiet | `-q` | `-q` |
| Rerun only failures | *(no built-in; read `target/surefire-reports/`)* | *(no built-in; verify before relying on this)* |
| By tag/group | `-Dgroups=fast -DexcludedGroups=slow` | `test { useJUnitPlatform { includeTags 'fast' } }` |

Results land in `target/surefire-reports/*.xml` (Maven, unit),
`target/failsafe-reports/` (Maven, IT), `build/test-results/test/*.xml` and
`build/reports/tests/test/index.html` (Gradle). These are JUnit XML and are the
machine-readable source of truth — parse them rather than scraping stdout.

Spring Boot specifically: prefer slice annotations over `@SpringBootTest`.
A full-context test is dramatically slower to start than a `@WebMvcTest` —
measure the gap in your own repo before deciding (`./gradlew test --profile`, or
`logging.level.org.springframework.test.context.cache=DEBUG` to watch the
context cache). Context reuse
breaks whenever a test uses `@MockBean`/`@MockitoBean` or `@DirtiesContext`,
causing the context cache to thrash. Grep for these when a suite is slow:

```bash
grep -rln "@DirtiesContext\|@MockBean\|@MockitoBean\|@TestPropertySource" src/test | head -30
```

---

## 3. Coverage

Target format that `scripts/normalize_coverage.py` parses: **JaCoCo XML**
(`jacoco.xml`). It is auto-detected from the `<report>` root element.

JaCoCo reports a `BRANCH` counter alongside `LINE` — the normalizer reads both.
But **the XML report is not generated by default**; you must enable it.

### Maven

```xml
<!-- pom.xml -->
<plugin>
  <groupId>org.jacoco</groupId>
  <artifactId>jacoco-maven-plugin</artifactId>
  <version>0.8.12</version> <!-- verify the current version before pinning -->
  <executions>
    <execution><id>prepare</id><goals><goal>prepare-agent</goal></goals></execution>
    <execution><id>report</id><phase>test</phase><goals><goal>report</goal></goals></execution>
  </executions>
</plugin>
```

```bash
./mvnw test jacoco:report
# or without touching the pom:
./mvnw org.jacoco:jacoco-maven-plugin:prepare-agent test org.jacoco:jacoco-maven-plugin:report
```

Output: `target/site/jacoco/jacoco.xml`, `target/site/jacoco/index.html`,
`target/jacoco.exec` (binary, intermediate).

Multi-module: each module writes its own `target/site/jacoco/jacoco.xml`.
Normalize each separately, or aggregate with the `report-aggregate` goal in a
dedicated reporting module.

### Gradle

```kotlin
// build.gradle.kts
plugins { jacoco }
tasks.test { finalizedBy(tasks.jacocoTestReport) }
tasks.jacocoTestReport {
    dependsOn(tasks.test)
    reports { xml.required.set(true); html.required.set(true); csv.required.set(false) }
}
```

```bash
./gradlew test jacocoTestReport
```

Output: `build/reports/jacoco/test/jacocoTestReport.xml`,
`build/reports/jacoco/test/html/index.html`, `build/jacoco/test.exec`.

**`xml.required.set(true)` is essential** — Gradle's JaCoCo default emits HTML
only, and a missing XML report is the most common reason a JVM coverage step
appears to "produce nothing".

Branch coverage: JaCoCo measures it unconditionally via bytecode
instrumentation; no extra flag. But note two JaCoCo-specific distortions worth
reporting rather than hiding:

- **Kotlin** generates synthetic null-check and default-argument branches.
  Raw branch coverage on Kotlin reads artificially low. Filter with
  `jacoco { excludes = listOf(...) }` or accept the distortion and say so.
- **Lombok** generates `equals`/`hashCode`/`toString` branches. Add
  `lombok.addLombokGeneratedAnnotation = true` to `lombok.config`; JaCoCo
  excludes `@lombok.Generated` members automatically.

Normalize:

```bash
python3 scripts/normalize_coverage.py target/site/jacoco/jacoco.xml --out .testsquad/coverage.json
python3 scripts/normalize_coverage.py build/reports/jacoco/test/jacocoTestReport.xml --out .testsquad/coverage.json
```

The normalizer builds paths as `<package name>/<sourcefile name>`, e.g.
`com/acme/cart/CartService.java`. To join with `git diff --name-only` output
(`src/main/java/com/acme/cart/CartService.java`), strip the source root on the
git side or match on suffix.

### Kover (Kotlin-first alternative)

If the repo uses `org.jetbrains.kotlinx.kover`, it can emit JaCoCo-compatible
XML and handles Kotlin's synthetic branches better:

```bash
./gradlew koverXmlReport     # build/reports/kover/report.xml
```

Confirm the emitted dialect before feeding it to the normalizer — Kover's XML is
JaCoCo-shaped but verify before relying on this.

---

## 4. Mutation testing

**Tool: PIT (pitest) 1.30.x — healthy as of 2026-11-12** (1.30.2 verified).
Mature, fast (bytecode-level, coverage-driven), the best-supported mutation
ecosystem after StrykerJS.

### Install

```xml
<!-- Maven -->
<plugin>
  <groupId>org.pitest</groupId>
  <artifactId>pitest-maven</artifactId>
  <version>1.30.2</version>
  <dependencies>
    <dependency>
      <groupId>org.pitest</groupId>
      <artifactId>pitest-junit5-plugin</artifactId>
      <version>1.2.1</version> <!-- verify the current version -->
    </dependency>
  </dependencies>
</plugin>
```

The **`pitest-junit5-plugin` dependency is required for JUnit 5**. Without it
PIT finds no tests and reports a mutation score of 0 with no error that reads
like one. For Kotest use `pitest-kotest` *(verify availability before relying on
this)*; Spock runs on the JUnit platform and works via the JUnit 5 plugin.

```kotlin
// Gradle
plugins { id("info.solidsoft.pitest") version "1.15.0" }  // verify current version
pitest {
    junit5PluginVersion.set("1.2.1")
    targetClasses.set(listOf("com.acme.*"))
    outputFormats.set(listOf("XML", "HTML"))
    timestampedReports.set(false)
}
```

### Diff-scoped invocation — and the honest caveat

> PIT's incremental mode is `historyInputLocation` / `historyOutputLocation`.
> **It is experimental and can produce false "still killed" results** — a mutant
> recorded as killed in a previous run may be reported as killed again even
> though the test that killed it has changed or been deleted. Do not gate a merge
> on an incremental PIT score alone.
>
> **Real git-based scoping requires the commercial arcmutate plugin**
> (`-Dfeatures="+GIT"` / `+GIT(from[origin/main])`). It is not free and not
> bundled.

```bash
# Experimental incremental mode — faster, but see the caveat above
./mvnw test-compile org.pitest:pitest-maven:mutationCoverage \
  -DhistoryInputLocation=target/pit-history.bin \
  -DhistoryOutputLocation=target/pit-history.bin \
  -DtargetClasses='com.acme.*' \
  -DoutputFormats=XML,HTML -DtimestampedReports=false
```

The history file must be **persisted in CI** or incremental mode degrades to a
full run every time — cache `target/pit-history.bin` the same way you would a
Stryker incremental file.

**The reliable free approach: scope by class list from the diff.** This is
deterministic and has none of the history-mode caveats.

```bash
CHANGED=$(git diff --name-only --diff-filter=ACMR origin/main...HEAD -- 'src/main/**/*.java' 'src/main/**/*.kt')
TARGETS=$(echo "$CHANGED" \
  | sed 's|^src/main/\(java\|kotlin\)/||; s|\.\(java\|kt\)$||; s|/|.|g' \
  | paste -sd, -)

./mvnw test-compile org.pitest:pitest-maven:mutationCoverage \
  -DtargetClasses="$TARGETS" \
  -DtargetTests='com.acme.*' \
  -DoutputFormats=XML,HTML -DtimestampedReports=false \
  -DmutationThreshold=80

# Gradle equivalent
./gradlew pitest -Dpitest.targetClasses="$TARGETS"
```

Output: `target/pit-reports/mutations.xml` + `index.html` (Maven),
`build/reports/pitest/` (Gradle). `timestampedReports=false` keeps the path
stable so CI can find it.

Useful options: `-Dthreads=4`, `-DtimeoutConstant=4000`,
`-Dmutators=DEFAULTS` (or `STRONGER` for a harsher set),
`-DexcludedClasses='com.acme.generated.*'`,
`-DavoidCallsTo=java.util.logging,org.slf4j,org.apache.log4j`.

### Make it affordable — Google's rules (RESEARCH §15)

Google generated 16.9M mutants across 10 languages and showed developers
**12.5%**. Median per changelist: **820 → 7**. Median surfaced: **2** (99th
percentile 43). Hard cap: **≤7 mutants per file**. Developer-reported usefulness
rose **15% → 89%** over six years, almost entirely through suppression rules.

1. **One mutant per changed line.** In >90% of lines with multiple applicable
   mutants, all share the same fate.
2. **Arid-node suppression** — never mutate: logging (`-DavoidCallsTo` covers
   slf4j/log4j/JUL), metrics, `assert` statements, trivial getters/setters,
   `equals`/`hashCode`/`toString`, pure delegation, builder chains, string
   constants in exception messages, Lombok-generated members (JaCoCo and PIT
   both honour `@lombok.Generated` when `lombok.addLombokGeneratedAnnotation`
   is set). **This step is the difference between 820 and 7 — it matters more
   than the choice of tool.**
3. **Gate on covered-MSI of the diff, never project score.**

And the payoff number: **70% of 1,502 high-priority real bugs at Google were
coupled to at least one mutant that would have been reported during code
review.**

Blind spots to state in any report: a change in class A can destroy coverage of
class B invisibly; a test-only change runs **no mutants at all**, so deleted
assertions pass the gate; and PIT explicitly reports dependency/config-driven
status changes as **"unproven"**. Keep an occasional full run off the critical
path.

---

## 5. E2E / integration options

| Tool | Use when | Honest cost |
|---|---|---|
| **Testcontainers (JVM)** | The idiomatic JVM integration seam — real Postgres/Kafka/Redis/Elasticsearch in a JUnit lifecycle | Docker daemon required; real container startup, usually seconds — time it in your own suite. Reusable containers (`testcontainers.reuse.enable=true`) and `@Container static` cut this materially |
| **Spring Boot slices** (`@WebMvcTest`, `@DataJpaTest`, `@JsonTest`) | Fast integration without a full context | Each distinct slice config is a separate cached context; too many variants and the cache thrashes |
| **`@SpringBootTest(webEnvironment = RANDOM_PORT)` + `TestRestTemplate`/`WebTestClient`** | Full-stack HTTP in-process | Slowest unit-adjacent test you can write; use sparingly |
| **WireMock** | Stubbing a third-party HTTP dependency, including fault/latency injection | Another server lifecycle to manage; stubs drift from the real API unless generated from a spec |
| **MockWebServer** (OkHttp) | Lighter WireMock for simple cases | No record/replay, no standalone mode |
| **Spring Cloud Contract / Pact** | Consumer-driven contracts between services | Real organizational cost — both sides must adopt it |
| **H2/HSQLDB in-memory** | Fast DB tests | **Dialect divergence from Postgres/MySQL is a bug factory.** Prefer Testcontainers; if H2 is already in use, flag it rather than extending it |
| **Playwright for Java / Selenium** | Browser E2E | Playwright is the better choice for new work; Selenium only where a grid already exists. 10%-flake tier per Meta's floors |
| **ArchUnit** | Architectural rules as tests (layering, naming, no-cycles) | Cheap and deterministic — a good early roadmap item |
| **jqwik** (Java) / **Kotest property testing** | Property-based testing | **Assess-tier, not a default** (RESEARCH §16): no head-to-head evidence that LLM-written property tests beat LLM-written example tests under equal budget |

**Derive mocks from reality, not from the model** (RESEARCH §14). Letting an LLM
invent what a dependency returns is *the* root cause of tautological tests — the
test then asserts exactly what the mock was told to say. Derive mock shapes from
the interface signature, an OpenAPI/Avro schema, or a recorded WireMock mapping.

Mockito note: `Mockito.verify` on a mock the test itself configured proves
nothing about production behaviour. Prefer state assertions; treat
verify-only tests as a flag.

---

## 6. Flake taxonomy — Java / JVM

Per-language distributions differ sharply; a shared checklist would be wrong.
For Java the reported breakdown is:

| Cause | Share |
|---|---|
| **Async wait** | **45%** |
| Concurrency | **20%** |
| Order dependency | **12%** |

Java's dominant flake is **waiting for something that has not happened yet** —
`Thread.sleep` as a synchronization primitive, a future/callback polled without
a condition, a Spring context or embedded server assumed ready, an async event
listener assumed to have fired. This is the opposite of Python (59% order
dependency) and unlike JavaScript (async 20%, order ~0%).

### Detection commands

```bash
# NonDex — randomizes the iteration order of underdetermined JDK APIs
# (HashMap/HashSet iteration, Class.getDeclaredMethods, File.list, ...).
# This is the Java-specific detector: it finds assumptions on orders the JDK
# never promised, which normal reruns will never surface.
./mvnw edu.illinois:nondex-maven-plugin:2.1.1:nondex -Dtest=CartServiceTest
./mvnw edu.illinois:nondex-maven-plugin:2.1.1:nondex          # whole module
# verify the current plugin version before pinning

# Order dependency (12% — real but third)
./mvnw test -Dsurefire.runOrder=random
./mvnw test -Dsurefire.runOrder=reversealphabetical
# Gradle / JUnit 5 — method and class order randomization
./gradlew test -Djunit.jupiter.testmethod.order.default=org.junit.jupiter.api.MethodOrderer\$Random
./gradlew test -Djunit.jupiter.testclass.order.default=org.junit.jupiter.api.ClassOrderer\$Random

# Isolation: does it pass alone?
./mvnw test -Dtest=CartServiceTest#flakyMethod
./gradlew test --tests '*CartServiceTest.flakyMethod'

# Serial vs parallel — isolates the 20% concurrency bucket
./mvnw test -Dsurefire.forkCount=1 -Dsurefire.parallel=none
./mvnw test -Dsurefire.forkCount=1C -Dsurefire.parallel=classes

# Repeat a test
# JUnit 5: @RepeatedTest(20) on the method, or
./gradlew test --tests '*flakyMethod*' --rerun-tasks   # once per invocation; loop in shell

# Environment sweep
./mvnw test -Duser.timezone=Pacific/Kiritimati -Duser.language=tr -Duser.country=TR
./mvnw test -Duser.timezone=UTC -Dfile.encoding=UTF-8
```

### Static flake smells — check before the test ever runs (Tier 0)

```bash
grep -rn "Thread.sleep" src/test | head -40          # the single biggest Java flake source
grep -rn "new Random()\|UUID.randomUUID()\|System.currentTimeMillis()\|Instant.now()\|LocalDate.now()" src/test | head -40
grep -rn "HashMap\|HashSet" src/test | grep -i "assert\|expect" | head
grep -rn "\.get()" src/test | grep -i "future\|completable" | head
grep -rn "@Order\|@TestMethodOrder\|@FixMethodOrder" src/test | head
grep -rn "static " src/test | grep -vE "static (final|class|import|void main)" | head
```

Specific smells: `Thread.sleep(n)` anywhere · unseeded `new Random()` ·
`Instant.now()`/`LocalDate.now()` in an assertion (inject a `Clock` instead) ·
`assertEquals` on `HashMap.toString()` or `HashSet` iteration order ·
`CompletableFuture.get()` without a timeout · non-final mutable `static` fields
in a test class · `@DirtiesContext` missing after a test mutates a shared bean ·
float/double equality without a delta · default-locale `String.format` /
`toUpperCase()` (the Turkish-i case) · real network or DNS · fixed port numbers
(use `0`/`RANDOM_PORT`) · `File.createTempFile` without cleanup.

The fix for the 45% async bucket is almost always **Awaitility**, not a longer
sleep:

```java
await().atMost(5, SECONDS).untilAsserted(() -> assertThat(repo.count()).isEqualTo(1));
```

And for time, inject `java.time.Clock` rather than calling `now()`:

```java
Clock fixed = Clock.fixed(Instant.parse("2026-01-01T00:00:00Z"), ZoneOffset.UTC);
```

**Any pass/fail disagreement across identical same-commit runs means reject.**
A test that does not pass 5 of 5 runs is discarded (Meta's TestGen-LLM rule).

---

## 7. Production-danger signals

```bash
# Spring profile pointing at prod — in config, CI, or a test annotation
grep -rn "spring.profiles.active" --include='*.yml' --include='*.yaml' --include='*.properties' .
grep -rn "@ActiveProfiles" src/test | grep -viE "test|local|dev|integration"
ls src/main/resources/application-prod*.yml src/main/resources/application-production*.yml 2>/dev/null
ls src/test/resources/application*.yml src/test/resources/application*.properties 2>/dev/null

# Real datasource URLs
grep -rnE "jdbc:(postgresql|mysql|oracle|sqlserver)://" --include='*.yml' --include='*.yaml' --include='*.properties' --include='*.java' --include='*.kt' . \
  | grep -vE "localhost|127\.0\.0\.1|::1|testcontainers|tc:|h2:|hsqldb"

# Destructive schema handling
grep -rn "hibernate.ddl-auto\|hbm2ddl.auto" --include='*.yml' --include='*.properties' .
grep -rn "flyway.clean\|cleanDisabled\|liquibase.drop" --include='*.yml' --include='*.properties' .
```

| Red flag | Why |
|---|---|
| `spring.profiles.active=prod` reachable from a test, or an `application-prod.yml` with no test override | the suite boots against production datasources, caches and message brokers |
| `src/test/resources/` has **no** `application.yml` / `application-test.yml` | tests inherit `src/main/resources/application.yml` verbatim — whatever it points at |
| `@ActiveProfiles("prod")` or `@ActiveProfiles` absent on a `@SpringBootTest` | same |
| `spring.jpa.hibernate.ddl-auto: create-drop` with a non-test JDBC URL | **drops the schema of whatever database it is pointed at** |
| `spring.flyway.clean-on-validation-error: true` or a Flyway `clean` in a test | wipes the target database |
| A JDBC/Kafka/Redis URL with a real host in `application.yml`, no override in test | one profile flip from live |
| `@SpringBootTest` with `@AutoConfigureTestDatabase(replace = NONE)` | explicitly opts out of the embedded DB — points at the configured real one |
| `spring.mail` / `MailSender` not replaced by a mock | real emails |
| Payment/SMS keys without `test`/`sandbox` in the value | live-money side effects |
| `-Dspring.config.location` in CI pointing outside the repo | the config you reviewed is not the config that ran |

Assert the guard, don't assume it:

```java
@BeforeAll
static void refuseProduction(@Autowired Environment env) {
    String url = env.getProperty("spring.datasource.url", "");
    if (!url.isEmpty() && !url.matches(".*(localhost|127\\.0\\.0\\.1|tc:|h2:|hsqldb).*")) {
        throw new IllegalStateException("Refusing to run tests against " + url);
    }
}
```

---

## 8. House conventions to match

```bash
find src/test -name '*Test.java' -o -name '*Tests.java' -o -name '*IT.java' \
  -o -name '*Spec.groovy' -o -name '*Test.kt' | head -30
ls src/test/resources src/integrationTest src/testFixtures 2>/dev/null
grep -rn "includes\|excludes\|testClassesDir\|sourceSets" build.gradle* 2>/dev/null | head
grep -n "surefire\|failsafe" -A 10 pom.xml | head -40
```

| Convention | Looks like |
|---|---|
| Standard Maven/Gradle layout | `src/main/java/com/acme/CartService.java` → `src/test/java/com/acme/CartServiceTest.java` — **same package, mirrored tree** |
| Unit vs integration naming | Surefire runs `*Test`/`*Tests`/`Test*`; Failsafe runs `*IT`/`IT*`/`*ITCase`. **The suffix decides which phase runs it** — a misnamed integration test runs in `mvn test` |
| Gradle separate source set | `src/integrationTest/java` + a custom `integrationTest` task |
| Kotlin | `src/test/kotlin/...`, `CartServiceTest.kt`, often backtick-quoted test names: ``fun \`adds item to cart\`()`` |
| Spock | `src/test/groovy/...`, `CartServiceSpec.groovy`, `given:/when:/then:` blocks, `where:` data tables |
| Kotest | `src/test/kotlin/...`, `CartServiceSpec.kt`, spec style varies per file — **match the file you edit** |

| Concern | Usual location |
|---|---|
| Test resources / config | `src/test/resources/` — `application-test.yml`, `logback-test.xml` |
| Shared test helpers | `src/testFixtures/` (Gradle `java-test-fixtures` plugin), or a `test-support` module |
| Object mothers / builders | `src/test/java/.../support/`, `.../fixtures/`, `CartMother.aCart().withItem(...).build()` |
| Static fixture data | `src/test/resources/fixtures/*.json`, loaded with Jackson or `ClassPathResource` |
| Testcontainers base class | `AbstractIntegrationTest` with `@Container static` fields, extended by every IT |
| WireMock stubs | `src/test/resources/mappings/` + `__files/` |
| Spring test slices | one base class per slice config, to keep the context cache small |

Mocking idiom: **Mockito** is the default (`@Mock`/`@InjectMocks` +
`MockitoExtension`, or `mock()`/`when()`/`verify()`); Spring adds `@MockBean`
(deprecated in recent Boot in favour of `@MockitoBean` — *verify which your Boot
version uses*). Kotlin repos often use **MockK** (`mockk()`, `every {}`,
`verify {}`) because Mockito struggles with final classes. Spock has mocking
built in (`Mock()`, `1 * service.call(_)`). Assertions: **AssertJ**
(`assertThat(x).isEqualTo(y)`) dominates new code; Hamcrest and bare JUnit
assertions appear in older code — match the file.

---

## 9. Static analysis + type checking

The JVM compiler is the type checker, so the cheap wins here are compile-time
plugins and bytecode analyzers. Rank these first on any roadmap — they are
deterministic and run in seconds to low minutes.

```bash
# Compile only — the baseline check
./mvnw -q compile test-compile
./gradlew compileJava compileTestJava -q
./gradlew compileKotlin compileTestKotlin -q

# ErrorProne — compile-time bug patterns. The highest-value JVM static tool.
# Catches: == on boxed types, format-string mismatch, unused return values,
# mutable constants, missing @Override, misused Optional, incorrect Mockito usage.
./mvnw compile -Perrorprone
./gradlew compileJava   # with net.ltgt.errorprone plugin applied

# SpotBugs — bytecode analysis, post-compile
./mvnw com.github.spotbugs:spotbugs-maven-plugin:check
./gradlew spotbugsMain spotbugsTest
# reports: target/spotbugsXml.xml  |  build/reports/spotbugs/main.xml

# Checkstyle / PMD — style + simple correctness
./mvnw checkstyle:check pmd:check
./gradlew checkstyleMain pmdMain

# Kotlin
./gradlew detekt        # build/reports/detekt/detekt.xml
./gradlew ktlintCheck

# Dependency / supply chain
./mvnw org.owasp:dependency-check-maven:check
./gradlew dependencyCheckAnalyze
./gradlew dependencyUpdates
```

| Tool | What it buys | Roadmap tier |
|---|---|---|
| `compile` + `test-compile` | catches the whole class of errors other stacks need a type checker for | 0 — free, already in the build |
| **ErrorProne** (+ **NullAway**) | real bug patterns at compile time; NullAway gives Java practical null safety | 1 — highest value per hour |
| **SpotBugs** + `findsecbugs` | bytecode-level bug and security patterns | 1 |
| **ArchUnit** | layering/cycle/naming rules enforced as ordinary tests | 2 |
| **Checkstyle / PMD** | style + simple smells; high noise, low signal unless tuned | 3 — tune hard or skip |
| **detekt / ktlint** (Kotlin) | the Kotlin equivalents of the above | 1 for Kotlin repos |

Config to read before trusting the output:

```bash
grep -rn "failOnError\|failsOnError\|ignoreFailures\|severity" \
     pom.xml build.gradle build.gradle.kts config/checkstyle/*.xml 2>/dev/null
grep -rn "@SuppressWarnings\|@SuppressFBWarnings\|// NOPMD\|@Suppress(" src/main | wc -l
```

`ignoreFailures = true` means the tool runs and reports nothing actionable — the
build is green regardless. A large and growing suppression count means findings
are being silenced rather than fixed; report the count and its trend, not a
clean run.

Compiler flags worth proposing early (cheap, high yield):

```
-Xlint:all -Werror          # javac
-Xjsr305=strict             # kotlinc, for Java interop nullability
--release 21                # verify the toolchain the repo actually targets
```
