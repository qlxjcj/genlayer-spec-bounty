"""SpecBounty: escrow, evidence corroboration and a deterministic rule engine.

Groups
  A  open_bounty input validation
  B  provider registration and identity
  C  collect: fetch + one narrow extraction, values anchored verbatim
  D  submit: corroboration across independent sources, rules, verdict
  E  settle: deterministic winner, real payout, or real refund
  F  cancel: requester escape hatch with a real refund
  G  views

Every money movement is asserted against the VM balance ledger.
"""

import json

import pytest

from conftest import (
    BODY_A, BODY_B, BODY_C, BODY_TRAP,
    COLLECT_BY, FIELDS, PROVIDER_A, PROVIDER_B, PROVIDER_C, PURSE, REQUESTER,
    RULES_OK, RULES_STRICT, STRANGER, SUBMIT_BY, T0, T_END, T_MID,
    URL_A, URL_B, URL_C, URL_EMPTY, URL_ERROR, URL_TRAP,
    VAL_AB, VAL_C, VAL_HALF, VAL_LONG, VAL_TRAP,
    balance, collect_all, open_bounty, register, remock,
)


def cbal(vm):
    return vm._balances.get(vm._contract_address, 0)


def collect_and_register(vm, c, bid, indices=(0, 1, 2), providers=(PROVIDER_A,)):
    collect_all(vm, c, bid, indices)
    for who in providers:
        register(vm, c, who)
    vm.warp(T_MID)      # collection is final now; submissions may begin
    return bid


def submit_claim(vm, c, bid, who, values):
    """Submit inside the submission window. Collection is closed by then, so
    every claim is scored against the same finalised evidence."""
    vm.warp(T_MID)
    vm.sender = who
    return c.submit(bid, values)


# ---------------------------------------------------------------- A: creation


def test_empty_subject_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, subject="   ")


def test_oversized_subject_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, subject="s" * 300)


def test_blank_field_entry_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, fields=["price", "  "])


def test_duplicate_fields_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, fields=["price", "price"])


def test_non_http_source_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, sources=["ftp://a.example.com/x", URL_B])


def test_duplicate_sources_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, sources=[URL_A, URL_A])


def test_min_sources_below_two_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, min_sources=1)


def test_min_sources_above_source_count_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, min_sources=5)


def test_bar_out_of_range_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, bar=0)
    with pytest.raises(Exception):
        open_bounty(vm, c, bar=9)


def test_rule_on_untracked_field_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, rules=[{"field": "colour", "op": "==", "ref": "red"}])


def test_rule_with_unsupported_operator_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, rules=[{"field": "price", "op": "contains", "ref": "5"}])


def test_rule_ref_must_be_numeric_for_ordering(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, rules=[{"field": "price", "op": "<=", "ref": "cheap"}])


def test_deadlines_must_be_ordered(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, collect_by=SUBMIT_BY, submit_by=COLLECT_BY)
    with pytest.raises(Exception):
        open_bounty(vm, c, collect_by=T0_EPOCH_PAST, submit_by=SUBMIT_BY)


T0_EPOCH_PAST = 1


def test_zero_purse_rejected(bounty):
    vm, c = bounty
    with pytest.raises(Exception):
        open_bounty(vm, c, purse=0)


def test_opening_escrows_the_purse(bounty):
    vm, c = bounty
    before = balance(vm, REQUESTER)
    bid = open_bounty(vm, c)
    assert bid == "1"
    assert balance(vm, REQUESTER) == before - PURSE
    assert cbal(vm) == PURSE
    b = json.loads(c.get_bounty(bid))
    assert b["state"] == "OPEN" and b["purse"] == PURSE
    assert b["requester"] == REQUESTER


# ---------------------------------------------------------------- B: identity


def test_provider_registers_and_is_queryable(bounty):
    vm, c = bounty
    who = register(vm, c, PROVIDER_A)
    assert who == PROVIDER_A
    assert json.loads(c.get_provider(PROVIDER_A))["provider"] == PROVIDER_A


def test_duplicate_registration_rejected(bounty):
    vm, c = bounty
    register(vm, c, PROVIDER_A)
    with pytest.raises(Exception):
        register(vm, c, PROVIDER_A)


