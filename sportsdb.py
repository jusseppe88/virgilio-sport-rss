"""Italy TV listings from TheSportsDB, merged with Virgilio in Rome time."""
import concurrent.futures
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from dispatcharr_match import ROME, canonical

URL = 'https://www.thesportsdb.com/browse_tv/?c=italy'
MONTHS = 'january february march april may june july august september october november december'.split()
SPORTS = {'Soccer': 'Calcio', 'Basketball': 'Basket', 'Rugby': 'Rugby',
          'Tennis': 'Tennis', 'Volleyball': 'Pallavolo', 'Handball': 'Pallamano',
          'Boxing': 'Pugilato', 'Fighting': 'Sport da combattimento',
          'Motorsport': 'Motori', 'Ice Hockey': 'Hockey su ghiaccio'}


def fetch_html(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; SportsGuide/1.0)'})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('TheSportsDB page exceeds size limit')
    return raw.decode('utf-8')


def channel_label(anchor):
    # Some source responses put the label just outside the channel anchor.
    label = anchor.get_text(' ', strip=True)
    if label:
        return label
    parts = []
    for node in anchor.next_siblings:
        if getattr(node, 'name', None):
            break
        parts.append(str(node))
    return ''.join(parts).strip()


def parse_listings(raw, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(dt.timezone.utc).date()
    soup = BeautifulSoup(raw, 'html.parser')
    heading = next((h.get_text(' ', strip=True) for h in soup.find_all('h2')
                    if 'Sports On TV today:' in h.get_text()), '')
    match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b', heading)
    if not match or match[2].lower() not in MONTHS:
        raise ValueError('TheSportsDB broadcast date is missing')
    if (int(match[1]), MONTHS.index(match[2].lower()) + 1) != (today.day, today.month):
        raise ValueError('TheSportsDB returned a stale broadcast day')
    if not re.search(r'Timezone:\s*UTC\b', soup.get_text(' ', strip=True), re.I):
        raise ValueError('TheSportsDB timezone is not UTC')
    rows = []
    for card in soup.select('.tv-event-card'):
        event = card.select_one('a[href^="/event/"]')
        time = re.search(r'\b(\d{2}:\d{2})\s+UTC\b', card.get_text(' ', strip=True))
        channels = [channel_label(a) for a in card.select('a[href^="/channel/"]')]
        if not event or not time or not channels or not all(channels):
            raise ValueError('Incomplete TheSportsDB event card')
        url = urljoin(URL, event['href'])
        if urlsplit(url).hostname != 'www.thesportsdb.com':
            raise ValueError('Unexpected event host')
        broadcast = dt.datetime.combine(today, dt.time.fromisoformat(time[1]), dt.timezone.utc).astimezone(ROME)
        rows.append({'date': broadcast.date(), 'time': broadcast.strftime('%H:%M'),
                     'url': url, 'channels': ', '.join(dict.fromkeys(channels))})
    if not rows:
        raise ValueError('TheSportsDB returned no Italy TV events')
    return rows


def parse_event(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    event = None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get('@type') == 'SportsEvent':
            event = data
            break
    if not event or not event.get('name'):
        raise ValueError('Full TheSportsDB event identity is missing')
    sports = [a.get_text(' ', strip=True) for a in soup.select('a[href^="/sport/"]')
              if a.get('href') != '/sport/leagues']
    if not sports:
        raise ValueError('TheSportsDB sport is missing')
    title = re.sub(r'\s+vs\.?\s+', '-', event['name'], flags=re.I)
    sport = SPORTS.get(sports[0], sports[0])
    if re.search(r'\bWomen\b', title, re.I) and sport == 'Calcio':
        sport = 'Calcio femminile'
    return {'sport': sport, 'competition': event.get('organizer', {}).get('name', ''),
            'title': title, 'sources': ['TheSportsDB']}


def load_events():
    rows = parse_listings(fetch_html(URL))
    urls = list(dict.fromkeys(row['url'] for row in rows))
    if len(urls) > 200:
        raise ValueError('Unexpected TheSportsDB event count')
    def details(url):
        return url, parse_event(fetch_html(url))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        metadata = dict(pool.map(details, urls))
    for row in rows:
        row.update(metadata[row.pop('url')])
    Path('sportsdb_status.json').write_text(json.dumps({
        'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source': URL, 'events': len(rows), 'status': 'ok'
    }, indent=2) + '\n', encoding='utf-8')
    return rows


def identity(row):
    title = canonical(row['title'])
    # These suffixes describe the same team in the two sources. Keep gender in
    # the identity separately so women's and men's fixtures can never collapse.
    female = bool(re.search(r'\b(?:women|womens|femminile)\b', canonical(row.get('sport', '') + ' ' + row['title'])))
    title = re.sub(r'\b(?:women|womens|rugby)\b', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'\s+vs\.?\s+', '-', title, flags=re.I)
    # canonical() normalises punctuation, so split the original fixture first.
    parts = re.split(r'\s*[-–—]\s*|\s+vs\.?\s+', row['title'], flags=re.I)
    if len(parts) == 2:
        cleaned = [' '.join(re.sub(r'\b(?:women|womens|rugby)\b', '', canonical(p)).split()) for p in parts]
        title = tuple(cleaned)
    sport = canonical(row.get('sport', ''))
    sport = re.sub(r'\bfemminile\b', '', sport).strip()
    return row['time'], sport, female, title


def merge_events(grouped, additional):
    days = {date: [dict(row, sources=row.get('sources', ['Virgilio'])) for row in rows]
            for date, rows in grouped}
    for incoming in additional:
        incoming = dict(incoming)
        date = incoming.pop('date')
        rows = days.setdefault(date, [])
        match = next((row for row in rows if identity(row) == identity(incoming)), None)
        if match is None:
            rows.append(incoming)
        else:
            match['sources'] = list(dict.fromkeys(match['sources'] + incoming['sources']))
            channels = match.get('channels', '').split(',') + incoming.get('channels', '').split(',')
            match['channels'] = ', '.join(dict.fromkeys(c.strip() for c in channels if c.strip()))
            match.pop('dispatcharr_html', None)
    return [(date, sorted(rows, key=lambda row: (row['time'], row['title'])))
            for date, rows in sorted(days.items())]
