"""Content-addressed assets, immutable task runs and separately recorded corrections."""
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from . import __version__
from .tool_support import publish
from .audio import digest, probe, decode, wav_tags


def dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('MUSIC_HOME', '~/.cache/music-understanding/data')).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'catalog.sqlite', timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        migrations = sorted((Path(__file__).parent / 'migrations').glob('*.sql'))
        if version > len(migrations):
            raise ValueError('数据库版本高于本工具；请升级工具')
        for number, path in enumerate(migrations, 1):
            if version < number:
                self.db.executescript('BEGIN IMMEDIATE;\n' + path.read_text() +
                                     f'\nPRAGMA user_version={number};\nCOMMIT;')


    def close(self):
        self.db.close()

    def asset_dir(self, asset_id):
        p = self.root / 'assets' / asset_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def register(self, source, decode_audio=True):
        source = Path(source).expanduser().resolve(strict=True)
        if self.root == source.parent or source.parent in self.root.parents:
            raise ValueError('缓存目录不能放在源文件所在目录内')
        sha = digest(source)
        row = self.db.execute('SELECT metadata FROM assets WHERE id=?', (sha,)).fetchone()
        meta = json.loads(row[0]) if row else dict(probe(source), sha256=sha)
        meta['embedded'] = wav_tags(source)
        canonical = self.asset_dir(sha) / 'source.wav'
        if decode_audio and (not canonical.exists() or 'decoded' not in meta):
            with tempfile.TemporaryDirectory(dir=canonical.parent) as tmp:
                staged = Path(tmp) / 'source.wav'
                info = decode(source, staged)
                if digest(source) != sha:
                    raise ValueError('读取期间源文件改变，请重试')
                try:
                    publish(staged, canonical)
                except FileExistsError:
                    pass
                meta['decoded'] = info
        with self.db:
            self.db.execute('INSERT INTO assets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata',
                            (sha, dumps(meta)))
            self.db.execute('INSERT INTO locations VALUES(?,?) ON CONFLICT(path) DO UPDATE SET asset_id=excluded.asset_id',
                            (str(source), sha))
        return self.get(sha)

    def resolve(self, ref):
        if Path(ref).expanduser().is_file():
            return self.register(ref)['id']
        if not ref or len(ref) > 64 or any(c not in '0123456789abcdefABCDEF' for c in ref):
            raise ValueError('无效素材 ID')
        rows = self.db.execute('SELECT id FROM assets WHERE id LIKE ?', (ref + '%',)).fetchall()
        if len(rows) != 1:
            raise ValueError('素材 ID 不存在或前缀不唯一')
        return rows[0][0]

    def get(self, asset_id):
        r = self.db.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
        if not r:
            raise ValueError('素材不存在')
        paths = [x[0] for x in self.db.execute('SELECT path FROM locations WHERE asset_id=? ORDER BY path', (asset_id,))]
        return {'id': asset_id, 'metadata': json.loads(r[0]), 'paths': paths,
                'audio': str(self.asset_dir(asset_id) / 'source.wav')}

    def ensure_audio(self, asset_id):
        asset = self.get(asset_id)
        if not Path(asset['audio']).exists() or 'decoded' not in asset['metadata']:
            for p in asset['paths']:
                if Path(p).is_file() and digest(p) == asset_id:
                    return self.register(p)
            raise ValueError('原文件已移动或改变，无法解码该素材')
        return asset

    def signature(self, asset_id, task, params):
        return hashlib.sha256(dumps([__version__, asset_id, task, params]).encode()).hexdigest()

    def cached(self, key):
        row = self.db.execute('SELECT * FROM runs WHERE id=? AND status="complete"', (key,)).fetchone()
        if not row:
            return None
        out = dict(row); out['summary'] = json.loads(out['summary'])
        def present(value):
            if isinstance(value, dict):
                if 'path' in value and 'shape' in value and not Path(value['path']).is_file(): return False
                return all(present(v) for v in value.values())
            if isinstance(value, list): return all(present(v) for v in value)
            return True
        return out if present(out['summary']) else None

    def save_run(self, asset_id, task, key, status, summary, events):
        for i, e in enumerate(events):
            e['id'] = f'{key}:{i}'
            e['run_id'] = key
            e['asset_id'] = asset_id
        with self.db:
            self.db.execute('DELETE FROM events WHERE run_id=?', (key,))
            self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?)',
                            (key, asset_id, task, key, status, dumps(summary), time.time()))
            for e in events:
                self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                                (e['id'], key, asset_id, e['type'], e.get('part', 'mix'), e['t0'], e['t1'], dumps(e)))
        return {'run_id': key, 'task': task, 'status': status, 'summary': summary,
                'event_count': len(events)}

    def runs(self, asset_id):
        return [dict(r, summary=json.loads(r["summary"])) for r in self.db.execute(
            "SELECT * FROM runs WHERE asset_id=? ORDER BY created DESC", (asset_id,))]

    def latest(self, asset_id):
        rows = self.db.execute('SELECT * FROM runs WHERE asset_id=? ORDER BY created DESC', (asset_id,))
        result = {}
        for row in rows:
            if row['task'] not in result:
                result[row['task']] = dict(row, summary=json.loads(row['summary']))
        return result

    def events(self, asset_id, start=0, end=float('inf'), part=None, kind=None, limit=100, offset=0, task=None):
        ids = [v['id'] for k,v in self.latest(asset_id).items() if v['status'] == 'complete' and (task is None or k == task)]
        if not ids:
            return []
        where = ['run_id IN (' + ','.join('?' for _ in ids) + ')', 't1>=?', 't0<?']
        values = ids + [start, end]
        if task is None:
            where.append("(type != 'note' OR run_id NOT IN (SELECT id FROM runs WHERE task='pitch') OR NOT EXISTS (SELECT 1 FROM events n JOIN runs r ON r.id=n.run_id WHERE n.asset_id=events.asset_id AND n.part=events.part AND n.type='note' AND r.task='notes' AND n.run_id IN (" + ','.join('?' for _ in ids) + ")))")
            values.extend(ids)
        if part:
            where.append('part=?'); values.append(part)
        if kind:
            where.append('type=?'); values.append(kind)
        sql = 'SELECT payload FROM events WHERE ' + ' AND '.join(where) + ' ORDER BY t0,id LIMIT ? OFFSET ?'
        return [json.loads(x[0]) for x in self.db.execute(sql, values + [limit, offset])]

    def evidence(self, event_id):
        r = self.db.execute('SELECT payload FROM events WHERE id=?', (event_id,)).fetchone()
        if not r:
            raise ValueError('事件 ID 不存在')
        return json.loads(r[0])

    def correct(self, asset_id, field, value, reason):
        if field != 'root_midi' or not isinstance(value, (float, int)) or not 0 <= value <= 127:
            raise ValueError('当前可修正字段为 root_midi，范围 0–127')
        with self.db:
            c = self.db.execute('INSERT INTO corrections(asset_id,field,value,reason,created) VALUES(?,?,?,?,?)',
                                (asset_id, field, dumps(value), reason, time.time()))
        return {'correction_id': c.lastrowid, 'field': field, 'value': value,
                'scope': 'manual overlay; algorithm observations retained'}

    def corrections(self, asset_id):
        return [dict(r, value=json.loads(r['value'])) for r in self.db.execute(
            'SELECT * FROM corrections WHERE asset_id=? ORDER BY id', (asset_id,))]
