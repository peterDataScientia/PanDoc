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
    at.chat_input[0].set_value('What does docking exhaustiveness mean?').run()
    assert not list(at.exception)
    assert len(calls)==1
    assert calls[0]['model']==assistant.MODEL
    assert len(calls[0]['messages'])==3
    assert 'box_units' in calls[0]['messages'][1]['content']
    at.run()
    assert len(calls)==1
    assert at.session_state['assistant_answer']['answer'].startswith('Exhaustiveness')


def test_missing_key_is_nonfatal(monkeypatch,tmp_path):
    monkeypatch.setattr(assistant,'setting',lambda *args:'')
    at=AppTest.from_string("import streamlit as st\nfrom pandoc import assistant\nassistant.render(st, "+repr(str(tmp_path))+")").run()
    assert at.chat_input[0].disabled
    assert not list(at.exception)
    assert list(at.info)


def test_empty_app_has_guide_and_simple_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(assistant, 'setting', lambda *args: '')
    at = AppTest.from_string("import streamlit as st\nfrom pandoc import assistant\nassistant.render(st, " + repr(str(tmp_path)) + ", panel=True)").run()
    assert not list(at.exception)
    assert len(at.chat_input) == 1 and not list(at.text_area)
    assert 'How do I start?' in [b.label for b in at.button]
    assert 'Ask' not in [b.label for b in at.button]
    assert assistant.context_snapshot({})['experiment_state']['complex_loaded'] is False


def test_followup_sends_conversation_history(monkeypatch):
    import groq
    captured = []
    class FakeClient:
        def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def create(self, **kwargs):
            captured.extend(kwargs['messages'])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Follow-up answer'))])
    monkeypatch.setattr(groq, 'Groq', FakeClient)
    assistant.ask('Why?', 'test', history=[dict(question='Explain RMSD', answer='RMSD measures pose deviation')])
    assert [m['role'] for m in captured] == ['system', 'user', 'assistant', 'user']
    assert captured[2]['content'] == 'RMSD measures pose deviation'
    assert 'Search PDB' in captured[0]['content']
    assert 'heavy' in captured[0]['content']


def test_diagnostic_sanitization_and_contextual_suggestions():
    context = assistant.context_snapshot({'workflow_stage':'2 · Prepare structures', 'assistant_diagnostic':'gsk_secret123 /private/user/preparation.log Invalid valence', 'preparation_record':{'pH_context':5,'curated_upload':'private.pdb','rationale':'private notes'}})
    assert 'gsk_secret123' not in json.dumps(context)
    assert '/private' not in json.dumps(context)
    assert 'private.pdb' not in json.dumps(context)
    assert 'Invalid valence' in context['diagnostic']
    assert assistant.suggestions(context)[0][0]=='Explain this error'
    assert context['preparation']['pH_context']==5


def test_compounds_do_not_merge_and_current_snapshot_follows_history(tmp_path, monkeypatch):
    import groq
    (tmp_path/'config.json').write_text('{}')
    (tmp_path/'results.json').write_text(json.dumps([{'ligand_id':'privateA','score_kcal_mol':-8}, {'ligand_id':'privateB','score_kcal_mol':-7}]))
    context = assistant.context_snapshot({}, tmp_path)
    assert [r['compound'] for r in context['results']]==['Compound 1','Compound 2']
    captured=[]
    class FakeClient:
        def __init__(self, **kwargs): self.chat=SimpleNamespace(completions=self)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def create(self, **kwargs):
            captured.extend(kwargs['messages'])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Answer'))])
    monkeypatch.setattr(groq,'Groq',FakeClient)
    assistant.ask('Explain this selection','test',context,history=[dict(question='Old selection',answer='Old answer')])
    assert 'current snapshot' in captured[-2]['content']
    assert captured[-3]['role']=='assistant'


