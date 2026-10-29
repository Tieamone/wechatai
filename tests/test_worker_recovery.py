import sqlite3
import threading
import time

from backend.worker import PreparationWorker


def test_busy_database_retries_without_losing_worker(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.worker.recover_expired',lambda path:None)
    processed = threading.Event()
    calls = []
    def prepare(path):
        calls.append(path)
        if len(calls)==1:
            raise sqlite3.OperationalError('database is locked')
        processed.set()
        return {'processed':False}
    monkeypatch.setattr('backend.worker.prepare_one',prepare)
    worker=PreparationWorker(tmp_path/'test.sqlite3')
    worker.start()
    try:
        assert processed.wait(4)
        for _ in range(100):
            if worker.error is None:
                break
            time.sleep(.01)
        assert worker.status()['running']
        assert worker.error is None
        thread=worker.thread
        worker.start()
        assert worker.thread is thread
    finally:
        worker.stop()


def test_failed_worker_can_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.worker.recover_expired',lambda path:None)
    def fail(path):
        raise ValueError('private data must not be logged')
    monkeypatch.setattr('backend.worker.prepare_one',fail)
    worker=PreparationWorker(tmp_path/'test.sqlite3')
    worker.start()
    worker.thread.join(2)
    assert not worker.status()['running']
    assert worker.error=='preparation_worker_failed'
    monkeypatch.setattr('backend.worker.prepare_one',lambda path:{'processed':False})
    worker.start()
    try:
        assert worker.status()['running']
        assert worker.error is None
    finally:
        worker.stop()
