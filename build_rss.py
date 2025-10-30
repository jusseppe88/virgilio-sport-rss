#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, json, csv, hashlib, datetime, time
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://sport.virgilio.it/guida-tv/"

IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
BROADCASTER_KWS = ("Sky", "Dazn", "DAZN", "Rai", "Eurosport", "NOW", "Mediaset",
                   "Sportitalia", "Amazon", "Prime", "Infinity", "La7", "Nove")

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
                try:
                    page.wait_for_selector("h2, h3, .guida-tv", timeout=90_000)
                except Exception:
                    page.wait_for_selector("article, main, #main, body", timeout=60_000)

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
def parse_sport_comp_event(block: str):
    """
    Parse variants like:
      'Calcio, Serie A: Pisa-Lazio'
      'Basket, Eurolega: Bayern-Virtus'
      'Tennis, ATP & WTA:'
    Returns (sport, competition, title)
    """
    s = (block or "").replace("\xa0", " ").strip()
    # some sources use '<br>' inside middle cell; collapse multiple spaces/commas
    s = re.sub(r"\s+", " ", s)
    if ":" in s:
        left, right = s.split(":", 1)
        title = right.strip()
    else:
        left, title = s, ""
    if "," in left:
        sport, competition = [x.strip() for x in left.split(",", 1)]
    else:
        sport, competition = left.strip(), ""
    return sport, competition, title

def _looks_like_channels(text: str) -> bool:
    if not text: return False
    # if any broadcaster keyword is present, treat this cell as channels-ish
    return any(k.lower() in text.lower() for k in BROADCASTER_KWS)

def extract_rows_from_table(table: BeautifulSoup):
    """
    Robust row parser that IGNORES any site-provided headers.
    Strategy:
      - Find the first cell with HH:MM => time_idx
      - Pick channels_idx:
          * rightmost cell that 'looks like channels' OR
          * else the last cell
      - middle = all cells except time_idx and channels_idx
      - parse middle => sport, competition, title
    """
    out = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = tr.find_all(["td","th"])
        if not tds:
            continue
        texts = [c.get_text(" ", strip=True) for c in tds]
        if not any(texts):
            continue

        # 1) time
        time_idx, time_val = None, ""
        for i, tx in enumerate(texts):
            m = TIME_RE.search(tx)
            if m:
                time_idx, time_val = i, m.group(1)
                break
        if not time_val:
            # skip header/separator-like rows
            continue

        # 2) channels index = rightmost channel-ish cell; fallback: last cell
        channels_idx = None
        for i in range(len(texts)-1, -1, -1):
            if i == time_idx: 
                continue
            if _looks_like_channels(texts[i]):
                channels_idx = i
                break
        if channels_idx is None:
            channels_idx = len(texts) - 1 if len(texts) > 1 else 0

        channels = texts[channels_idx].strip()

        # 3) middle cells = everything except time/channels
        middle_parts = []
        for i, tx in enumerate(texts):
            if i in (time_idx, channels_idx): 
                continue
            if tx:
                middle_parts.append(tx)
        middle = " ".join(middle_parts).strip()
        sport, competition, title = parse_sport_comp_event(middle)

        out.append({
            "time": time_val,
            "sport": sport,
            "competition": competition,
            "title": title,
            "channels": channels
        })
    return out

LINE_RE = re.compile(r"^\s*(?P<time>\d{1,2}:\d{2})\s+(?P<body>.+?)\s*$")
def split_free_text(line: str):
    m = LINE_RE.match(line)
    if not m: return None
    time_str = m.group("time")
    rest = m.group("body")

    sport = competition = title = channels = ""

    if ":" in rest:
        left, right = [x.strip() for x in rest.split(":", 1)]
        if "," in left:
            sport, competition = [x.strip() for x in left.split(",", 1)]
        else:
            sport = left
        title = right
        parts = [p.strip() for p in right.split(",")]
        if any(any(k.lower() in p.lower() for k in BROADCASTER_KWS) for p in parts):
            idx = len(parts)-1
            while idx >= 0 and not any(k.lower() in parts[idx].lower() for k in BROADCASTER_KWS):
                idx -= 1
            if idx >= 0:
                channels = ", ".join(parts[idx:]).strip()
                title = ", ".join(parts[:idx]).strip()
        if not channels:
            tokens = right.split()
            if tokens and tokens[-1].isalpha() and tokens[-1][0].isupper():
                channels = tokens[-1]
                title = " ".join(tokens[:-1]).strip()
    else:
        title = rest

    return {"time": time_str, "sport": sport, "competition": competition, "title": title, "channels": channels}

def block_has_events_text(node: BeautifulSoup) -> bool:
    if not getattr(node, "get_text", None): return False
    txt = node.get_text("\n", strip=True)
    return bool(TIME_RE.search(txt))

