import datetime as dt
import unittest
from sportsdb import parse_listings, parse_event, merge_events


class SportsDBTests(unittest.TestCase):
    now = dt.datetime(2026,11,7,12,tzinfo=dt.timezone.utc)

    def page(self, time='23:30', day='7th November'):
        return f'''<h2>Sports On TV today: Saturday {day}</h2><td class="tv-event-card">
        <a href="/event/123-full-event-name">Truncated event</a> {time} UTC
        <a href="/channel/1-sky-sport-uno"></a>Sky Sport Uno<br>
        <a href="/channel/2-tv8">TV8</a><br></td><footer>Timezone: UTC</footer>'''

    def test_utc_date_rollover_and_all_broadcasters(self):
        row = parse_listings(self.page(),self.now)[0]
        self.assertEqual(row['date'],dt.date(2026,11,8))
        self.assertEqual(row['time'],'00:30')
        self.assertEqual(row['channels'],'Sky Sport Uno, TV8')

    def test_dst_summer(self):
        now=dt.datetime(2026,7,7,12,tzinfo=dt.timezone.utc)
        row=parse_listings(self.page('22:30','7th July'),now)[0]
        self.assertEqual((row['date'],row['time']),(dt.date(2026,7,8),'00:30'))

    def test_missing_stale_and_timezone_changed_fail(self):
        for raw in ['',self.page(day='6th November'),self.page().replace('Timezone: UTC','Timezone: CET')]:
            with self.assertRaises(ValueError):
                parse_listings(raw,self.now)

    def test_full_name_from_structured_event(self):
        raw='''<a href="/sport/basketball">Basketball</a>
        <script type="application/ld+json">{"@type":"SportsEvent","name":"Dallas Mavericks vs Utah Jazz", "organizer":{"name":"NBA"}}</script>'''
        row=parse_event(raw)
        self.assertEqual(row['title'],'Dallas Mavericks-Utah Jazz')
        self.assertEqual((row['sport'],row['competition']),('Basket','NBA'))

    def test_merge_translated_fixtures_keep_all_channels(self):
        date=dt.date(2026,11,7)
        row={'time':'12:40','sport':'Rugby','competition':'Nations Championship',
             'title':'Italia-Sudafrica','channels':'Sky Sport Arena'}
        other={'date':date,'time':'12:40','sport':'Rugby','competition':'Rugby',
               'title':'Italy Rugby-South Africa Rugby','channels':'Sky Sport Uno, TV8',
               'sources':['TheSportsDB']}
        merged=merge_events([(date,[row])],[other])
        self.assertEqual(len(merged[0][1]),1)
        self.assertEqual(merged[0][1][0]['sources'],['Virgilio','TheSportsDB'])
        self.assertEqual(merged[0][1][0]['channels'],'Sky Sport Arena, Sky Sport Uno, TV8')

    def test_gender_and_time_remain_distinct(self):
        date=dt.date(2026,11,7)
        row={'time':'15:00','sport':'Calcio','competition':'Serie A','title':'Milan-Inter','channels':'Sky'}
        woman={**row,'date':date,'sport':'Calcio femminile','title':'AC Milan Women-Inter Milano Women','sources':['TheSportsDB']}
        replay={**row,'date':date,'time':'18:00','sources':['TheSportsDB']}
        self.assertEqual(len(merge_events([(date,[row])],[woman,replay])[0][1]),3)
        original={**row,'sport':'Calcio femminile'}
        self.assertEqual(len(merge_events([(date,[original])],[woman])[0][1]),1)


if __name__=='__main__':
    unittest.main()
