"""Review edits must cover all specified fields and preserve the source candidate."""
from copy import deepcopy
import pytest
from app.tasks.synthesis_revision import apply_plan


def test_multi_section_revision_changes_every_requested_section_without_mutating_history():
    data={'scenarios':[{'text':'segment'}], 'conclusions':[{'text':'unsupported global scope','assumptions':['unknown domestic size']}],
          'edges':[{'mechanism':'unproven isolation'}]}
    before=deepcopy(data)
    edits=[{'operation':'replace','path':path,'expected':old,'value':new} for path,old,new in [
        (['conclusions',0,'text'],'unsupported global scope','insufficient evidence'),
        (['conclusions',0,'assumptions'],['unknown domestic size'],['conditional evidence required']),
        (['edges',0,'mechanism'],'unproven isolation','transmission unverified')]]
    revised=apply_plan(data,edits,{})
    assert data==before and revised['scenarios']==data['scenarios']
    assert revised['conclusions'][0]['text']=='insufficient evidence'
    assert revised['conclusions'][0]['assumptions']==['conditional evidence required']
    assert revised['edges'][0]['mechanism']=='transmission unverified'


def test_continuation_is_added_to_the_statement_itself_and_preserves_existing_sources():
    data={'evidence':[{'text':'claim spans two excerpts','evidence_ids':['ev_header','ev_first']}]}
    edit={'operation':'append_source','path':['evidence',0,'evidence_ids'],
          'expected_sources':['header','first'], 'source':'continuation'}
    revised=apply_plan(data,[edit],{'header':'ev_header','first':'ev_first','continuation':'ev_next'})
    assert revised['evidence'][0]['evidence_ids']==['ev_header','ev_first','ev_next']
    assert data['evidence'][0]['evidence_ids']==['ev_header','ev_first']


def test_changed_source_is_rejected_before_applying_a_stale_plan():
    with pytest.raises(ValueError,match='Source field differs'):
        apply_plan({'text':'changed since review'},[{'operation':'replace','path':['text'],'expected':'old','value':'new'}],{})


def test_missing_continuation_binding_is_not_invented():
    with pytest.raises(KeyError):
        apply_plan({'evidence_ids':['ev_first']},[{'operation':'append_source','path':['evidence_ids'],'expected':['ev_first'],'source':'missing'}],{})
