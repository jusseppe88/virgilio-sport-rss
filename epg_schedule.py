"""Build the public guide from imported Dispatcharr programmes (stdlib only)."""
import datetime as dt
import functools
import hashlib
import html
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from dispatcharr_match import canonical, normalise, TEAM_ALIASES, channel_key, CHANNEL_ALIASES, event_matches

ROME = ZoneInfo('Europe/Rome')
SPORT = re.compile(r'\b(sport|sports|calcio|football|soccer|rugby|basket|basketball|tennis|volley|pallavolo|pallamano|handball|motogp|moto2|moto3|formula|f1|hockey|baseball|boxing|boxe|ufc|mma|ciclismo|cycling|футбол|теннис|баскетбол|регби|волейбол)\b', re.I)
EDITORIAL = re.compile(r'\b(news|notiziario|telegiornale|meteo|highlights|highlights show|magazine|talk show|sport 24|sport24|обзор|новости)\b', re.I)
REPLAY = re.compile(r'\b(replay|replica|reprise|повтор|differita|classics)\b', re.I)
PLACEHOLDER = re.compile(r'^(?:sky sport|sky sport \d+|dazn \d*|aci sport tv|no event|nessun evento|programmazione|programmi|off air|to be announced|tba)\W*$', re.I)
NON_EVENT = re.compile(r'\b(studio|full court|calcio today|sports? non.event|sports? talk|dibattito|rassegna stampa|analysis|analisi|preview|previa|post.match|pre.match|post partita|pre partita|dopo partita|pregame|postgame|documentary|documentario|magazine|talk|highlights|sintesi|resumo|noticias|stirile|compact|champions league today)\b|\bdazn post\b', re.I)
NON_EVENT = re.compile(NON_EVENT.pattern + r'|\b(hoogtepunten|avant.match|apres.match|travel|тревел|шоу|чеκ.лист|memory|amarcord|show|sekrety|miedzy legenda|między legendą|look back|kick .t met|sailing to the games)\b', re.I)
SPORT_NAMES = [
    ('Rugby', r'rugby|регби|six nations|sei nazioni|top 14|nations championship'),
    ('Basket', r'basket(?:ball)?|basquetebol|baloncesto|koszykowka|баскетбол|nba|wnba|euroleague|eurolega|euroliga'),
    ('Tennistavolo', r'table tennis|tennistavolo'),
    ('Tennis', r'tennis|теннис|atp|wta|wimbledon|roland garros'),
    ('Pallavolo', r'pallavolo|volley(?:ball)?|волейбол'),
    ('Pallamano', r'pallamano|handball|handbal|andebol|гандбол'),
    ('Football americano', r'american football|football americano|nfl|ncaa football'),
    ('Calcio', r'calcio|soccer|football|futebol|futbol|voetbal|pi[łl]ka nozna|футбол|футболу|laliga|la liga|premier league|bundesliga|serie a|serie b|ligue [12]|liga francesa|liga a 2 a|superliga|fa cup|a league|nwsl|champions league|europa league|conference league|eredivisie'),
    ('Motociclismo', r'motogp|moto[23]|superbike|motociclismo|motorcycling'),
    ('Automobilismo', r'formula ?[123e]|f1|f2|f3|automobilismo|автоспорт|формула|nascar|indycar|rally|dtm|wec|porsche|supercars'),
    ('Ciclismo', r'ciclismo|cycling|cyclocross|ciclocross|tour de france|giro d.italia'),
    ('Hockey', r'hockey|nhl'), ('Baseball', r'baseball|mlb'),
    ('Golf', r'golf|golfe|pga|dp world tour'), ('Padel', r'padel'),
    ('Boxe', r'boxe|boxeo|boxing|бокс|bkfc'), ('Arti marziali', r'ufc|mma|oktagon|martial arts|kickboxing'),
    ('Snooker', r'snooker|billiard|biliardo'), ('Freccette', r'darts|freccette'),
    ('Sci', r'sci|ski|slalom|biathlon'), ('Nuoto', r'nuoto|swimming'),
    ('Atletica', r'atletica|athletics|marathon|maratona'),
    ('Pattinaggio', r'skating|pattinaggio'), ('Cricket', r'cricket'),
    ('Vela', r'vela|sailing'), ('Surf', r'surf|surfing'),
    ('Badminton', r'badminton'), ('Pallanuoto', r'pallanuoto|water polo'),
]
SESSION = re.compile(r'\b(gp|grand prix|gara|race|rennen|corsa|qualific\w*|practice|prove libere|fp[123]|sprint|final\w*|semifinal\w*|quarti|round|giornata|session\w*|singles|doubles|singolare|doppio|torneo|tournament|championship|campionato|diretta gol|redzone)\b', re.I)


