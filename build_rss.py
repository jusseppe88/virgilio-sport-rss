#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import time
import hashlib
import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://sport.virgilio.it/guida-tv/"

# Italian month names → numbers
IT_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}

TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def fetch_html() -> str:
    """Render the page (JS included) and return the final HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch()  # headless by default
        page = browser.new_page()
        page.goto(URL, timeout=90_000, wait_until="networkidle")
        # Be lenient: wait for either tables or at least the date headings to appear
        page.wait_for_selector("table, h2", timeout=60_000)
        html = page.content()
        browser.close()
        return html


def parse_date_heading(text: str, today: datetime.date | None = None) -> datetime.date | None:
    """Parse headings like 'Oggi – 30 ottobre 2025 …' or 'Domani – …' → date."""
    text = re.sub(r"\s+", " ", text).strip()
    if today is None:
        today = datetime.date.today()

    # Explicit date: "30 ottobre 2025"
    m = re.search(r"(\d{1,2})\s+([A-Za-zàéìòù]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        month_name = m.group(2).lower()
        y = int(m.group(3))
        month = IT_MONTHS.get(month_name)
        if month:
            return datetime.date(y, month, d)

    # Relative words
    if re.search(r"\bOggi\b", text, re.IGNORECASE):
        return today
    if re.search(r"\bDomani\b", text, re.IGNORECASE):
        return today + datetime.timedelta(days=1)
    return None


def iter_events_from_tables(soup: BeautifulSoup):
    """
    Primary parser: walk H2 (date sections), then collect rows (tr) until next H2.
    Accept rows whose first cell is HH:MM; fallback: any cell that looks like time.
    """
    for h2 in soup.find_all("h2"):
        section_date = parse_date_heading(h2.get_text(" "))
        if not section_date:
            continue

        # iterate siblings until next h2
        for sib in h2.next_siblings:
            if getattr(sib, "name", None) == "h2":
                break
            if getattr(sib, "name", None) in (None, "script", "style"):
                continue

            # look for rows inside this sibling subtree
            for tr in getattr(sib, "find_all", lambda *_: [])("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue

                time_str = None
                rest_cells = []

                # Prefer time in first cell
                if TIME_RE.match(cells[0]):
                    time_str = TIME_RE.match(cells[0]).group(1)
                    rest_cells = cells[1:]
                else:
                    # Fallback: any cell that looks like time
                    for i, c in enumerate(cells):
                        m = TIME_RE.match(c)
                        if m:
                            time_str = m.group(1)
                            rest_cells = cells[:i] + cells[i+1:]
                            break

                if not time_str:
                    continue

                # Join the rest for further splitting
                rest = " ".join([x for x in rest_cells if x]).strip()
                if not rest:
                    continue

                yield section_date, time_str, rest


def split_event_text(rest: str):
    """
    Try to split "Sport, Competition: Title [Channels]".
    Keeps it resilient; channels aren't strictly parsed.
    """
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


def to_rfc822_europe_rome(date_obj: datetime.date, time_str: str) -> str:
    hh, mm = map(int, time_str.split(":"))
    dt = datetime.datetime(date_obj.year, date_obj.month, date_obj.day, hh, mm, tzinfo=ZoneInfo("Europe/Rome"))
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_guid(date_obj: datetime.date, time_str: str, title: str) -> str:
    key = f"{date_obj.isoformat()}|{time_str}|{title}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()


def build_rss(items: list[dict], now_utc: datetime.datetime | None = None) -> str:
    if now_utc is None:
        now_utc = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    now_rfc822 = now_utc.strftime("%a, %d %b %Y %H:%M:%S %z")

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0">')
    out.append('<channel>')
    out.append('<title>Virgilio Sport – Guida TV (events only)</title>')
    out.append(f'<link>{URL}</link>')
    out.append('<description>Parsed schedule of sports events only (times, titles, competitions)</description>')
    out.append(f'<lastBuildDate>{now_rfc822}</lastBuildDate>')
    out.append('<ttl>60</ttl>')

    for it in items:
        out.append("<item>")
        out.append(f"<title>{esc(it['title'])}</title>")
        out.append(f"<link>{URL}</link>")
        out.append(f"<guid isPermaLink=\"false\">{it['guid']}</guid>")
        out.append(f"<pubDate>{it['pubDate']}</pubDate>")
        out.append(f"<description>{esc(it['description'])}</description>")
        out.append("</item>")

    out.append("</channel></rss>")
    return "\n".join(out)


def main():
    html = fetch_html()
    soup = BeautifulSoup(html, "html.parser")
    print("Parsed title:", (soup.title.string if soup.title else "NO TITLE"))

    events = []

    # Primary path: parse via tables grouped by H2 date headings
    for section_date, time_str, rest in iter_events_from_tables(soup):
        sport, competition, title, channels = split_event_text(rest)

        # Compose RSS title
        sc_part = ""
        if sport and competition:
            sc_part = f"{sport} · {competition} — "
        elif sport:
            sc_part = f"{sport} — "
        rss_title = f"{time_str} — {sc_part}{title}"

        desc_bits = [f"Data: {section_date.isoformat()} {time_str} (Europe/Rome)"]
        if sport:
            desc_bits.append(f"Sport: {sport}")
        if competition:
            desc_bits.append(f"Competizione: {competition}")
        if channels:
            desc_bits.append(f"Canali: {channels}")
        description = " | ".join(desc_bits)

        events.append({
            "guid": make_guid(section_date, time_str, title or ""),
            "title": rss_title,
            "pubDate": to_rfc822_europe_rome(section_date, time_str),
            "description": description,
            # keep a numeric sort key to guarantee correct order
            "_sort_ts": datetime.datetime(
                section_date.year, section_date.month, section_date.day,
                int(time_str[:2]), int(time_str[3:5]),
                tzinfo=ZoneInfo("Europe/Rome")
            ).timestamp()
        })

    # Sort chronologically
    events.sort(key=lambda e: e["_sort_ts"])

    # Build and write RSS
    rss = build_rss(events)
    with open("rss.xml", "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote rss.xml with {len(events)} items.")


if __name__ == "__main__":
    main()