def test_unregistered_provider_cannot_submit(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    vm.warp(T_MID)
    vm.sender = STRANGER
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000", "price": "50"})


# ---------------------------------------------------------------- C: collect


def test_collect_stores_anchored_values(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    got = json.loads(c.collect(bid, 0))
    assert got == {"rate_limit": "10000", "price": "50"}
    src = json.loads(c.get_source(bid, 0))
    assert src["body"].startswith("SPEC-A")
    assert src["values"] == {"rate_limit": "10000", "price": "50"}
    assert src["dropped"] == []


def test_unanchored_values_are_dropped_not_kept(bounty):
    """The model invents 99 and 50; the page says 1999 and 50.00. Neither claim
    is a whole token in the page, so neither survives as evidence."""
    vm, c = bounty
    remock(vm, extra=[("docs-t", "SPEC-T", BODY_TRAP, VAL_TRAP)])
    bid = open_bounty(vm, c, sources=[URL_TRAP, URL_A, URL_B])
    vm.sender = PROVIDER_A
    got = json.loads(c.collect(bid, 0))
    assert got == {}
    src = json.loads(c.get_source(bid, 0))
    assert sorted(src["dropped"]) == ["price", "rate_limit"]


def test_sentence_final_value_is_still_anchored(bounty):
    """'price: 50.' -- the full stop is punctuation, not part of the number."""
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    assert json.loads(c.collect(bid, 0))["price"] == "50"


def test_collect_is_fail_closed_on_http_error(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c, sources=[URL_ERROR, URL_A, URL_B])
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)
    assert json.loads(c.get_source(bid, 0)) == {}
    assert json.loads(c.get_bounty(bid))["n_collected"] == 0


def test_collect_is_fail_closed_on_empty_body(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c, sources=[URL_EMPTY, URL_A, URL_B])
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)


def test_extraction_with_wrong_field_set_rejected(bounty):
    vm, c = bounty
    remock(vm, spec=[
        ("docs-a", "SPEC-A", BODY_A, VAL_HALF),
        ("docs-b", "SPEC-B", BODY_B, VAL_AB),
        ("docs-c", "SPEC-C", BODY_C, VAL_C),
    ])
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)


def test_extraction_with_oversized_value_rejected(bounty):
    vm, c = bounty
    remock(vm, spec=[
        ("docs-a", "SPEC-A", BODY_A, VAL_LONG),
        ("docs-b", "SPEC-B", BODY_B, VAL_AB),
        ("docs-c", "SPEC-C", BODY_C, VAL_C),
    ])
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)


def test_duplicate_collect_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    c.collect(bid, 0)
    with pytest.raises(Exception):
        c.collect(bid, 0)


def test_collect_index_out_of_range_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.sender = PROVIDER_A
    for bad in (-1, 3, 99):
        with pytest.raises(Exception):
            c.collect(bid, bad)


def test_collect_after_window_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)


def test_collect_on_unknown_bounty_rejected(bounty):
    vm, c = bounty
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect("99", 0)


# ---------------------------------------------------------------- D: submit


def test_submit_before_enough_sources_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    register(vm, c, PROVIDER_A)
    vm.sender = PROVIDER_A
    c.collect(bid, 0)
    vm.warp(T_MID)
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000", "price": "50"})


def test_submit_with_wrong_field_set_rejected(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000"})


def test_submit_with_oversized_value_rejected(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.submit(bid, json.loads(VAL_LONG))


def test_duplicate_submission_rejected(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000", "price": "50"})


def test_submit_after_window_rejected(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.warp(T_END)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000", "price": "50"})


def test_corroboration_scores_the_proven_fields(bounty):
    """Two independent sources publish 10000 and 50 -> both fields proven."""
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "10000", "price": "50"}))
    assert res == {"score": 2, "verdict": "PASS"}
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert sub["support"] == {"rate_limit": 3, "price": 2}
    assert sub["proven"] == {"rate_limit": True, "price": True}
    assert [r["result"] for r in sub["rules"]] == ["SATISFIED", "SATISFIED"]


def test_single_source_support_is_not_enough(bounty):
    """Only C publishes 60, and min_sources is 2, so price is not proven."""
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "10000", "price": "60"}))
    assert res == {"score": 1, "verdict": "PARTIAL"}
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert sub["support"]["price"] == 1
    assert sub["proven"]["price"] is False


