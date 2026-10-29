"""Local synthetic-provider orchestration; real provider transport is not enabled."""
import time

from fastapi import HTTPException

from .model_contract import issue_request, commit_response
from .store import connect


def recover_expired(path):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE model_request SET state='failed' WHERE state='issued' AND id IN (SELECT owner FROM job WHERE state='extracting' AND lease_until<=?)",(time.time(),))
        db.execute("UPDATE job SET state='failed',error='extraction_lease_expired',owner=NULL,lease_until=NULL WHERE state='extracting' AND lease_until<=?",(time.time(),))


def extract_next(path, synthetic_provider):
    """The injected callable accepts a request and returns JSON; it must be bounded.

    This entry point is for local fixture providers only. It is not exposed as an
    HTTP endpoint and does not authorize or configure external transmission.
    """
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        # Expired extraction is failed, not silently retried (a real API might charge).
        db.execute("UPDATE job SET state='failed',error='extraction_lease_expired',owner=NULL,lease_until=NULL WHERE state='extracting' AND lease_until<=?", (time.time(),))
        row = db.execute("SELECT j.id FROM job j JOIN initialization i ON i.id=j.initialization_id WHERE j.state='prepared' AND i.state='active' ORDER BY j.id LIMIT 1").fetchone()
    if row is None:
        return {'processed':False}
    try:
        request = issue_request(path, row['id'], claim=True)
    except HTTPException:
        return {'processed':False}
    try:
        response = synthetic_provider(request)
        result = commit_response(path, request['request_id'], response)
        return {'processed':True, 'state':'validated_synthetic', **result}
    except TimeoutError:
        error = 'model_timeout'
    except HTTPException:
        error = 'model_result_rejected'
    except Exception:
        error = 'model_provider_failed'
    with connect(path) as db:
        # Do not overwrite cancellation, source invalidation or a newer owner.
        db.execute("UPDATE job SET state='failed',error=?,owner=NULL,lease_until=NULL WHERE id=? AND state='extracting' AND owner=?", (error,row['id'],request['request_id']))
        db.execute("UPDATE model_request SET state='failed' WHERE id=? AND state='issued'", (request['request_id'],))
    return {'processed':True, 'state':'failed', 'error':error}
