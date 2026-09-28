"""Match website locations to CRM accounts and emit reviewable proposals.

Every proposal is a list of concrete API operations plus the evidence behind it.
Proposal ids are a hash of what the proposal would change (never of dates), so a
re-run that reaches the same conclusion produces the same id and is skipped.
"""
import difflib, hashlib, json, re
from . import config as C

SUFFIX = {"street": "st", "road": "rd", "avenue": "ave", "av": "ave", "boulevard": "blvd", "drive": "dr",
          "lane": "ln", "pike": "pike", "pk": "pike", "court": "ct", "place": "pl", "parkway": "pkwy"}
DIRS = {"north": "n", "south": "s", "east": "e", "west": "w",
        "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se"}
FIELDS = ("name", "billing_street", "billing_city", "billing_state", "billing_zip", "care_type", "status")


def norm_street(s):
    toks = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    return " ".join(SUFFIX.get(t, DIRS.get(t, t)) for t in toks)


def norm_name(n):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (n or "").lower().replace("&", " and ")).split())


def name_sim(a, b):
    return round(difflib.SequenceMatcher(None, norm_name(a), norm_name(b)).ratio(), 2)


def is_parent_account(a):
    return a["name"].endswith("(Parent Account)")


def retired(a):
    """Accounts already resolved as duplicates or CHOW predecessors are never matched again."""
    return bool(a.get("duplicate_of_account") or a.get("chow_current_account"))


