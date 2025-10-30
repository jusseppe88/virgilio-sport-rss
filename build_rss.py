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
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")

# ----- robust tz fallback -----
try:
    from zoneinfo import ZoneInfo
    def _rome_dt(y, m, d, hh=0, mm=0):
        try:
            return datetime.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Europe/Rome"))
        except Exception:
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

# ----- small utils -----
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

# ----- fetch page (with retries) -----
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

                _write_file("debug_stage.txt", f"attempt {attempt}: waiting content...\n", mode="a")
                # Wait for either obvious structured content or just the main article text to appear
                try:
                    page.wait_for_selector("h2", timeout=90_000)
                except Exception:
                    page.wait_for_selector("article, main, #main, .guida-tv", timeout=60_000)

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

# ----- dates -----
def parse_date_heading(text: str, today: datetime.date | None = None) -> datetime.date | None:
    # Accept both “Oggi – 30 ottobre 2025 – …” and “– 01 novembre 2025 – …”
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

# ----- parsing helpers -----
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
            texts = [c.get_text(" ", strip=True) for c in tds]
            m = TIME_RE.search(texts[0]) if texts else None
            time_val = m.group(1) if m else ""
            if not time_val:
                # try any cell
                for i, tx in enumerate(texts):
                    m = TIME_RE.search(tx)
                    if m:
                        time_val = m.group(1)
                        texts = texts[:i] + texts[i+1:]
                        break
            if not time_val: continue
            sport = texts[0] if len(texts)>0 else ""
            competition = texts[1] if len(texts)>1 else ""
            channels = texts[-1] if len(texts)>2 else ""
            event = " ".join(texts[2:-1]) if len(texts)>3 else (texts[2] if len(texts)>2 else "")
        out.append({"time": time_val, "sport": sport, "competition": competition, "title": event, "channels": channels})
    return out

LINE_RE = re.compile(
    r"^\s*(?P<time>\d{1,2}:\d{2})\s+(?P<body>.+?)\s*$"
)
def split_free_text(line: str):
    """
    Parse lines like:
      20:45 Calcio, Serie A: Pisa-Lazio Sky, Dazn, Sky Sport Calcio
      11:00 Tennis, ATP Masters 1000 Parigi-Bercy: Ottavi Di Finale Sky Sport Tennis, Sky Sport 1
    Return dict with time, sport, competition, title, channels.
    """
    m = LINE_RE.match(line)
    if not m: return None
    time_str = m.group("time")
    rest = m.group("body")

    sport = competition = title = channels = ""

    # Split "left: right"
    if ":" in rest:
        left, right = [x.strip() for x in rest.split(":", 1)]
        # left may be "Calcio, Serie A" or "Tennis, ATP Masters 1000 Parigi-Bercy"
        if "," in left:
            sport, competition = [x.strip() for x in left.split(",", 1)]
        else:
            sport = left
        # right often ends with channels list
        # Heuristic: last comma-separated chunk(s) containing words like Sky, Dazn, Rai, Eurosport, NOW...
        # Try to detect channels by keywords; else take last tokens after two spaces groups
        kws = ("Sky", "Dazn", "Rai", "Eurosport", "NOW", "Mediaset", "Sportitalia", "Amazon", "Prime", "Infinity", "La7", "Nove")
        # Split by spaces, but better: try last '  ' (double space) separation first
        title = right
        # If there are multiple broadcasters separated by commas, keep them together
        parts = [p.strip() for p in right.split(",")]
        if any(any(k.lower() in p.lower() for k in kws) for p in parts):
            # find from the end the first part that contains a broadcaster keyword
            idx = len(parts)-1
            while idx >= 0 and not any(k.lower() in parts[idx].lower() for k in kws):
                idx -= 1
            if idx >= 0:
                channels = ", ".join(parts[idx:]).strip()
                title = ", ".join(parts[:idx]).strip()
        # Cleanup: if still no channels but last word is uppercase-ish, take it as channel
        if not channels:
            tokens = right.split()
            if tokens and tokens[-1].isalpha() and tokens[-1][0].isupper():
                channels = tokens[-1]
                title = " ".join(tokens[:-1]).strip()
    else:
        # No colon case — rare; treat whole rest as title
        title = rest

    return {"time": time_str, "sport": sport, "competition": competition, "title": title, "channels": channels}

def block_has_events_text(node: BeautifulSoup) -> bool:
    # True if the node contains any line with HH:MM
    if not getattr(node, "get_text", None): return False
    txt = node.get_text("\n", strip=True)
    return bool(TIME_RE.search(txt))

# ----- build a clean, predictable mirror -----
def collect_styles(html: str):
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for link in soup.select("link[rel=stylesheet]"):
        href = link.get("href")
        if href: hrefs.append(urljoin(URL, href))
    return hrefs

def pick_container(soup: BeautifulSoup):
    return soup.select_one(".guida-tv") or soup.select_one("article") or soup.select_one("main") or soup.select_one("#main") or soup.body or soup