def sport_name(c, p):
    return _sport_name(' '.join(p.get('categories', [])), p['title'] + ' ' + p.get('subtitle', ''), c['name'])


@functools.lru_cache(maxsize=16384)
def _sport_name(categories, title, channel):
    # Specific imported categories win over a channel's general branding.
    for text in (categories, title, channel):
        text = text + ' ' + normalise(text)
        for name, pattern in SPORT_NAMES:
            if re.search(r'\b(?:' + pattern + r')\b', text, re.I):
                return name
    return 'Altri sport'


@functools.lru_cache(maxsize=16384)
def fixture_identity(title):
    """Two named opponents, retaining age/gender and session qualifiers."""
    parts = re.split(r'\s*[–—]\s*|\s*(?:(?<!\d)-|-(?!\d))\s*|\s+(?:vs?\.?|contro|gegen)\s+', title, flags=re.I)
    if len(parts) < 2:
        return None
    # League/season prefixes sometimes use the same dash as the fixture.
    left, right = parts[-2:]
    teams = []
    known = {canonical(group[0]) for group in TEAM_ALIASES}
    for side in (left, right):
        side = side.rsplit(':', 1)[-1]
        side = re.sub(r'^.*?\.\s+(?=[A-ZА-Я])', '', side) if not re.match(r'^\s*St\.', side) else side
        side = canonical(side)
        matches = [team for team in known if side == team or side.endswith(' ' + team)]
        if matches:
            side = max(matches, key=len)
        else:
            side = re.split(r'[:.]\s*', side)[-1]
            side = re.sub(r'^(?:fc|cf|afc|ac|sc)\s+|\s+(?:fc|cf|afc)$', '', side).strip()
        # MATCH's short Russian Real label is an exact opponent alias only.
        if side == 'реал':
            side = 'real madrid'
        if len(side) < 3 or not re.search(r'[^\W\d_]', side) or len(side.split()) > 8:
            return None
        teams.append(side)
    if teams[0] == teams[1]:
        return None
    qualifiers = tuple(re.findall(r'\bu\s*\d{2}\b|\b(?:women|womens|femminile|feminine|женщины|youth|primavera|reserves|reserve|ii|game \d+|race \d+|gara \d+|singles|doubles)\b', normalise(title)))
    return tuple(sorted(teams)), tuple(sorted(qualifiers))