def money(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def sop_check(a):
    rev, ar = money(a["lifetime_revenue"]), money(a["outstanding_ar"])
    chow = rev > 0 and ar > 0
    return {"lifetime_revenue": rev, "outstanding_ar": ar, "outcome": "CHOW" if chow else "REPARENT",
            "rule": ("Revenue history AND open AR: preserve old account, create new under correct parent, "
                     "link via chow_current_account") if chow else
                    ("No revenue history or no open AR: reparent the existing account directly")}


def addr(x, pre=""):
    return f'{x[pre+"street"]}, {x[pre+"city"]}, {x[pre+"state"]} {x[pre+"zip"]}'


def acct_addr(a):
    return f'{a["billing_street"]}, {a["billing_city"]}, {a["billing_state"]} {a["billing_zip"]}'


def site_fields(loc, parent_id):
    care = [C.CARE_MAP.get(c, c) for c in loc["care"]]
    return {"name": loc["name"], "parent_id": parent_id, "billing_street": loc["street"],
            "billing_city": loc["city"], "billing_state": loc["state"], "billing_zip": loc["zip"],
            "care_type": care[0] if care else "", "status": "Active"}


def make(kind, title, ops, *, account_id=None, loc=None, evidence=(), accounts=(), confidence="high",
         sop=None, expect=None):
    key = json.dumps({"kind": kind, "account": account_id, "slug": loc and loc["slug"], "ops": ops}, sort_keys=True)
    return {"id": hashlib.sha1(key.encode()).hexdigest()[:16], "kind": kind, "title": title,
            "account_id": account_id, "site": loc, "ops": ops, "evidence": list(evidence),
            "accounts": {a["account_id"]: {k: a.get(k) for k in (*FIELDS, "account_id", "parent_id", "parent_name",
                                                                 "lifetime_revenue", "outstanding_ar", "note")}
                         for a in accounts},
            "confidence": confidence, "sop": sop, "expect": expect or {}}


def build(locs, accounts, contacts):
    parents = {a["name"]: a["account_id"] for a in accounts if is_parent_account(a)}
    BH = parents[C.PARENT_NAME]
    acquired = {parents[n] for n in C.ACQUIRED_PARENTS if n in parents}
    pname = {a["account_id"]: a["name"].replace(" (Parent Account)", "") for a in accounts}
    admins = {}
    for c in contacts:
        if c.get("is_active", True) and "administrator" in (c.get("title") or "").lower():
            admins.setdefault(c["name"].strip().lower(), set()).add(c["account_id"])
    live = [a for a in accounts if not is_parent_account(a) and not retired(a)]
    proposals, report, claimed = [], [], set()

    for loc in locs:
        adm_ids = admins.get(loc["administrator"].lower(), set())
        cands = []
        for a in live:
            if a["billing_state"] != loc["state"]:
                continue
            sig = {"street": norm_street(a["billing_street"]) == norm_street(loc["street"]),
                   "zip": a["billing_zip"] == loc["zip"],
                   "city": a["billing_city"].lower() == loc["city"].lower(),
                   "admin": a["account_id"] in adm_ids,
                   "name": name_sim(a["name"], loc["name"])}
            if (sig["street"] and (sig["zip"] or sig["city"])) or (sig["city"] and (sig["admin"] or sig["name"] >= 0.9)):
                cands.append((a, sig))

        if not cands:
            look = [a for a in accounts if not is_parent_account(a) and
                    (name_sim(a["name"], loc["name"]) >= 0.75 or
                     (a["billing_city"].lower() == loc["city"].lower() and a["billing_state"] == loc["state"]))]
            ev = [f'Website lists {loc["name"]} at {addr(loc)} ({", ".join(loc["care"])}).',
                  "No CRM account shares this street address, and none in this city has the website administrator as a contact."]
            for a in look:
                ev.append(f'Rejected look-alike {a["name"]} ({a["account_id"]}) at {acct_addr(a)} under '
                          f'{pname.get(a["parent_id"], "no parent")}: different address, so a different facility.')
            proposals.append(make("create", f'Create {loc["name"]}',
                                  [{"op": "create", "ref": "new", "set": site_fields(loc, BH),
                                    "note_append": f'Created from Bellhaven website listing {loc["url"]}.'}],
                                  loc=loc, evidence=ev, accounts=look))
            report.append({"slug": loc["slug"], "name": loc["name"], "class": "new", "account_id": None})
            continue

        def rank(c):
            a, s = c
            return (a["parent_id"] == BH, money(a["lifetime_revenue"]) > 0, s["admin"], a["parent_id"] in acquired,
                    a["billing_street"].strip().lower() == loc["street"].lower(), s["name"],
                    a["status"] == "Active", a["account_id"])
        cands.sort(key=rank, reverse=True)
        (sv, ssig), losers = cands[0], cands[1:]
        claimed.update(a["account_id"] for a, _ in cands)
        def why(s):
            return ", ".join(k for k in ("street", "zip", "city", "admin") if s[k]) + f', name similarity {s["name"]}'
        conf = "high" if ssig["street"] and ssig["zip"] else "medium"
        base_ev = [f'Website: {loc["name"]}, {addr(loc)}, administrator {loc["administrator"] or "not listed"}.',
                   f'Matched CRM {sv["name"]} ({sv["account_id"]}) on {why(ssig)}.']
        if ssig["admin"]:
            base_ev.append(f'CRM contact {loc["administrator"]} is Administrator on this account and on the website listing.')

        for lo, lsig in losers:
            proposals.append(make(
                "duplicate", f'Mark {lo["name"]} as duplicate of {sv["name"]}',
                [{"op": "patch", "account_id": lo["account_id"],
                  "set": {"duplicate_of_account": sv["account_id"], "status": "Inactive"},
                  "note_append": f'Duplicate of {sv["account_id"]} ({sv["name"]}); same facility at {loc["street"]}, {loc["city"]}.'}],
                account_id=lo["account_id"], loc=loc, accounts=[lo, sv],
                expect={lo["account_id"]: {"duplicate_of_account": lo["duplicate_of_account"], "status": lo["status"]}},
                evidence=[f'{lo["name"]} ({lo["account_id"]}, under {pname.get(lo["parent_id"], "no parent")}) matches the same '
                          f'website location on {why(lsig)}.',
                          f'Survivor {sv["name"]} chosen by rank: correct parent, then billing history, then administrator '
                          f'contact, then acquired lineage, then exact address.',
                          f'Loser billing: revenue {money(lo["lifetime_revenue"]):,.0f}, AR {money(lo["outstanding_ar"]):,.0f}; '
                          f'history stays on the record.']))

        target = site_fields(loc, BH)
        if sv["parent_id"] != BH:
            sop = sop_check(sv)
            if sop["outcome"] == "CHOW":
                proposals.append(make(
                    "chow", f'CHOW: new {loc["name"]} account, preserve {sv["name"]}',
                    [{"op": "create", "ref": "new", "set": target,
                      "note_append": f'CHOW successor of {sv["account_id"]} ({sv["name"]}, formerly under '
                                     f'{pname.get(sv["parent_id"], "no parent")}). Old account preserved for billing.'},
                     {"op": "patch", "account_id": sv["account_id"], "set": {"chow_current_account": "$new"}}],
                    account_id=sv["account_id"], loc=loc, accounts=[sv], sop=sop,
                    expect={sv["account_id"]: {"parent_id": sv["parent_id"], "chow_current_account": ""}},
                    evidence=base_ev + [f'CRM parent is {pname.get(sv["parent_id"], "missing")}; website lists it as a Bellhaven community.',
                                        "Old account is left untouched apart from chow_current_account."],
                    confidence=conf))
                report.append({"slug": loc["slug"], "name": loc["name"], "class": "chow", "account_id": sv["account_id"]})
                continue
            proposals.append(make(
                "reparent", f'Reparent {sv["name"]} to Bellhaven',
                [{"op": "patch", "account_id": sv["account_id"], "set": {"parent_id": BH},
                  "note_append": f'Reparented from {pname.get(sv["parent_id"], "no parent")} to Bellhaven Senior Living '
                                 f'(listed on website; SOP: direct reparent).'}],
                account_id=sv["account_id"], loc=loc, accounts=[sv], sop=sop,
                expect={sv["account_id"]: {"parent_id": sv["parent_id"]}},
                evidence=base_ev + [f'CRM parent is {pname.get(sv["parent_id"], "missing")}; website lists it as a Bellhaven community.'],
                confidence=conf))

        diff = {}
        if sv["name"] != loc["name"]:
            diff["name"] = loc["name"]
        if norm_street(sv["billing_street"]) != norm_street(loc["street"]):
            diff["billing_street"] = loc["street"]
        if sv["billing_city"].lower() != loc["city"].lower():
            diff["billing_city"] = loc["city"]
        if sv["billing_state"] != loc["state"]:
            diff["billing_state"] = loc["state"]
        if sv["billing_zip"] != loc["zip"]:
            diff["billing_zip"] = loc["zip"]
        offered = [C.CARE_MAP.get(c, c) for c in loc["care"]]
        if offered and sv["care_type"] not in offered:
            diff["care_type"] = offered[0]
        if sv["status"] != "Active":
            diff["status"] = "Active"
        if diff:
            what = "; ".join(f'{k} {sv.get(k)!s} to {v}' for k, v in diff.items())
            proposals.append(make(
                "update", f'Update {sv["name"]}: {", ".join(diff)}',
                [{"op": "patch", "account_id": sv["account_id"], "set": diff,
                  "note_append": f"Updated to match website listing: {what}."}],
                account_id=sv["account_id"], loc=loc, accounts=[sv],
                expect={sv["account_id"]: {k: sv.get(k) for k in diff}},
                evidence=base_ev + [f'Website {k.replace("billing_", "")}: {v}; CRM: {sv.get(k) or "blank"}.' for k, v in diff.items()],
                confidence=conf))
        cls = "match" if not diff and sv["parent_id"] == BH and not losers else "fix"
        report.append({"slug": loc["slug"], "name": loc["name"], "class": cls, "account_id": sv["account_id"],
                       "duplicates": [a["account_id"] for a, _ in losers]})

    # CRM accounts under Bellhaven that the website no longer lists
    for a in live:
        if a["parent_id"] != BH or a["account_id"] in claimed or a["status"] == "Needs Review":
            continue
        same = [b for b in live if b is not a and b["parent_id"] != BH and b["billing_state"] == a["billing_state"]
                and norm_street(b["billing_street"]) == norm_street(a["billing_street"]) and b["billing_zip"] == a["billing_zip"]]
        base_ev = [f'{a["name"]} ({a["account_id"]}) is under Bellhaven in the CRM but is not on any page of the website.']
        if same:
            succ = same[0]
            sop = sop_check(a)
            base_ev.append(f'{succ["name"]} ({succ["account_id"]}) under {pname.get(succ["parent_id"], "no parent")} '
                           f'occupies the same address {acct_addr(a)}: the facility changed hands.')
            if sop["outcome"] == "CHOW":
                proposals.append(make(
                    "chow_link", f'Link {a["name"]} to current owner account {succ["name"]}',
                    [{"op": "patch", "account_id": a["account_id"], "set": {"chow_current_account": succ["account_id"]}}],
                    account_id=a["account_id"], accounts=[a, succ], sop=sop,
                    expect={a["account_id"]: {"chow_current_account": ""}},
                    evidence=base_ev + ["The successor account already exists, so no new account is created; the old "
                                        "account is otherwise left exactly as is for billing."]))
            else:
                proposals.append(make(
                    "duplicate", f'Mark {a["name"]} as duplicate of {succ["name"]}',
                    [{"op": "patch", "account_id": a["account_id"],
                      "set": {"duplicate_of_account": succ["account_id"], "status": "Inactive"},
                      "note_append": f'Facility now operated under {succ["name"]} ({succ["account_id"]}); no longer a Bellhaven community.'}],
                    account_id=a["account_id"], accounts=[a, succ], sop=sop,
                    expect={a["account_id"]: {"duplicate_of_account": "", "status": a["status"]}}, evidence=base_ev))
            report.append({"slug": None, "name": a["name"], "class": "departed", "account_id": a["account_id"]})
            continue
        proposals.append(make(
            "orphan", f'Flag {a["name"]} for review: not on website',
            [{"op": "patch", "account_id": a["account_id"], "set": {"status": "Needs Review"},
              "note_append": "Not listed on the Bellhaven website and no successor account found at this address. "
                             "Confirm closure or new owner before changing parent."}],
            account_id=a["account_id"], accounts=[a], confidence="medium",
            expect={a["account_id"]: {"status": a["status"]}},
            evidence=base_ev + [f'No other CRM account at {acct_addr(a)}.',
                                f'Billing: revenue {money(a["lifetime_revenue"]):,.0f}, AR {money(a["outstanding_ar"]):,.0f}.',
                                "Absence from a marketing site is not proof of closure, so status goes to Needs Review "
                                "rather than Inactive, and the parent is left unchanged."]))
        report.append({"slug": None, "name": a["name"], "class": "orphan", "account_id": a["account_id"]})
    return proposals, report
