# SpecBounty

A GenLayer intelligent contract that runs **evidence-backed spec verification as a
competition**. A requester escrows a purse together with a requirement spec:
named fields to read off a set of authoritative sources, and deterministic rules
over those fields. Competitors submit the published values. The contract checks
every claim against the pages **it fetched itself**, scores it, and pays the best
submission — or nobody at all, in which case the purse goes home.

Bradbury testnet: `0x30b6Ff5107bB80DEa6D608CeB3BAf0ee88BB2638`

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

## One finalised evidence set for everyone

Scoring providers against whatever happens to be collected *at the moment they
submit* would let an early submitter be judged on a thinner evidence set than a
later one, and would leave early scores stale once more sources arrive. The
lifecycle closes both doors.

```
collecting   now <  collect_by        collect() yes   submit() no
submitting   collect_by <= now < submit_by   collect() no    submit() yes
settling     submit_by <= now                     settle() yes
```

- `submit` is **refused while the collection window is open**, so nobody can be
  scored against a half-collected evidence set. The evidence is frozen at
  `collect_by`.
- `collect` is **refused once that window shuts**, so the frozen set cannot
  change afterwards.
- `settle` **re-scores every entry from scratch** against that frozen set before
  ranking, so all providers are ranked on identical evidence even if anything
  upstream had changed.

`tests/direct/test_spec_bounty.py` group **H** holds the regression tests:
submitting during collection is refused, collecting after the freeze is refused,
a later collection cannot stale an earlier submission, and two providers making
the same claim at different moments come out with identical scores that
settlement confirms.

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

61 direct-mode tests, every fund movement asserted against the VM balance ledger:

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

## Live on chain

The whole lifecycle was driven end to end on Bradbury from `0x6F33Ec…8959` with a
**real 1 GEN purse**, against two real Wikipedia sources:

```
open_bounty   FINISHED_WITH_RETURN   purse 1000000000000000000 escrowed
collect 0     FINISHED_WITH_RETURN   n_collected 0 -> 1
collect 1     FINISHED_WITH_RETURN   n_collected 1 -> 2
submit        FINISHED_WITH_RETURN   score 2  verdict PASS
settle        FINISHED_WITH_RETURN   state SETTLED   payout 1000000000000000000
```

Both sources yielded `{"lang":"en","type":"standard"}` with `dropped=[]` — every
extracted value was anchored verbatim in the page it came from. The submission
the contract stored:

```json
{"values":  {"lang": "en", "type": "standard"},
 "support": {"lang": 2, "type": 2},
 "proven":  {"lang": true, "type": true},
 "rules":   [{"field": "lang", "op": "==", "ref": "en", "result": "SATISFIED"}],
 "score": 2, "verdict": "PASS", "note": "score 2/2 PASS"}
```

`support` shows each field carried by two independent sources; the rule engine
resolved deterministically; the purse left the contract to the winner.

The other exit is just as real. In an earlier run no submission was in when the
window closed, and the contract refunded the full purse:

```
settle   FINISHED_WITH_RETURN   state REFUNDED   note "no submission"
```

Sixteen input-validation gates were also exercised live and every one of them
reverted as designed:

```
zero purse · non-http source · min_sources below 2 · bar out of range
rule on untracked field · unsupported operator · rule ref not numeric
duplicate fields · collect/submit/settle/cancel on an unknown bounty
duplicate registration · submit/collect on a settled bounty · settling twice
```

`collect` needs consensus to agree over a live web fetch plus a model extraction,
which is where Bradbury intermittently answers `NOT_VOTED` / `TIMEOUT`; the calls
above are the successful attempts. The extraction, corroboration, rule engine and
payout are additionally covered by the 61 direct-mode tests with real balance
assertions.

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

## Funding a purse from the command line

The GenLayer CLI's `write` hardcodes `value: 0n`, so a purse cannot be attached
through it. The contract is funded by sending the transaction directly through
`genlayer-js`:

```js
const client = createClient({ chain: chains.testnetBradbury, endpoint: RPC,
                              account: createAccount(privateKey) });
const hash = await client.writeContract({ address, functionName: "open_bounty",
                                          args: [...], value: 10n ** 18n });
await client.waitForTransactionReceipt({ hash, retries: 100, interval: 5000 });
```

`open_bounty` is declared `@gl.public.write.payable`. That declaration is what
makes an attached value legal — without it the same call reverts on chain while
every direct-mode test still passes.
