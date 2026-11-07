"""Resolve Virgilio broadcasters and simulcasts using a sanitised Dispatcharr lineup.

Only public guide URLs are used. No Dispatcharr access or playback URLs are needed.
"""
import concurrent.futures
import datetime as dt
import gzip
import functools
import html
import json
import re
import tempfile
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
CONFIG = Path(__file__).with_name('dispatcharr_channels.json')


def normalise(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in text if not unicodedata.combining(c))))


def channel_key(name):
    name = re.sub(r'\s*\(IT\)\s*$', '', name, flags=re.I)
    name = re.sub(r'\b(?:HD|FHD|UHD|4K)\b', '', name, flags=re.I)
    return normalise(name).replace('sky sport uno', 'sky sport 1')


# Explicit synonyms only: never substring-match DAZN, Eurosport or Sky to all
# their numbered channels, or erase regional editions from channel names.
CHANNEL_ALIASES = {'sky sport nba': 'sky sport basket', 'la7': 'la 7',
                   'rai sport 1': 'rai sport', 'dazn italy': 'dazn',
                   'dazn 1 italy': 'dazn 1', 'dazn 2 italy': 'dazn 2'}
TEAM_ALIASES = [
    ('roma', 'as roma', 'роме', 'рома'), ('milan', 'ac milan', 'милан'),
    ('inter', 'inter milan', 'inter milano', 'internazionale', 'интер'),
    ('juventus', 'ювентус'), ('empoli', 'эмполи'),
    ('atalanta', 'аталанта'), ('sassuolo', 'сассуоло'),
    ('bologna', 'болонья'), ('parma', 'парма'), ('torino', 'торино'),
    ('lecce', 'лечче'), ('cagliari', 'кальяри'), ('frosinone', 'фрозиноне'),
    ('leeds united', 'leeds', 'лидс'), ('tottenham', 'tottenham hotspur', 'тоттенхэм'),
    ('arsenal', 'арсенал'), ('hull city', 'hull', 'халл'),
    ('nottingham forest', 'nottingham', 'ноттингем форест'),
    ('manchester city', 'man city', 'манчестер сити'),
    ('manchester united', 'man utd', 'манчестер юнайтед'),
    ('mainz', 'mainz 05', 'майнц'), ('bayern monaco', 'bayern munich', 'bayern munchen', 'бавария'),
    ('stoccarda', 'stuttgart', 'штутгарт'), ('werder brema', 'werder bremen', 'вердер'),
    ('italia', 'italy', 'italie', 'italien', 'италия'),
    ('sudafrica', 'south africa', 'afrique du sud', 'юар'),
    ('scozia', 'scotland', 'ecosse', 'шотландия'),
    ('nuova zelanda', 'new zealand', 'nouvelle zelande', 'новая зеландия'),
    ('galles', 'wales', 'pays de galles', 'уэльс'),
    ('giappone', 'japan', 'japon', 'япония'),
    ('francia', 'france', 'frankreich', 'франция'), ('figi', 'fiji', 'fidji', 'фиджи'),
    ('belgio', 'belgium', 'belgique', 'бельгия'),
    ('barcellona', 'barcelona', 'барселона'),
    ('valencia', 'fc valencia', 'valencia cf', 'валенсия'),
    ('real madrid', 'реал мадрид'),
    ('osasuna', 'ca osasuna', 'осасуна'),
    ('athletic bilbao', 'athletic club', 'атлетик бильбао', 'атлетик'),
]


@functools.lru_cache(maxsize=8192)
def canonical(text):
    text = ' ' + normalise(text) + ' '
    replacements = []
    for group in TEAM_ALIASES:
        for alias in group:
            replacements.append((normalise(alias), normalise(group[0])))
    # Longest first prevents "Inter Milan" becoming "Inter Milan" in two steps.
    pattern = '|'.join(re.escape(a) for a, _ in sorted(replacements, key=lambda x: -len(x[0])))
    lookup = dict(replacements)
    return re.sub(r'(?<!\w)(' + pattern + r')(?!\w)', lambda m: lookup[m[0]], text).strip()


