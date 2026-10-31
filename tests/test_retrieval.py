"""Lock down the legacy ranking before collecting an evaluation baseline."""
import sqlite3
import pytest
from backend.associations import rank_candidates,enqueue_candidates
from backend.store import initialize,connect
from legacy_retrieval import enqueue_candidates as legacy_enqueue


@pytest.mark.parametrize('people',[False,True])
def test_readonly_recall_matches_frozen_algorithm(tmp_path,monkeypatch,people):
    path=tmp_path/'memory.sqlite3';initialize(path)
    with connect(path) as db:
        db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
        for mid,text,instant,role in [(1,'合成咖啡人物','2026-01-01T00:00:00+00:00','peer'),(2,'咖啡人物','2026-02-01T00:00:00+00:00','peer'),(3,'不是咖啡','2026-02-01T00:01:00+00:00','self'),(4,'咖啡','2026-02-01T00:02:00+00:00','peer')]:
            db.execute("INSERT INTO message(id,conversation_id,adapter,source_id,partition,sender_role,text,event_utc,type,revision) VALUES(?,'fixture','test',?,'test',?,?,?,'text',1)",(mid,str(mid),role,text,instant))
        for mid in range(1,21):
            db.execute("INSERT INTO memory(id,summary,subject,polarity,assertion,temporal_kind,fingerprint,status) VALUES(?,?,'peer','positive','direct','fact',?,?)",(mid,'咖啡' if mid%2 else '人物',str(mid),'incorrect' if mid==20 else 'active'))
            if mid!=19:
                db.execute('INSERT INTO memory_evidence VALUES(?,?,?,?,?)',(mid,2 if mid==18 else 1,2 if mid==17 else 1,0,2))
    if people:
        def mentions(db,sources,cutoff,**kwargs):
            return [{'person_token':'fixture'}] if any('人物' in text for text,_ in sources) else []
        monkeypatch.setattr('backend.associations.mention_candidates',mentions)
        monkeypatch.setattr('legacy_retrieval.mention_candidates',mentions)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');sql=[];db.set_trace_callback(sql.append)
        actual={trigger:rank_candidates(db,trigger) for trigger in (2,3,4)}
        assert db.total_changes==0
        assert all(s.startswith('SELECT') for s in sql)
    with connect(path) as db:
        for trigger in (2,3,4):
            legacy=legacy_enqueue(db,trigger)
            assert actual[trigger]==(legacy or [])
            expected=[dict(r) for r in db.execute('SELECT trigger_revision,memory_id,memory_version FROM association WHERE trigger_id=? ORDER BY id',(trigger,))]
            assert [{k:v for k,v in row.items() if k!='score'} for row in actual[trigger]]==expected
            assert all(r['score']>0 for r in actual[trigger])
            db.execute('DELETE FROM association')
            enqueue_candidates(db,trigger)
            assert [dict(r) for r in db.execute('SELECT trigger_revision,memory_id,memory_version FROM association WHERE trigger_id=? ORDER BY id',(trigger,))]==expected
            db.execute('DELETE FROM association')


@pytest.mark.parametrize('people',[False,True])
@pytest.mark.parametrize('dense',[False,True])
@pytest.mark.parametrize('evidence_count',[1,20])
def test_scale_queries_constant_and_exact_results(tmp_path,monkeypatch,people,dense,evidence_count):
    from backend.associations import prefetch_evidence
    counts=[]
    for size in (1000,5000):
        path=tmp_path/f'{size}.sqlite3';initialize(path)
        with connect(path) as db:
            db.execute("INSERT INTO conversation VALUES('fixture','fixture','fixture')")
            for mid,text,instant in [(1,'合成咖啡小陈','2026-01-01T00:00:00+00:00'),(2,'合成咖啡'+('小陈' if people else ''),'2026-02-01T00:00:00+00:00')]:
                db.execute("INSERT INTO message(id,conversation_id,adapter,source_id,partition,sender_role,text,event_utc,type,revision) VALUES(?,'fixture','test',?,'test','peer',?,?,'text',1)",(mid,str(mid),text,instant))
            db.execute("INSERT INTO person VALUES(1,'fixture','[PERSON_fixture]')")
            db.execute("INSERT INTO person_property(id,person_id,kind,value,confirmation_note) VALUES(1,1,'name','小陈','fixture')")
            db.execute('INSERT INTO property_evidence VALUES(1,1,1,0,2)')
            db.executemany("INSERT INTO memory(id,summary,subject,polarity,assertion,temporal_kind,fingerprint) VALUES(?,?,'peer','positive','direct','fact',?)",((i,'咖啡' if dense or i==1 else '周末运动',str(i)) for i in range(1,size+1)))
            db.execute("UPDATE message SET text=text||'abcdefghijklmnopqrst' WHERE id=1")
            db.executemany('INSERT INTO memory_evidence VALUES(?,1,1,?,?)',((i,span,span+1) for i in range(1,size+1) for span in range(evidence_count)))
            fetched=[]
            def observed(db,ids,*,with_text=False):
                rows=dict(prefetch_evidence(db,ids,with_text=with_text))
                fetched.append((len(ids),sum(len(v) for v in rows.values()),with_text))
                assert all(('text' in r.keys())==people for group in rows.values() for r in group)
                return iter(rows.items())
            monkeypatch.setattr('backend.associations.prefetch_evidence',observed)
            sql=[];db.set_trace_callback(sql.append)
            result=rank_candidates(db,2)
            counts.append(len(sql));db.set_trace_callback(None)
            assert result==legacy_enqueue(db,2)
            selected=size if people or dense else 1
            assert fetched==[(selected,selected*evidence_count,people)]
    assert counts==[6,6]


def test_memory_word_cache_tracks_version_and_content():
    from backend.associations import memory_terms
    memory_terms.cache_clear()
    assert memory_terms(1,1,'咖啡')=={'咖啡'}
    memory_terms(1,1,'咖啡')
    assert memory_terms.cache_info().hits==1
    assert memory_terms(1,2,'周末')=={'周末'}
    assert memory_terms(1,1,'运动')=={'运动'}  # Same ID/version in a different fixture database.
