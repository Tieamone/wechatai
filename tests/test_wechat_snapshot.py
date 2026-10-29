import sqlite3
import struct

import pytest

from backend.wechat_snapshot import checksum, committed_frames, snapshot, PAGE


def test_real_sqlite_wal_snapshot_and_cleanup(tmp_path):
    path = tmp_path/'source.db'
    db = sqlite3.connect(path)
    db.execute('PRAGMA page_size=4096')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE messages (text TEXT)')
    db.commit()
    db.execute("INSERT INTO messages VALUES('committed')")
    db.commit()
    db.execute("INSERT INTO messages VALUES('not committed')")
    wal_path = tmp_path/'source.db-wal'
    original = path.read_bytes(), wal_path.read_bytes()
    with snapshot(path, b'', lambda key, page, number:page, tmp_path/'temp') as reader:
        assert [r[0] for r in reader.execute('SELECT text FROM messages')] == ['committed']
    assert not list((tmp_path/'temp').iterdir())
    assert original == (path.read_bytes(), wal_path.read_bytes())
    db.close()


def test_uncommitted_tail_not_applied_and_corruption_rejected(tmp_path):
    path = tmp_path/'source.db'
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE test (id INTEGER)')
    db.commit()
    wal = bytearray((tmp_path/'source.db-wal').read_bytes())
    frames, size = committed_frames(wal)
    assert frames and size
    # A valid frame after the last commit must not become visible yet.
    endian = '<' if struct.unpack('>I',wal[:4])[0] == 0x377f0682 else '>'
    tail_header = struct.pack('>II',1,0) + wal[16:24]
    tail_page = b'z' * PAGE
    prior = struct.unpack('>II',wal[-(PAGE+24)+16:-(PAGE+24)+24])
    check = checksum(tail_page,endian,checksum(tail_header[:8],endian,prior))
    tail = tail_header + struct.pack('>II',*check) + tail_page
    assert committed_frames(wal+tail) == (frames,size)
    wal[32 + 24 + 100] ^= 1
    with pytest.raises(ValueError, match='checksum'):
        committed_frames(wal)
    db.close()
