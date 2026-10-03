import json
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest
from pandoc import assistant


def test_context_excludes_files_and_includes_scores(tmp_path):
    (tmp_path/'config.json').write_text(json.dumps(dict(reference='/private/reference.sdf', receptor='/private/protein.pdbqt', center=[1,2,3], exhaustiveness=16)))
    (tmp_path/'results.json').write_text(json.dumps([dict(ligand='private compound', sdf='private.sdf', seed=2026, rank=1, score_kcal_mol=-7.2, reference_rmsd_A=1.4)]*60))
    context = assistant.context_snapshot({'center':[1,2,3], 'GROQ_API_KEY':'secret'}, tmp_path)
    encoded = json.dumps(context)
    assert 'private' not in encoded and 'secret' not in encoded
    assert context['included_result_rows']==50 and context['total_result_rows']==60
    assert context['results'][0]['score_kcal_mol']==-7.2


def test_sdk_request_and_session_persistence(monkeypatch, tmp_path):
    import groq
    calls=[]
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs['max_retries']==0 and kwargs['timeout']==60
            self.chat=SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Exhaustiveness controls search effort.'))])
    monkeypatch.setattr(groq,'Groq',FakeClient)
    monkeypatch.setattr(assistant,'setting',lambda st,name,default='': 'test-key' if name=='GROQ_API_KEY' else default)
    script="import streamlit as st\nfrom pandoc import assistant\nassistant.render(st, "+repr(str(tmp_path))+")"
    at=AppTest.from_string(script).run()
    at.text_area[0].set_value('What does docking exhaustiveness mean?')
    next(b for b in at.button if b.label=='Ask').click().run()
    assert not list(at.exception)
    assert len(calls)==1
    assert calls[0]['model']==assistant.MODEL
    assert len(calls[0]['messages'])==2
    at.run()
    assert len(calls)==1
    assert at.session_state['assistant_answer']['answer'].startswith('Exhaustiveness')


def test_missing_key_is_nonfatal(monkeypatch,tmp_path):
    monkeypatch.setattr(assistant,'setting',lambda *args:'')
    at=AppTest.from_string("import streamlit as st\nfrom pandoc import assistant\nassistant.render(st, "+repr(str(tmp_path))+")").run()
    at.text_area[0].set_value('Explain redocking')
    next(b for b in at.button if b.label=='Ask').click().run()
    assert not list(at.exception)
    assert list(at.warning)