def xml_time(value):
    # A timezone is required; guessing UTC silently shifts a fixture.
    return dt.datetime.strptime(value.strip(), '%Y%m%d%H%M%S %z')


def fetch_programmes(url, channels, first, last):
    wanted = {c['epg_id'] for c in channels}
    programmes = []
    deadline = time.monotonic() + 180
    request = urllib.request.Request(url, headers={'User-Agent': 'VirgilioSportGuide/1.0', 'Accept-Encoding': 'gzip'})
    with tempfile.TemporaryFile() as downloaded:
        with urllib.request.urlopen(request, timeout=25) as response:
            size = 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError('guide download deadline')
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError('guide exceeds download limit')
                downloaded.write(chunk)
        downloaded.seek(0)
        compressed = downloaded.read(2) == b'\x1f\x8b'
        downloaded.seek(0)
        source = gzip.GzipFile(fileobj=downloaded) if compressed else downloaded
        context = ET.iterparse(source, events=('start', 'end'))
        _, root = next(context)
        for event, element in context:
            if time.monotonic() > deadline:
                raise TimeoutError('guide parsing deadline')
            if event != 'end' or element.tag not in ('programme', 'channel'):
                continue
            if element.tag == 'programme' and element.get('channel') in wanted:
                try:
                    start, stop = xml_time(element.get('start', '')), xml_time(element.get('stop', ''))
                except ValueError:
                    element.clear()
                    root.clear()
                    continue
                if first <= start.astimezone(ROME).date() <= last and stop > start:
                    title = ' '.join(n.text or '' for tag in ('title', 'sub-title') for n in element.findall(tag))
                    programmes.append({'epg_id': element.get('channel'), 'start': start,
                                       'stop': stop, 'title': title})
            element.clear()
            root.clear()
    return programmes


def event_matches(row, date, programme):
    hh, mm = map(int, row['time'].split(':'))
    start = dt.datetime.combine(date, dt.time(hh, mm), ROME)
    # Allow bounded pre-match coverage, not arbitrary overlapping programmes.
    delta = (start - programme['start']).total_seconds()
    if not -900 <= delta <= 3600 or not programme['start'] <= start + dt.timedelta(minutes=15) < programme['stop']:
        return False
    if programme['stop'] - programme['start'] > dt.timedelta(hours=6):
        return False  # multi-day event placeholders are not coverage evidence
    title = canonical(programme['title'])
    if re.search(r'\b(replay|highlights|replica|sintesi|повтор|обзор|resumo|reprise)\b', title):
        return False
    sports = [r'\b(?:calcio|football|футбол|futebol)\b',
              r'\b(?:rugby|регби)\b', r'\b(?:basket|basketball|баскетбол)\b',
              r'\b(?:tennis|теннис)\b', r'\b(?:pallavolo|volleyball|волейбол)\b',
              r'\b(?:pallamano|handball|гандбол)\b']
    expected_sport = canonical(row.get('sport', ''))
    expected_categories = {i for i, pattern in enumerate(sports) if re.search(pattern, expected_sport)}
    actual_categories = {i for i, pattern in enumerate(sports) if re.search(pattern, title)}
    if expected_categories and actual_categories and not expected_categories & actual_categories:
        return False
    event = canonical(row.get('title', ''))
    parts = re.split(r'\s*[-–—]\s*|\s+(?:vs?\.?|contro)\s+', row.get('title', ''), flags=re.I)
    if len(parts) == 2 and all(len(normalise(p)) >= 3 for p in parts):
        # Distinguish youth, women's and reserve fixtures involving the same clubs.
        qualifiers = (r'\bu\s*23\b', r'\bu\s*21\b', r'\bu\s*19\b',
                      r'\b(?:primavera|youth)\b', r'\b(?:femminile|women|womens|feminine|женщины)\b')
        expected = canonical(' '.join([row.get('sport', ''), row.get('competition', ''), row.get('title', '')]))
        for qualifier in qualifiers:
            if bool(re.search(qualifier, expected)) != bool(re.search(qualifier, title)):
                return False
        return all(re.search(r'(?<!\w)' + re.escape(canonical(part)) + r'(?!\w)', title) for part in parts)
    # Non-fixture events need both the named competition and the specific session.
    # A generic "tennis", "GP" or "semifinal" alone is never a match.
    competition = canonical(row.get('competition', ''))
    return ((len(competition.split()) >= 2 or competition in ('motogp', 'moto2', 'moto3', 'superbike', 'formula 1'))
            and len(event.split()) >= 2
            and bool(re.search(r'(?<!\w)' + re.escape(competition) + r'(?!\w)', title))
            and bool(re.search(r'(?<!\w)' + re.escape(event) + r'(?!\w)', title)))


