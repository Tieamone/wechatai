"""Explicit local acceptance: stop/resume the bound collector, never sends messages."""
import time
from backend.runtime import ROOT, control, status


def wait_for(states, seconds=60):
    for _ in range(seconds):
        current = status(ROOT/'data', 'collector')
        if current['state'] in states:
            return current
        time.sleep(1)
    raise RuntimeError('Collector state transition timed out')


if __name__ == '__main__':
    data = ROOT/'data'
    print('before:', status(data, 'collector'), flush=True)
    try:
        control(data, 'collector', 'stop')
        print('stopped:', wait_for({'stopped','error'}), flush=True)
    finally:
        control(data, 'collector', 'start')
    print('resumed:', wait_for({'running','error'}), flush=True)
    # Wait for a completed collection cycle, not merely process startup.
    import json
    for _ in range(60):
        value = json.loads((data/'collector/status.json').read_text(encoding='utf-8'))
        if value['state'] == 'watching':
            print('verified cycle:', {k:value.get(k) for k in ('state','rows','counts')}, flush=True)
            break
        if value['state'] == 'stopped_error':
            raise RuntimeError('Collector failed to resume')
        time.sleep(1)
    else:
        raise RuntimeError('No successful collection cycle')
