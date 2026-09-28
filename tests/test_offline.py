"""End to end against the snapshot: propose, approve via the app, rerun, verify end state."""
import json, os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ.update(BH_OFFLINE=os.path.join(os.path.dirname(__file__), "..", "snapshot"),
                  BH_DB=os.path.join(tmp, "state.db"), BH_REVIEWER="test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bellhaven_sync import app as appmod, crm, pipeline, store, apply as applier

BH = "0015QAPLGS3FVYEEEM"
pipeline.main()
db = store.connect()
pend = [dict(r) for r in db.execute("SELECT id, kind, title FROM proposals WHERE status='pending'")]
assert len(pend) == 31, len(pend)

client = appmod.app.test_client()
assert client.get("/").status_code == 200 and client.get("/report").status_code == 200

# Simulate a crash mid-CHOW: create done, link not done. Retry must not create a second account.
chow = next(p for p in pend if p["kind"] == "chow")
c = crm.get_crm()
p = json.loads(store.get(db, chow["id"])["payload"])
new = c.create_account({**p["ops"][0]["set"]})
# progress lost on purpose; the create guard should find the account by name/address/parent
reject_one = next(p for p in pend if p["kind"] == "orphan")
client.post(f"/p/{reject_one['id']}/reject", data={"reason": "testing reject"})
for q in pend:
    if q["id"] != reject_one["id"]:
        client.post(f"/p/{q['id']}/approve")
statuses = [r[0] for r in db.execute("SELECT status FROM proposals")]
assert statuses.count("applied") == 30, statuses
c = crm.get_crm()
A = c.accounts
tiffin_like = [a for a in A.values() if a["name"] == p["ops"][0]["set"]["name"] and a["parent_id"] == BH]
assert len(tiffin_like) == 1, "CHOW retry created a duplicate account"
old = A[p["ops"][1]["account_id"]]
assert old["chow_current_account"] == tiffin_like[0]["account_id"] and old["parent_id"] != BH and old["note"] == ""

# Second run must propose nothing new
pipeline.main()
assert db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0] == 0
report = json.loads(db.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0])
site_rows = [r for r in report if r["slug"]]
assert len(site_rows) == 35 and all(r["class"] == "match" for r in site_rows), [r for r in site_rows if r["class"] != "match"]
live_bh = [a for a in A.values() if a["parent_id"] == BH and a["status"] == "Active"
           and not a["duplicate_of_account"] and not a["chow_current_account"]]
print("active Bellhaven facilities:", len(live_bh), "(35 on site + 1 rejected orphan)")
assert len(live_bh) == 36
print("ALL CHECKS PASSED")
