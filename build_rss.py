#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, sys, traceback, hashlib, datetime, time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://sport.virgilio.it/guida-tv/"

IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}
TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")

# ---------- tz helper (survives missing tzdata) ----------
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    def _rome_dt(y, m, d, hh=0, mm=0):
        try:
            return datetime.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Europe/Rome"))
        except Exception:
            # Fallback: +0200 for Apr–Oct, +0100 otherwise
            off = 2 if 4 <= m <= 10 else 1
            return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone(datetime.timedelta(hours=off)))
except Exception:
    def _rome_dt(y, m, d, hh=0, mm=0):
        off = 2 if 4 <= m <= 10 else 1
        return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone(datetime.timedelta(hours=off)))

def to_rfc822_europe_rome(date_obj: datetime.date, time_str: str | None = None) -> str:
    if time_str:
        hh, mm = map(int, time_str.split(":"))
    else:
        hh, mm = 0, 0
    dt = _rome_dt(date_obj.year, date_obj.month, date_obj.day, hh, mm)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

# ---------- misc utils ----------
def _write_file(path: str, content: str, mode="w", enc="utf-8"):
    try:
        with open(path, mode, encoding=enc) as f:
            f.write(content)
    except Exception:
        pass

def esc(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def make_guid(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

# ---------- fetch with retries ----------
def fetch_html() -> str:
    _write_file("debug_stage.txt", "starting playwright...\n")
    attempts = 3
    last_err = None
    for attempt in range(1, attempts + 1):
        console_lines = []
        page = None
        try:
            with sync_playwright() as p:
                launch_args = ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--disable-setuid-sandbox"]
                browser = p.chromium.launch(headless=True, args=launch_args)
                context = browser.new_context(
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
                    locale="it-IT",
                    extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
                )
                page = context.new_page()
                page.on("console", lambda msg: console_lines.append(f"[{msg.type()}] {msg.text()}"))

                _write_file("debug_stage.txt", f"attempt {attempt}: navigating...\n", mode="a")
                page.goto(URL, timeout=120_000, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle", timeout=60_000)
                _write_file("debug_stage.txt", f"attempt {attempt}: waiting tables/h2...\n", mode="a")
                try:
                    page.wait_for_selector("table", timeout=90_000)
                except Exception:
                    page.wait_for_selector("h2", timeout=60_000)

                html = page.content()
                _write_file("debug.html", html)
                try:
                    page.screenshot(path="debug.png", full_page=True)
                except Exception:
                    pass

                _write_file("playwright_console.log", "\n".join(console_lines))
                browser.close()
                return html

        except Exception as e:
            last_err = e
            _write_file("debug_stage.txt", f"attempt {attempt}: ERROR {e}\n", mode="a")
            try:
                _write_file("playwright_console.log", "\n".join(console_lines))
                if page:
                    _write_file("debug.html", page.content())
            except Exception:
                pass
            time.sleep(3)

    _write_file("debug_error.txt", f"Failed after {attempts} attempts: {last_err}\n")
    raise last_err

# ---------- dates ----------
def parse_date_heading(text: str, today: datetime.date | None = None) -> datetime.date | None:
    text = re.sub(r"\s+", " ", text).strip()
    if today is None: today = datetime.date.today()
    m = re.search(r"(\d{1,2})\s+([A-Za-zàéìòù]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        d = int(m.group(1)); month_name = m.group(2).lower(); y = int(m.group(3))
        month = IT_MONTHS.get(month_name)
        if month: return datetime.date(y, month, d)
    if re.search(r"\bOggi\b", text, re.IGNORECASE): return today
    if re.search(r"\bDomani\b", text, re.IGNORECASE): return today + datetime.timedelta(days=1)
    return None

# ---------- structural parsing ----------
def normalize_header(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("dove vederla", "canali").replace("dove vedere", "canali").replace("canale", "canali")
    s = s.replace("ora inizio", "ora")
    return s

def header_index_map(table: BeautifulSoup) -> dict:
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
        for i,h in enumerate(headers):
            if w == h or w in h:
                idx[w] = i; break
    return idx

def extract_rows_from_table(table: BeautifulSoup):
    idx = header_index_map(table)
    body = table.find("tbody") or table
    out = []
    for tr in body.find_all("tr"):
        tds = tr.find_all(["td","th"])
        if not tds: continue
        def cell(i): return tds[i].get_text(" ", strip=True) if 0 <= i < len(tds) else ""
        if idx:
            time_val   = cell(idx.get("ora", 0)).strip()
            sport      = cell(idx.get("sport", 1)).strip()
            competition= cell(idx.get("competizione", 2)).strip()
            event      = cell(idx.get("evento", 3)).strip()
            channels   = cell(idx.get("canali", len(tds)-1)).strip()
        else:
            # very defensive fallback (rare)
            texts = [c.get_text(" ", strip=True) for c in tds]
            time_val = texts[0] if texts and TIME_RE.match(texts[0]) else ""
            if not time_val:
                for i,tx in enumerate(texts):
                    if TIME_RE.match(tx): time_val = tx; texts = texts[:i]+texts[i+1:]; break
            if not time_val: continue
            sport = texts[0] if len(texts)>0 else ""
            competition = texts[1] if len(texts)>1 else ""
            channels = texts[-1] if len(texts)>2 else ""
            event = " ".join(texts[2:-1]) if len(texts)>3 else (texts[2] if len(texts)>2 else "")
        if TIME_RE.match(channels): channels, event = event, channels
        out.append({"time": time_val, "sport": sport, "competition": competition, "title": event, "channels": channels})
    return out

# ---------- cleaning / mirroring ----------
def strip_non_tables(container: BeautifulSoup) -> BeautifulSoup:
    # 1) Remove obvious junk by snapshotting nodes first (avoid mutating while iterating)
    junk_tags = []
    for tag in list(container.find_all(True)):
        name = (tag.name or "").lower()
        if name in ("script","style","noscript","iframe"):
            junk_tags.append(tag); continue
        classes = " ".join(tag.get("class", [])).lower()
        idv = (tag.get("id") or "").lower()
        data_attrs = " ".join([k for k in tag.attrs.keys() if isinstance(k, str)]).lower()
        if any(x in classes for x in ["adv","ads","ad-", "banner", "pubblicit", "social"]):
            junk_tags.append(tag); continue
        if any(x in idv for x in ["adv","ads","ad-", "banner", "pubblicit", "social"]):
            junk_tags.append(tag); continue
        if "data-ad" in data_attrs or "data-adv" in data_attrs:
            junk_tags.append(tag); continue
    for t in junk_tags:
        t.decompose()

    # 2) Keep only sections that contain h2/tables; drop large wrappers that don't
    for tag in list(container.find_all(True)):
        if tag.name and tag.find(["table","h2"]):
            continue
        # allow minimal layout wrappers
        if tag.name in ("table","thead","tbody","tr","th","td","h2","section","article","div","p","a"):
            continue
        tag.decompose()
    return container

def collect_styles_and_clean_fragment(html: str):
    soup = BeautifulSoup(html, "html.parser")

    # CSS links (absolute)
    hrefs = []
    for link in soup.select("link[rel=stylesheet]"):
        href = link.get("href")
        if href: hrefs.append(urljoin(URL, href))

    # main container
    container = soup.select_one(".guida-tv") or soup.select_one("main") or soup.select_one("#main") or soup.body
    if not container:
        return hrefs, (soup.body.decode() if soup.body else html), None

    container = strip_non_tables(container)

    # inject anchors before each h2
    today = datetime.date.today()
    for h2 in container.find_all("h2"):
        d = parse_date_heading(h2.get_text(" "), today=today)
        if d:
            anchor = soup.new_tag("a", id=d.isoformat())
            h2.insert_before(anchor)

    return hrefs, container.decode(), container

def build_tables_html(style_hrefs, fragment_html) -> str:
    base_css = """
      body{margin:16px;background:#fff;color:#000;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
      .source-note{margin:8px 0 16px;font-size:12px;color:#444}
      .wrap{max-width:1100px;margin:0 auto}
    """
    links = "\n".join(f"<link rel='stylesheet' href='{h}' crossorigin>" for h in style_hrefs)
    return f"""<!doctype html>
<html lang="it">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Guida TV Sport — tabelle</title>
{links}
<style>{base_css}</style>
<body>
  <div class="wrap">
    <div class="source-note">Fonte originale: <a href="{URL}" target="_blank" rel="noopener">{URL}</a></div>
    {fragment_html}
  </div>
</body>
</html>"""

# ---------- grouping for RSS ----------
def iter_rows_grouped_by_date_from_container(container: BeautifulSoup):
    groups = {}
    for h2 in container.find_all("h2"):
        d = parse_date_heading(h2.get_text(" "))
        if not d: continue
        rows = groups.setdefault(d, [])
        for sib in h2.next_siblings:
            if getattr(sib, "name", None) == "h2":
                break
            if getattr(sib, "name", None) in (None, "script", "style"):
                continue
            for table in getattr(sib, "find_all", lambda *_: [])("table"):
                rows.extend(extract_rows_from_table(table))
    for d, lst in groups.items():
        lst.sort(key=lambda r: r["time"])
    for d in sorted(groups.keys()):
        yield d, groups[d]

def render_table_html_for_rss(date_obj: datetime.date, rows):
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

def build_rss_tables(grouped, site_base: str, now_utc: datetime.datetime | None = None) -> str:
    if now_utc is None:
        now_utc = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    now_rfc822 = now_utc.strftime("%a, %d %b %Y %H:%M:%S %z")
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0"><channel>')
    out.append('<title>Virgilio Sport – Guida TV (per giorno)</title>')
    out.append(f'<link>{site_base}/tables.html</link>')
    out.append('<description>Un item per ogni giorno; il contenuto è una tabella HTML con gli eventi.</description>')
    out.append(f'<lastBuildDate>{now_rfc822}</lastBuildDate><ttl>60</ttl>')
    for d, rows in grouped:
        title = d.strftime("Guida TV – %A %d %B %Y").title()
        anchor = d.isoformat()
        link = f"{site_base}/tables.html#{anchor}"
        table_html = render_table_html_for_rss(d, rows)
        guid = make_guid(f"{d.isoformat()}|{len(rows)}")
        pub = to_rfc822_europe_rome(d)
        out.append("<item>")
        out.append(f"<title>{esc(title)}</title><link>{link}</link>")
        out.append(f"<guid isPermaLink=\"false\">{guid}</guid><pubDate>{pub}</pubDate>")
        out.append("<description><![CDATA["); out.append(table_html); out.append("]]></description>")
        out.append("</item>")
    out.append("</channel></rss>")
    return "\n".join(out)

# ---------- main ----------
def main():
    try:
        html = fetch_html()
    except Exception:
        print("FATAL: fetch_html failed; see debug artifacts.")
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")

    # Clean tables page (no ads), preserve CSS, add anchors
    style_hrefs, fragment_html, container = collect_styles_and_clean_fragment(html)
    tables_html = build_tables_html(style_hrefs, fragment_html)
    _write_file("tables.html", tables_html)
    _write_file("index.html", tables_html)  # convenience

    # Build RSS (one item per day)
    if container is None:
        container = soup
    grouped = list(iter_rows_grouped_by_date_from_container(container))

    site_base = "https://jusseppe88.github.io/virgilio-sport-rss"
    rss = build_rss_tables(grouped, site_base=site_base)
    _write_file("rss_tables.xml", rss)
    _write_file("rss.xml", rss)

    print(f"Wrote tables.html & index.html with {len(grouped)} tables and rss_tables.xml")

if __name__ == "__main__":
    main()
