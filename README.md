# Virgilio Sport guide with Dispatcharr channel names

[Daily guide](https://jusseppe88.github.io/virgilio-sport-rss/home.html) ·
[Tables](https://jusseppe88.github.io/virgilio-sport-rss/tables.html) ·
[RSS](https://jusseppe88.github.io/virgilio-sport-rss/rss.xml)

Each event lists the exact channel names and numbers from the checked Dispatcharr
lineup. All identified matches are kept, including international editions. Channel names
are plain text; no playback or DaddyLive links are generated.

The schedule combines Virgilio with the current-day listings on
[TheSportsDB Italy](https://www.thesportsdb.com/browse_tv/?c=italy).
TheSportsDB's UTC broadcast times are converted to Europe/Rome, including
daylight saving and midnight date changes. Full names, sports and competitions
come from each event's structured metadata rather than truncated card labels.
Matching fixtures at the same local time appear once with all listed
broadcasters; their source labels identify both providers. TheSportsDB's page
only supplies today's UTC listings; future-day coverage remains Virgilio's.
Unavailable, empty or stale TheSportsDB data fails the build visibly instead of
silently publishing a schedule with that source omitted.

`home.html` always starts on today's Rome date, including reloads and returns
from the browser's back/forward cache. Selecting another day works during the
visit but the selected date is not restored on a later page load. Theme
preferences are still remembered.

* **Guida** identifies a specific broadcaster named in either source schedule.
* **EPG** identifies a matching event in a mapped public XMLTV feed, in the same
  Rome-time window. This is guide evidence, not a playback or stream-health test.
* Generic broadcasters such as Sky, DAZN and Eurosport are not expanded to their
  numbered channels without an event match. Unresolved labels stay visible.

The existing daily 05:00 UTC GitHub workflow downloads the public guides. It also
rebuilds when the matching code, lineup, home page or workflow changes on main,
so code updates are published without waiting until the following day. It does
not connect to Dispatcharr, need HSH/Mini online, or use any home-server secrets.
The workflow schedule is unchanged. `epg_status.json` records feed failures and
per-channel programme counts. Missing feeds and unrecognised titles mean the
channel list may be incomplete; multilingual aliases are deliberately explicit,
and generic sessions without enough identity are not guessed.

## Lineup maintenance

`dispatcharr_channels.json` is a checked snapshot, **not a live connection** to
Dispatcharr. Update it when a channel is renamed, renumbered or remapped. It
contains only channel names, numbers, mapped XMLTV IDs and public guide URLs.
Never add stream URLs, private hosts, credentials, account IDs or tokenised URLs.
Update `lineup_checked` after checking against Dispatcharr. The page displays it.
The snapshot covers sports channels and the principal national channels used by
the sports guide; it is not an export of every entertainment channel.

Add known team-name translations to `TEAM_ALIASES` in `dispatcharr_match.py`.
Youth/women's variants, explicit replays, different sports and multi-day
placeholder listings must not be treated as the same live event. Non-fixture
events require both the competition and the specific session to match.

## Validation and rollback

Install the Python packages from `.github/workflows/build.yml`, then run
`python -m unittest -v` and `python build_rss.py`. The build writes `index.html`,
`tables.html`, both RSS files, `epg_status.json` and `sportsdb_status.json`. Open `home.html` through an
HTTP server to check the date controls and channel columns.

To roll back, revert the channel-matching commit and run the existing Build RSS
workflow. Do not restore only generated HTML: the next build regenerates it.
