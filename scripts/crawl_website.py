"""Crawl prosurfaceworks.com sitemap + blog/projects subpages into pages/*.md.

Output:
  prosurfaceworks_knowledge_base/
    pages/*.md          (clean markdown with frontmatter + source_url)

The runtime (prosurface_agent/data/store.py) reads pages/*.md directly and
chunks them live — no prebuilt index or chunks file needed.
"""
from __future__ import annotations

import re
import time
from datetime import date, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://www.prosurfaceworks.com"
SITEMAP = f"{BASE}/sitemap.xml"
OUT = Path(__file__).resolve().parents[1] / "prosurfaceworks_knowledge_base"
PAGES = OUT / "pages"

UA = {"User-Agent": "Mozilla/5.0 (compatible; ProSurfaceAgent-KB/1.0)"}

def classify(url: str) -> dict:
    p = urlparse(url).path.strip("/")
    parts = p.split("/") if p else []
    meta = {"page_type": "general", "category": "", "service": ""}
    if not parts:
        meta["page_type"] = "home"
    elif parts[0] == "services" and len(parts) == 1:
        meta["page_type"] = "services-index"
    elif parts[0] == "services" and len(parts) == 2:
        meta.update(page_type="category", category=parts[1])
    elif parts[0] == "services" and len(parts) >= 3:
        meta.update(page_type="service", category=parts[1], service=parts[2])
    elif parts[0] in ("about", "contact", "faq", "blog", "projects", "service-areas"):
        meta["page_type"] = parts[0].replace("-", "_")
        if len(parts) > 1:
            meta["page_type"] += "_detail"
    return meta


def clean_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title").get_text(strip=True) if soup.find("title") else ""
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.find("body") or soup
    text = main.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # Drop nav boilerplate lines repeated sitewide
    seen: dict[str, int] = {}
    out_lines = []
    for l in lines:
        if len(l) < 2:
            continue
        seen[l] = seen.get(l, 0) + 1
        out_lines.append(l)
    # dedupe consecutive duplicates
    dedup = []
    for l in out_lines:
        if not dedup or dedup[-1] != l:
            dedup.append(l)
    return title, "\n".join(dedup)


def slug(url: str) -> str:
    p = urlparse(url).path.strip("/").replace("/", "__") or "home"
    p = re.sub(r"[^a-z0-9_]+", "-", p.lower()).strip("-")
    return p[:120]


def fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  FAIL {url}: {e}")
        return None


def discover_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"])
        pu = urlparse(u)
        if pu.netloc.endswith("prosurfaceworks.com") and pu.path not in ("/", ""):
            links.add(pu._replace(fragment="", query="").geturl())
    return sorted(links)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    PAGES.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update(UA)

    print("Fetching sitemap...")
    xml = fetch(SITEMAP, session) or ""
    urls = re.findall(r"<loc>(.*?)</loc>", xml)
    urls = [u.replace("https://prosurfaceworks.com", BASE) for u in urls]
    print(f"sitemap urls: {len(urls)}")

    # Discover blog + projects subpages (cap to avoid explosion)
    extra: list[str] = []
    for hub in [f"{BASE}/blog/", f"{BASE}/projects/"]:
        html = fetch(hub, session)
        if html:
            for link in discover_links(html, hub):
                if link not in urls and ("/blog/" in link or "/projects/" in link):
                    # skip asset files
                    if re.search(r"\.(png|jpg|jpeg|webp|css|js|pdf)(\?|$)", link):
                        continue
                    extra.append(link)
        time.sleep(0.5)
    extra = sorted(set(extra))[:60]
    print(f"discovered blog/project subpages: {len(extra)}")

    all_urls = sorted(set(urls + extra))
    today = date.today().isoformat()
    count = 0

    for i, url in enumerate(all_urls, 1):
        print(f"[{i}/{len(all_urls)}] {url}")
        html = fetch(url, session)
        if not html:
            continue
        title, text = clean_text(html)
        if len(text) < 300:
            print(f"  skip thin ({len(text)} chars)")
            continue
        meta = classify(url)
        s = slug(url)
        md_path = PAGES / f"{s}.md"
        md_path.write_text(
            f"---\nsource_url: {url}\ntitle: {title}\n"
            f"page_type: {meta['page_type']}\ncategory: {meta['category']}\n"
            f"service: {meta['service']}\nlast_crawled: {today}\n---\n\n# {title}\n\n{title}\n\n{text}\n",
            encoding="utf-8",
        )
        count += 1
        time.sleep(0.4)

    print(f"DONE pages={count} -> {OUT}")


if __name__ == "__main__":
    main()