# ----- style collection & mirror -----
def collect_styles(html: str):
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for link in soup.select("link[rel=stylesheet]"):
        href = link.get("href")
        if href: hrefs.append(urljoin(URL, href))
    return hrefs

def pick_container(soup: BeautifulSoup):
    return (soup.select_one(".guida-tv") or soup.select_one("article") or
            soup.select_one("main") or soup.select_one("#main") or soup.body or soup)

def build_clean_mirror(html: str):
    soup = BeautifulSoup(html, "html.parser")
    src = pick_container(soup)
    h2s = src.find_all(["h2","h3"])
    mirror = soup.new_tag("div", **{"class": "guide-mirror"})
    today = datetime.date.today()

    for h in h2s:
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

# ----- grouping from mirror -----
def iter_rows_grouped_by_date_from_mirror(mirror: BeautifulSoup):
    groups = {}
    for section in mirror.select("section.day"):
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

        # tables
        for table in section.find_all("table"):
            rows.extend(extract_rows_from_table(table))

        # free text with HH:MM
        for blk in section.find_all(["p","div","li","span","section","article"]):
            if not block_has_events_text(blk): continue
            txt = blk.get_text("\n", strip=True)
            for ln in txt.splitlines():
                ln = ln.strip()
                if not TIME_RE.search(ln): continue
                parsed = split_free_text(ln)
                if parsed:
                    rows.append(parsed)

        # dedupe
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

# ----- FALLBACK: parse full page if mirror failed -----
def iter_rows_grouped_fallback_fullpage(html: str):
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.date.today()
    groups = {}

    candidates = soup.find_all(["h2","h3"])
    day_blocks = []
    for h in candidates:
        d = parse_date_heading(h.get_text(" ", strip=True), today=today)
        if not d:
            continue
        block = []
        sib = h.next_sibling
        while sib and not (getattr(sib, "name", None) in ("h2","h3")):
            if getattr(sib, "name", None):
                block.append(sib)
            sib = sib.next_sibling
        day_blocks.append((d, block))

    if day_blocks:
        for d, blocks in day_blocks:
            rows = []
            for node in blocks:
                for table in getattr(node, "find_all", lambda *_: [])("table"):
                    rows.extend(extract_rows_from_table(table))
                txt = node.get_text("\n", strip=True) if getattr(node, "get_text", None) else ""
                for ln in txt.splitlines():
                    ln = ln.strip()
                    if not TIME_RE.search(ln): 
                        continue
                    parsed = split_free_text(ln)
                    if parsed:
                        rows.append(parsed)
            uniq, seen = [], set()
            for r in rows:
                key = (r.get("time",""), r.get("title",""), r.get("channels",""))
                if key in seen: continue
                seen.add(key); uniq.append(r)
            uniq.sort(key=lambda r: r["time"])
            groups[d] = uniq
        for d in sorted(groups.keys()):
            yield d, groups[d]
        return

    # Last resort: one "today" group
    rows = []
    for table in soup.find_all("table"):
        rows.extend(extract_rows_from_table(table))
    for node in soup.find_all(["p","div","li","span","section","article"]):
        if not block_has_events_text(node): 
            continue
        txt = node.get_text("\n", strip=True)
        for ln in txt.splitlines():
            ln = ln.strip()
            if not TIME_RE.search(ln):
                continue
            parsed = split_free_text(ln)
            if parsed:
                rows.append(parsed)
    uniq, seen = [], set()
    for r in rows:
        key = (r.get("time",""), r.get("title",""), r.get("channels",""))
        if key in seen: continue
        seen.add(key); uniq.append(r)
    uniq.sort(key=lambda r: r["time"])
    yield today, uniq

