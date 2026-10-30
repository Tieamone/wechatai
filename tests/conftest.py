"""Historical fixture suites isolate wall-clock policy; test_p0 exercises it live.

Older suites intentionally use fixed dated conversations to test evidence/identity.
Do not make their outcome depend on the day or local hour pytest runs.
"""
import pytest


@pytest.fixture(autouse=True)
def historical_fixture_policy(request, monkeypatch):
    if request.path.name.startswith(('test_p0','test_p1')):
        return
    monkeypatch.setattr('backend.policy.age_reason', lambda *a, **k: None)
    monkeypatch.setattr('backend.policy.quiet', lambda *a, **k: False)