def test_unproven_field_makes_its_rule_unresolved(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "60"})
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert [r["result"] for r in sub["rules"]] == ["UNRESOLVED", "SATISFIED"]


def test_violated_rule_is_a_fail(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c, rules=RULES_STRICT))
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "10000", "price": "50"}))
    assert res["verdict"] == "FAIL"
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert [r["result"] for r in sub["rules"]] == ["VIOLATED", "SATISFIED"]


def test_unverifiable_evidence_cannot_score(bounty):
    """99 is inside 1999 and 50 inside 50.00 on the trap page, so nothing from
    that source corroborates anything."""
    vm, c = bounty
    remock(vm, extra=[("docs-t", "SPEC-T", BODY_TRAP, VAL_TRAP)])
    bid = open_bounty(vm, c, sources=[URL_A, URL_B, URL_TRAP])
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "99", "price": "50"}))
    assert res["score"] == 1
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert sub["support"]["rate_limit"] == 0


def test_invented_value_is_never_proven(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "12345", "price": "50"}))
    assert res["score"] == 1
    sub = json.loads(c.get_submission(bid, PROVIDER_A))
    assert sub["support"]["rate_limit"] == 0


def test_numeric_equality_ignores_formatting(bounty):
    """A competitor may write 50.0 where the pages write 50.000000 style values;
    ordering and equality compare as numbers when both sides parse."""
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(
        vm, c, rules=[{"field": "price", "op": "==", "ref": "50"}]))
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "10000", "price": "50"}))
    assert res["verdict"] == "PASS"


# ---------------------------------------------------------------- E: settle


def test_settle_before_window_closes_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    with pytest.raises(Exception):
        c.settle(bid)
    vm.warp(T_MID)
    with pytest.raises(Exception):
        c.settle(bid)


def test_no_entries_refunds_the_requester(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    vm.warp(T_END)
    before = balance(vm, REQUESTER)
    res = json.loads(c.settle(bid))
    assert res["state"] == "REFUNDED"
    assert balance(vm, REQUESTER) == before + PURSE
    assert cbal(vm) == 0
    assert json.loads(c.get_bounty(bid))["state"] == "REFUNDED"


def test_nobody_clearing_the_bar_refunds(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c, bar=2)
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "60"})   # score 1 < bar 2
    vm.warp(T_END)
    before = balance(vm, REQUESTER)
    res = json.loads(c.settle(bid))
    assert res["state"] == "REFUNDED"
    assert "bar" in res["reason"]
    assert balance(vm, REQUESTER) == before + PURSE
    assert balance(vm, PROVIDER_A) == 5 * PURSE


def test_winner_is_paid_and_the_purse_moves(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c), providers=(PROVIDER_A,))
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.warp(T_END)
    before_a = balance(vm, PROVIDER_A)
    res = json.loads(c.settle(bid))
    assert res["state"] == "SETTLED"
    assert res["winner"] == PROVIDER_A
    assert res["payout"] == PURSE
    assert balance(vm, PROVIDER_A) == before_a + PURSE
    assert cbal(vm) == 0
    b = json.loads(c.get_bounty(bid))
    assert b["winner"] == PROVIDER_A and b["state"] == "SETTLED"
    assert b["note"] == "score 2/2 PASS"


def test_ties_go_to_the_earliest_submission(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    register(vm, c, PROVIDER_B)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.sender = PROVIDER_B
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.warp(T_END)
    res = json.loads(c.settle(bid))
    assert res["winner"] == PROVIDER_A


def test_higher_score_beats_an_earlier_entry(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    register(vm, c, PROVIDER_B)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "60"})   # score 1
    vm.sender = PROVIDER_B
    c.submit(bid, {"rate_limit": "10000", "price": "50"})   # score 2
    vm.warp(T_END)
    res = json.loads(c.settle(bid))
    assert res["winner"] == PROVIDER_B


