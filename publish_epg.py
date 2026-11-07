"""Mini-only hourly publisher. Uses a repository-scoped SSH deploy key."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path('/srv/local-repos/virgilio-sport-rss')
STATE = Path.home() / '.local/state/dispatcharr-guide'
KEY = Path.home() / '.ssh/virgilio_sport_publish'
REMOTE = 'git@github.com:jusseppe88/virgilio-sport-rss.git'


def run(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=60, **kwargs)
    if result.returncode:
        # Git output contains only host/repository metadata (no credential URLs).
        raise RuntimeError(args[0] + ' failed: ' + result.stderr[-1800:])
    return result.stdout


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'export.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another export is active')
            return
        reader = Path(__file__).with_name('read_dispatcharr_epg.py').read_text(encoding='utf-8')
        raw = run(['docker', 'exec', '-i', 'dispatcharr', 'python', '-'], input=reader)
        data = json.loads(raw)
        # Validate using the locally installed, reviewed module, not fetched code.
        from epg_schedule import validate_snapshot
        validate_snapshot(data)
        if len(raw.encode()) > 12 * 1024 * 1024:
            raise ValueError('Snapshot exceeds publication size limit')
        target = STATE / 'dispatcharr_epg.json'
        target.with_suffix('.tmp').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
        target.with_suffix('.tmp').replace(target)
        if '--export-only' in sys.argv:
            print(json.dumps({'export_only': True, 'channels': len(data['channels']),
                              'programmes': sum(len(c['programmes']) for c in data['channels']), 'path': str(target)}))
            return
        if not KEY.is_file() or KEY.stat().st_mode & 0o077:
            raise ValueError('Dedicated deploy key missing or permissions too broad')
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',
               'GIT_SSH_COMMAND': f'ssh -i {KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes'}
        if not REPO.exists():
            run(['git', 'clone', '--single-branch', '--branch', 'main', REMOTE, str(REPO)], env=env)
        def git(*args):
            return run(['git', '-C', str(REPO), *args], env=env)
        if git('remote', 'get-url', 'origin').strip() != REMOTE:
            raise ValueError('Unexpected repository origin')
        if git('status', '--porcelain').strip():
            raise ValueError('Publisher checkout has local changes; refusing to overwrite')
        git('config', 'user.name', 'jusseppe88')
        git('config', 'user.email', '50551533+jusseppe88@users.noreply.github.com')
        git('fetch', 'origin', 'main')
        try:
            git('rebase', 'origin/main')
        except RuntimeError:
            subprocess.run(['git', '-C', str(REPO), 'rebase', '--abort'], capture_output=True, timeout=10)
            raise
        (REPO / 'dispatcharr_epg.json').write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
        git('add', '--', 'dispatcharr_epg.json')
        git('-c', 'user.name=jusseppe88', '-c', 'user.email=50551533+jusseppe88@users.noreply.github.com',
            'commit', '-m', 'Refresh imported Dispatcharr EPG snapshot')
        try:
            git('push', 'origin', 'HEAD:main')
        except RuntimeError:
            # The daily builder can commit rendered files between fetch/push.
            git('fetch', 'origin', 'main')
            try:
                git('rebase', 'origin/main')
            except RuntimeError:
                subprocess.run(['git', '-C', str(REPO), 'rebase', '--abort'], capture_output=True, timeout=10)
                raise
            git('push', 'origin', 'HEAD:main')
        (STATE / 'last-success.json').write_text(json.dumps({'at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'channels': len(data['channels']), 'head': git('rev-parse', 'HEAD').strip()}) + '\n', encoding='utf-8')
        print('Published metadata for', len(data['channels']), 'channels')


if __name__ == '__main__':
    main()