def build_clean_mirror(html: str):
    """
    Wrap each date as:
      <section class="day" id="YYYY-MM-DD">
        <h2>...</h2>
        <!-- only blocks that contain events: a table OR text with HH:MM lines -->
      </section>
    """
    soup = BeautifulSoup(html, "html.parser")
    src = pick_container(soup)
    h2s = src.find_all(["h2","h3"])  # be lenient
    mirror = soup.new_tag("div", **{"class": "guide-mirror"})
    today = datetime.date.today()

    for i, h in enumerate(h2s):
        d = parse_date_heading(h.get_text(" ", strip=True), today=today)
        if not d:
            continue

        section = soup.new_tag("section", **{"class": "day", "id": d.isoformat()})
        new_h = soup.new_tag("h2"); new_h.string = h.get_text(" ", strip=True)
        section.append(new_h)

        sib = h.next_sibling
        while sib and not (getattr(sib, "name", None) in ("h2","h3")):
            if getattr(sib, "name", None):
                keep = False
                if getattr(sib, "find", None):
                    if sib.find("table"):
                        keep = True
                    elif block_has_events_text(sib):
                        keep = True
                if keep:
                    section.append(BeautifulSoup(str(sib), "html.parser"))
            sib = sib.next_sibling

        mirror.append(section)

    return mirror

def build_tables_html(style_hrefs, fragment_html) -> str:
    base_css = """
      body{margin:16px;background:#fff;color:#000;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
      .wrap{max-width:1100px;margin:0 auto}
      .wrap .day{margin:0 0 16px 0}
      /* Force visible table borders */
      table{border-collapse:collapse}
      th,td{border:1px solid #d1d5db;padding:6px 8px}
      thead th{background:#f3f4f6}
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
    {fragment_html}
  </div>
</body>
</html>"""


# ----- grouping for RSS from the clean mirror -----
def iter_rows_grouped_by_date_from_mirror(mirror: BeautifulSoup):
    groups = {}
    for section in mirror.select("section.day"):
        # date
        d = None
        id_attr = section.get("id")
        if id_attr:
            try: d = datetime.date.fromisoformat(id_attr)
            except Exception: d = None
        if not d:
            h2 = section.find(["h2","h3"])
            if h2: d = parse_date_heading(h2.get_text(" ", strip=True))
        if not d: continue

        rows = groups.setdefault(d, [])

        # 1) tables
        for table in section.find_all("table"):
            rows.extend(extract_rows_from_table(table))

        # 2) free text blocks with HH:MM lines
        for blk in section.find_all(["p","div","li","span","section","article"]):
            if not block_has_events_text(blk): continue
            txt = blk.get_text("\n", strip=True)
            for ln in txt.splitlines():
                ln = ln.strip()
                if not TIME_RE.search(ln): continue
                parsed = split_free_text(ln)
                if parsed:
                    rows.append(parsed)

        # Deduplicate by time+title to avoid double grabs if the site wraps lines twice
        seen = set()
        uniq = []
        for r in rows:
            key = (r.get("time",""), r.get("title",""), r.get("channels",""))
            if key in seen: continue
            seen.add(key); uniq.append(r)
        groups[d] = uniq

    for d, lst in groups.items():
        lst.sort(key=lambda r: r["time"])
    for d in sorted(groups.keys()):
        yield d, groups[d]

def render_table_html_for_rss(date_obj: datetime.date, rows):
    table_style = (
        "border-collapse:collapse;width:100%;max-width:980px;"
        "border:1px solid #ddd;"
        "font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial"
    )
    th_td_style = "border:1px solid #ddd;padding:6px 8px;"
    th_style = th_td_style + "background:#f5f5f5;text-align:left;"
    time_th_style = th_style + "white-space:nowrap;width:1%;"

    head = (
        f"<a id='{date_obj.isoformat()}'></a>"
        f"<h2>{date_obj.strftime('%A %d %B %Y').title()}</h2>"
    )

    body = [f"<table style=\"{table_style}\"><thead><tr>"
            f"<th style=\"{time_th_style}\">Ora</th>"
            f"<th style=\"{th_style}\">Sport</th>"
            f"<th style=\"{th_style}\">Competizione</th>"
            f"<th style=\"{th_style}\">Evento</th>"
            f"<th style=\"{th_style}\">Canali</th>"
            f"</tr></thead><tbody>"]

    for r in rows:
        body.append("<tr>"
                    f"<td style=\"{th_td_style}white-space:nowrap;width:1%\">{esc(r.get('time') or '')}</td>"
                    f"<td style=\"{th_td_style}\">{esc(r.get('sport') or '')}</td>"
                    f"<td style=\"{th_td_style}\">{esc(r.get('competition') or '')}</td>"
                    f"<td style=\"{th_td_style}\">{esc(r.get('title') or '')}</td>"
                    f"<td style=\"{th_td_style}\">{esc(r.get('channels') or '')}</td>"
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

# ----- main -----
def main():
    try:
        html = fetch_html()
    except Exception:
        print("FATAL: fetch_html failed; see debug artifacts.")
        sys.exit(1)

    # 1) keep site CSS
    style_hrefs = collect_styles(html)

    # 2) build a predictable, clean mirror (tables or time-based text)
    mirror = build_clean_mirror(html)
    fragment_html = str(mirror)

    # 3) write tables page (and index)
    tables_html = build_tables_html(style_hrefs, fragment_html)
    _write_file("tables.html", tables_html)
    _write_file("index.html", tables_html)

    # 4) build RSS by reading from the same clean mirror
    grouped = list(iter_rows_grouped_by_date_from_mirror(BeautifulSoup(fragment_html, "html.parser")))

    site_base = "https://jusseppe88.github.io/virgilio-sport-rss"
    rss = build_rss_tables(grouped, site_base=site_base)
    _write_file("rss_tables.xml", rss)
    _write_file("rss.xml", rss)

    print(f"Wrote tables.html & index.html with {len(grouped)} tables and rss_tables.xml")

# helpers used in main but defined later
def collect_styles(html: str):
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for link in soup.select("link[rel=stylesheet]"):
        href = link.get("href")
        if href: hrefs.append(urljoin(URL, href))
    return hrefs

if __name__ == "__main__":
    main()
