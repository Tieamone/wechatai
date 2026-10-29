"""Fixed-account reader: no package constructor, account fallback, disk key cache or sender."""
import hashlib
import json
import os
from pathlib import Path

from backend.wechat_snapshot import signature, snapshot
from tools.wechat_source import ROOT, load_reader


class ContactReader:
    def __init__(self, account_dir, pid):
        self.module = load_reader()
        self.account_dir = Path(account_dir).resolve()
        self.pid = pid
        self.storage = self.account_dir/'db_storage'
        self.temp_root = ROOT/'data/collector-tmp'
        self.keys = {}
        self.cache = {}
        # No WeChatDB.__init__: that implementation tries other accounts and saves keys.
        self.reader = object.__new__(self.module.WeChatDB)
        self.reader._db_files = []
        self.peer = None
        self.self_id = self.account_dir.name.rsplit('_', 1)[0]

    def paths(self):
        files = [self.storage/'contact/contact.db'] + sorted((self.storage/'message').glob('message_[0-9]*.db'))
        if not files[0].is_file() or len(files) < 2:
            raise ValueError('Required contact/message files missing')
        return files

    def unlock(self):
        files = self.paths()
        self.reader._db_files = [(str(p.relative_to(self.storage)), str(p), p.stat().st_size) for p in files]
        self.keys = self.reader._extract_keys_pid(self.pid, set())
        if len(self.keys) != len(files):
            raise ValueError(f'Only {len(self.keys)}/{len(files)} required databases validated; stopped')

    def open(self, path):
        rel = str(path.relative_to(self.storage))
        if rel not in self.keys:
            raise ValueError('New shard needs restart and key validation')
        return snapshot(path, self.keys[rel], self.module._decrypt_page, self.temp_root)

    def resolve(self):
        with self.open(self.paths()[0]) as db:
            binding_path = ROOT/'data/collector/binding.json'
            binding = json.loads(binding_path.read_text(encoding='utf-8')) if binding_path.exists() else {}
            if binding and Path(binding['account_dir']).resolve() != self.account_dir:
                raise ValueError('Stored account binding differs; verify local configuration')
            if binding.get('peer'):
                matches = db.execute('SELECT username FROM contact WHERE username=?', (binding['peer'],)).fetchall()
            else:
                name = os.environ.get('WECHAT_CONTACT_NAME', '').strip()
                if not name:
                    raise ValueError('Set WECHAT_CONTACT_NAME locally before first binding')
                matches = db.execute("SELECT username FROM contact WHERE remark=? OR (COALESCE(remark,'')='' AND nick_name=?)", (name, name)).fetchall()
            if len(matches) != 1 or not matches[0][0] or matches[0][0].endswith('@chatroom'):
                raise ValueError('指定联系人 did not resolve to exactly one individual contact')
            self.peer = matches[0][0]
        return self.peer

    def messages(self):
        if not self.peer:
            raise ValueError('Contact not bound')
        table = 'Msg_' + hashlib.md5(self.peer.encode()).hexdigest()
        result = []
        for path in self.paths()[1:]:
            rel = str(path.relative_to(self.storage))
            stamp = signature(path)
            cached = self.cache.get(rel)
            if cached and cached[0] == stamp:
                result.extend(cached[1])
                continue
            with self.open(path) as db:
                if not db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone():
                    rows = []
                else:
                    # Join only senders of the authorized conversation, not the full contact list.
                    rows = db.execute(f'''SELECT m.local_id,m.local_type,m.server_id,m.create_time,
                        m.sort_seq,m.message_content,m.compress_content,n.user_name AS sender
                        FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id
                        ORDER BY m.sort_seq,m.local_id''').fetchall()
                output = []
                for row in rows:
                    kind = int(row['local_type']) & 0xffffffff
                    role = 'self' if row['sender'] == self.self_id else 'peer' if row['sender'] == self.peer else 'system'
                    if kind == 1 and role == 'system':
                        raise ValueError('Text sender cannot be resolved; refusing guessed attribution')
                    text = row['message_content']
                    if kind == 1 and isinstance(text, bytes):
                        text = self.module.WeChatDB._friendly_content(text, '文本')
                    if kind == 1 and (not isinstance(text, str) or text == '[文本]'):
                        raise ValueError('Text decode failed')
                    output.append({'id':str(row['local_id']), 'partition':rel, 'role':role,
                        'time':row['create_time'], 'seq':row['sort_seq'],
                        'type':{1:'text',3:'image',34:'voice',43:'video',10000:'system'}.get(kind,'unsupported'),
                        'text':text if kind == 1 else ''})
            self.cache[rel] = (stamp, output)
            result.extend(output)
        return sorted(result, key=lambda r:(r['seq'], r['partition'], int(r['id'])))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--account', required=True)
    args = parser.parse_args()
    from tools.collect_contact import process_birth
    born = process_birth(args.pid)
    reader = ContactReader(args.account, args.pid)
    print('Validating selected process/database keys (never saved)...', flush=True)
    reader.unlock()
    print('Required database keys validated; resolving exact contact...', flush=True)
    reader.resolve()
    rows = reader.messages()
    if process_birth(args.pid) != born:
        raise ValueError('Source process changed during initial read')
    directory = ROOT/'data/collector'
    directory.mkdir(parents=True, exist_ok=True)
    # Only the selected conversation is persisted. No full shard or key is retained.
    (directory/'initial.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    (directory/'binding.json').write_text(json.dumps({'account_dir':str(reader.account_dir), 'peer':reader.peer,
        'self_id':reader.self_id, 'pid':args.pid, 'process_birth':born}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'messages':len(rows), 'text':sum(r['type']=='text' for r in rows),
        'self':sum(r['role']=='self' for r in rows), 'peer':sum(r['role']=='peer' for r in rows),
        'temporary_files':len(list(reader.temp_root.rglob('*')))}, ensure_ascii=False), flush=True)
