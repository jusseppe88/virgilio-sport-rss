# Sports guide from Dispatcharr imported EPG

[Daily guide](https://jusseppe88.github.io/virgilio-sport-rss/home.html) · [RSS](https://jusseppe88.github.io/virgilio-sport-rss/rss.xml)

The main schedule is built from programme records actually imported in Dispatcharr.
Channels and numbers come from the same live database export. No playback URLs,
source-feed URLs, credentials or server access are published. The guide does not
certify stream health. Replays are labelled; news, studio/talk shows, highlights,
generic channel placeholders and programme windows longer than six hours are
excluded. A listing needs an identified sport and fixture/session evidence;
merely appearing on a sports channel is insufficient.

Fixture identities normalise known multilingual team aliases, league prefixes
and club abbreviations. Matching broadcasts within fifteen minutes are grouped
with every channel retained and the earliest coverage time displayed. The time
window is anchored rather than chained. Different sports, age/gender qualifiers,
repeat flags and later broadcasts stay separate. Specific sport names (for
example Calcio, Basket or Tennis) are shown; the redundant EPG row badge is gone.
Virgilio and TheSportsDB Italy provide supplementary fixture identities and gap
checks. A cross-language fixture can combine channels only when matching records
exist in the imported EPG. An external broadcaster label never supplies a channel
by itself. Generic Sky, DAZN, Sky Go and Eurosport labels remain unresolved.

The page separates **Channels to add**, **Existing channels with missing/generic
EPG**, and **External events not found in imported EPG**. The latter is not proof
that a channel is missing. The snapshot timestamp is visible; opening the home
page warns when it is older than three hours. The home page always opens on the
current Europe/Rome date. Date navigation and remembered theme still work.

## Refresh paths

The Mini user timer dispatcharr-guide-export.timer runs hourly at :07 except
01:00 Europe/Rome, avoiding the protected backup window. It runs the pinned local
publish_epg.py and read_dispatcharr_epg.py from
/srv/docker/dispatcharr/guide-export. A read-only database transaction exports
seven local calendar days into dispatcharr_epg.json, using a fixed field allowlist.
The publisher pushes only that file through a dedicated repository-only deploy
key at /home/mini/.ssh/virgilio_sport_publish (mode 600). Its checkout is
/srv/local-repos/virgilio-sport-rss; state is under
/home/mini/.local/state/dispatcharr-guide. Dirty checkouts stop the publisher;
rejected pushes get one bounded fetch/rebase retry, never a force push.

The snapshot commit triggers publish-epg.yml, a lightweight standard-library
renderer and Pages deploy in one job. The existing daily 05:00 UTC/manual/source
build refreshes the two external sources and supplementary_events.json, then
renders the same imported snapshot. Both workflows share deployment concurrency.
Generated-output commits do not trigger another run. The Mini needs no inbound
public endpoint. New lineup names, numbers and mappings flow through the next
export, without editing a static channel map.

## Tests and rollback

Run python -m unittest -v in the full build environment. The lightweight job runs
python -m unittest test_epg_schedule -v and python epg_schedule.py. Host export
verification uses publish_epg.py --export-only, which never accesses GitHub.

Stop/disable the Mini user timer and revoke its deploy key to stop publishing.
Revert the EPG-first website/workflow changes and rebuild to restore the prior
schedule. Preserve the previous site commits b39206c and bddd99c as the rollback
baseline. Private SSH keys must never be copied into this repository.
