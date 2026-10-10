# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from genlayer import *

MAX_FIELDS = 8
MAX_SOURCES = 5
MAX_RULES = 8
MAX_VALUE_LEN = 160
MAX_SUBJECT_LEN = 140
MAX_BODY_CHARS = 6000

OPEN = "OPEN"
SETTLED = "SETTLED"
REFUNDED = "REFUNDED"
CANCELLED = "CANCELLED"

V_PASS = "PASS"
V_PARTIAL = "PARTIAL"
V_FAIL = "FAIL"
V_NONE = "NOT_VERIFIABLE"

R_SAT = "SATISFIED"
R_VIO = "VIOLATED"
R_UNR = "UNRESOLVED"

OPS = ("<=", ">=", "==", "!=")


class SpecBounty(gl.Contract):
    """A purse plus a requirement spec: fields to read off a set of sources and
    deterministic rules over them. Competitors submit the published values; the
    contract checks each claim against the sources it fetched itself and pays the
    best one -- or nobody, in which case the purse goes home."""

    bounties: TreeMap[str, str]
    bodies: TreeMap[str, str]
    extracts: TreeMap[str, str]
    subs: TreeMap[str, str]
    providers: TreeMap[str, str]
    bounty_count: u256

    def __init__(self):
        self.bounty_count = 0

    def _addr_hex(self, a) -> str:
        if isinstance(a, str):
            return a
        h = getattr(a, "as_hex", None)
        return h if isinstance(h, str) else str(a)

    def _iso_to_epoch(self, s: str) -> int:
        s = s.strip()
        if len(s) < 19:
            return 0
        off = 0
        tail = s[19:]
        if tail.endswith("Z"):
            tail = tail[:-1]
        if len(tail) >= 6 and (tail[0] == "+" or tail[0] == "-"):
            try:
                off = (1 if tail[0] == "+" else -1) * (int(tail[1:3]) * 3600 + int(tail[4:6]) * 60)
            except (ValueError, IndexError):
                off = 0
        try:
            y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            hh, mm, ss = int(s[11:13]), int(s[14:16]), int(s[17:19])
        except (ValueError, IndexError):
            return 0
        if not (1 <= mo <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60 and ss < 61):
            return 0
        yy = y - 1 if mo <= 2 else y
        era = yy // 400
        yoe = yy - era * 400
        doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
        return (era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468) * 86400 \
            + hh * 3600 + mm * 60 + ss - off

    def _now(self) -> int:
        raw = getattr(gl, "message_raw", None)
        d = None
        if raw is not None:
            try:
                d = raw["datetime"]
            except Exception:
                d = None
        return self._iso_to_epoch(str(d)) if d is not None else 0

    def _decode_body(self, content) -> str:
        body = getattr(content, "body", None)
        if body is None:
            return str(content)
        return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)

    def _digit(self, c: str) -> bool:
        return "0" <= c <= "9"

    def _word_char(self, c: str) -> bool:
        return ("a" <= c <= "z") or ("A" <= c <= "Z") or self._digit(c) or c in "_%"

    def _glued(self, body: str, i: int, j: int) -> bool:
        before = body[i - 1] if i > 0 else ""
        after = body[j] if j < len(body) else ""
        if self._word_char(before) or self._word_char(after):
            return True
        if before == "." and self._digit(body[j - 1]) and i >= 2 and self._digit(body[i - 2]):
            return True
        return after == "." and self._digit(body[i]) and j + 1 < len(body) and self._digit(body[j + 1])

    def _anchored(self, body: str, value: str) -> bool:
        """Value occurs verbatim as a whole token: "99" is not anchored by "1999"."""

        if not value or not body:
            return False
        n = len(value)
        i = body.find(value)
        while i >= 0:
            if not self._glued(body, i, i + n):
                return True
            i = body.find(value, i + 1)
        return False

    def _to_num(self, s):
        if not isinstance(s, str):
            return None
        t = s.strip().replace(",", "")
        if not t:
            return None
        for ch in t:
            if not ("0" <= ch <= "9") and ch not in "+-.":
                return None
        try:
            return float(t)
        except (ValueError, OverflowError):
            return None

    def _cmp(self, a: str, op: str, b: str):
        na, nb = self._to_num(a), self._to_num(b)
        if op == "==" or op == "!=":
            eq = (na == nb) if (na is not None and nb is not None) else (a == b)
            return eq if op == "==" else not eq
        if na is None or nb is None:
            return None
        return na <= nb if op == "<=" else na >= nb

    def _str_list(self, v, what: str, limit: int) -> list:
        if not isinstance(v, list) or len(v) == 0:
            raise gl.vm.UserError(what + " must be a non-empty list")
        out = []
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise gl.vm.UserError(what + " entries must be non-empty strings")
            s = item.strip()
            if s in out:
                raise gl.vm.UserError(what + " not unique: " + s)
            if len(s) > MAX_VALUE_LEN:
                raise gl.vm.UserError(what + " entry too long")
            out.append(s)
        if len(out) > limit:
            raise gl.vm.UserError(what + " exceeds " + str(limit))
        return out

    def _rules(self, v, fields: list) -> list:
        if not isinstance(v, list) or not v:
            raise gl.vm.UserError("rules must be a non-empty list")
        if len(v) > MAX_RULES:
            raise gl.vm.UserError("rules exceeds " + str(MAX_RULES))
        out = []
        for r in v:
            if not isinstance(r, dict):
                raise gl.vm.UserError("rule must be an object")
            f, op, ref = r.get("field", ""), r.get("op", ""), r.get("ref", "")
            if f not in fields:
                raise gl.vm.UserError("rule field not tracked: " + str(f))
            if op not in OPS:
                raise gl.vm.UserError("unsupported op: " + str(op))
            if not isinstance(ref, str) or not ref.strip() or len(ref) > MAX_VALUE_LEN:
                raise gl.vm.UserError("rule ref invalid")
            ref = ref.strip()
            if op in ("<=", ">=") and self._to_num(ref) is None:
                raise gl.vm.UserError("rule ref not numeric")
            out.append({"field": f, "op": op, "ref": ref})
        return out

    def _values(self, v, fields: list) -> dict:
        if not isinstance(v, dict) or set(v.keys()) != set(fields):
            raise gl.vm.UserError("values must name exactly the tracked fields")
        out = {}
        for f in fields:
            val = v.get(f, "")
            if not isinstance(val, str) or len(val) > MAX_VALUE_LEN:
                raise gl.vm.UserError("value for " + f + " invalid")
            out[f] = val.strip()
        return out

    def _extract(self, url: str, fields: list) -> dict:
        """The model's one narrow question: what does this page publish per field?
        Every answer is then checked verbatim against the page text."""
        def check() -> dict:
            body = ""
            try:
                body = self._decode_body(gl.nondet.web.render(url))
            except Exception:
                body = ""
            if not body.strip():
                return {"retrieved": False, "body": "", "values": {}}
            fmt = "{" + ", ".join('"' + f + '": "<value>"' for f in fields) + "}"
            task = (
                "Copy the published value of each FIELD from SOURCE exactly as written, "
                'same digits and punctuation. Use "" when SOURCE does not state it. '
                "JSON only.\nFIELDS: " + ", ".join(fields) + "\nSOURCE:\n" + body[:2500] + "\n" + fmt
            )
            result = gl.nondet.exec_prompt(task)
            if isinstance(result, str):
                result = json.loads(result.replace("```json", "").replace("```", ""))
            if not isinstance(result, dict):
                raise gl.vm.UserError("[LLM_ERROR] non-dict result")
            return {"retrieved": True, "body": body, "values": result}

        principle = "Equivalent if retrieved matches and every field carries the same published value."
        return gl.eq_principle.prompt_comparative(check, principle)

    def _extraction_ok(self, fields: list, ex: dict) -> bool:
        if not isinstance(ex, dict) or not isinstance(ex.get("retrieved"), bool):
            return False
        if not isinstance(ex.get("body"), str):
            return False
        vals = ex.get("values")
        if not isinstance(vals, dict) or set(vals.keys()) != set(fields):
            return False
        for v in vals.values():
            if not isinstance(v, str) or len(v) > MAX_VALUE_LEN:
                return False
        return True

    @gl.public.write.payable
    def open_bounty(self, subject: str, fields: list, sources: list, rules: list,
                    min_sources: int, bar: int, collect_by: int, submit_by: int) -> str:
        if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_LEN:
            raise gl.vm.UserError("Subject required and short")
        fields = self._str_list(fields, "fields", MAX_FIELDS)
        sources = self._str_list(sources, "sources", MAX_SOURCES)
        for u in sources:
            if not u.startswith("http://") and not u.startswith("https://"):
                raise gl.vm.UserError("sources must be http(s): " + u)
        rules = self._rules(rules, fields)
        if int(min_sources) < 2 or int(min_sources) > len(sources):
            raise gl.vm.UserError("min_sources must be 2..source count")
        if int(bar) < 1 or int(bar) > len(fields):
            raise gl.vm.UserError("bar must be 1..field count")
        now = self._now()
        if int(collect_by) <= now or int(submit_by) <= int(collect_by):
            raise gl.vm.UserError("need now < collect_by < submit_by")
        purse = int(str(gl.message.value))
        if purse <= 0:
            raise gl.vm.UserError("A bounty must carry a purse")

        self.bounty_count += 1
        bid = str(self.bounty_count)
        self.bounties[bid] = json.dumps({
            "bounty_id": bid, "subject": subject.strip(),
            "requester": self._addr_hex(gl.message.sender_address),
            "fields": fields, "sources": sources, "rules": rules,
            "min_sources": int(min_sources), "bar": int(bar), "purse": purse,
            "collect_by": int(collect_by), "submit_by": int(submit_by),
            "created_at": now, "state": OPEN, "n_collected": 0, "n_subs": 0,
            "entrants": [], "winner": "", "payout": 0, "note": "", "settled_at": 0,
        })
        return bid

    @gl.public.write
    def register_provider(self) -> str:
        who = self._addr_hex(gl.message.sender_address)
        if who in self.providers:
            raise gl.vm.UserError("Already registered")
        self.providers[who] = json.dumps({"provider": who, "registered_at": self._now()})
        return who

    @gl.public.write
    def collect(self, bounty_id: str, source_index: int) -> str:
        """One fetch, one extraction. Values the page does not literally publish
        are dropped here, never passed on as evidence."""
        bid = str(bounty_id)
        b = json.loads(self.bounties.get(bid, "{}"))
        if not b:
            raise gl.vm.UserError("Bounty not found")
        if b["state"] != OPEN:
            raise gl.vm.UserError("Bounty is closed")
        if self._now() >= int(b["collect_by"]):
            raise gl.vm.UserError("Collection window closed")
        idx = int(source_index)
        if idx < 0 or idx >= len(b["sources"]):
            raise gl.vm.UserError("source_index out of range")
        if self.bodies.get(bid + "|" + str(idx), ""):
            raise gl.vm.UserError("Source already collected")

        ex = self._extract(b["sources"][idx], b["fields"])
        if not self._extraction_ok(b["fields"], ex):
            raise gl.vm.UserError("Invalid extraction: field set or value types wrong")
        if not ex.get("retrieved"):
            raise gl.vm.UserError("Source could not be retrieved")

        body = ex["body"][:MAX_BODY_CHARS]
        values = {}
        for f in b["fields"]:
            v = ex["values"].get(f, "")
            if v and self._anchored(body, v):
                values[f] = v

        self.bodies[bid + "|" + str(idx)] = json.dumps({
            "bounty_id": bid, "source_index": idx, "url": b["sources"][idx],
            "body": body, "fetched_at": self._now(),
        })
        self.extracts[bid + "|" + str(idx)] = json.dumps({
            "bounty_id": bid, "source_index": idx, "values": values,
            "dropped": [f for f in b["fields"] if f not in values],
        })
        b["n_collected"] = int(b["n_collected"]) + 1
        self.bounties[bid] = json.dumps(b)
        return json.dumps(values)

    @gl.public.write
    def submit(self, bounty_id: str, values: dict) -> str:
        """Nothing is taken on trust: a field counts only if enough independent
        sources publish that exact value, and the rules are applied to survivors."""
        bid = str(bounty_id)
        b = json.loads(self.bounties.get(bid, "{}"))
        if not b:
            raise gl.vm.UserError("Bounty not found")
        if b["state"] != OPEN:
            raise gl.vm.UserError("Bounty is closed")
        if self._now() >= int(b["submit_by"]):
            raise gl.vm.UserError("Submission window closed")
        who = self._addr_hex(gl.message.sender_address)
        if who not in self.providers:
            raise gl.vm.UserError("Only registered providers may submit")
        if self.subs.get(bid + "|" + who, ""):
            raise gl.vm.UserError("Already submitted")
        if int(b["n_collected"]) < int(b["min_sources"]):
            raise gl.vm.UserError("Not enough sources collected yet")

        vals = self._values(values, b["fields"])
        need = int(b["min_sources"])

        proven, support = {}, {}
        for f in b["fields"]:
            v = vals[f]
            n = 0
            if v:
                for i in range(len(b["sources"])):
                    k = bid + "|" + str(i)
                    ext = json.loads(self.extracts.get(k, "{}"))
                    if not ext:
                        continue
                    if ext["values"].get(f, "") == v and self._anchored(
                            json.loads(self.bodies.get(k, "{}"))["body"], v):
                        n += 1
            support[f] = n
            proven[f] = n >= need

        results = []
        for r in b["rules"]:
            f = r["field"]
            if not proven.get(f, False):
                res = R_UNR
            else:
                c = self._cmp(vals[f], r["op"], r["ref"])
                res = R_UNR if c is None else (R_SAT if c else R_VIO)
            results.append({"field": f, "op": r["op"], "ref": r["ref"], "result": res})

        hits = sum(1 for x in results if x["result"] == R_SAT)
        if any(x["result"] == R_VIO for x in results):
            verdict = V_FAIL
        elif hits == len(results):
            verdict = V_PASS
        elif hits > 0:
            verdict = V_PARTIAL
        else:
            verdict = V_NONE

        score = sum(1 for x in proven.values() if x)
        idx = int(b["n_subs"]) + 1
        self.subs[bid + "|" + who] = json.dumps({
            "bounty_id": bid, "index": idx, "provider": who, "values": vals,
            "support": support, "proven": proven, "rules": results,
            "score": score, "verdict": verdict, "submitted_at": self._now(),
        })
        b["n_subs"] = idx
        b["entrants"] = b.get("entrants", []) + [who]
        self.bounties[bid] = json.dumps(b)
        return json.dumps({"score": score, "verdict": verdict})

    @gl.public.write
    def settle(self, bounty_id: str) -> str:
        """Deterministic. The purse is paid only to a submission that proved enough
        fields from verifiable evidence. Otherwise it goes home."""
        bid = str(bounty_id)
        b = json.loads(self.bounties.get(bid, "{}"))
        if not b:
            raise gl.vm.UserError("Bounty not found")
        if b["state"] != OPEN:
            raise gl.vm.UserError("Bounty already settled")
        if self._now() < int(b["submit_by"]):
            raise gl.vm.UserError("Submission window still open")

        purse = int(b["purse"])
        entrants = [json.loads(self.subs[bid + "|" + who]) for who in b.get("entrants", [])]
        eligible = [s for s in entrants
                    if s["verdict"] in (V_PASS, V_PARTIAL) and int(s["score"]) >= int(b["bar"])]
        b["settled_at"] = self._now()
        if not eligible:
            b["state"] = REFUNDED
            b["note"] = "no submission" if not entrants else "none cleared the bar"
            self.bounties[bid] = json.dumps(b)
            self._pay(b["requester"], purse)
            return json.dumps({"state": REFUNDED, "reason": b["note"]})

        best = eligible[0]
        for s in eligible:
            if int(s["score"]) > int(best["score"]):
                best = s
        b["state"] = SETTLED
        b["winner"] = best["provider"]
        b["payout"] = purse
        b["note"] = "score " + str(best["score"]) + "/" + str(len(b["fields"])) + " " + str(best["verdict"])
        self.bounties[bid] = json.dumps(b)
        self._pay(best["provider"], purse)
        return json.dumps({"state": SETTLED, "winner": best["provider"], "payout": purse})

    @gl.public.write
    def cancel(self, bounty_id: str) -> str:
        bid = str(bounty_id)
        b = json.loads(self.bounties.get(bid, "{}"))
        if not b:
            raise gl.vm.UserError("Bounty not found")
        if b["state"] != OPEN:
            raise gl.vm.UserError("Bounty already settled")
        if self._addr_hex(gl.message.sender_address).lower() != str(b["requester"]).lower():
            raise gl.vm.UserError("Only the requester can cancel")
        if int(b["n_subs"]) > 0:
            raise gl.vm.UserError("Cannot cancel once a submission is in")
        if self._now() >= int(b["submit_by"]):
            raise gl.vm.UserError("Submission window closed")
        b["state"] = CANCELLED
        b["note"] = "cancelled by requester"
        b["settled_at"] = self._now()
        self.bounties[bid] = json.dumps(b)
        self._pay(b["requester"], int(b["purse"]))
        return json.dumps({"state": CANCELLED, "refunded": int(b["purse"])})

    def _pay(self, to: str, amount: int):
        if amount > 0:
            gl.get_contract_at(Address(to)).emit_transfer(value=u256(amount))

    @gl.public.view
    def get_bounty(self, bounty_id: str) -> str:
        return self.bounties.get(str(bounty_id), "{}")

    @gl.public.view
    def get_source(self, bounty_id: str, source_index: int) -> str:
        k = str(bounty_id) + "|" + str(int(source_index))
        body = self.bodies.get(k, "")
        if not body:
            return "{}"
        rec = json.loads(body)
        rec.update(json.loads(self.extracts.get(k, "{}")))
        return json.dumps(rec)

    @gl.public.view
    def get_submission(self, bounty_id: str, provider: str) -> str:
        return self.subs.get(str(bounty_id) + "|" + str(provider), "{}")

    @gl.public.view
    def get_submissions(self, bounty_id: str) -> list:
        rec = json.loads(self.bounties.get(str(bounty_id), "{}"))
        return [json.loads(self.subs[str(bounty_id) + "|" + who]) for who in rec.get("entrants", [])]

    @gl.public.view
    def get_provider(self, provider: str) -> str:
        return self.providers.get(str(provider), "{}")

    @gl.public.view
    def get_bounty_count(self) -> int:
        return self.bounty_count
