"""CRM API client, plus an offline snapshot double with the same interface."""
import glob, json, os, random, string, time
import requests
from . import config as C


def _unwrap(body):
    if isinstance(body, dict) and "account_id" not in body and isinstance(body.get("data"), dict):
        return body["data"]
    return body


class CRM:
    def __init__(self, api_url=C.API_URL, token=C.TOKEN):
        if not token:
            raise SystemExit("BH_TOKEN is not set")
        self.api = api_url
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _req(self, method, path, **kw):
        for attempt in range(5):
            r = self.s.request(method, self.api + path, timeout=30, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"{method} {path} failed after retries")

    def _list(self, path, **params):
        out, page = [], 1
        while True:
            body = self._req("GET", path, params={**params, "page": page, "page_size": 50})
            rows = body.get("data", [])
            out += rows
            if not rows or len(out) >= body.get("total", 0):
                return out
            page += 1

    def list_accounts(self, **filters):
        return self._list("/accounts", **filters)

    def list_contacts(self, **filters):
        return self._list("/contacts", **filters)

    def get_account(self, account_id):
        return _unwrap(self._req("GET", f"/accounts/{account_id}"))

    def patch_account(self, account_id, fields):
        return _unwrap(self._req("PATCH", f"/accounts/{account_id}", json=fields))

    def create_account(self, fields):
        return _unwrap(self._req("POST", "/accounts", json=fields))


class SnapshotCRM:
    """In-memory CRM loaded from curl dumps; persists writes to a JSON file."""

    def __init__(self, snap_dir, state_file=None):
        self.state_file = state_file
        if state_file and os.path.exists(state_file):
            st = json.load(open(state_file))
            self.accounts, self.contacts = st["accounts"], st["contacts"]
        else:
            self.accounts = {a["account_id"]: a for f in sorted(glob.glob(f"{snap_dir}/accounts_*.json"))
                             for a in json.load(open(f))["data"]}
            self.contacts = [c for f in sorted(glob.glob(f"{snap_dir}/contacts_*.json"))
                             for c in json.load(open(f))["data"]]
        self.parent_names = {a["account_id"]: a["name"] for a in self.accounts.values()}

    def _save(self):
        if self.state_file:
            json.dump({"accounts": self.accounts, "contacts": self.contacts}, open(self.state_file, "w"))

    def list_accounts(self, **filters):
        q = filters.get("q", "").lower()
        return [dict(a) for a in self.accounts.values() if q in a["name"].lower()]

    def list_contacts(self, **_):
        return [dict(c) for c in self.contacts]

    def get_account(self, account_id):
        return dict(self.accounts[account_id])

    def patch_account(self, account_id, fields):
        a = self.accounts[account_id]
        a.update(fields)
        if "parent_id" in fields:
            a["parent_name"] = self.parent_names.get(fields["parent_id"], "")
        self._save()
        return dict(a)

    def create_account(self, fields):
        new_id = "001SIM" + "".join(random.choices(string.ascii_uppercase + string.digits, k=12))
        base = {k: "" for k in ("parent_id", "billing_street", "billing_city", "billing_state", "billing_zip",
                                "care_type", "phone", "chow_current_account", "duplicate_of_account", "note")}
        a = {**base, "account_id": new_id, "status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0,
             "created_by_candidate": True, **fields}
        a["parent_name"] = self.parent_names.get(a["parent_id"], "")
        self.accounts[new_id] = a
        self._save()
        return dict(a)


def get_crm():
    if C.OFFLINE_DIR:
        state = os.path.join(os.path.dirname(os.path.abspath(C.DB_PATH)), "offline_crm.json")
        return SnapshotCRM(C.OFFLINE_DIR, state_file=state)
    return CRM()
