"""Local review app. Nothing reaches the CRM unless a reviewer approves it here."""
import json, traceback
from flask import Flask, redirect, render_template_string, request, url_for
from . import apply as applier, config as C, crm as crm_mod, pipeline, store

app = Flask(__name__)
KINDS = {"chow": "Change of ownership", "reparent": "Reparent", "create": "New account", "update": "Field fix",
         "duplicate": "Duplicate", "orphan": "Not on website", "chow_link": "Link to new owner"}
LABELS = {"billing_street": "Street", "billing_city": "City", "billing_state": "State", "billing_zip": "Zip",
          "care_type": "Care type", "parent_id": "Parent", "duplicate_of_account": "Duplicate of",
          "chow_current_account": "CHOW current account", "status": "Status", "name": "Name"}

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bellhaven CRM review</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--ink:#1D2433;--muted:#5B6576;--paper:#F3F5F7;--sheet:#FFFFFF;--rule:#D6DCE3;
--old:#F6E3E1;--old-ink:#8E2F26;--new:#DDF0E6;--new-ink:#1E6A45;
--k-chow:#9A5B00;--k-reparent:#B7791F;--k-create:#2A63A6;--k-update:#5A4E9C;--k-duplicate:#6E4B3A;--k-orphan:#A33A3A;--k-chow_link:#9A5B00}
*{box-sizing:border-box}
body{margin:0;font-family:"Public Sans",system-ui,-apple-system,Segoe UI,sans-serif;color:var(--ink);background:var(--paper);
font-size:15px;line-height:1.5;font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;gap:16px;align-items:baseline;justify-content:space-between;padding:20px 28px 0;max-width:1180px;margin:auto}
h1{font-size:22px;font-weight:700;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px}
nav{display:flex;gap:4px;padding:14px 28px 0;max-width:1180px;margin:auto;border-bottom:1px solid var(--rule)}
nav a{padding:8px 14px;color:var(--muted);text-decoration:none;border-bottom:3px solid transparent;font-weight:500}
nav a[aria-current]{color:var(--ink);border-color:var(--ink)}
main{max-width:1180px;margin:auto;padding:20px 28px 80px}
.flash{background:var(--sheet);border-left:4px solid var(--k-create);padding:10px 14px;margin-bottom:16px}
.flash.err{border-color:var(--k-orphan)}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
.filters a{font-size:13px;padding:4px 10px;border:1px solid var(--rule);border-radius:999px;color:var(--ink);text-decoration:none;background:var(--sheet)}
.filters a[aria-current]{background:var(--ink);color:#fff;border-color:var(--ink)}
.item{background:var(--sheet);border:1px solid var(--rule);border-left:6px solid var(--kc);margin-bottom:14px}
.item>summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:150px 1fr auto;gap:14px;align-items:center;padding:14px 18px}
.item>summary::-webkit-details-marker{display:none}
.kind{font-size:13px;font-weight:600;color:var(--kc)}
.title{font-weight:600}
.meta{font-size:13px;color:var(--muted)}
.body{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:24px;padding:4px 18px 18px}
@media (max-width:820px){.body{grid-template-columns:1fr}.item>summary{grid-template-columns:1fr}}
h3{font-size:13px;font-weight:600;color:var(--muted);margin:14px 0 6px}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:500;color:var(--muted);width:24%}
.was{background:var(--old);color:var(--old-ink);text-decoration:line-through;text-decoration-thickness:1px}
.now{background:var(--new);color:var(--new-ink);font-weight:600}
.opname{font-size:13px;margin:12px 0 4px;font-weight:600}
ul.ev{margin:0;padding-left:18px}ul.ev li{margin-bottom:6px}
.sop{border:1px solid var(--k-chow);padding:10px 12px;margin-top:12px}
.sop strong{color:var(--k-chow)}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px}
button{font:inherit;font-weight:600;padding:8px 16px;border:1px solid var(--ink);background:var(--sheet);color:var(--ink);cursor:pointer}
button.approve{background:var(--new-ink);border-color:var(--new-ink);color:#fff}
input[type=text]{font:inherit;padding:7px 10px;border:1px solid var(--rule);min-width:220px}
:focus-visible{outline:3px solid var(--k-create);outline-offset:2px}
.status{font-size:13px;font-weight:600}
.empty{background:var(--sheet);border:1px dashed var(--rule);padding:28px;text-align:center;color:var(--muted)}
.rep td:first-child{white-space:nowrap}
</style></head><body>
<header><h1>Bellhaven CRM review</h1>
<span class="sub">Last run {{ last.finished_at or "never" }} &middot; {{ counts.pending }} waiting for review</span></header>
<nav>
<a href="{{ url_for('queue') }}" {% if view=='queue' %}aria-current="page"{% endif %}>Review queue ({{ counts.pending }})</a>
<a href="{{ url_for('history') }}" {% if view=='history' %}aria-current="page"{% endif %}>Decided ({{ counts.decided }})</a>
<a href="{{ url_for('report') }}" {% if view=='report' %}aria-current="page"{% endif %}>Match report</a>
</nav>
<main>
{% for cls, msg in flashes %}<div class="flash {{ cls }}">{{ msg }}</div>{% endfor %}
{% if view=='report' %}
  <form method="post" action="{{ url_for('run_now') }}" class="actions" style="margin:0 0 16px"><button>Run pipeline now</button>
  <span class="meta">Reads the website and CRM and adds any new proposals. It never writes to the CRM.</span></form>
  {% if meta %}<p class="meta">Crawl found {{ meta.found }} communities; homepage claims {{ meta.homepage_claimed }}, directory lists {{ meta.directory_claimed }}.
  {% for slug, pages in meta.linked_from.items() if 'communities' not in (pages|join) %} {{ slug }} is linked only from {{ pages|join(', ') }}.{% endfor %}</p>{% endif %}
  <table class="rep"><tr><th>Outcome</th><th>Website location</th><th>CRM account</th></tr>
  {% for r in report %}<tr><td>{{ rlabels[r.class] }}</td><td>{{ r.name if r.slug else '' }}</td>
  <td>{{ r.account_id or '' }}{% if not r.slug %} {{ r.name }}{% endif %}{% if r.duplicates %} plus duplicates {{ r.duplicates|join(', ') }}{% endif %}</td></tr>{% endfor %}
  </table>
{% else %}
  <div class="filters">{% for k, label in kinds.items() %}<a href="?kind={{ k }}" {% if kind==k %}aria-current="true"{% endif %}>{{ label }} ({{ kc.get(k,0) }})</a>{% endfor %}
  {% if kind %}<a href="?">Show all</a>{% endif %}</div>
  {% for p in items %}
  <details class="item" style="--kc:var(--k-{{ p.kind }})" {% if view=='queue' %}open{% endif %} id="{{ p.id }}">
   <summary><span class="kind">{{ kinds[p.kind] }}</span><span class="title">{{ p.title }}</span>
   <span class="meta">{% if view=='history' %}<span class="status">{{ p.status }}</span> by {{ p.decided_by or 'system' }}{% else %}{{ p.confidence }} confidence{% endif %}</span></summary>
   <div class="body"><div>
    {% for op in p.ops %}
     <div class="opname">{% if op.op=='create' %}Create account{% else %}Update {{ p.accounts.get(op.account_id,{}).get('name', op.account_id) }} ({{ op.account_id }}){% endif %}</div>
     <table>{% for f, v in op.set.items() %}{% set cur = p.accounts.get(op.account_id,{}).get(f) if op.op=='patch' else None %}
      <tr><th>{{ labels.get(f,f) }}</th><td>{% if op.op=='patch' %}<span class="was">{{ show(f, cur) }}</span> {% endif %}<span class="now">{{ show(f, v) }}</span></td></tr>{% endfor %}
      {% if op.note_append %}<tr><th>Note added</th><td>{{ op.note_append }}</td></tr>{% endif %}
     </table>
    {% endfor %}
    {% if p.site %}<h3>Website listing</h3><table>
     <tr><th>Name</th><td><a href="{{ p.site.url }}" target="_blank" rel="noopener">{{ p.site.name }}</a></td></tr>
     <tr><th>Address</th><td>{{ p.site.street }}, {{ p.site.city }}, {{ p.site.state }} {{ p.site.zip }}</td></tr>
     <tr><th>Care</th><td>{{ p.site.care|join(', ') }}</td></tr><tr><th>Administrator</th><td>{{ p.site.administrator }}</td></tr></table>{% endif %}
   </div><div>
    <h3>Why</h3><ul class="ev">{% for e in p.evidence %}<li>{{ e }}</li>{% endfor %}</ul>
    {% if p.sop %}<div class="sop"><strong>Billing SOP check: {{ p.sop.outcome }}</strong><br>
     Lifetime revenue {{ '{:,.0f}'.format(p.sop.lifetime_revenue) }}, outstanding AR {{ '{:,.0f}'.format(p.sop.outstanding_ar) }}.<br>{{ p.sop.rule }}.
     Rechecked against live values at approval.</div>{% endif %}
    <h3>CRM records involved</h3><table>{% for a in p.accounts.values() %}
     <tr><th>{{ a.account_id }}</th><td>{{ a.name }}<br><span class="meta">{{ a.parent_name or 'No parent' }} &middot; {{ a.billing_street }}, {{ a.billing_city }} {{ a.billing_zip }} &middot; {{ a.care_type }} &middot; {{ a.status }}</span></td></tr>{% endfor %}</table>
    {% if view=='queue' %}
     <div class="actions">
      <form method="post" action="{{ url_for('approve', pid=p.id) }}"><button class="approve">Approve and write to CRM</button></form>
      <form method="post" action="{{ url_for('reject', pid=p.id) }}" style="display:flex;gap:8px">
       <input type="text" name="reason" placeholder="Reason for rejecting (optional)" aria-label="Reason for rejecting"><button>Reject</button></form>
     </div>
    {% else %}
     {% if p.reason %}<p class="meta">Reviewer note: {{ p.reason }}</p>{% endif %}
     {% if p.result %}<p class="meta">{{ p.result }}</p>{% endif %}
     {% if p.status in ('failed',) %}<form method="post" action="{{ url_for('approve', pid=p.id) }}"><button>Retry write</button></form>{% endif %}
    {% endif %}
   </div></div></details>
  {% else %}
   <div class="empty">{% if view=='queue' %}Nothing waiting for review. Run the pipeline from the Match report tab to check for new changes.{% else %}No decisions yet.{% endif %}</div>
  {% endfor %}
{% endif %}
</main></body></html>"""

FLASH = []
RLABELS = {"match": "Confident match", "fix": "Match, needs fix", "new": "No CRM account", "chow": "Change of ownership",
           "orphan": "Not on website", "departed": "Moved to another owner"}


def _names():
    db = store.connect()
    names = {}
    for r in db.execute("SELECT payload FROM proposals"):
        for a in json.loads(r["payload"])["accounts"].values():
            names[a["account_id"]] = a["name"]
            if a.get("parent_id"):
                names.setdefault(a["parent_id"], a.get("parent_name"))
    return names


def _render(view):
    db = store.connect()
    kind = request.args.get("kind", "")
    where = "status='pending'" if view == "queue" else "status NOT IN ('pending','superseded')"
    rows = [dict(r) for r in db.execute(f"SELECT * FROM proposals WHERE {where} ORDER BY kind, title")]
    items, kc = [], {}
    for r in rows:
        kc[r["kind"]] = kc.get(r["kind"], 0) + 1
        if kind and r["kind"] != kind:
            continue
        p = json.loads(r["payload"])
        p.update(status=r["status"], decided_by=r["decided_by"], reason=r["reason"], result=r["result"])
        items.append(p)
    last = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    counts = {"pending": db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0],
              "decided": db.execute("SELECT COUNT(*) FROM proposals WHERE status NOT IN ('pending','superseded')").fetchone()[0]}
    names = _names()
    def show(field, v):
        if v in (None, ""):
            return "blank"
        if v == "$new":
            return "the new account created above"
        if field in ("parent_id", "duplicate_of_account", "chow_current_account") and v in names:
            return f"{names[v]} ({v})"
        return v
    flashes, FLASH[:] = list(FLASH), []
    return render_template_string(PAGE, view=view, items=items, kinds=KINDS, kind=kind, kc=kc, labels=LABELS,
                                  show=show, counts=counts, last=dict(last) if last else {}, flashes=flashes,
                                  report=json.loads(last["report"] or "[]") if last else [],
                                  meta=json.loads(last["meta"] or "{}") if last else {}, rlabels=RLABELS)


@app.get("/")
def queue():
    return _render("queue")


@app.get("/decided")
def history():
    return _render("history")


@app.get("/report")
def report():
    return _render("report")


@app.post("/p/<pid>/approve")
def approve(pid):
    db = store.connect()
    row = store.get(db, pid)
    if not row or row["status"] not in ("pending", "failed"):
        FLASH.append(("err", "That proposal was already decided."))
        return redirect(url_for("queue"))
    store.set_status(db, pid, "approved", decided_at=store.now(), decided_by=C.REVIEWER)
    try:
        refs = applier.run(db, pid, crm_mod.get_crm())
        msg = "Written to CRM." + (f" New account {refs['new']}." if refs.get("new") else "")
        store.set_status(db, pid, "applied", result=msg)
        FLASH.append(("", f"Applied: {row['title']}. {msg}"))
    except applier.Stale as e:
        store.set_status(db, pid, "stale", result=f"Not written: {e}")
        FLASH.append(("err", f"Not written, CRM changed since proposal: {e}"))
    except Exception as e:
        store.set_status(db, pid, "failed", result=f"Write failed: {e}")
        FLASH.append(("err", f"Write failed and can be retried from Decided: {e}"))
        traceback.print_exc()
    return redirect(url_for("queue"))


@app.post("/p/<pid>/reject")
def reject(pid):
    db = store.connect()
    row = store.get(db, pid)
    if row and row["status"] == "pending":
        store.set_status(db, pid, "rejected", decided_at=store.now(), decided_by=C.REVIEWER,
                         reason=request.form.get("reason", "").strip())
        FLASH.append(("", f"Rejected: {row['title']}. It will not be proposed again."))
    return redirect(url_for("queue"))


@app.post("/run")
def run_now():
    try:
        pipeline.main()
        FLASH.append(("", "Pipeline finished."))
    except SystemExit as e:
        FLASH.append(("err", str(e)))
    return redirect(url_for("report"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=False)
