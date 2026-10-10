"""Fixtures for SpecBounty direct-mode tests.

Direct mode does NOT track native value flows, so this conftest installs the two
accounting hooks the escrow assertions depend on:

1. Payable value -- ``open_bounty`` is wrapped so that ``vm.value`` moves
   sender -> contract in the VM balance ledger.
2. Transfers -- the contract's ``emit_transfer`` raises a ``PostMessage``
   gl_call; the hook moves contract -> recipient.

Without them the purse/refund/payout assertions would be tautologies.

Mocks are keyed per source (URL for the fetch, body marker for the extraction).
Markers must not be substrings of one another: mocks are first-match-wins.
"""

import json
import os

import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "spec_bounty.py",
)

REQUESTER = "0x" + "aa" * 20
PROVIDER_A = "0x" + "b1" * 20
PROVIDER_B = "0x" + "b2" * 20
PROVIDER_C = "0x" + "b3" * 20
STRANGER = "0x" + "44" * 20

FIELDS = ["rate_limit", "price"]

URL_A = "https://docs-a.example.com/spec"
URL_B = "https://docs-b.example.com/spec"
URL_C = "https://docs-c.example.com/spec"
URL_TRAP = "https://docs-t.example.com/spec"
URL_EMPTY = "https://docs-empty.example.com/spec"
URL_ERROR = "https://docs-error.example.com/spec"
URL_FLAT = "https://docs-flat.example.com/spec"

# A and B agree on both fields; C disagrees on price.
BODY_A = "SPEC-A. rate limit: 10000 requests per day. price: 50."
BODY_B = "SPEC-B. rate limit: 10000 requests per day. price: 50."
BODY_C = "SPEC-C. rate limit: 10000 requests per day. price: 60."

# Token traps: 99 is inside 1999, 50 is inside 50.00. Neither is a whole token.
BODY_TRAP = "SPEC-T. rate limit: 1999. price: 50.00."

VAL_AB = json.dumps({"rate_limit": "10000", "price": "50"})
VAL_C = json.dumps({"rate_limit": "10000", "price": "60"})
VAL_TRAP = json.dumps({"rate_limit": "99", "price": "50"})
VAL_HALF = json.dumps({"rate_limit": "10000"})
VAL_LONG = json.dumps({"rate_limit": "10000", "price": "x" * 300})

RULES_OK = [
    {"field": "price", "op": "<=", "ref": "55"},
    {"field": "rate_limit", "op": ">=", "ref": "5000"},
]
RULES_STRICT = [
    {"field": "price", "op": "<=", "ref": "40"},
    {"field": "rate_limit", "op": ">=", "ref": "5000"},
]

# 2026-01-01T00:00:00Z
T0 = "2026-01-01T00:00:00Z"
T0_EPOCH = 1767225600
T_MID = "2026-01-01T01:06:40Z"   # T0 + 4000: collection shut, submission open
T_END = "2026-01-01T02:13:20Z"   # T0 + 8000: submission shut, settling open

COLLECT_BY = T0_EPOCH + 3600
SUBMIT_BY = T0_EPOCH + 7200
PURSE = 10**18


def _addr_bytes(vm, addr):
    return vm._to_bytes(addr)


def balance(vm, addr):
    return vm._balances.get(_addr_bytes(vm, addr), 0)


def remock(vm, spec=None, extra=()):
    """spec: list of (url_marker, body_marker, body, llm_response)."""
    if spec is None:
        spec = [
            ("docs-a", "SPEC-A", BODY_A, VAL_AB),
            ("docs-b", "SPEC-B", BODY_B, VAL_AB),
            ("docs-c", "SPEC-C", BODY_C, VAL_C),
        ]
    vm.clear_mocks()
    for url_marker, body_marker, body, resp in spec:
        vm.mock_web(".*" + url_marker + ".*", {"method": "GET", "status": 200, "body": body})
        if resp is not None:
            vm.mock_llm(".*" + body_marker + ".*", resp)
    for url_marker, body_marker, body, resp in extra:
        vm.mock_web(".*" + url_marker + ".*", {"method": "GET", "status": 200, "body": body})
        if resp is not None:
            vm.mock_llm(".*" + body_marker + ".*", resp)
    vm.mock_web(".*docs-empty.*", {"method": "GET", "status": 200, "body": ""})
    vm.mock_web(".*docs-error.*", {"method": "GET", "status": 500, "body": "Internal Server Error"})
    vm.mock_web(".*docs-flat.*", {"method": "GET", "status": 200, "body": "SPEC-F. rate limit: 5000."})


@pytest.fixture
def bounty(direct_vm, direct_deploy):
    vm = direct_vm
    remock(vm)

    def _value_transfer_hook(vm, request):
        if "PostMessage" not in request:
            return None
        msg = request["PostMessage"]
        amount = int(msg.get("value", 0))
        if amount > 0:
            contract = vm._contract_address
            recipient = _addr_bytes(vm, msg["address"])
            vm._balances[contract] = balance(vm, contract) - amount
            vm._balances[recipient] = balance(vm, recipient) + amount
        return {"ok": None}

    vm._gl_call_hook = _value_transfer_hook
    c = direct_deploy(CONTRACT)

    _orig_open = c.open_bounty

    def _open_with_value(*args):
        if vm._value > 0:
            sender = _addr_bytes(vm, vm.sender)
            contract = vm._contract_address
            vm._balances[sender] = balance(vm, sender) - vm._value
            vm._balances[contract] = balance(vm, contract) + vm._value
        return _orig_open(*args)

    c.open_bounty = _open_with_value

    for who in (REQUESTER, PROVIDER_A, PROVIDER_B, PROVIDER_C, STRANGER):
        vm._balances[_addr_bytes(vm, who)] = 5 * PURSE
    vm.warp(T0)
    return vm, c


def open_bounty(vm, c, subject="Does the vendor meet the published spec?",
                fields=None, sources=None, rules=None, min_sources=2, bar=2,
                purse=PURSE, collect_by=COLLECT_BY, submit_by=SUBMIT_BY):
    vm.sender = REQUESTER
    vm.value = purse
    bid = c.open_bounty(
        subject,
        list(FIELDS) if fields is None else fields,
        [URL_A, URL_B, URL_C] if sources is None else sources,
        RULES_OK if rules is None else rules,
        min_sources,
        bar,
        collect_by,
        submit_by,
    )
    vm.value = 0
    return bid


def collect_all(vm, c, bid, indices=(0, 1, 2)):
    out = []
    for i in indices:
        out.append(c.collect(bid, i))
    return out


def register(vm, c, who):
    vm.sender = who
    return c.register_provider()
