"""Execute an approved proposal against the CRM.

Safety rules:
- Optimistic check: before the first write, re-read each account and confirm the
  fields we are about to change still hold the values the reviewer saw. If not,
  the proposal is marked stale and nothing is written.
- SOP re-check: for reparent/CHOW, revenue and AR are re-read at apply time; if the
  SOP outcome would now differ, the proposal is marked stale.
- Resumable: progress (completed steps, created ids) is saved after every step,
  so a retry after a crash never creates a second account.
"""
import json
from datetime import date
from . import config as C
from .match import sop_check, norm_street
from . import store


class Stale(Exception):
    pass


def _note(existing, text):
    line = f"[{C.NOTE_TAG} {date.today().isoformat()}] {text}"
    if text in (existing or ""):
        return existing
    return f"{existing}\n{line}" if existing else line


def _resolve(v, refs):
    return refs[v[1:]] if isinstance(v, str) and v.startswith("$") else v


def preflight(p, crm):
    for acct_id, fields in p.get("expect", {}).items():
        fresh = crm.get_account(acct_id)
        for k, v in fields.items():
            if (fresh.get(k) or "") != (v or ""):
                raise Stale(f"{acct_id}.{k} is now {fresh.get(k)!r}, proposal expected {v!r}. Rerun the pipeline.")
        if p["kind"] in ("reparent", "chow", "chow_link") and p.get("sop"):
            if sop_check(fresh)["outcome"] != p["sop"]["outcome"]:
                raise Stale(f"Revenue/AR on {acct_id} changed; SOP outcome is now {sop_check(fresh)['outcome']}. Rerun the pipeline.")


def _find_existing_create(crm, fields):
    """Guard against a create that succeeded but whose id was never recorded."""
    for a in crm.list_accounts(q=fields["name"]):
        if (a["name"] == fields["name"] and a.get("parent_id") == fields["parent_id"]
                and norm_street(a["billing_street"]) == norm_street(fields["billing_street"])
                and a["billing_zip"] == fields["billing_zip"] and not a.get("duplicate_of_account")):
            return a["account_id"]
    return None


def run(db, pid, crm):
    row = store.get(db, pid)
    p = json.loads(row["payload"])
    prog = json.loads(row["progress"] or "{}")
    done, refs = set(prog.get("done", [])), prog.get("refs", {})
    if not done:
        preflight(p, crm)
    for i, op in enumerate(p["ops"]):
        if i in done:
            continue
        fields = {k: _resolve(v, refs) for k, v in op["set"].items()}
        if op["op"] == "create":
            new_id = _find_existing_create(crm, fields)
            if not new_id:
                if op.get("note_append"):
                    fields["note"] = _note("", op["note_append"])
                new_id = crm.create_account(fields)["account_id"]
            refs[op["ref"]] = new_id
        else:
            if op.get("note_append"):
                fields["note"] = _note(crm.get_account(op["account_id"]).get("note", ""), op["note_append"])
            crm.patch_account(op["account_id"], fields)
        done.add(i)
        db.execute("UPDATE proposals SET progress=? WHERE id=?",
                   (json.dumps({"done": sorted(done), "refs": refs}), pid))
    return refs
