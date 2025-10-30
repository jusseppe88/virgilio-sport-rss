#!/usr/bin/env python3
import re, sys, time, datetime
import requests
from bs4 import BeautifulSoup

URL = "https://sport.virgilio.it/guida-tv/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
}

# Italian month names → month numbers
IT_MONTHS = {
    "gennaio":1, "febbraio":2, "marzo":3, "aprile":4, "maggio":5, "giugno":6,
    "luglio":7, "agosto":8, "settembre":9, "ottobre":10, "novembre":11, "dicembre":12
}

# Convert "Oggi – 30 ottobre 2025 – ..." / "Domani – 31 ottobre 2025 – ..." / "– 01 novembre 2025 – ..."
def parse_date_heading(text, today=None, tz_offset="+0100"):
    text = re.sub(r"\s+", " ", text).strip()
    if today is None:
        today = datetime.date.today()
    # Try explicit date: "30 ottobre 2025" or "01 novembre 2025"
    m = re.search(r"(\d{1,2})\s+([A-Za-zàéìòù]+)\s+(\d{4})", text, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        month_name = m.group(2).lower()
        y = int(m.group(3))
        month = IT_MONTHS.get(month_name)
        if month:
            return datetime.date(y, month, d)
    # Fallback for words like "Oggi" or "Domani"
    if re.search(r"\bOggi\b", text, re.IGNORECASE):
        return today
    if re.search(r"\bDomani\b", text, re.IGNORECASE):
        return today + datetime.timedelta(days=1)
    return None

from requests_html import HTMLSession

def fetch_html():
    session = HTMLSession()
    r = session.get("https://sport.virgilio.it/guida-tv/")
    r.html.render(timeout=60, sleep=3)
    return r.html.html

def iter_events(soup):
    # Grab all H2 (date section headers), then walk siblings until next H2
    for h2 in soup.select("h2"):
        section_date = parse_date_heading(h2.get_text(" "))
        if not section_date:
            continue
        # iterate siblings until next h2
        for sib in h2.find_all_next():
            if sib is h2:  # skip self
                continue
            if sib.name == "h2":
                break
            text = sib.get_text("\n").strip()
            if not text:
                continue
            # Split into lines and keep those starting with HH:MM
            for line in [ln.strip() for ln in text.splitlines()]:
                if re.match(r"^\d{1,2}:\d{2}", line):
                    yield section_date, line

def split_event_line(line):
    # Examples:
    # "20:45 Calcio, Serie A: Milan-Roma Dazn"
    # "11:00 Tennis, ATP Masters 1000 Parigi-Bercy: Ottavi di Finale Sky Sport 1"
    # Return: time_str, sport, competition, title, channels
    m_time = re.match(r"^(\d{1,2}:\d{2})\s+(.*)$", line)
    if not m_time:
        return None
    time_str, rest = m_time.group(1), m_time.group(2)

    # Try to split "Sport, Competition: Title Channels"
    sport, competition, title, channels = None, None, None, None

    # First split on ":" for title/channels
    if ":" in rest:
        left, right = rest.split(":", 1)
        # left like "Calcio, Serie A" or "Tennis, ATP Masters 1000 Parigi-Bercy"
        # right like " Milan-Roma Dazn"
        left = left.strip()
        right = right.strip()

        # sport,competition
        if "," in left:
            sp, comp = left.split(",", 1)
            sport = sp.strip()
            competition = comp.strip()
        else:
            sport = left.strip()

        # Try to extract channels as last token(s). Often they're proper nouns; we keep all.
        title = right
        channels = None
        # Heuristic: last token chunk is channels if it doesn’t contain commas and has spaces → keep as is
        # Safer: leave full right as title; channels can be extracted by recognizing known broadcaster words,
        # but to avoid hardcoding, we expose 'channels' == trailing words after two spaces from end if present.
        # Minimal approach: channels = words after last two spaces if they look like broadcaster names.
        # For feed readers, having title = full right is fine. We'll keep channels None.
    else:
        # No ":" — rare; just keep entire rest as title
        title = rest.strip()

    return time_str, sport, competition, title, channels

def iso_zoned(dt):
    # RFC822 for RSS pubDate; we’ll also add ISO in description
    # Use Europe/Rome offset (CET/CEST). We’ll guess by date: CEST late Mar–late Oct; else CET.
    # For simplicity, use +0200 in summer (Apr–Oct) else +0100.
    month = dt.month
    offset = "+0200" if 4 <= month <= 10 else "+0100"
    return dt.strftime("%a, %d %b %Y %H:%M:00 ") + offset

def build_rss(items, now=None):
    if now is None:
        now = datetime.datetime.utcnow()
    now_rfc822 = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
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
        guid = it["guid"]
        title = it["title"]
        link = URL
        pub = it["pubDate"]
        desc = it["description"]
        out.append("<item>")
        out.append(f"<title>{escape_xml(title)}</title>")
        out.append(f"<link>{link}</link>")
        out.append(f"<guid isPermaLink=\"false\">{guid}</guid>")
        out.append(f"<pubDate>{pub}</pubDate>")
        out.append(f"<description>{escape_xml(desc)}</description>")
        out.append("</item>")
    out.append("</channel></rss>")
    return "\n".join(out)

def escape_xml(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def main():
    html = fetch_html()
    soup = BeautifulSoup(html, "html.parser")

    events = []
    today = datetime.date.today()

    for section_date, line in iter_events(soup):
        parsed = split_event_line(line)
        if not parsed:
            continue
        time_str, sport, competition, title, channels = parsed

        # Build datetime in Europe/Rome (naive, we'll add CET/CEST offset later in iso_zoned)
        hh, mm = map(int, time_str.split(":"))
        dt_local = datetime.datetime(section_date.year, section_date.month, section_date.day, hh, mm)

        # Title for RSS: "HH:MM — [sport/competition] title"
        sc_part = ""
        if sport and competition:
            sc_part = f"{sport} · {competition} — "
        elif sport:
            sc_part = f"{sport} — "

        rss_title = f"{time_str} — {sc_part}{title}"

        # Description with structured fields
        desc_bits = [f"Data: {section_date.isoformat()} {time_str} (Europe/Rome)"]
        if sport: desc_bits.append(f"Sport: {sport}")
        if competition: desc_bits.append(f"Competizione: {competition}")
        if channels: desc_bits.append(f"Canali: {channels}")
        description = " | ".join(desc_bits)

        guid = f"{section_date.isoformat()}-{time_str}-{hash(title) & 0xffffffff}"
        events.append({
            "guid": guid,
            "title": rss_title,
            "pubDate": iso_zoned(dt_local),
            "description": description
        })

    # Sort by datetime ascending (use pubDate string -> rough, but okay; better to store dt)
    # For accuracy, rebuild using a key we stored; quick parse back:
    def key_from_pub(pub):
        # Example: "Thu, 30 Oct 2025 20:45:00 +0100"
        try:
            return time.mktime(time.strptime(pub[:-6], "%a, %d %b %Y %H:%M:%S "))
        except Exception:
            return 0
    events.sort(key=lambda e: key_from_pub(e["pubDate"]))

    rss = build_rss(events)
    with open("rss.xml", "w", encoding="utf-8") as f:
        f.write(rss)
    print("Wrote rss.xml with", len(events), "items.")

if __name__ == "__main__":
    main()

