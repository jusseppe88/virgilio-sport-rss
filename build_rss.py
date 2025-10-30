#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, sys, traceback, hashlib
import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://sport.virgilio.it/guida-tv/"

IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}
TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")

def _write_file(path: str, content: str, mode="w", enc="utf-8"):
    try:
        with open(path, mode, encoding=enc) as f:
            f.write(content)
    except Exception:
        pass

def fetch_html() -> str:
    """Render the page and return final HTML. Always try to write debug files."""
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
        # If we *have* a page, dump whatever content exists
        try:
            if page:
                _write_file("debug.html", page.content())
        except Exception:
            pass
        raise

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

def iter_rows_grouped_by_date(soup: BeautifulSoup):
    groups: dict[datetime.date, list] = {}
    for h2 in soup.find_all("h2"):
        section_date = parse_date_heading(h2.get_text(" "))
        if not section_date:
            continue
        rows = groups.setdefault(section_date, [])
        for sib in h2.next_siblings:
            if getattr(sib, "name", None) == "h2":
                break
            if getattr(sib, "name", None) in (None, "script", "style"):
                continue
            for tr in getattr(sib, "find_all", lambda *_: [])("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td","th"])]
                if not cells:
                    continue
                time_str = None; rest_cells = []
                if TIME_RE.match(cells[0]):
                    time_str = TIME_RE.match(cells[0]).group(1); rest_cells = cells[1:]
                else:
                    for i,c in enumerate(cells):
                        m = TIME_RE.match(c)
                        if m:
                            time_str = m.group(1); rest_cells = cells[:i]+cells[i+1:]; break
                if not time_str:
                    continue
                rest = " ".join([x for x in rest_cells if x]).strip()
                if not rest:
                    continue
                sport, competition, title, channels = split_event_text(rest)
                rows.append({"time": time_str, "sport": sport, "competition": competition, "title": title, "channels": channels})
    for d, rows in groups.items():
        rows.sort(key=lambda r: r["time"])
    for d in sorted(groups.keys()):
        yield d, groups[d]

def split_event_text(rest: str):
    sport = competition = title = channels = None
    if ":" in rest:
        left, right = [x.strip() for x in rest.split(":", 1)]
        if "," in left:
            sport, competition = [x.strip() for x in left.split(",", 1)]
        else:
            sport = left
        title = right
    else:
        title = rest
    return sport, competition, title, channels

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
    css = (
        "table{border-collapse:collapse;width:100%;max-width:980px}"
        "th,td{border:1px solid #ddd;padding:6px 8px;font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial}"
        "th{background:#f5f5f5;text-align:left}"
        ".time{white-space:nowrap;width:1%}"
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

def build_index_html(grouped: list[tuple[datetime.date, list[dict]]]) -> str:
    parts = []
    parts.append("<!doctype html><html lang='it'><meta charset='utf-8'>"
                 "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                 "<title>Guida TV Sport – Tables</title>"
                 "<body style='margin:20px'>"
                 "<h1 style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial'>Guida TV Sport</h1>"
                 f"<p>Fonte: <a href='{URL}'>Virgilio Sport – Guida TV</a></p>")
    for d, rows in grouped:
        parts.append(render_table_html(d, rows))
    parts.append("</body></html>")
    return "".join(parts)

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
    out.append('<description>Un item per ogni giorno; il contenuto è una tabella HTML con gli eventi.</description>')
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

def main():
    try:
        html = fetch_html()
    except Exception:
        # ensure we fail with exit code 1 but keep debug files for the workflow to upload
        print("FATAL: fetch_html failed; see debug artifacts.")
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")
    grouped = list(iter_rows_grouped_by_date(soup))

    index_html = build_index_html(grouped)
    _write_file("index.html", index_html)

    site_base = "https://jusseppe88.github.io/virgilio-sport-rss"

    rss = build_rss_tables(grouped, site_base=site_base)
    _write_file("rss_tables.xml", rss)
    _write_file("rss.xml", rss)

    print(f"Wrote index.html with {len(grouped)} tables and rss_tables.xml")

if __name__ == "__main__":
    main()
