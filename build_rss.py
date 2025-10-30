#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, sys, traceback, hashlib
import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://sport.virgilio.it/guida-tv/"

IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}
TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")

# ----------------- helpers for debug -----------------
def _write_file(path: str, content: str, mode="w", enc="utf-8"):
    try:
        with open(path, mode, encoding=enc) as f:
            f.write(content)
    except Exception:
        pass

# ----------------- fetch & render -----------------
def fetch_html() -> str:
    """Render the page (JS) and return final HTML. Always try to write debug files."""
    _write_file("debug_stage.txt", "starting playwright...\n")
    console_lines = []
    page = None
    try:
        with sync_playwright() as p:
            launch_args = ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--disable-setuid-sandbox"]
            browser = p.chromium.launch(headless=True, args=launch_args)
            page = browser.new_page()
            page.on("console", lambda msg: console_lines.append(f"[{msg.type()}] {msg.text()}"))

            _write_file("debug_stage.txt", "navigating...\n")
            page.goto(URL, timeout=90_000, wait_until="networkidle")

            _write_file("debug_stage.txt", "waiting for selectors...\n", mode="a")
            try:
                page.wait_for_selector("table", timeout=60_000)
            except Exception:
                page.wait_for_selector("h2", timeout=30_000)

            html = page.content()
            _write_file("debug.html", html)
            try:
                page.screenshot(path="debug.png", full_page=True)
            except Exception:
                pass

            browser.close()
            _write_file("playwright_console.log", "\n".join(console_lines))
            return html

    except Exception as e:
        _write_file("playwright_console.log", "\n".join(console_lines))
        _write_file("debug_error.txt", f"{e}\n\n{traceback.format_exc()}")
        try:
            if page:
                _write_file("debug.html", page.content())
        except Exception:
            pass
        raise

