"""Run inside Dispatcharr: export a bounded, read-only public guide snapshot."""
import datetime as dt
import json
import os
import re
from zoneinfo import ZoneInfo


def public_text(value, limit=512):
    value = str(value or '').strip()[:limit]
    # Only programme text is published; links, addresses and token-shaped text
    # are never needed to identify an event.
    if re.search(r'https?://|\b\d{1,3}(?:\.\d{1,3}){3}\b|Bearer\s|(?:ghp_|github_pat_|eyJ)[A-Za-z0-9_]{20}', value, re.I):
        return '[testo omesso]'
    return value


def snapshot():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dispatcharr.settings')
    import django
    django.setup()
    from django.db import connection, transaction
    from apps.channels.models import Channel
    from apps.epg.models import ProgramData

    now = dt.datetime.now(dt.timezone.utc)
    start = dt.datetime.combine(now.astimezone(ZoneInfo('Europe/Rome')).date(), dt.time(), ZoneInfo('Europe/Rome'))
    end = start + dt.timedelta(days=7)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute("SET LOCAL statement_timeout = '20000ms'")
        channels = list(Channel.objects.order_by('channel_number').values('name', 'channel_number', 'epg_data_id'))
        if not 1 <= len(channels) <= 1000:
            raise ValueError('Unexpected channel count')
        ids = {c['epg_data_id'] for c in channels if c['epg_data_id']}
        listings = list(ProgramData.objects.filter(epg_id__in=ids, end_time__gt=start,
                        start_time__lt=end).order_by('start_time').values(
                            'epg_id', 'start_time', 'end_time', 'title', 'sub_title', 'custom_properties')[:30001])
        if not listings or len(listings) > 30000:
            raise ValueError('Missing or excessive imported programme data')
    by_epg = {}
    for p in listings:
        props = p['custom_properties'] if isinstance(p['custom_properties'], dict) else {}
        categories = props.get('categories', [])
        if not isinstance(categories, list):
            categories = []
        item = {'start': p['start_time'].isoformat(), 'stop': p['end_time'].isoformat(),
                'title': public_text(p['title']), 'subtitle': public_text(p['sub_title']),
                'categories': [public_text(c, 80) for c in categories[:12] if isinstance(c, str)],
                'live': props.get('live') is True,
                'repeat': props.get('previously_shown') is True}
        by_epg.setdefault(p['epg_id'], []).append(item)
    return {'schema': 1, 'generated_at': now.isoformat(), 'timezone': 'Europe/Rome',
            'window_start': start.isoformat(), 'window_end': end.isoformat(),
            'channels': [{'name': public_text(c['name'], 160), 'number': c['channel_number'],
                          'mapped': c['epg_data_id'] is not None,
                          'programmes': by_epg.get(c['epg_data_id'], [])} for c in channels]}


if __name__ == '__main__':
    print(json.dumps(snapshot(), ensure_ascii=False, separators=(',', ':')))