def test_unverifiable_finding_is_never_paid_for(bounty):
    """Everything is proven and the bar is cleared, but the spec cannot be
    evaluated because both values are non-numeric. The purse still goes home."""
    remock_spec = [
        ("docs-q1", "SPEC-Q1", "SPEC-Q1. rate limit: unlimited. price: free.",
         json.dumps({"rate_limit": "unlimited", "price": "free"})),
        ("docs-q2", "SPEC-Q2", "SPEC-Q2. rate limit: unlimited. price: free.",
         json.dumps({"rate_limit": "unlimited", "price": "free"})),
    ]
    vm, c = bounty
    remock(vm, spec=remock_spec)
    bid = open_bounty(vm, c,
                      sources=["https://docs-q1.example.com/spec",
                               "https://docs-q2.example.com/spec",
                               "https://docs-q3.example.com/spec"],
                      rules=[{"field": "price", "op": "<=", "ref": "55"},
                             {"field": "rate_limit", "op": ">=", "ref": "5000"}],
                      bar=2)
    vm.mock_web(".*docs-q3.*", {"method": "GET", "status": 200,
                                "body": "SPEC-Q3. rate limit: unlimited. price: free."})
    vm.mock_llm(".*SPEC-Q3.*", json.dumps({"rate_limit": "unlimited", "price": "free"}))
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "unlimited", "price": "free"}))
    assert res == {"score": 2, "verdict": "NOT_VERIFIABLE"}
    vm.warp(T_END)
    before = balance(vm, REQUESTER)
    out = json.loads(c.settle(bid))
    assert out["state"] == "REFUNDED"
    assert balance(vm, REQUESTER) == before + PURSE


def test_settle_twice_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.warp(T_END)
    c.settle(bid)
    with pytest.raises(Exception):
        c.settle(bid)


def test_settle_on_unknown_bounty_rejected(bounty):
    vm, c = bounty
    vm.warp(T_END)
    with pytest.raises(Exception):
        c.settle("77")


# ---------------------------------------------------------------- F: cancel