# ----------------- date parsing -----------------
def parse_date_heading(text: str, today: datetime.date | None = None) -> datetime.date | None:
    text = re.sub(r"\s+", " ", text).strip()
    if today is None:
        today = datetime.date.today()
    m = re.search(r"(\d{1,2})\s+([A-Za-zàéìòù]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        d = int(m.group(1)); month_name = m.group(2).lower(); y = int(m.group(3))
        month = IT_MONTHS.get(month_name)
        if month:
            return datetime.date(y, month, d)
    if re.search(r"\bOggi\b", text, re.IGNORECASE): return today
    if re.search(r"\bDomani\b", text, re.IGNORECASE): return today + datetime.timedelta(days=1)
    return None

# ----------------- structural table parsing -----------------
def normalize_header(s: str) -> str:
    s = (s or "").strip().lower()
    # unify common header variations
    s = s.replace("dove vederla", "canali")
    s = s.replace("dove vedere", "canali")
    s = s.replace("canale", "canali")
    s = s.replace("ora inizio", "ora")
    return s

def header_index_map(table: BeautifulSoup) -> dict:
    """
    Build a header→index map for a given table using THEAD if present, else first row.
    We look for keys among: 'ora', 'sport', 'competizione', 'evento', 'canali'
    """
    headers = []
    thead = table.find("thead")
    if thead:
        ths = thead.find_all("th")
        headers = [normalize_header(th.get_text(" ", strip=True)) for th in ths]
    else:
        first_tr = table.find("tr")
        if first_tr:
            ths = first_tr.find_all(["th","td"])
            headers = [normalize_header(th.get_text(" ", strip=True)) for th in ths]

    wanted = ["ora", "sport", "competizione", "evento", "canali"]
    idx = {}
    for w in wanted:
        for i, h in enumerate(headers):
            if w == h or w in h:
                idx[w] = i
                break
    return idx

def extract_rows_from_table(table: BeautifulSoup) -> list[dict]:
    """
    Extract rows using header positions. Falls back to a lenient guess if headers missing.
    """
    idx = header_index_map(table)

    # body rows
    body = table.find("tbody") or table
    rows = []
    for tr in body.find_all("tr"):
        tds = tr.find_all(["td","th"])
        if not tds:
            continue

        def cell(i):
            return tds[i].get_text(" ", strip=True) if 0 <= i < len(tds) else ""

        # Use mapped indices if available
        if idx:
            time_val = cell(idx.get("ora", 0)).strip()
            sport = cell(idx.get("sport", 1)).strip()
            competition = cell(idx.get("competizione", 2)).strip()
            event = cell(idx.get("evento", 3)).strip()
            channels = cell(idx.get("canali", len(tds)-1)).strip()
        else:
            # Very defensive fallback: detect time cell, then split rest by typical order
            texts = [c.get_text(" ", strip=True) for c in tds]
            time_val, rest_cells = None, []
            if texts and TIME_RE.match(texts[0]):
                time_val = texts[0]
                rest_cells = texts[1:]
            else:
                for i, tx in enumerate(texts):
                    if TIME_RE.match(tx):
                        time_val = tx
                        rest_cells = texts[:i] + texts[i+1:]
                        break
            if not time_val:
                continue
            # heuristic order: sport, competition, evento, canali
            sport = rest_cells[0] if len(rest_cells) > 0 else ""
            competition = rest_cells[1] if len(rest_cells) > 1 else ""
            # last cell likely channels
            channels = rest_cells[-1] if len(rest_cells) > 2 else ""
            event = " ".join(rest_cells[2:-1]) if len(rest_cells) > 3 else (rest_cells[2] if len(rest_cells)>2 else "")

        # sanity: if channels accidentally looks like a datetime, swap with event
        if TIME_RE.match(channels):
            channels, event = event, channels

        rows.append({
            "time": time_val,
            "sport": sport,
            "competition": competition,
            "title": event,
            "channels": channels,
        })
    return rows

def iter_rows_grouped_by_date_from_container(container: BeautifulSoup):
    """
    Walk inside the main guide container:
    for each H2 (date section), parse following tables until next H2.
    """
    groups: dict[datetime.date, list] = {}
    all_h2 = container.find_all("h2")
    for h2 in all_h2:
        section_date = parse_date_heading(h2.get_text(" "))
        if not section_date:
            continue
        rows = groups.setdefault(section_date, [])

        # gather all tables until next H2
        for sib in h2.next_siblings:
            if getattr(sib, "name", None) == "h2":
                break
            if getattr(sib, "name", None) in (None, "script", "style"):
                continue
            for table in getattr(sib, "find_all", lambda *_: [])("table"):
                rows.extend(extract_rows_from_table(table))

    # sort rows in each date by time
    for d, lst in groups.items():
        lst.sort(key=lambda r: r["time"])

    for d in sorted(groups.keys()):
        yield d, groups[d]

# ----------------- build preserved-format index.html -----------------
def collect_styles_and_fragment(html: str):
    soup = BeautifulSoup(html, "html.parser")

    # Collect stylesheet hrefs (absolute)
    hrefs = []
    for link in soup.select("link[rel=stylesheet]"):
        href = link.get("href")
        if href:
            hrefs.append(urljoin(URL, href))

    # Prefer the main schedule container, fall back to main/body
    container = soup.select_one(".guida-tv") or soup.select_one("main") or soup.select_one("#main") or soup.body
    if not container:
        fragment = soup.body.decode() if soup.body else html
        return hrefs, fragment, None

    # Add anchor ids before each H2 so #YYYY-MM-DD works
    today = datetime.date.today()
    for h2 in container.find_all("h2"):
        date_val = parse_date_heading(h2.get_text(" "), today=today)
        if date_val:
            anchor = soup.new_tag("a", id=date_val.isoformat())
            h2.insert_before(anchor)

    return hrefs, container.decode(), container

def build_index_html_from_live(style_hrefs, fragment_html) -> str:
    wrapper_css = """
      body{margin:16px;background:#fff;color:#000;font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
      .source-note{margin:8px 0 16px;font-size:12px;color:#444}
    """
    head_links = "\n".join(f"<link rel='stylesheet' href='{href}' crossorigin>" for href in style_hrefs)
    return f"""<!doctype html>
<html lang="it">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guida TV Sport — mirror</title>
{head_links}
<style>{wrapper_css}</style>
<body>
  <div class="source-note">Fonte originale: <a href="{URL}" target="_blank" rel="noopener">{URL}</a></div>
  {fragment_html}
</body>
</html>"""

# ----------------- RSS building -----------------
def esc(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def to_rfc822_europe_rome(date_obj: datetime.date, time_str: str | None = None) -> str:
    if time_str:
        hh, mm = map(int, time_str.split(":"))
    else:
        hh, mm = 0, 0
    dt = datetime.datetime(date_obj.year, date_obj.month, date_obj.day, hh, mm, tzinfo=ZoneInfo("Europe/Rome"))
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

def make_guid(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

def render_table_html(date_obj: datetime.date, rows: list[dict]) -> str:
    # Minimal, used only inside RSS description
    css = (
        "table{border-collapse:collapse;width:100%;max-width:980px}"
        "th,td{border:1px solid #ddd;padding:6px 8px;font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial}"
        "th{background:#f5f5f5;text-align:left}.time{white-space:nowrap;width:1%}"
    )
    head = (f"<style>{css}</style>"
            f"<a id='{date_obj.isoformat()}'></a>"
            f"<h2>{date_obj.strftime('%A %d %B %Y').title()}</h2>")
    body = ["<table><thead><tr><th class='time'>Ora</th><th>Sport</th><th>Competizione</th><th>Evento</th><th>Canali</th></tr></thead><tbody>"]
    for r in rows:
        body.append("<tr>"
                    f"<td class='time'>{esc(r['time'])}</td>"
                    f"<td>{esc(r['sport'] or '')}</td>"
                    f"<td>{esc(r['competition'] or '')}</td>"
                    f"<td>{esc(r['title'] or '')}</td>"
                    f"<td>{esc(r['channels'] or '')}</td>"
                    "</tr>")
    body.append("</tbody></table>")
    return head + "".join(body)

def build_rss_tables(grouped: list[tuple[datetime.date, list[dict]]], site_base: str,
                     now_utc: datetime.datetime | None = None) -> str:
    if now_utc is None:
        now_utc = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    now_rfc822 = now_utc.strftime("%a, %d %b %Y %H:%M:%S %z")

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0">')
    out.append('<channel>')
    out.append('<title>Virgilio Sport – Guida TV (per giorno)</title>')
    out.append(f'<link>{site_base}/index.html</link>')
    out.append('<description>Un item per ogni giorno; il contenuto è una tabella HTML con gli eventi (formattazione preservata nella pagina mirror).</description>')
    out.append(f'<lastBuildDate>{now_rfc822}</lastBuildDate>')
    out.append('<ttl>60</ttl>')

    for d, rows in grouped:
        title = d.strftime("Guida TV – %A %d %B %Y").title()
        anchor = d.isoformat()
        link = f"{site_base}/index.html#{anchor}"
        table_html = render_table_html(d, rows)
        guid = make_guid(f"{d.isoformat()}|{len(rows)}")
        pub = to_rfc822_europe_rome(d)

        out.append("<item>")
        out.append(f"<title>{esc(title)}</title>")
        out.append(f"<link>{link}</link>")
        out.append(f"<guid isPermaLink=\"false\">{guid}</guid>")
        out.append(f"<pubDate>{pub}</pubDate>")
        out.append("<description><![CDATA[")
        out.append(table_html)
        out.append("]]></description>")
        out.append("</item>")

    out.append("</channel></rss>")
    return "\n".join(out)

# ----------------- main -----------------
def main():
    try:
        html = fetch_html()
    except Exception:
        print("FATAL: fetch_html failed; see debug artifacts.")
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")

    # Build preserved-format index.html (CSS + exact fragment)
    style_hrefs, fragment_html, container = collect_styles_and_fragment(html)
    index_html = build_index_html_from_live(style_hrefs, fragment_html)
    _write_file("index.html", index_html)

    # Parse rows structurally from tables within the same container (for RSS)
    if container is None:
        # fallback: parse on whole doc if container not found
        container = soup
    grouped = list(iter_rows_grouped_by_date_from_container(container))

    site_base = "https://jusseppe88.github.io/virgilio-sport-rss"
    rss = build_rss_tables(grouped, site_base=site_base)
    _write_file("rss_tables.xml", rss)
    _write_file("rss.xml", rss)

    print(f"Wrote index.html with {len(grouped)} tables and rss_tables.xml")

if __name__ == "__main__":
    main()
