# Bellhaven CRM sync

Scrapes every Bellhaven community from the website, matches each one to the CRM, and queues proposed fixes for a human reviewer. Approved proposals write to the CRM through the API. Nothing writes without approval, and a rerun never proposes an item a reviewer already decided.

## Run it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export BH_TOKEN=<your token>
python -m bellhaven_sync.pipeline        # scrape, match, queue proposals (read only)
python -m bellhaven_sync.app             # review at http://127.0.0.1:5057
python tests/test_offline.py             # end to end test against snapshot/ (no network)
```

Set `BH_OFFLINE=snapshot` to run the pipeline and app against the bundled snapshot instead of the live API.

## Layout

| File | Job |
|---|---|
| `scrape.py` | Crawls the homepage, about page and every directory page, and collects each `/communities/<slug>` link it finds anywhere. |
| `crm.py` | API client with pagination and retries; `SnapshotCRM` offers the same interface over saved dumps for tests. |
| `match.py` | Matches locations to accounts, classifies them, and emits proposals with evidence. |
| `store.py` | SQLite record of every run, proposal and decision. |
| `apply.py` | Executes an approved proposal with stale checks, an SOP recheck and resumable steps. |
| `app.py` | Review app. |
| `.github/workflows/bellhaven-sync.yml`, `deploy/crontab` | Daily schedule, two options. |

## What the data showed

The directory says 34 communities; the homepage says 35 and links Bellhaven Meadows of Findlay, which the directory omits. The scraper therefore crawls links from every page rather than trusting the directory, and the match report shows which pages linked each community.

Website phone numbers look unreliable (Findlay, Ohio lists a northern Michigan area code, and area codes are scattered across states throughout), so phone plays no part in matching. The administrator named on each listing is useful: most CRM accounts carry that person as an Administrator contact, which confirms matches where names differ, such as Riverbend Manor Care Center being the Chagrin Falls community.

The About page says Bellhaven absorbed Harborview Care Group in 2025 and some Cedar Trail communities in 2026. Most of the mess traces to those deals: facilities still under the old parent, or a second copy of the account created under the old operator's name.

## Matching

A CRM account is a candidate for a website location when it is in the same state and either

1. its normalized street matches (suffixes, directions and punctuation folded, so 1250 Northwest Franklin St equals 1250 NW Franklin Street) and the zip or city matches, or
2. the city matches and the account has the website administrator as a contact, or its name is nearly identical.

Parent accounts, accounts already marked as duplicates, and accounts already carrying a CHOW pointer are never candidates. A retired record cannot be matched again, which is part of what makes reruns stable.

When several accounts match one location, one survives. Ranking, in order: already under Bellhaven, has billing history, has the administrator contact, sits under an acquired operator, has the exact website street string, has the closest name, is Active, then account id as a final tiebreak so the choice is deterministic. Every other candidate gets `duplicate_of_account` set to the survivor and status Inactive, with a note naming the shared address.

The survivor is then compared field by field against the listing: name, street (only when the normalized form differs, so Road versus Rd is not churn), city, state, zip, care type and status. Care offerings map to CRM vocabulary (Short-Term Rehabilitation & Nursing to Skilled Nursing, Memory Support to Memory Care). Care type only changes when the CRM value is not among the offerings; the CRM holds one value and some communities offer two, so an account listing either is left alone.

## Classifications

| Class | Outcome |
|---|---|
| Confident match | No proposal. Shown in the match report. |
| Match needing a fix | Field update proposal: name, address, zip, care type, status. |
| Wrong parent | Reparent or CHOW proposal, decided by the billing SOP. |
| Duplicate | `duplicate_of_account` set to the survivor, status Inactive. |
| No CRM account | Create proposal under Bellhaven with website values. |
| Under Bellhaven, not on website | If another operator's account holds the same address, the facility changed hands (see below). Otherwise status Needs Review. |

## Billing SOP

Before any parent change the matcher reads `lifetime_revenue` and `outstanding_ar`. When both are above zero the proposal becomes a CHOW: create a new account under Bellhaven carrying the website values, then set `chow_current_account` on the old account to the new id. The old account is otherwise untouched: no name, parent, status or note change. When either is zero the existing account is reparented directly. The review card shows both figures and the rule applied, and the apply step rereads them from the live record at approval time; if the outcome would now differ (for example AR was paid off), the proposal is marked stale and nothing is written.

A CHOW is two writes. Progress is saved after each, and before creating an account the apply step looks for one already matching the name, address and parent. A crash between the two writes can therefore be retried without producing a second account; the offline test simulates exactly this.

## Judgment calls

**Accounts under Bellhaven that the website no longer lists.** Absence from a marketing site is weak evidence of closure, and the reviewer has no confirmed new owner to point to. These go to Needs Review with a note, and the parent stays unchanged. Inactive would hide a facility sales may still want to reach.

**Bellhaven of Sandusky.** It is off the website, and Millstone Care of Sandusky holds the same address under Millstone Health Partners, so the facility changed hands. Moving it to Millstone is a parent change, and it has revenue history and open AR, so the SOP requires preserving the old account. The successor account already exists, so no new account is created; the old account only gets `chow_current_account` pointing at Millstone's. Had it carried no open AR, the rule would mark it a duplicate of the Millstone account instead.

**Kettering.** Three accounts share 3313 Wilmington Pike, none under Bellhaven and none with revenue. The Harborview record survives because Harborview joined Bellhaven in full and its street string matches the website exactly; the Cedar Trail and unparented copies become duplicates. The survivor is then reparented and renamed.

**Ashtabula.** The CRM street is PO Box 517. Every other account stores a physical address, so the update replaces it with the listed street, and the note records the old value.

**Look-alikes left alone.** Amberly Manor in the CRM is in Colorado Springs under Juniper Point; the website's Amberly Manor is in Hudson, Ohio. Union Square Senior Living in New Albany sits at a different address with a different administrator. Neither is the website community, so both website communities get new accounts and the look-alikes are listed as rejected candidates on the create card.

## Reruns and scheduling

Each proposal id is a hash of its kind, target and exact operations, with no dates in it. A rerun that reaches the same conclusion yields the same id and is skipped whether it was approved, rejected, failed or stale. Applied changes also stop reproducing on their own, because the CRM now agrees with the website. Pending items a later run no longer produces are marked superseded, since the data changed underneath them. A proposal only resurfaces when the facts change, for example a community renamed again.

The pipeline refuses to run if the scrape returns under 80 percent of the previous run's count, so a site outage cannot flood the queue with false orphans. Two schedule options are included: a crontab for a single host running both pipeline and app (simplest, since they share one `state.db`), and a GitHub Actions workflow that keeps `state.db` on a `sync-state` branch. In production I would move state to Postgres so the scheduler and the app share it without file copies.

## Not done, and why

Contacts on duplicate losers stay where they are; relinking them to the survivor is a natural next proposal type. Administrator and phone mismatches between website and CRM contacts are reported as evidence only, since contacts were outside the brief. Care offerings beyond the first cannot be stored in a single field.
