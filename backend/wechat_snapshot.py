"""Read a stable encrypted SQLite snapshot; apply only checksum-valid committed WAL frames."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import struct
import tempfile

PAGE = 4096


def checksum(data, endian, state=(0, 0)):
    a, b = state
    for x, y in struct.iter_unpack(endian + 'II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


def committed_frames(wal):
    if not wal:
        return [], None
    if len(wal) < 32:
        raise ValueError('Incomplete WAL header')
    magic, version, size = struct.unpack('>III', wal[:12])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or size != PAGE:
        raise ValueError('Unsupported WAL format')
    endian = '<' if magic == 0x377f0682 else '>'
    state = checksum(wal[:24], endian)
    if state != struct.unpack('>II', wal[24:32]):
        raise ValueError('WAL header checksum mismatch')
    frames, last_commit, pages = [], 0, None
    for offset in range(32, len(wal) - PAGE - 23, PAGE + 24):
        header, data = wal[offset:offset+24], wal[offset+24:offset+24+PAGE]
        if header[8:16] != wal[16:24]:
            break  # pre-reset tail, not part of current WAL
        state = checksum(data, endian, checksum(header[:8], endian, state))
        if state != struct.unpack('>II', header[16:24]):
            raise ValueError('WAL frame checksum mismatch; retry snapshot')
        number, commit = struct.unpack('>II', header[:8])
        if not number:
            raise ValueError('Invalid WAL page')
        frames.append((number, data))
        if commit:
            last_commit, pages = len(frames), commit
    return frames[:last_commit], pages


def signature(path):
    def stamp(p):
        try:
            s = p.stat()
            return s.st_size, s.st_mtime_ns
        except FileNotFoundError:
            return None
    return stamp(path), stamp(Path(str(path) + '-wal'))


@contextmanager
def snapshot(path, key, decrypt_page, temp_root):
    path = Path(path)
    before = signature(path)
    encrypted = path.read_bytes()
    wal_path = Path(str(path) + '-wal')
    wal = wal_path.read_bytes() if wal_path.exists() else b''
    if before != signature(path):
        raise ValueError('Source changed during snapshot')
    if len(encrypted) % PAGE:
        raise ValueError('Incomplete database page')
    frames, pages = committed_frames(wal)
    temp_root.mkdir(parents=True, exist_ok=True)
    # Whole-shard plaintext exists only inside this context and is removed on exit.
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=temp_root) as directory:
        output = Path(directory)/'read.sqlite3'
        with output.open('w+b') as stream:
            for offset in range(0, len(encrypted), PAGE):
                stream.write(decrypt_page(key, encrypted[offset:offset+PAGE], offset//PAGE+1))
            for number, data in frames:
                stream.seek((number-1)*PAGE)
                stream.write(decrypt_page(key, data, number))
            if pages is not None:
                stream.truncate(pages*PAGE)
                stream.seek(28)
                stream.write(struct.pack('>I', pages))
            # Isolated checkpointed copy; no source SHM/WAL is opened by SQLite.
            stream.seek(18)
            stream.write(b'\x01\x01')
        db = sqlite3.connect(output.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            if [r[0] for r in db.execute('PRAGMA quick_check')] != ['ok']:
                raise ValueError('Snapshot failed SQLite integrity check')
            yield db
        finally:
            db.close()
