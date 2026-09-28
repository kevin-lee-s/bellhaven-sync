"""All tunables in one place. Secrets come from the environment only."""
import os

BASE_URL = os.environ.get("BH_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_URL = BASE_URL + "/api/v1"
TOKEN = os.environ.get("BH_TOKEN", "")
DB_PATH = os.environ.get("BH_DB", "state.db")
OFFLINE_DIR = os.environ.get("BH_OFFLINE", "")          # snapshot dir for offline runs/tests
REVIEWER = os.environ.get("BH_REVIEWER", os.environ.get("USER", "reviewer"))

PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
# Operators whose communities Bellhaven absorbed (per /about). Used only to rank
# duplicate survivors; never to decide ownership on its own.
ACQUIRED_PARENTS = [
    "Harborview Care Group (Parent Account)",
    "Cedar Trail Communities (Parent Account)",
]

# Website care offering -> CRM care_type vocabulary
CARE_MAP = {
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Independent Living": "Independent Living",
}

# Safety: abort the run if the scrape returns far fewer locations than last time
# (site outage or markup change would otherwise flag every account as an orphan).
MIN_SITE_FRACTION = 0.8
NOTE_TAG = "bellhaven-sync"
