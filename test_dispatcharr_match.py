import datetime as dt
import unittest
from dispatcharr_match import ChannelResolver, ROME, event_matches


class MatchingTests(unittest.TestCase):
    date = dt.date(2026, 11, 7)
    row = {'time': '20:45', 'sport': 'Calcio', 'competition': 'Serie A',
           'title': 'Roma-Sassuolo', 'channels': 'Sky, DAZN, Sky Sport 1, Sky Sport Calcio'}

    def programme(self, title, hour=20, minute=30, duration=150):
        start = dt.datetime(2026, 11, 7, hour, minute, tzinfo=ROME)
        return {'title': title, 'epg_id': 'intl', 'start': start,
                'stop': start + dt.timedelta(minutes=duration)}

    def test_multilingual_fixture(self):
        self.assertTrue(event_matches(self.row, self.date, self.programme('Серия А. Рома - Сассуоло. Прямая трансляция')))
        self.assertFalse(event_matches(self.row, self.date, self.programme('Roma - Milan')))

    def test_time_replay_and_placeholder_guards(self):
        for programme in [self.programme('Roma - Sassuolo', 15),
                          self.programme('Highlights Roma - Sassuolo'),
                          self.programme('Roma - Sassuolo', duration=1440)]:
            self.assertFalse(event_matches(self.row, self.date, programme))
        # Same fixture on a different local day is not today's simulcast.
        self.assertFalse(event_matches(self.row, self.date + dt.timedelta(days=1), self.programme('Roma - Sassuolo')))

    def test_wrong_sport_is_rejected(self):
        row = {**self.row, 'title': 'Italia-Francia', 'sport': 'Rugby'}
        self.assertFalse(event_matches(row, self.date, self.programme('Football: Italy - France')))
        self.assertTrue(event_matches(row, self.date, self.programme('Rugby: Italy - France')))

    def test_gender_and_youth_are_not_interchangeable(self):
        row = {**self.row, 'title': 'Milan-Inter', 'sport': 'Calcio femminile'}
        self.assertFalse(event_matches(row, self.date, self.programme('AC Milan - Inter Milan')))
        self.assertTrue(event_matches(row, self.date, self.programme('Women: AC Milan - Inter Milan')))
        row = {**self.row, 'title': 'Inter U23-Salernitana', 'competition': 'Serie C'}
        self.assertFalse(event_matches(row, self.date, self.programme('Inter - Salernitana')))

    def test_all_channels_no_links_or_broadcaster_guess(self):
        channels = [
            {'name': 'Sky Sport 1 HD', 'number': 201, 'epg_id': 'sky', 'epg_url': 'it'},
            {'name': 'Sky Sport Calcio', 'number': 202, 'epg_id': 'calcio', 'epg_url': 'it'},
            {'name': 'DAZN 1', 'number': 214, 'epg_id': 'dazn1', 'epg_url': 'it'},
            {'name': 'DAZN 1 (DE)', 'number': 500, 'epg_id': 'intl', 'epg_url': 'de'},
            {'name': 'DAZN 2 (DE)', 'number': 501, 'epg_id': 'intl', 'epg_url': 'de'},
        ]
        resolver = ChannelResolver({'channels': channels}, {'de': [self.programme('Roma - Sassuolo')]})
        result = resolver.render(self.row, self.date)
        for name in ['Sky Sport 1 HD', 'Sky Sport Calcio', 'DAZN 1 (DE)', 'DAZN 2 (DE)']:
            self.assertIn(name, result)
        self.assertNotIn('DAZN 1 ·', result)  # generic DAZN is not DAZN 1
        self.assertNotIn('<a ', result)
        self.assertEqual(result.count('Sky Sport 1 HD'), 1)

    def test_unknown_and_failed_sources_are_visible(self):
        resolver = ChannelResolver({'channels': []}, failures=['unavailable'])
        result = resolver.render(self.row, self.date)
        self.assertIn('Nessun canale', result)
        self.assertIn('parziale', result)
        self.assertIn('DAZN', result)

    def test_session_must_match_not_just_competition(self):
        row = {**self.row, 'sport': 'Tennis', 'competition': 'ATP Parigi', 'title': '1a Semifinale'}
        self.assertFalse(event_matches(row, self.date, self.programme('ATP Parigi 2a Semifinale')))
        self.assertTrue(event_matches(row, self.date, self.programme('ATP Parigi 1a Semifinale')))


if __name__ == '__main__':
    unittest.main()