class ChannelResolver:
    def __init__(self, config, programmes=None, failures=None):
        self.config = config
        self.channels = config['channels']
        self.programmes = programmes or {}
        self.failures = failures or []
        self.direct = {}
        self.by_channel = {}
        for url, listings in self.programmes.items():
            for programme in listings:
                self.by_channel.setdefault((url, programme['epg_id']), []).append(programme)
        for channel in self.channels:
            key = channel_key(channel['name'])
            self.direct.setdefault(key, []).append(channel)

    @classmethod
    def load(cls, grouped):
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        sources = {}
        for channel in config['channels']:
            sources.setdefault(channel['epg_url'], []).append(channel)
        dates = [date for date, _ in grouped]
        if not dates:
            raise ValueError('No event dates to resolve')
        programmes, failures = {}, []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tasks = {pool.submit(fetch_programmes, url, channels, min(dates), max(dates)): url
                     for url, channels in sources.items()}
            for task in concurrent.futures.as_completed(tasks):
                url = tasks[task]
                try:
                    programmes[url] = task.result()
                    if not programmes[url]:
                        failures.append(url)
                except Exception as exc:
                    failures.append(url)
                    print(f'EPG unavailable: {url}: {type(exc).__name__}: {exc}', flush=True)
        resolver = cls(config, programmes, failures)
        # Public report contains guide metadata only, never playback addresses.
        report = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'lineup_checked': config['lineup_checked'], 'failed_sources': failures,
                  'channels': [{'name': c['name'], 'number': c['number'],
                                'programmes': sum(p['epg_id'] == c['epg_id'] for p in programmes.get(c['epg_url'], []))}
                               for c in config['channels']]}
        Path('epg_status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return resolver

    def render(self, row, date):
        found = {}
        unresolved = []
        for broadcaster in row.get('channels', '').split(','):
            broadcaster = broadcaster.strip()
            if not broadcaster:
                continue
            key = channel_key(broadcaster)
            matches = self.direct.get(CHANNEL_ALIASES.get(key, key), [])
            if matches:
                for channel in matches:
                    found[channel['name']] = (channel, 'Guida')
            else:
                unresolved.append(broadcaster)
        for channel in self.channels:
            listings = self.by_channel.get((channel['epg_url'], channel['epg_id']), [])
            if any(event_matches(row, date, p) for p in listings):
                found[channel['name']] = (channel, 'EPG')
        lines = [f'<div>{html.escape(c["name"])} · {c["number"]:g} <small>({evidence})</small></div>'
                 for c, evidence in sorted(found.values(), key=lambda item: item[0]['number'])]
        if not lines:
            lines.append('<div>Nessun canale Dispatcharr identificato</div>')
        if unresolved:
            lines.append('<small>Emittenti indicate: ' + html.escape(', '.join(unresolved)) + ' — canale non specificato o non presente</small>')
        # A per-event warning prevents an empty/failed feed from looking exhaustive.
        if self.failures:
            lines.append('<div><small>Ricerca EPG parziale: alcune guide non disponibili.</small></div>')
        return ''.join(lines)