def test_stranger_cannot_cancel(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.sender = STRANGER
    with pytest.raises(Exception):
        c.cancel(bid)


def test_cancel_after_a_submission_rejected(bounty):
    vm, c = bounty
    bid = collect_and_register(vm, c, bid=open_bounty(vm, c))
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.sender = REQUESTER
    with pytest.raises(Exception):
        c.cancel(bid)


def test_cancel_after_window_rejected(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.warp(T_END)
    vm.sender = REQUESTER
    with pytest.raises(Exception):
        c.cancel(bid)


def test_cancel_refunds_the_requester(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    before = balance(vm, REQUESTER)
    vm.sender = REQUESTER
    res = json.loads(c.cancel(bid))
    assert res == {"state": "CANCELLED", "refunded": PURSE}
    assert balance(vm, REQUESTER) == before + PURSE
    assert cbal(vm) == 0
    assert json.loads(c.get_bounty(bid))["state"] == "CANCELLED"


# ---------------------------------------------------------------- G: views


def test_submissions_are_returned_in_order(bounty):
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    for who in (PROVIDER_A, PROVIDER_B, PROVIDER_C):
        register(vm, c, who)
    vm.warp(T_MID)
    vm.sender = PROVIDER_B
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.sender = PROVIDER_C
    c.submit(bid, {"rate_limit": "10000", "price": "60"})
    got = c.get_submissions(bid)
    assert [s["provider"] for s in got] == [PROVIDER_B, PROVIDER_C]
    assert [s["index"] for s in got] == [1, 2]


def test_unknown_keys_read_as_empty(bounty):
    vm, c = bounty
    assert c.get_bounty("42") == "{}"
    assert c.get_source("42", 0) == "{}"
    assert c.get_submission("42", PROVIDER_A) == "{}"
    assert c.get_provider(STRANGER) == "{}"
    assert c.get_submissions("42") == []
    assert c.get_bounty_count() == 0


def test_real_provider_lifecycle_end_to_end(bounty):
    """Open -> collect three real sources -> two competitors -> settle -> paid."""
    vm, c = bounty
    bid = open_bounty(vm, c)
    assert json.loads(c.get_bounty(bid))["n_collected"] == 0
    collect_all(vm, c, bid)
    assert json.loads(c.get_bounty(bid))["n_collected"] == 3
    for who in (PROVIDER_A, PROVIDER_B):
        register(vm, c, who)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.sender = PROVIDER_B
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.warp(T_END)
    out = json.loads(c.settle(bid))
    assert out["winner"] == PROVIDER_A
    assert cbal(vm) == 0
    assert json.loads(c.get_bounty(bid))["state"] == "SETTLED"


# ------------------------------- H: one finalised evidence set for everyone


def test_submit_is_refused_while_collection_is_open(bounty):
    """A provider must not be scored against a half-collected evidence set."""
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.submit(bid, {"rate_limit": "10000", "price": "50"})
    assert json.loads(c.get_submission(bid, PROVIDER_A)) == {}


def test_collection_cannot_change_after_the_freeze(bounty):
    """Once the collection window shuts, the evidence set is final."""
    vm, c = bounty
    bid = open_bounty(vm, c)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    with pytest.raises(Exception):
        c.collect(bid, 0)
    assert json.loads(c.get_bounty(bid))["n_collected"] == 0


def test_later_collection_cannot_stale_an_earlier_submission(bounty):
    """The regression the steward asked for. A claim is accepted only after
    collection has closed, and settlement re-scores from scratch, so a later
    collection can never leave an earlier submission with a stale score."""
    vm, c = bounty
    bid = open_bounty(vm, c)                       # 3 sources, min_sources 2
    collect_all(vm, c, bid, indices=(0, 1))        # only two of three collected
    register(vm, c, PROVIDER_A)
    register(vm, c, PROVIDER_B)

    vm.warp(T_MID)                                 # evidence frozen here
    vm.sender = PROVIDER_A
    res = json.loads(c.submit(bid, {"rate_limit": "10000", "price": "50"}))
    assert res == {"score": 2, "verdict": "PASS"}

    # the exact scenario: someone tries to add the third source afterwards
    vm.sender = PROVIDER_B
    with pytest.raises(Exception):
        c.collect(bid, 2)
    assert json.loads(c.get_bounty(bid))["n_collected"] == 2

    vm.warp(T_END)
    c.settle(bid)
    stored = json.loads(c.get_submission(bid, PROVIDER_A))
    assert stored["score"] == 2 and stored["verdict"] == "PASS"
    assert stored["support"] == {"rate_limit": 2, "price": 2}
    assert json.loads(c.get_bounty(bid))["note"] == "score 2/2 PASS"


def test_every_entry_is_rescored_against_the_same_frozen_evidence(bounty):
    """Two providers, the same claim, submitted at different moments: identical
    scores, and settlement confirms both against the finalised evidence."""
    vm, c = bounty
    bid = open_bounty(vm, c)
    collect_all(vm, c, bid)
    register(vm, c, PROVIDER_A)
    register(vm, c, PROVIDER_B)
    vm.warp(T_MID)
    vm.sender = PROVIDER_A
    c.submit(bid, {"rate_limit": "10000", "price": "50"})
    vm.sender = PROVIDER_B
    c.submit(bid, {"rate_limit": "10000", "price": "50"})

    first = json.loads(c.get_submission(bid, PROVIDER_A))
    second = json.loads(c.get_submission(bid, PROVIDER_B))
    assert first["score"] == second["score"] == 2
    assert first["support"] == second["support"]
    assert first["proven"] == second["proven"]
    assert first["rules"] == second["rules"]

    vm.warp(T_END)
    out = json.loads(c.settle(bid))
    assert out["winner"] == PROVIDER_A            # tie goes to the earliest
    assert out["payout"] == PURSE
    # settlement re-scored everyone, so the stored numbers still hold
    assert json.loads(c.get_submission(bid, PROVIDER_A))["score"] == 2
    assert json.loads(c.get_submission(bid, PROVIDER_B))["score"] == 2
