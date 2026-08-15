# old-coder: is evidence-first development worth adopting?

- **Date:** 2026-08-15
- **Status:** open — recommendation is "adopt in part", see [Conclusion](#conclusion--next-step)

## Question

[AmazingAng/old-coder](https://github.com/AmazingAng/old-coder) is a single
Agent Skill built on an Uncle Bob line: *don't read the code your agents
write — surround them with constraints instead.* The human approves a **SPEC**
before any code exists and reads an **EVIDENCE** report afterwards; in between
the agent runs a **GAUNTLET** of tests, types, lint, changed-line coverage,
mutation testing, property tests, real execution, supply-chain and secrets
scans, and suite-health checks.

Three things to establish:

1. What does it actually do, mechanically, stripped of the pitch?
2. Where does it duplicate what we already run across `sediment`,
   `life-and-times`, `acs-agent-skills` and this repo?
3. Does it work? Not "does it sound rigorous" — does a change built under it
   catch defects a change built under our current standards would ship?

Question 3 is the one nobody answers, so most of the effort went there: a
sandbox, a bug corpus, and measurements. Everything below is reproducible
from [`old-coder-benchmark/`](old-coder-benchmark/INDEX.md).

## What old-coder actually is

One `SKILL.md` (305 lines) plus three reference files and a worked demo. No
code to install, no runtime — it is a prompt-shaped process contract. That
matters for adoption cost: it is markdown, so it costs nothing to try and
nothing to remove.

The loop:

```
SPEC → (human approves the spec, not the code) → RED → GREEN → REFACTOR → GAUNTLET → EVIDENCE
                                                  ↑______________________|
                                                      per behaviour
```

The parts that are genuinely well-designed, and that I had not seen stated
this precisely anywhere else:

- **Trust is relocated, and the skill says what that does and doesn't buy.**
  It states plainly that the gauntlet "cannot show the spec expresses
  everything that matters, and it is not self-authenticating, because a
  checker can be unsound and a mapping can claim more than it demonstrates."
  It is unusually honest for a document that is also selling a method.
- **"An answer to a question is not an approval."** If you asked the human
  something, their answer is an *input* to the spec and changes it, so any
  approval you held is approval of a document that no longer exists. This is a
  real failure mode of agent workflows and I have not seen it named elsewhere.
- **Fail-closed checkers.** Home-grown gates (grep checks, custom scripts)
  must fail closed and must be proven able to fail via a negative control
  before their pass is trusted. The dangerous failure is a layer that prints
  "pass" because it silently broke.
- **Anti-gaming rules.** Never weaken a test to reach green; never edit test
  and implementation in the same step; never report a layer you didn't run;
  never chase the coverage number.
- **Tier calibration.** Tier 1 typo → suite + lint. Tier 3 (money, auth, data
  loss, concurrency, public API) → failure model first, then the full stack.

The weak part is `Independent verification`, which the skill itself labels
experimental and backs with a single case study. Ignore it for now.

## What we already have

Our practice is spread across four repos, and the overlap with old-coder is
real but partial.

| Where | What it already says | old-coder equivalent | Verdict |
|---|---|---|---|
| `sediment/docs/standards/testing.md` | failing test first; no done with red/absent tests; risk-ordered coverage; unit/integration boundary; no test weakened to pass | RED step, "failing gauntlet blocks done", anti-gaming rule 1 | **Duplicate.** Ours is a 5-rule subset of theirs, minus the spec artifact. |
| `sediment/docs/standards/code-review.md` | correctness/edge-case checklist, idempotency, fail-safe dedupe, external-format validation | Tier 3 failure model | **Complementary.** Ours is domain-specific and better targeted; theirs is generic and asks you to *generate* the list per change. |
| `sediment/docs/standards/security.md` | deny-list at capture layer, no raw media synced, secrets never committed, retention policy | supply-chain & secrets layer | **Ours is stronger.** old-coder's is a generic "run pip-audit and grep for keys". |
| `sediment/docs/decisions/` (ADRs) | non-trivial decisions recorded | — | **No equivalent.** old-coder has no memory across tasks. |
| `life-and-times/docs/engineering.md` | TDD; govulncheck, CodeQL, Dependabot, gofmt/vet gates, race detector | gauntlet layers | **Duplicate, and ours is enforced in CI** rather than asserted in a report. |
| `life-and-times/docs/superpowers/specs/` | dated design doc written before implementation, one per substantial change | SPEC step | **Duplicate** — and this is the single most valuable piece of old-coder (see results). We already have the convention; it just isn't repo-wide. |
| `acs-agent-skills` | the skill library itself | old-coder ships as a skill | **Distribution match.** It would drop into `skills/old-coder/` unchanged. |
| This repo | setups, research, agent docs | — | No overlap; this is where the note lives. |

Two structural differences worth naming, because they decide *how* to adopt
rather than *whether*:

- **Ours are per-repo standards; old-coder is a per-task protocol.** A repo
  standard is read once and drifts; a per-task protocol re-imposes itself on
  every change. They layer — one does not replace the other.
- **Ours produce no artifact.** `testing.md` says "write the failing test
  first" and nothing checks that you did. old-coder's output is two documents
  a human actually reads. That is the part we do not have.

## The experiment

### Design

Everything is in [`old-coder-benchmark/`](old-coder-benchmark/INDEX.md) and
reruns with one command.

I built a module in the domain `sediment`'s own testing standard names as risk
#1 — *"dedupe logic (Screenpipe vs. Meetily overlap) — silent data loss or
duplication here is the most likely real bug"*. `build_timeline()` normalizes
capture segments to UTC, merges same-source overlaps, and drops Screenpipe
blocks that Meetily already covers past a threshold. 77 lines. Real edge cases:
half-open intervals, touching boundaries, union coverage, timezone
normalization, idempotency, input immutability.

Then, in this order, so nothing is right by construction:

1. **`spec.md`** — 15 scenarios + 4 Must-NOTs, written first.
2. **The implementation** — written to the spec.
3. **Three test suites**, each a different process regime:
   - **A — ad-hoc TDD.** What you write when the rule is only "TDD is not
     optional" and there is no spec artifact. Happy paths, obvious edges, one
     validation check. 8 tests, 60 lines. *This is our current sediment standard.*
   - **B — spec-driven.** One test per spec scenario and per Must-NOT. 21
     tests, 158 lines. *This is old-coder's SPEC phase alone, no gauntlet.*
   - **C — spec + gauntlet.** B, plus a test for every mutant that survived
     `mutmut` against B, plus 8 hypothesis properties. +14 tests, +207 lines.
     *This is the full old-coder loop.*
4. **A 17-bug corpus**, written from `spec.md` and never from the test suites.
5. **A harness** that applies each bug, runs each configuration, and records
   which go red.

### Guarding the measurement

Two checks, because the first version of this experiment produced a wrong
answer and I only caught it by applying old-coder's own advice about
negative controls:

- **Equivalent-mutant screening.** Two bugs escaped every configuration. Rather
  than report them as misses, I differential-tested each against the original
  over 20,000 randomized inputs. Both are **equivalent mutants** — provably
  unobservable given that the authoritative list is merged before coverage is
  computed. Reported detection rates are against the **15 real defects**, not 17.
- **A negative control on the screening tool itself.** My first generator only
  emitted well-formed UTC segments, so it declared five *validation* bugs
  "equivalent" simply because it never produced input that could trigger them.
  The second version emits naive datetimes, zero-length intervals, unknown
  sources, mixed timezones **drawn per field rather than per segment** — and
  those five immediately showed as real defects. A checker that cannot fail is
  not evidence, which is exactly old-coder's point about home-grown gates.

## Results

### Detection, against 15 confirmed-real defects

| Configuration | Bugs caught | Rate | Branch coverage | Runtime | Test LOC |
|---|---|---|---|---|---|
| Lint (`ruff`) | 0 | 0% | — | 0.01s | — |
| Types (`mypy`) | 2 | 13% | — | 1.43s | — |
| **A — ad-hoc TDD** | 6 | **40%** | 95% | 0.35s | 60 |
| **B — spec-driven** | 13 | **87%** | 99% | 0.36s | 158 |
| **C — + gauntlet** | 15 | **100%** | 99% | 5.89s | 365 |

### The three findings that matter

**FINDING 1 — the SPEC is where almost all the value is, and it is nearly free.**
Going from ad-hoc TDD to spec-driven tests took detection from 40% to 87% for
about 100 extra lines of test code and **10 milliseconds** of extra runtime.
Going from spec-driven to the full gauntlet took 87% to 100% — real, but it
cost 207 more lines and made the suite **16× slower**. If only one half of
old-coder gets adopted, it is unambiguously the first half.

**FINDING 2 — coverage is actively misleading, and we are relying on it.**
Suite A hit **95% branch coverage while catching 40% of the bugs.** Suite B hit
99% while catching 87%. Four points of coverage, forty-seven points of
detection. Coverage measures whether a line *ran*, not whether anything would
have noticed it misbehaving.

This is not academic for us. `sediment`'s CI runs:

```yaml
- run: pytest --cov=sediment_aggregator --cov-report=term-missing
```

No `--cov-fail-under`. That layer **prints a number and exits 0 no matter what**
— it is a report wearing a gate's clothing, and it will sit there green while
coverage falls. This is precisely the fail-open checker old-coder warns about,
and it is in our pipeline today.

**FINDING 3 — the gauntlet's verdict depends on the machine it runs on.**
Mutation testing surfaced a mutant that replaces `astimezone(timezone.utc)`
with `astimezone(None)` — i.e. convert to *local* time instead of UTC. Under
`TZ=UTC` (every CI runner's default, including ours) the suite passes and the
mutant survives. Under `TZ=Australia/Sydney`, the same unchanged suite fails
immediately:

```
--- TZ=UTC ---              35 passed
--- TZ=Australia/Sydney --- 2 failed, 33 passed
```

A whole class of timezone bug is invisible to a green CI run. For a project
whose entire job is reconciling timestamps from two capture tools, that is the
most important thing this exercise turned up.

**Directly actionable:** `sediment/aggregator` parses both sources with bare
`datetime.fromisoformat()` and its fixtures are timezone-naive. Screenpipe
writes offset-aware timestamps; Meetily writes local wall-clock. On real data
`Meeting.overlaps()` will either raise `TypeError: can't compare offset-naive
and offset-aware datetimes`, or — worse — silently compare two different zones
as if they were the same. No current test can see it, because every fixture is
naive and CI is UTC.

### Mutation testing: real, noisy, and expensive

Against Suite B: 109 mutants, 94 killed, **15 survived**. Triaged by hand:

| Survivor class | Count | Verdict |
|---|---|---|
| Error-message mutants (`ValueError(None)`, string casing) | 5 | Noise — only killable by asserting message text |
| Boundary-equivalent (`<=`→`<` where the guard is unreachable) | 4 | Equivalent, unkillable |
| **Genuine holes in the test suite** | **4** | Fixed → became Suite C |
| **Genuine bug, masked by the CI timezone** | **2** | See Finding 3 |

**Signal-to-noise: 6 of 15 survivors were worth acting on — 40%.** The four
genuine holes were all things spec-driven example tests could not have caught,
including one where I asserted `result.end == expected` and aware-datetime
equality compares *instants*, so an un-normalized value compared equal and the
assertion proved nothing.

Cost scales badly: the same 109 mutants took **5s against Suite B and 33s
against Suite B+C**, because mutation cost is mutants × suite runtime, and
property tests make the suite slow. Property tests and mutation testing
multiply each other.

### One finding about the domain, not the process

Hypothesis found a case no example test would have: a Screenpipe fragment with
**no meeting overlap of its own** can still be dropped, because same-source
merge runs before the drop rule and the merged block clears the threshold.
That is what my spec said, and it is silent data loss — the exact failure
`sediment/docs/standards/code-review.md` demands we fail safe against. Example
tests confirm the cases you thought of; properties find the ones you didn't.

## Honest limits of this experiment

Stated plainly, because the numbers above are more precise than they are
general:

- **n = 1 module, one domain, one language.** 77 lines of interval arithmetic.
  Interval logic is unusually property-shaped, which flatters hypothesis.
- **Single author.** I wrote the spec, the implementation, all three suites and
  the bug corpus. Ordering the work (spec → impl → suites → bugs) limits the
  contamination but does not remove it. Suite A is my good-faith reconstruction
  of "TDD without a spec", not an observation of anyone actually working.
- **This measures the artifacts, not the agent.** It shows a spec-derived suite
  catches more than an ad-hoc one. It does **not** show that giving an agent
  `SKILL.md` makes it produce better specs — that needs many independent runs.
- **The bug corpus is hand-authored.** Realistic and spec-derived, but not
  sampled from our real defect history.
- **Detection rates are ceilings**, measured on a spec I wrote for a module I
  understood completely.

What survives all of that: the *relative ordering* (spec ≫ gauntlet ≫ types ≫
lint), the coverage/detection divergence, and the timezone finding. Those are
mechanism, not measurement artifacts.

## Conclusion / next step

**Adopt the SPEC half properly. Adopt the gauntlet selectively. Don't install
the skill wholesale.**

The reason not to take it whole is structural: old-coder assumes the human
reads *nothing but* the spec and the evidence report. That is a bet about how
Alan works, and it is not how these repos are run — `life-and-times` is a
personal site where reading the diff is cheap and enjoyable. Adopting a
"never read the code" protocol there buys nothing and costs a 16× slower suite.

### Adopt

1. **A spec artifact before non-trivial implementation.** Highest-value,
   lowest-cost change available (40% → 87%). `life-and-times` already has the
   convention in `docs/superpowers/specs/`; generalize it — concrete
   inputs/outputs and explicit Must-NOTs, one file, human-approved.
2. **`--cov-fail-under` in `sediment`'s CI, today.** A coverage layer that
   cannot fail is not a layer. One line.
3. **A timezone axis in CI.** Run the aggregator suite under a non-UTC `TZ`.
   Cheapest high-value check found in this whole exercise.
4. **Make timezone-awareness a validated precondition** in
   `sediment/aggregator`, not an assumption, and make at least one fixture
   offset-aware. This is a live latent bug, not a hypothetical.
5. **Property tests on the four risk areas `testing.md` already names** —
   dedupe, timestamp joins, idempotency, external-format parsing. That list is
   already written; it just doesn't say *how* to test them.
6. **Fail-closed discipline for home-grown gates**, with a negative control
   proving each can fail. This is old-coder's best under-appreciated idea and
   it cost me a wrong answer in this very experiment.
7. **Tier calibration**, adapted: most work in these repos is Tier 1–2, and
   saying so explicitly is what stops a heavyweight process from being ignored
   entirely.

### Adopt with limits

8. **Mutation testing as a periodic audit, never a CI gate.** 40% signal, cost
   scaling with suite runtime. Run it once when a module stabilizes, triage
   survivors by hand, fold the real ones into the suite, move on.
9. **An evidence report only for Tier 3 changes.** Complementary to ADRs, not a
   replacement — ADRs record *decisions* and persist; evidence records *one
   change* and is disposable.

### Don't adopt

10. **Coverage as a quality signal.** Keep it as a detector of untested code.
    95%/40% is the whole argument.
11. **Independent verification.** Self-declared experimental, one case study.
12. **The "I won't read the code" premise itself**, as a default. It is the
    right frame for `sediment`'s aggregator internals and the wrong one for a
    personal site.

### Next step

Two follow-ups, in order. First, the four `sediment` fixes (2, 3, 4 and a
fixture) — small, evidence-backed, independent of any decision about the
skill. Second, if the spec convention proves itself across a few changes,
write our own `evidence-first` skill into `acs-agent-skills` — old-coder's
SPEC + calibration + fail-closed rules, dropping the gauntlet layers our CI
already enforces and the verification protocol. Adapted, not adopted; this
note is the source material for it.