# ----- channels mapping & linkify -----
def load_channel_map():
    m = {}
    if os.path.exists("channels.csv"):
        try:
            with open("channels.csv", newline="", encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    name = (row.get("name") or "").strip()
                    url = (row.get("url") or "").strip()
                    if name and url:
                        m[name.lower()] = url
        except Exception:
            pass
    if os.path.exists("channels.json"):
        try:
            with open("channels.json", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    name = (k or "").strip()
                    url = (v or "").strip()
                    if name and url:
                        m[name.lower()] = url
        except Exception:
            pass
    return m

def _lookup_channel_url(display_name: str, cmap: dict) -> str | None:
    key = (display_name or "").strip().lower()
    if not key:
        return None
    if key in cmap:
        return cmap[key]
    for k, url in cmap.items():
        if key in k or k in key:
            return url
    return None

def linkify_channels(ch_str: str, cmap: dict) -> str:
    if not ch_str:
        return ""
    parts = [p.strip() for p in ch_str.split(",") if p.strip()]
    out = []
    for p in parts:
        url = _lookup_channel_url(p, cmap)
        if url:
            out.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(p)}</a>')
        else:
            out.append(esc(p))
    return ", ".join(out)

# ----- renderers (page & RSS) -----
def render_table_html_for_rss(date_obj: datetime.date, rows, channel_map=None, inline_styles=True):
    """
    Full table with header.
    inline_styles=True for RSS (survives readers that strip <style>).
    inline_styles=False for page (uses CSS).
    """
    if inline_styles:
        table_style = (
            "border-collapse:collapse;width:100%;max-width:980px;"
            "border:1px solid #ddd;"
            "font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial"
        )
        th_td = "border:1px solid #ddd;padding:6px 8px;"
        th = th_td + "background:#f5f5f5;text-align:left;"
        time_th = th + "white-space:nowrap;width:1%;"
        td_time = th_td + "white-space:nowrap;width:1%;"
        open_table = f'<table style="{table_style}">'
    else:
        th = time_th = td_time = th_td = ""
        open_table = "<table>"

    head = (
        f"<a id='{date_obj.isoformat()}'></a>"
        f"<h2>{date_obj.strftime('%A %d %B %Y').title()}</h2>"
    )

    body = [open_table,
            "<thead><tr>"
            f"<th style=\"{time_th}\">Ora</th>"
            f"<th style=\"{th}\">Sport</th>"
            f"<th style=\"{th}\">Competizione</th>"
            f"<th style=\"{th}\">Evento</th>"
            f"<th style=\"{th}\">Canali</th>"
            "</tr></thead><tbody>"]

    cmap = channel_map or {}
    for r in rows:
        if not TIME_RE.fullmatch((r.get('time') or '').strip()):
            continue
        channels_html = linkify_channels(r.get('channels') or '', cmap)
        body.append("<tr>"
                    f"<td style=\"{td_time}\">{esc(r.get('time') or '')}</td>"
                    f"<td style=\"{th_td}\">{esc(r.get('sport') or '')}</td>"
                    f"<td style=\"{th_td}\">{esc(r.get('competition') or '')}</td>"
                    f"<td style=\"{th_td}\">{esc(r.get('title') or '')}</td>"
                    f"<td style=\"{th_td}\">{channels_html}</td>"
                    "</tr>")
    body.append("</tbody></table>")
    return head + "".join(body)

def build_tables_html_from_grouped(style_hrefs, grouped, channel_map) -> str:
    base_css = """
      body{margin:16px;background:#0b0c0f;color:#f3f4f6;font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
      .wrap{max-width:1100px;margin:0 auto}
      .wrap .day{margin:0 0 16px 0}
      table{border-collapse:collapse;width:100%}
      th,td{border:1px solid #d1d5db;padding:8px 10px}
      thead th{background:#111827;color:#e5e7eb;text-align:left}
      .time{white-space:nowrap;width:1%}
      a{color:#93c5fd}
      a:hover{text-decoration:underline}
    """
    links = "\n".join(f"<link rel='stylesheet' href='{h}' crossorigin>" for h in style_hrefs)

    sections = []
    for d, rows in grouped:
        sections.append(
            f"<section class='day' id='{d.isoformat()}'>"
            f"{render_table_html_for_rss(d, rows, channel_map=channel_map, inline_styles=False)}"
            f"</section>"
        )
    fragment_html = "\n".join(sections)

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

def build_rss_tables(grouped, site_base: str, now_utc: datetime.datetime | None = None) -> str:
    if now_utc is None:
        now_utc = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    now_rfc822 = now_utc.strftime("%a, %d %b %Y %H:%M:%S %z")
    channel_map = load_channel_map()

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
        table_html = render_table_html_for_rss(d, rows, channel_map=channel_map, inline_styles=True)
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

    style_hrefs = collect_styles(html)
    mirror = build_clean_mirror(html)
    fragment_html = str(mirror)

    grouped = list(iter_rows_grouped_by_date_from_mirror(BeautifulSoup(fragment_html, "html.parser")))
    _write_file("debug_stage.txt", f"mirror groups: {len(grouped)}\n", mode="a")
    if not grouped or all(len(rows)==0 for _, rows in grouped):
        _write_file("debug_stage.txt", "mirror empty; using full-page fallback\n", mode="a")
        grouped = list(iter_rows_grouped_fallback_fullpage(html))
        _write_file("debug_stage.txt", f"fallback groups: {len(grouped)}\n", mode="a")

    channel_map = load_channel_map()
    _write_file("debug_stage.txt", f"channel_map size: {len(channel_map)}\n", mode="a")

    tables_html = build_tables_html_from_grouped(style_hrefs, grouped, channel_map)
    _write_file("tables.html", tables_html)
    _write_file("index.html", tables_html)

    site_base = "https://jusseppe88.github.io/virgilio-sport-rss"
    rss = build_rss_tables(grouped, site_base=site_base)
    _write_file("rss_tables.xml", rss)
    _write_file("rss.xml", rss)

    total_rows = sum(len(rows) for _, rows in grouped)
    print(f"Wrote tables.html & index.html with {len(grouped)} day sections and {total_rows} rows; also rss_tables.xml")

if __name__ == "__main__":
    main()
