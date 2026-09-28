"""Daily run: scrape site, pull CRM, propose changes. Never writes to the CRM."""
import json, sys
from . import config as C, crm as crm_mod, match, scrape, store


def main():
    db = store.connect()
    started = store.now()
    locs, meta = scrape.crawl(scrape.get_fetcher())
    prev = store.last_site_count(db)
    if not locs or (prev and len(locs) < C.MIN_SITE_FRACTION * prev):
        sys.exit(f"ABORT: scraped {len(locs)} locations vs {prev} last run; refusing to propose orphans.")
    crm = crm_mod.get_crm()
    accounts, contacts = crm.list_accounts(), crm.list_contacts()
    proposals, report = match.build(locs, accounts, contacts)
    run_id = db.execute("INSERT INTO runs (started_at, site_count, account_count, meta) VALUES (?,?,?,?)",
                        (started, len(locs), len(accounts), json.dumps(meta))).lastrowid
    new = store.record(db, run_id, proposals)
    db.execute("UPDATE runs SET finished_at=?, new_proposals=?, report=? WHERE id=?",
               (store.now(), new, json.dumps(report), run_id))
    notes = []
    if meta.get("homepage_claimed") and meta["homepage_claimed"] != meta.get("directory_claimed"):
        notes.append(f'homepage claims {meta["homepage_claimed"]}, directory {meta.get("directory_claimed")}, crawl found {len(locs)}')
    print(f"run {run_id}: {len(locs)} locations, {len(accounts)} accounts, {len(proposals)} proposals "
          f"({new} new, {len(proposals) - new} already known). {'; '.join(notes)}")


if __name__ == "__main__":
    main()