def parse_time(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Guide timestamps must include a timezone')
    return result


def validate_snapshot(data, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if data.get('schema') != 1 or data.get('timezone') != 'Europe/Rome':
        raise ValueError('Unsupported Dispatcharr snapshot')
    age = (now - parse_time(data['generated_at'])).total_seconds()
    if age < -300:
        raise ValueError('Snapshot timestamp is in the future')
    channels = data.get('channels')
    if not isinstance(channels, list) or not 1 <= len(channels) <= 1000:
        raise ValueError('Missing Dispatcharr lineup')
    seen = set()
    for channel in channels:
        identity = (channel['number'], channel['name'])
        if identity in seen:
            raise ValueError('Duplicate channel in snapshot')
        seen.add(identity)
        if not isinstance(channel.get('programmes'), list):
            raise ValueError('Invalid programme list')
        for p in channel['programmes']:
            if parse_time(p['stop']) <= parse_time(p['start']):
                raise ValueError('Invalid programme window')
    return age > 3 * 3600


def is_sport_channel(c):
    return bool(SPORT.search(c['name']) or re.search(r'\b(dazn|eurosport|espn|bein|okko|setanta|ziggo|nba|nfl|матч|старт|спорт)\b', c['name'], re.I))


def is_event(c, p):
    text = ' '.join([p['title'], p.get('subtitle', '')])
    categories = ' '.join(p.get('categories', []))
    if PLACEHOLDER.fullmatch(p['title'].strip()) or EDITORIAL.search(text) or NON_EVENT.search(text + ' ' + categories):
        return False
    if parse_time(p['stop']) - parse_time(p['start']) > dt.timedelta(hours=6):
        return False
    # A channel name alone does not make a programme a sporting event.
    return sport_name(c, p) != 'Altri sport' and bool(fixture_identity(text)
        or re.search(r'\bsports event\b', categories, re.I) or SESSION.search(text))


def known_broadcaster(name, channels):
    key = CHANNEL_ALIASES.get(channel_key(name), channel_key(name))
    return [c for c in channels if channel_key(c['name']) == key]


def programme_key(c, p):
    return (parse_time(p['start']).astimezone(ROME).isoformat(),
            canonical(p['title'] + ' ' + p.get('subtitle', '')),
            bool(p.get('repeat', False) or REPLAY.search(p['title'])), sport_name(c, p))


def unify_rows(rows):
    """Alias source rows to bounded simulcast groups, never chained time windows."""
    groups = {}
    for key, row in sorted(rows.items(), key=lambda item: item[1]['start']):
        fixture = fixture_identity(row['title'])
        identity = ('fixture', fixture) if fixture else ('title', canonical(row['title']))
        bucket = groups.setdefault((row['date'], identity, row['repeat']), [])
        target = None
        for candidate in bucket:
            if (row['sport'] != 'Altri sport' and candidate['sport'] != 'Altri sport'
                    and row['sport'] != candidate['sport']):
                continue
            # Fifteen minutes accommodates channel lead-ins without merging a
            # later replay, doubleheader or a chain of adjacent sessions.
            delta = abs((parse_time(row['start']) - parse_time(candidate['start'])).total_seconds())
            overlap = min(parse_time(row['stop']), parse_time(candidate['stop'])) - max(parse_time(row['start']), parse_time(candidate['start']))
            # Some providers split a match into short EPG slots (DAZN's
            # Torino–Lecce entry is ten minutes). Identified opponents plus
            # overlapping coverage suffice; duration must not split simulcasts.
            enough_overlap = overlap > dt.timedelta(0) if fixture else overlap >= dt.timedelta(minutes=15)
            if delta <= 900 and enough_overlap:
                target = candidate
                break
        if target is None:
            bucket.append(row)
            continue
        target['channels'].update(row['channels'])
        if target['sport'] == 'Altri sport':
            target['sport'] = row['sport']
        if fixture:
            # Prefer a readable local title over a foreign league prefix.
            if (not re.search('[а-яА-Я]', row['title']) and
                    (re.search('[а-яА-Я]', target['title']) or len(row['title']) < len(target['title']))):
                target['title'] = row['title']
        rows[key] = target
    return rows


def build_rows(snapshot, supplemental=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    stale = validate_snapshot(snapshot, now)
    channels = snapshot['channels']
    supplemental = supplemental or []
    rows = {}
    coverage = []
    imported_events = []
    for c in channels:
        future = [p for p in c['programmes'] if parse_time(p['stop']) > now]
        precise = [p for p in future if not PLACEHOLDER.fullmatch(p['title'].strip())
                   and parse_time(p['stop']) - parse_time(p['start']) <= dt.timedelta(hours=6)]
        if not c.get('mapped'):
            coverage.append(f'{c["name"]} · {c["number"]:g}: EPG non associata')
        elif not future:
            coverage.append(f'{c["name"]} · {c["number"]:g}: nessun programma futuro importato')
        elif not precise:
            coverage.append(f'{c["name"]} · {c["number"]:g}: soltanto voci generiche o finestre troppo lunghe')
        for p in c['programmes']:
            if not is_event(c, p):
                continue
            imported_events.append((c, p, {'start': parse_time(p['start']), 'stop': parse_time(p['stop']),
                                          'title': p['title'] + ' ' + p.get('subtitle', '')}))
            start = parse_time(p['start']).astimezone(ROME)
            title = p['title']
            subtitle = p.get('subtitle', '')
            repeat = p.get('repeat', False) or bool(REPLAY.search(title))
            key = programme_key(c, p)
            row = rows.setdefault(key, {'date': start.date().isoformat(), 'time': start.strftime('%H:%M'),
                'start': p['start'], 'stop': p['stop'], 'sport': sport_name(c, p), 'competition': '',
                'title': title + ((' — ' + subtitle) if subtitle else ''), 'repeat': bool(repeat),
                'channels': {}, 'sources': ['Dispatcharr EPG']})
            row['channels'][c['name']] = c
    rows = unify_rows(rows)
    missing, unresolved, gaps = {}, set(), []
    # External listings only find gaps and enrich existing imported events;
    # they cannot manufacture a watchable channel or an extra primary row.
    for event in supplemental:
        date = dt.date.fromisoformat(event['date'])
        candidates = []
        expected_sport = sport_name({'name': ''}, {'title': event.get('sport', ''), 'categories': []})
        for c, p, programme in imported_events:
            actual_sport = sport_name(c, p)
            if expected_sport != 'Altri sport' and actual_sport != 'Altri sport' and expected_sport != actual_sport:
                continue
            if not p.get('repeat') and event_matches(event, date, programme):
                candidates.append((c, p))
        broadcasters = [b.strip() for b in event.get('channels', '').split(',') if b.strip()]
        for broadcaster in broadcasters:
            if not known_broadcaster(broadcaster, channels):
                # Only numbered/specific linear services can be called missing.
                key = channel_key(broadcaster)
                generic = key in {'sky', 'dazn', 'dazn italy', 'sky go it', 'eurosport', 'eurosport italia', 'now', 'amazon prime video', 'prime video'}
                specific = bool(re.search(r'\d', key)) or key in {'sportitalia', 'rai sport', 'supertennis'}
                if specific and not generic:
                    missing.setdefault(broadcaster, set()).add(event['title'])
                else:
                    unresolved.add(broadcaster)
        if not candidates:
            # Report only future/current-day events, never old archival gaps.
            if date >= now.astimezone(ROME).date():
                gaps.append({'date': event['date'], 'time': event['time'], 'title': event['title'],
                             'broadcasters': broadcasters})
            continue
        # Cross-language fixture identification comes from a specific external
        # event + imported records. The records alone supply the channel list.
        target = None
        keys = []
        for c, p in candidates:
            key = programme_key(c, p)
            if key in rows:
                keys.append(key)
                if target is None:
                    target = rows[key]
        if target:
            target['title'] = event['title']
            target['sport'] = event.get('sport') or target['sport']
            target['competition'] = event.get('competition', '')
            target['sources'] = ['Dispatcharr EPG'] + event.get('sources', [])
            for key in dict.fromkeys(keys):
                other = rows[key]
                target['channels'].update(other['channels'])
                if other is not target:
                    for source_key in rows:
                        if rows[source_key] is other:
                            rows[source_key] = target
    result = sorted({id(row): row for row in rows.values()}.values(), key=lambda r: (r['date'], r['time'], r['title']))
    report = {'generated_at': snapshot['generated_at'], 'stale': stale,
              'missing_channels': [{'name': n, 'events': sorted(events)} for n, events in sorted(missing.items())],
              'epg_gaps': coverage, 'unresolved_broadcasters': sorted(unresolved), 'unmatched_events': gaps}
    return result, report


def esc(value):
    return html.escape(str(value), quote=True)


def render_outputs(snapshot, supplemental=None, now=None):
    rows, report = build_rows(snapshot, supplemental, now)
    status = ('Fonte principale: EPG importata in Dispatcharr. Aggiornamento: ' + snapshot['generated_at'] +
              '. Orari Europe/Rome. Le fonti esterne servono al confronto; la guida non verifica la riproduzione.')
    if report['stale']:
        status += ' ATTENZIONE: esportazione EPG non aggiornata da oltre 3 ore.'
    def items(values):
        return '<ul>' + ''.join('<li>' + esc(v) + '</li>' for v in values) + '</ul>' if values else '<p>Nessuno.</p>'
    gaps = '<section id="guide-gaps"><h2>Canali da aggiungere</h2>'
    gaps += items([c['name'] + ' — ' + ', '.join(c['events']) for c in report['missing_channels']])
    gaps += '<h2>Canali presenti con EPG mancante o generica</h2>' + items(report['epg_gaps'])
    gaps += '<details><summary>Emittenti non identificabili come canale specifico</summary>' + items(report['unresolved_broadcasters']) + '</details>'
    gaps += '<details><summary>Eventi esterni non trovati nella EPG importata</summary>'
    gaps += items([f'{e["date"]} {e["time"]} {e["title"]} — ' + ', '.join(e['broadcasters']) for e in report['unmatched_events']]) + '</details></section>'
    days = {}
    for row in rows:
        channels = '<br>'.join(esc(c['name']) + f' · {c["number"]:g}'
                               for c in sorted(row['channels'].values(), key=lambda c: c['number']))
        label = '<br><small>Replica / già trasmesso</small>' if row['repeat'] else ''
        cells = [row['time'], row['sport'], row['competition'], row['title']]
        tr = '<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in cells[:3])
        tr += '<td>' + esc(cells[3]) + label + '</td><td>' + channels + '</td></tr>'
        days.setdefault(row['date'], []).append(tr)
    tables = {date: '<h2>' + esc(date) + '</h2><table><thead><tr><th>Ora</th><th>Sport</th><th>Competizione</th><th>Evento</th><th>Canali</th></tr></thead><tbody>' + ''.join(trs) + '</tbody></table>' for date, trs in days.items()}
    sections = ''.join('<section class="day" id="' + date + '">' + table + '</section>' for date, table in tables.items())
    page = '<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sport — Dispatcharr EPG</title><style>body{background:#0b0c0f;color:#f3f4f6;font:15px/1.5 system-ui;margin:16px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #64748b;padding:8px;text-align:left}#guide-gaps{margin:18px 0}small{color:#cbd5e1}</style><body><p id="guide-status" data-exported-at="' + esc(snapshot['generated_at']) + '">' + esc(status) + '</p>' + gaps + sections + '</body></html>'
    rss = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Sport — Dispatcharr EPG</title><link>https://jusseppe88.github.io/virgilio-sport-rss/home.html</link><description>Eventi dalla EPG importata in Dispatcharr</description>']
    for date, table in tables.items():
        digest = hashlib.sha256(table.encode()).hexdigest()
        rss.append('<item><title>' + esc(date) + '</title><guid isPermaLink="false">' + digest + '</guid><description>' + esc(table) + '</description></item>')
    rss.append('</channel></rss>')
    for path in ['tables.html', 'index.html']:
        Path(path).write_text(page, encoding='utf-8')
    for path in ['rss.xml', 'rss_tables.xml']:
        Path(path).write_text('\n'.join(rss), encoding='utf-8')
    Path('epg_status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return len(rows)


if __name__ == '__main__':
    snapshot = json.loads(Path('dispatcharr_epg.json').read_text(encoding='utf-8'))
    supplements = json.loads(Path('supplementary_events.json').read_text(encoding='utf-8')) if Path('supplementary_events.json').exists() else []
    print('Rendered imported EPG events:', render_outputs(snapshot, supplements))
