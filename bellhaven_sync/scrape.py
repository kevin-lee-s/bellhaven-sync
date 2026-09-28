"""Crawl the Bellhaven website and return every community listing.

The directory is paginated and is NOT complete (the homepage links at least one
community the directory omits), so we collect every /communities/<slug> link
found on any crawled page rather than trusting the directory alone.
"""
import os, re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from . import config as C

SEEDS = ["/", "/about", "/communities"]


def live_fetcher(base=C.BASE_URL):
    s = requests.Session()
    def fetch(path):
        r = s.get(base + path, timeout=30)
        r.raise_for_status()
        return r.text
    return fetch


def snapshot_fetcher(snap_dir):
    def fetch(path):
        u = urlparse(path)
        if u.path == "/":
            name = "home.html"
        elif u.path == "/about":
            name = "about.html"
        elif u.path == "/communities":
            m = re.search(r"page=(\d+)", u.query)
            name = f"communities_{m.group(1) if m else 1}.html"
        else:
            name = f"detail_{u.path.rsplit('/', 1)[-1]}.html"
        return open(os.path.join(snap_dir, name)).read()
    return fetch


def _in_scope(path):
    return path in ("/", "/about", "/communities") or path.startswith("/communities/")


def parse_detail(html, path):
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for dt in soup.select("dl.detail dt"):
        dd = dt.find_next_sibling("dd")
        fields[dt.get_text(strip=True).lower()] = dd
    addr_lines = [x.strip() for x in fields["address"].get_text("\n").split("\n") if x.strip()]
    m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", addr_lines[-1])
    if not m:
        raise ValueError(f"unparseable address on {path}: {addr_lines}")
    care_dd = fields.get("care offerings")
    return {
        "slug": path.rsplit("/", 1)[-1],
        "url": C.BASE_URL + path,
        "name": soup.h1.get_text(strip=True),
        "street": " ".join(addr_lines[:-1]),
        "city": m.group(1).strip(),
        "state": m.group(2),
        "zip": m.group(3),
        "care": [b.get_text(strip=True) for b in care_dd.select(".badge")] if care_dd else [],
        "administrator": fields["administrator"].get_text(strip=True) if "administrator" in fields else "",
        "phone": fields["phone"].get_text(strip=True) if "phone" in fields else "",
    }


def crawl(fetch):
    queue, seen, details, meta = list(SEEDS), set(), {}, {"linked_from": {}}
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        html = fetch(path)
        if path.startswith("/communities/"):
            details[path] = parse_detail(html, path)
            continue
        text = BeautifulSoup(html, "html.parser").get_text(" ")
        if path == "/":
            m = re.search(r"serve (\d+) communities", text)
            meta["homepage_claimed"] = int(m.group(1)) if m else None
        m = re.search(r"(\d+) communities listed", text)
        if m:
            meta["directory_claimed"] = int(m.group(1))
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            u = urlparse(urljoin("http://x" + path, a["href"]))
            if u.netloc not in ("x", urlparse(C.BASE_URL).netloc):
                continue
            nxt = u.path + (f"?{u.query}" if u.query else "")
            if _in_scope(u.path) and nxt not in seen:
                queue.append(nxt)
                if u.path.startswith("/communities/"):
                    meta["linked_from"].setdefault(u.path.rsplit("/", 1)[-1], set()).add(path.split("?")[0])
    locs = sorted(details.values(), key=lambda d: d["slug"])
    meta["linked_from"] = {k: sorted(v) for k, v in meta["linked_from"].items()}
    meta["found"] = len(locs)
    return locs, meta


def get_fetcher():
    return snapshot_fetcher(C.OFFLINE_DIR) if C.OFFLINE_DIR else live_fetcher()
