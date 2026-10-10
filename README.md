# SpecBounty

A GenLayer intelligent contract that runs **evidence-backed spec verification as a
competition**. A requester escrows a purse together with a requirement spec:
named fields to read off a set of authoritative sources, and deterministic rules
over those fields. Competitors submit the published values. The contract checks
every claim against the pages **it fetched itself**, scores it, and pays the best
submission — or nobody at all, in which case the purse goes home.

Bradbury testnet: `0x45f8d81b9a982069B4071E7309b701cBc79F51C1`

---

## Why this is not another "fetch -> assess -> store" module

The dead skeleton is *caller-supplied URLs → fetch → one prompt → JSON record →
counter*. This contract differs on three axes at once.

| | one-shot assessor | adversarial committee | **this contract** |
|---|---|---|---|
| participants | one caller | submitter / challenger / arbiter | **competing providers racing for a purse** |
| money | none | bonds, slashed | **escrow, winner paid, otherwise refunded** |
| the decision | the model | majority of reveals | **a deterministic rule engine** |
| cross-source | none | quote grounding | **N independent sources must publish the value** |
| lifecycle | one call | 4 locked phases | **collect window / submit window / settle + 3 refunds** |
| unverifiable | guess | — | **never paid for, purse refunded** |

Two things follow from that.

**1. The model is asked one narrow question and its answer is verified.**
`collect(i)` asks *what does this page publish for each field?* Every returned
value is then checked to occur **verbatim in the page as a whole token**. A value
the page does not literally publish is dropped and never becomes evidence.

**2. Nothing else calls a model.** Corroboration, rule evaluation, verdict
grading, winner selection and the payout are all deterministic contract code.

## The deterministic core

### Whole-token anchoring

A value is anchored only if it occurs in the source text with non-word characters
on both sides, and a `.` only counts as a boundary when it is **not** between two
digits. So the traps are handled honestly:

| page says | claimed | anchored? |
|---|---|---|
| `1999` | `99` | no — inside a longer number |
| `50.00` | `50` | no — inside a longer number |
| `price: 50.` | `50` | **yes** — the full stop is punctuation |
| `rate limit: 10000` | `10000` | yes |

### Cross-source corroboration

A field counts for a submission only if **at least `min_sources` independent
sources publish that exact value**. One source is never enough. A value nobody
publishes scores 0 and is `unproven`.

### Rules and verdicts

The requester posts rules over the tracked fields using `<=`, `>=`, `==`, `!=`.
Each rule resolves to one of three outcomes, and an unproven field makes its rule
`UNRESOLVED` rather than guessed:

| | |
|---|---|
| every rule `SATISFIED` | `PASS` |
| some `SATISFIED`, none violated | `PARTIAL` |
| any `VIOLATED` | `FAIL` |
| none resolvable | `NOT_VERIFIABLE` |

`score` is the number of proven fields. Ordering and equality compare as numbers
when both sides parse as numbers, so `50.0` and `50` are the same value.

### The payout rule

The purse goes to the highest-scoring eligible submission — `PASS` or `PARTIAL`
with `score >= bar` — and ties go to the **earliest** submission. If no submission
is eligible the purse is refunded in full. That means a competitor can prove every
field and still get nothing when the spec cannot be evaluated, and the contract
will not pay for a finding it could not verify.

## Money

`open_bounty` is payable and the purse is held by the contract. There are three
ways out, and every one of them moves real value:

| path | when | who gets the purse |
|---|---|---|
| `cancel` | requester, before any submission, before the submit deadline | requester |
| `settle` | deadline passed, no submission cleared the bar | requester |
| `settle` | deadline passed, someone cleared the bar | that provider |

There is no way for the contract to keep the purse.

## Validation before anything is stored

- `subject`: required, length-bounded
- `fields`: non-empty, unique, bounded in length and count
- `sources`: unique, `http(s)` only, bounded count
- `rules`: non-empty, bounded count, fields must be tracked, operators bounded,
  `<=`/`>=` need a numeric reference
- `min_sources`: between 2 and the source count — a single source can never decide
- `bar`: between 1 and the field count
- `now < collect_by < submit_by`
- an extraction must name **exactly** the tracked field set, with bounded string values
- a source that cannot be retrieved, or that returns an empty body, **fails the
  transaction** — it never becomes an empty-but-successful reading
- one submission per provider per bounty, from a registered provider address

## Testing

```
python -m pytest tests/direct -q
```

57 direct-mode tests, every fund movement asserted against the VM balance ledger:

- **creation**: blank/oversized subject, blank and duplicate fields, non-`http`
  and duplicate sources, `min_sources` bounds, `bar` bounds, rule field/operator/
  reference errors, misordered deadlines, zero purse
- **identity**: registration, duplicate registration, unregistered submitter
- **collect**: anchored values kept, **unanchored values dropped**, sentence-final
  value still anchored, HTTP 500 and empty body both fail closed, wrong field set
  and oversized values rejected, duplicate collect, out-of-range index, window
  closed, unknown bounty
- **submit**: not enough sources collected, wrong field set, oversized value,
  duplicate submission, window closed, corroboration scoring, one source is not
  enough, unproven field -> `UNRESOLVED`, violated rule -> `FAIL`, token traps,
  invented value scores 0, numeric formatting
- **settle**: window still open, no entries -> refund, nobody clears the bar ->
  refund, winner paid, ties go to the earliest, higher score wins,
  **`NOT_VERIFIABLE` is never paid for**, settling twice, unknown bounty
- **cancel**: stranger, after a submission, after the window, refund
- **views**: ordering, unknown keys read as empty, full provider lifecycle

The direct-mode conftest installs two accounting hooks (payable debit to the
contract, `emit_transfer` credit to the recipient) — without them the escrow
assertions would be tautologies.

## Contract surface

| Method | Kind | Purpose |
|---|---|---|
| `open_bounty(subject, fields, sources, rules, min_sources, bar, collect_by, submit_by)` | write, payable | escrow a purse with a spec |
| `register_provider()` | write | enter the competition |
| `collect(bounty_id, source_index)` | write | fetch one source, one narrow extraction, anchor it |
| `submit(bounty_id, values)` | write | a competitor's claim; corroboration + rules + verdict |
| `settle(bounty_id)` | write | deterministic winner, or refund |
| `cancel(bounty_id)` | write | requester escape hatch, refund |
| `get_bounty(bounty_id)` | view | spec, purse, deadlines, state, winner |
| `get_source(bounty_id, i)` | view | page text, extracted values, which were dropped |
| `get_submission(bounty_id, provider)` | view | values, per-field support, rule results, verdict |
| `get_submissions(bounty_id)` | view | all entries in submission order |
| `get_provider(addr)` | view | registration |
| `get_bounty_count()` | view | counter |

## Known limitation

The GenLayer CLI's `write` hardcodes `value: 0n`, so a purse cannot be funded
from the command line. Funding and payout are exercised end to end in the test
suite with real balance assertions; live funding would need `genlayer-js`
`writeContract({value})` or Studio.
