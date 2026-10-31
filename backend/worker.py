"""One worker for local preparation and explicitly enabled automatic model processing."""
import threading
import sqlite3

from .jobs import prepare_one
from .extraction import extract_next, recover_expired
from .associations import judge_next


class PreparationWorker:
    def __init__(self, path, synthetic_provider=None, synthetic_judge=None):
        self.path = path
        self.synthetic_provider = synthetic_provider
        self.synthetic_judge = synthetic_judge
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='memory-prepare', daemon=True)
        self.error = None
        self.start_lock = threading.Lock()

    def start(self):
        with self.start_lock:
            if self.thread.is_alive():
                return
            self.error = None
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, name='memory-prepare', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=15)

    def run(self):
        while not self.stop_event.is_set():
            try:
                recover_expired(self.path)
                result = prepare_one(self.path)
                if self.synthetic_provider is None and self.synthetic_judge is None:
                    from .budgets import recover_expired as recover_budget
                    from .batches import process_next as process_batch
                    recover_budget(self.path)
                    from .automatic import process_next
                    automatic = process_next(self.path,should_stop=self.stop_event.is_set)
                    result['processed'] = result['processed'] or automatic['processed']
                    batch=process_batch(self.path,should_stop=self.stop_event.is_set)
                    result['processed'] = result['processed'] or batch['processed']
                if self.synthetic_judge is not None:
                    judged = judge_next(self.path, self.synthetic_judge)
                    result['processed'] = result['processed'] or judged['processed']
                if self.synthetic_provider is not None:
                    extracted = extract_next(self.path, self.synthetic_provider)
                    result['processed'] = result['processed'] or extracted['processed']
            except sqlite3.OperationalError as error:
                if str(error) in {'database is locked', 'database table is locked'}:
                    self.error = 'database_busy_retrying'
                    self.stop_event.wait(1)
                    continue
                self.error = 'preparation_database_error'
                return
            except Exception:
                # No chat text, SQL parameters or exception payloads in diagnostics.
                self.error = 'preparation_worker_failed'
                return
            self.error = None
            self.stop_event.wait(.05 if result['processed'] else .5)

    def status(self):
        return {'running': self.thread.is_alive(), 'error': self.error, 'scope': 'preparation_and_opt_in_automatic',
                'synthetic_extraction': self.synthetic_provider is not None}