def test_suggestion_sends_once_and_conversation_is_ordered(monkeypatch,tmp_path):
    monkeypatch.setattr(assistant,'setting',lambda st,name,default='':'test-key' if name=='GROQ_API_KEY' else default)
    calls=[]
    def fake_ask(question,*args,**kwargs):
        calls.append(question)
        return 'Specific explanation'
    monkeypatch.setattr(assistant,'ask',fake_ask)
    at=AppTest.from_string("import streamlit as st\nfrom pandoc import assistant\nst.session_state.pdb='fixture'\nst.session_state.workflow_stage='3 · Validate docking'\nassistant.render(st, "+repr(str(tmp_path))+")").run()
    next(b for b in at.button if b.label=='Explain my grid box').click().run()
    assert not list(at.exception)
    assert len(calls)==1 and 'current docking box' in calls[0]
    at.run()
    assert len(calls)==1
    assert [m.value for m in at.markdown]==[calls[0], 'Specific explanation']
    at.chat_input[0].set_value('Why?').run()
    assert len(at.session_state['assistant_history'])==2
    assert [m.value for m in at.markdown]==[calls[0], 'Specific explanation','Why?','Specific explanation']


def test_inspection_context_contains_evidence_and_current_choices():
    issue=dict(residue='A:687:CYS', problem='Invalid terminal oxygen geometry', severity='Error', action='Inspect')
    context=assistant.context_snapshot({'assistant_selected_issue':issue,
        'preparation_review':{'pH_context':5, 'templates':'A:17=HID'},
        'selection_record':{'chains':['A'],'overrides':{'A:10:ALA':'B'}},
        'structure_report':{'repair_changes':[{'atom':'OXT','change':'Added heavy atom'}]}})
    assert context['selected_structure_issue']==issue
    assert context['component_selection']['overrides']['A:10:ALA']=='B'
    assert context['preparation_under_review']['pH_context']==5
    assert context['total_preparation_changes']==1


def test_standalone_greeting_is_short_and_never_sends_session_to_groq(monkeypatch):
    import groq

    def should_not_call_groq(*args, **kwargs):
        raise AssertionError("A standalone greeting must not contact the model.")

    monkeypatch.setattr(groq, "Groq", should_not_call_groq)
    sensitive_context = {
        "workflow_stage": "1 · Load complex",
        "experiment_state": {"complex_loaded": False},
    }
    for greeting in ("HELLO", "Hello! 👋", " hi ", "Hey there", "Good morning"):
        assert assistant.ask(greeting, "fake-key", context=sensitive_context, evidence={}) == (
            "Hi! 👋 How can I help?"
        )
    assert assistant.ask("Mambo!", "fake-key", context=sensitive_context, evidence={}) == (
        "Habari! 👋 Naweza kukusaidia nini?"
    )


def test_greeting_plus_real_scientific_request_reaches_groq(monkeypatch):
    import groq
    sent = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        def __enter__(self): return self

        def __exit__(self, *args): pass

        def create(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="Reference RMSD measures recovery of the crystal pose.",
                tool_calls=None,
            ))])

    monkeypatch.setattr(groq, "Groq", FakeClient)
    reply = assistant.ask("Hi, explain my reference RMSD", "fake-key",
                          context={"workflow_stage": "3 · Validate docking"}, evidence={})
    assert "RMSD" in reply
    assert len(sent) == 1
    assert sent[0]["tools"]
    prompt = sent[0]["messages"][-2]["content"]
    assert "optional pandoc background" in prompt.lower()


def test_streamlit_greeting_keeps_chat_history_without_sending_groq(monkeypatch, tmp_path):
    import groq
    monkeypatch.setattr(groq, "Groq", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("Greeting must not call Groq")))
    monkeypatch.setattr(assistant, "setting", lambda st, name, default="":
                        "test-key" if name == "GROQ_API_KEY" else default)
    script = (
        "import streamlit as st\n"
        "from pandoc import assistant\n"
        "st.session_state.workflow_stage='1 · Load complex'\n"
        "assistant.render(st, " + repr(str(tmp_path)) + ")"
    )
    at = AppTest.from_string(script).run()
    at.chat_input[0].set_value("HELLO").run()
    assert not list(at.exception)
    assert at.session_state["assistant_history"][-1]["answer"] == "Hi! 👋 How can I help?"
    at.run()
    assert len(at.session_state["assistant_history"]) == 1
    assert "Load complex" not in at.session_state["assistant_history"][-1]["answer"]
