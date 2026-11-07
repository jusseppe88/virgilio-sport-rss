import datetime as dt
import json
from pathlib import Path
import tempfile
import os
import unittest
import xml.etree.ElementTree as ET
from epg_schedule import build_rows, render_outputs, validate_snapshot
from read_dispatcharr_epg import public_text

NOW = dt.datetime(2026,11,7,12,tzinfo=dt.timezone.utc)


def programme(title='Roma-Sassuolo', **changes):
    return {'start':'2026-11-07T19:45:00+00:00','stop':'2026-11-07T22:00:00+00:00',
            'title':title,'subtitle':'','categories':['Sport','Calcio'],'live':False,'repeat':False,**changes}


def channel(name='Sky Sport Calcio', number=202, programmes=None, **changes):
    return {'name':name,'number':number,'mapped':True,
            'programmes':programmes if programmes is not None else [programme()],**changes}


def snapshot(channels=None):
    return {'schema':1,'timezone':'Europe/Rome','generated_at':NOW.isoformat(),
            'channels':channels if channels is not None else [channel()]}


class EPGScheduleTests(unittest.TestCase):
    def test_imported_events_do_not_need_external_listing(self):
        rows,report=build_rows(snapshot(),[],NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['time'],'20:45')
        self.assertIn('Sky Sport Calcio',rows[0]['channels'])

    def test_all_exact_simulcast_channels_are_retained(self):
        rows,_=build_rows(snapshot([channel(),channel('Sky Sport 1 HD',201)]),[],NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['channels']),2)

    def test_translated_valencia_fixture_unifies_with_all_channels(self):
        listings = [
            channel('МАТЧ! Футбол 2 (RU)',1206,[programme('⋗ Испания. Валенсия – Реал', start='2026-11-07T19:50:00+00:00', categories=[])]),
            channel('DAZN 2 (DE)',501,[programme('LaLiga 26/27 – FC Valencia – Real Madrid', start='2026-11-07T19:55:00+00:00', categories=[])]),
            channel('Ziggo Sport 1 (NL)',800,[programme('⋗ LaLiga. Valencia CF – Real Madrid', start='2026-11-07T19:55:00+00:00', categories=[])]),
            channel('DAZN 1',214,[programme('Valencia - Real Madrid', start='2026-11-07T20:00:00+00:00')]),
        ]
        rows,_=build_rows(snapshot(listings),[],NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['sport'],'Calcio')
        self.assertEqual(rows[0]['title'],'Valencia - Real Madrid')
        self.assertEqual(rows[0]['time'],'20:50')
        self.assertEqual(set(rows[0]['channels']),{c['name'] for c in listings})

    def test_small_start_offsets_do_not_chain_into_later_broadcasts(self):
        channels=[channel('Sky Sport Calcio',202,[programme()]),
                  channel('Sky Sport 1',201,[programme(start='2026-11-07T19:55:00+00:00')]),
                  channel('DAZN 1',214,[programme(start='2026-11-07T20:05:00+00:00')])]
        rows,_=build_rows(snapshot(channels),[],NOW)
        self.assertEqual(len(rows),2)
        self.assertEqual(sorted(len(r['channels']) for r in rows),[1,2])

    def test_short_torino_slot_merges_with_full_match_and_keeps_competition(self):
        channels=[channel('МАТЧ! Футбол 1 (RU)',1205,[programme(
            '⋗ Италия. Торино – Лечче',start='2026-11-07T13:55:00+00:00',
            stop='2026-11-07T15:55:00+00:00',categories=[])]),
            channel('DAZN 1',214,[programme('Torino - Lecce',
                start='2026-11-07T14:00:00+00:00',stop='2026-11-07T14:10:00+00:00')])]
        extra=[{'date':'2026-11-07','time':'15:00','title':'Torino-Lecce','sport':'Calcio',
                'competition':'Serie A','channels':'DAZN','sources':['Virgilio']}]
        rows,_=build_rows(snapshot(channels),extra,NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['time'],'14:55')
        self.assertEqual(rows[0]['competition'],'Serie A')
        self.assertEqual(set(rows[0]['channels']),{c['name'] for c in channels})

    def test_short_fixture_slots_without_overlap_stay_separate(self):
        channels=[channel(programmes=[programme(stop='2026-11-07T19:50:00+00:00')]),
            channel('DAZN 1',214,[programme(start='2026-11-07T19:55:00+00:00',
                stop='2026-11-07T20:05:00+00:00')])]
        rows,_=build_rows(snapshot(channels),[],NOW)
        self.assertEqual(len(rows),2)

    def test_women_youth_other_sports_and_later_replays_stay_separate(self):
        programmes=[programme(),programme('Roma Women-Sassuolo Women'),
                    programme('Roma U19-Sassuolo U19'),programme(categories=['Rugby']),
                    programme(start='2026-11-07T22:30:00+00:00',stop='2026-11-08T00:30:00+00:00')]
        rows,_=build_rows(snapshot([channel(programmes=programmes)]),[],NOW)
        self.assertEqual(len(rows),5)

    def test_studio_and_generic_sports_programmes_are_not_events(self):
        programmes=[programme('Calcio today',subtitle='Studio Led'),programme('Full Court'),
                    programme('DAZN Previa (T26/27): Valencia – Real Madrid',categories=[]),
                    programme('DAZN Post (T26/27): Valencia – Real Madrid',categories=[]),
                    programme('Matchday Live',categories=['Sports talk']),
                    programme('A football programme',categories=['Sports non-event']),
                    programme('World Of Freesports',categories=['Sport'])]
        rows,_=build_rows(snapshot([channel(programmes=programmes)]),[],NOW)
        self.assertFalse(rows)

    def test_named_race_and_tennis_session_survive_without_opponents(self):
        rows,_=build_rows(snapshot([channel(programmes=[
            programme('GP Qatar: Gara',categories=['Motociclismo']),
            programme('ATP Finals: Singles R.R. P1',categories=['Tennis'])])]),[],NOW)
        self.assertEqual({r['sport'] for r in rows},{'Motociclismo','Tennis'})

    def test_external_claim_cannot_add_a_channel_to_event(self):
        extra=[{'date':'2026-11-07','time':'20:45','title':'Roma-Sassuolo','sport':'Calcio',
                'competition':'Serie A','channels':'Sky Sport 1, Sky Sport 999, DAZN','sources':['Virgilio']}]
        rows,report=build_rows(snapshot(),extra,NOW)
        self.assertEqual(list(rows[0]['channels']),['Sky Sport Calcio'])
        self.assertEqual([c['name'] for c in report['missing_channels']],['Sky Sport 1','Sky Sport 999'])
        self.assertIn('DAZN',report['unresolved_broadcasters'])

    def test_guide_gap_is_not_a_missing_channel(self):
        rows,report=build_rows(snapshot([channel(programmes=[])]),[],NOW)
        self.assertFalse(rows)
        self.assertFalse(report['missing_channels'])
        self.assertIn('Sky Sport Calcio',report['epg_gaps'][0])

    def test_news_placeholders_and_multiday_entries_are_not_events(self):
        rows,report=build_rows(snapshot([channel(programmes=[programme('News'),programme('SKY Sport'),
            programme('Inter-Como',stop='2026-11-10T22:00:00+00:00')])]),[],NOW)
        self.assertFalse(rows)

    def test_repeat_metadata_cannot_become_live_simulcast(self):
        extra=[{'date':'2026-11-07','time':'20:45','title':'Roma-Sassuolo','sport':'Calcio','channels':'Sky', 'sources':['Virgilio']}]
        rows,_=build_rows(snapshot([channel(),channel('Sky Sport 1 HD',201,[programme(repeat=True)])]),extra,NOW)
        self.assertEqual(len(rows),2)
        self.assertEqual(sorted(len(r['channels']) for r in rows),[1,1])

    def test_staleness_and_bad_timestamps(self):
        self.assertTrue(validate_snapshot(snapshot(),NOW+dt.timedelta(hours=4)))
        with self.assertRaises(ValueError):
            build_rows(snapshot([channel(programmes=[programme(start='2026-11-07T19:45:00')])]),[],NOW)

    def test_export_sanitises_urls_and_has_no_stream_fields(self):
        self.assertEqual(public_text('https://private.example/path?token=abc'),'[testo omesso]')
        self.assertEqual(public_text('Roma-Sassuolo'),'Roma-Sassuolo')

    def test_html_and_rss_are_escaped(self):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                render_outputs(snapshot([channel('<script>alert(1)</script>',202)]),[],NOW)
                page=Path('tables.html').read_text(encoding='utf-8')
                self.assertIn('&lt;script&gt;',page)
                self.assertNotIn('<script>',page)
                self.assertNotIn('<small>EPG</small>',page)
                self.assertIn('<td>Calcio</td>',page)
                ET.parse('rss.xml')
                report=json.loads(Path('epg_status.json').read_text(encoding='utf-8'))
                self.assertIn('missing_channels',report)
            finally:
                os.chdir(previous)


if __name__=='__main__': unittest.main()
