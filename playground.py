"""ADOTraceFoundry v0.5 — bounded engineering agent with isolated synthetic workspace.

Launch: streamlit run playground.py --server.port 8501
No real Azure DevOps writes. All live LLM calls require server-side Redis quota.
"""
import json
import os
from pathlib import Path
import uuid

import streamlit as st

from adotracefoundry.playground_core import (Workspace, load_export, edit_synthetic_item,
                                              change_synthetic_link, create_synthetic_test_case)
from adotracefoundry.engineering_agent import respond, generate_for_feature, lookup, gap_report
from adotracefoundry.traceability import analyze
from adotracefoundry.playground_ai import QuotaExceeded
from adotracefoundry.demo_compare import capture_before, answer_after, freeze_question

st.set_page_config(page_title='ADOTraceFoundry | Engineering Knowledge Agent', page_icon='🧠', layout='wide')


def open_demo():
    configured = os.getenv('ADOTRACE_DEMO_EXPORT')
    candidates = ([Path(configured)] if configured else []) + [Path('samples/playground-snapshot'), Path('output/live-v03-complete')]
    for path in candidates:
        if (path / 'workitems.jsonl').is_file() and (path / 'relationships.json').is_file():
            return load_export(path), str(path)
    raise FileNotFoundError('Missing approved synthetic snapshot; set ADOTRACE_DEMO_EXPORT to an export folder.')


if 'workspace' not in st.session_state:
    try:
        st.session_state.workspace, st.session_state.dataset_path = open_demo()
    except (FileNotFoundError, ValueError) as exc:
        st.error(str(exc)); st.stop()
for key, value in {'visitor_id': uuid.uuid4().hex, 'messages': [], 'draft': None,
                   'draft_feature': None, 'last_question': '', 'draft_version': 0, 'demo_compare': None}.items():
    if key not in st.session_state:
        st.session_state[key] = value
ws: Workspace = st.session_state.workspace
pending = ws.diff()

st.title('🧠 ADOTraceFoundry | Engineering Knowledge Agent')
st.caption('Synthetic SampleCommerce · Source-aware engineering intelligence · No real Azure DevOps changes')

with st.sidebar:
    st.header('Project status')
    st.metric('Source work items', len(ws.source))
    st.metric('Synced knowledge', len(ws.synced))
    st.metric('Synced relationships', len(ws.synced_edges))
    st.metric('Pending changes', len(pending['created_ids']) + len(pending['modified_ids']))
    st.caption('LLM requests are gated through Redis (20/hour configured server-side). Session-specific sample data resets on refresh/session loss.')
    if st.button('Reset demo session', use_container_width=True):
        st.session_state.workspace, st.session_state.dataset_path = open_demo()
        st.session_state.messages = []
        st.session_state.draft = None
        st.session_state.draft_feature = None
        st.session_state.demo_compare = None
        st.rerun()

chat_tab, changes_tab, explorer_tab, editor_tab = st.tabs([
    '💬 Engineering Agent', '🔄 Sync & Before/After', '📚 Evidence Explorer', '✏️ Work Item Lab'])

with chat_tab:
    st.caption('The model selects an allowlisted tool. Python executes the query; the answer is grounded in tool evidence. Creating or syncing requires an explicit click.')
    starters = [
        'Explain work item 3 and its linked tests',
        'Which requirements have no linked tests?',
        'Check test quality for work item 3',
        'What changed after my last synchronization?',
        'Generate a new User Story under a Feature',
    ]
    if not st.session_state.messages:
        st.info('Try a question below, or type your own. Each question can use up to two actual model calls.')
    cols = st.columns(2)
    chosen_prompt = None
    for i, q in enumerate(starters):
        if cols[i % 2].button(q, key=f'starter-{i}', use_container_width=True):
            chosen_prompt = q
    for ix, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg['role']):
            st.markdown(msg['text'])
            if msg.get('tool'):
                st.caption('Tool executed: `' + msg['tool'] + '` · Synchronized evidence only')
                with st.expander('Inspect exact tool evidence', expanded=False):
                    st.json(msg['evidence'])
    typed = st.chat_input('Ask about requirements, tests, coverage, changes, or generating a story…')
    prompt = typed or chosen_prompt
    if prompt:
        st.session_state.messages.append({'role': 'user', 'text': prompt})
        try:
            with st.spinner('Routing question and investigating synchronized engineering evidence…'):
                result = respond(prompt, ws, visitor_id=st.session_state.visitor_id)
            st.session_state.messages.append({'role': 'assistant', 'text': result['answer'],
                                              'tool': result['plan']['tool'], 'evidence': result['evidence']})
            if result['plan']['tool'] == 'draft_story':
                st.session_state.draft_feature = result['proposed_feature_id'] or None
        except (QuotaExceeded, RuntimeError, ValueError) as exc:
            st.session_state.messages.append({'role': 'assistant', 'text': f'⚠️ {exc}'})
        st.rerun()

    st.divider()
    st.subheader('✨ Generate and approve a new User Story')
    features = sorted((n for n in ws.source.values() if n['type'] == 'Feature'), key=lambda x: x['id'])
    if features:
        feature_ids = [f['id'] for f in features]
        requested = st.session_state.draft_feature
        default_idx = feature_ids.index(requested) if requested in feature_ids else 0
        parent = st.selectbox('Parent Feature', feature_ids, index=default_idx,
                              format_func=lambda i: f"{i} — {ws.source[i]['title']}", key='draft_parent')
        if st.button('✨ Generate new story with Foundry', type='primary'):
            try:
                with st.spinner('Requesting new, contextual story from Foundry…'):
                    st.session_state.draft = generate_for_feature(ws, parent, visitor_id=st.session_state.visitor_id)
                    st.session_state.draft_feature = parent
                    st.session_state.draft_version += 1
                st.success('Draft generated. Edit it before explicitly saving.')
            except (QuotaExceeded, RuntimeError, ValueError) as exc:
                st.error(str(exc))
        draft = st.session_state.draft
        if draft:
            st.caption('This question is generated alongside the story, from its actual content. You can edit it before saving.')
            nonce = st.session_state.draft_version
            with st.form(f'generated-story-{nonce}'):
                title = st.text_input('Title', value=draft['title'])
                description = st.text_area('Description', value=draft['description'], height=130)
                criteria = st.text_area('Acceptance criteria', value=draft['acceptance_criteria'], height=180)
                demo_q = st.text_area('❓ Generated demo question (editable)', value=draft['demo_question'],
                                     help='The SAME question will be used before and after sync. It can include {work_item_id}.',
                                     height=90)
                approved = st.form_submit_button('💾 Approve & save to simulated ADO')
            if approved:
                try:
                    freeze_question(demo_q, max(ws.source, default=0) + 1)
                    identifier = ws.save_story(title=title, description=description,
                                               acceptance_criteria=criteria,
                                               parent_id=st.session_state.draft_feature)
                    frozen_question = freeze_question(demo_q, identifier)
                    before = capture_before(ws, identifier, frozen_question)
                    st.session_state.demo_compare = {'before': before, 'after': None, 'show_before': False}
                    st.session_state.draft = None
                    st.session_state.messages.append({'role': 'assistant', 'text':
                        f'✅ Saved new synthetic User Story **{identifier}**. Source now has {len(ws.source)} items, '
                        f'but synchronized knowledge still has {len(ws.synced)}. Open **Sync & Before/After** to refresh.'})
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    comparison = st.session_state.demo_compare
    if comparison:
        before = comparison['before']
        item_id = before['item_id']
        st.divider()
        st.subheader(f'🔬 Before / After Agent Demo — Work Item {item_id}')
        st.info('The exact same frozen question is used both times. The BEFORE answer is captured from the old synchronized snapshot at save time (and contains no LLM guesswork). The AFTER answer reads the updated synchronized snapshot using Foundry.')
        st.code(before['question'], language=None)
        is_synced = item_id in ws.synced
        b1, b2 = st.columns(2)
        with b1:
            if st.button('🔍 Ask Agent — BEFORE Sync', key='demo-before',
                         disabled=comparison['show_before'], use_container_width=True):
                comparison['show_before'] = True
                st.rerun()
        with b2:
            if st.button('🧠 Ask Agent — AFTER Sync', key='demo-after',
                         disabled=not is_synced or not comparison['show_before'] or comparison['after'] is not None,
                         use_container_width=True):
                try:
                    with st.spinner('Explaining newly synchronized evidence using Foundry…'):
                        comparison['after'] = answer_after(ws, before, visitor_id=st.session_state.visitor_id)
                    st.rerun()
                except (QuotaExceeded, RuntimeError, ValueError) as exc:
                    st.error(str(exc))
        if not is_synced:
            st.warning('Not synchronized: save is complete, but the agent cannot see the new story. Open Sync & Before/After to update knowledge.')
        elif comparison['after'] is None:
            st.success('Story is synchronized. Run the AFTER question to compare answers.')
        if comparison['show_before']:
            st.markdown('#### 📊 Same question, different synchronized evidence')
            after_data = comparison['after']
            pre_count = before['synced_count']
            post_count = after_data['synced_count'] if after_data else len(ws.synced)
            pre_edges = before['relationship_count']
            post_edges = after_data['relationship_count'] if after_data else len(ws.synced_edges)
            m1, m2, m3 = st.columns(3)
            m1.metric('Knowledge records', post_count, delta=post_count - pre_count,
                      help=f'Before: {pre_count} · After: {post_count}')
            m2.metric('Synchronized relationships', post_edges, delta=post_edges - pre_edges,
                      help=f'Before: {pre_edges} · After: {post_edges}')
            m3.metric('Linked tests on new story',
                      len(after_data['linked_test_ids']) if after_data else 'Not evaluated',
                      help='Only direct tested-by relationships count.')
            if after_data:
                st.success(f'WI-{item_id}: absent before → present after; '
                           f'actual linked test cases: {len(after_data["linked_test_ids"])}.')
            left, right = st.columns(2)
            with left:
                st.markdown('#### 🔴 BEFORE — Outdated knowledge')
                st.caption(f'WI-{item_id} absent · {pre_count} knowledge records · {pre_edges} relationships')
                st.markdown(before['answer'])
                st.caption(f"Evidence found: No · Synced records: {before['synced_count']} · LLM calls: 0")
                with st.expander('Before: actual lookup evidence'):
                    st.json(before['evidence'])
            with right:
                st.markdown('#### 🟢 AFTER — Refreshed knowledge')
                if comparison['after']:
                    after = comparison['after']
                    st.caption(f'WI-{item_id} present · {after["synced_count"]} knowledge records · {after["relationship_count"]} relationships')
                    st.markdown(after['answer'])
                    st.caption(f"Evidence found: Yes · Synced records: {after['synced_count']} · LLM calls: {after['model_calls']}")
                    with st.expander('After: actual synchronized evidence'):
                        st.json(after['evidence'])
                else:
                    st.info('Run Sync, then click Ask Agent — AFTER Sync.')

with changes_tab:
    st.subheader('Source vs. last synchronized knowledge')
    current_delta = ws.diff()
    a, b, c, d = st.columns(4)
    a.metric('Source work items', len(ws.source))
    b.metric('Synced knowledge', len(ws.synced))
    c.metric('New', len(current_delta['created_ids']))
    d.metric('Modified', len(current_delta['modified_ids']))
    if current_delta['created_ids'] or current_delta['modified_ids'] or current_delta['added_edges'] or current_delta['removed_edges']:
        st.warning('The simulated source has changes not yet available to the Engineering Agent.')
    else:
        st.success('The simulated source and knowledge snapshots are synchronized.')
    with st.expander('Pending change details', expanded=False):
        st.json(current_delta)
    if st.button('🔄 Initiate incremental sync', type='primary'):
        with st.status('Applying measured incremental changes', expanded=True) as status:
            for stage in ws.sync():
                st.write('✅ ' + stage['stage'])
                st.caption(json.dumps(stage['detail'], ensure_ascii=False))
            status.update(label='Synchronization finished', state='complete')
        st.rerun()
    if ws.last_report and st.session_state.demo_compare:
        before_record = st.session_state.demo_compare['before']
        if before_record['item_id'] in ws.synced:
            st.info(f"🧠 Work item {before_record['item_id']} is now searchable. Return to **Engineering Agent** and click **Ask Agent — AFTER Sync** to see the answer change.")
    if ws.last_report:
        st.subheader('Last synchronization — persisted before/after')
        report = ws.last_report
        st.dataframe([{'Metric': name, 'Before': report['before'][name], 'After': report['after'][name],
                       'Change': report['after'][name] - report['before'][name]}
                      for name in report['before']], hide_index=True)
        st.write(f"Knowledge documents regenerated: **{len(report['regenerated_document_ids'])}** · Unchanged: **{report['unchanged_documents']}**")
        st.caption('Sync refreshes deterministic knowledge files and graph references. It does not recompute embeddings or AI assessments.')
        with st.expander('Exact measured sync report'):
            st.json(report)

with explorer_tab:
    st.subheader('Synchronized evidence explorer')
    st.caption('Queries read synchronized knowledge. Unsynced work items remain unavailable until you click Sync.')
    available = sorted(ws.synced)
    item_id = st.selectbox('Work item', available, key='explorer_item',
                           format_func=lambda i: f"{i} · {ws.synced[i]['type']} · {ws.synced[i]['title'][:65]}")
    data = lookup(ws, item_id)
    left, right = st.columns([3, 2])
    with left:
        st.markdown(ws.documents[item_id])
        st.download_button('⬇ Download agent-ready Markdown', ws.documents[item_id],
                           file_name=f'workitem-{item_id}.md', mime='text/markdown')
    with right:
        st.subheader('Verified graph context')
        st.json(data['linked_records'])
        st.caption(f"Relations: {len(data['relations'])} · Revision: {data['revision']}")
        if data['type'] in {'User Story', 'Requirement', 'Product Backlog Item'}:
            gaps = gap_report(ws)
            linked = [e for e in ws.synced_edges if e['kind'] == 'tested-by' and int(e['source']) == item_id]
            st.metric('Linked test cases (structural)', len(linked))
            if not linked:
                st.warning('No linked tests. Structural coverage gap; not proof of functional insufficiency.')
            st.caption(f"Project-wide unlinked requirements: {gaps['missing_total']}")

with editor_tab:
    st.subheader('Edit a synthetic work item')
    st.caption('Edits affect only your simulated source. Explicit sync is required to update agent knowledge.')
    ids = sorted(ws.source)
    editing = st.selectbox('Choose item', ids, key='editing_item',
                           format_func=lambda i: f"{i} · {ws.source[i]['type']} · {ws.source[i]['title'][:55]}")
    node = ws.source[editing]
    fields = node.get('fields') or {}
    with st.form(f'edit-item-{editing}'):
        title = st.text_input('Title', node['title'])
        description = st.text_area('Description', str(fields.get('System.Description') or ''), height=140)
        ac = st.text_area('Acceptance criteria', str(fields.get('Microsoft.VSTS.Common.AcceptanceCriteria') or ''), height=140)
        save_item = st.form_submit_button('💾 Save source revision')
    if save_item:
        try:
            edit_synthetic_item(ws, editing, title=title, description=description, acceptance_criteria=ac)
            st.success(f'Updated source work item {editing}; knowledge still reflects its previous revision.')
        except ValueError as exc:
            st.error(str(exc))
    st.divider()
    st.subheader('Create a test case in the synthetic source')
    with st.form('new-test-case'):
        tc_title = st.text_input('Test title', key='new-tc-title')
        tc_action = st.text_area('Action', key='new-tc-action')
        tc_expected = st.text_area('Expected result', key='new-tc-expected')
        req_options = [i for i, n in sorted(ws.source.items()) if n['type'] in {'User Story', 'Requirement', 'Product Backlog Item'}]
        req_id = st.selectbox('Link to requirement (optional)', [None] + req_options,
                              format_func=lambda i: 'Leave unlinked' if i is None else f"{i} — {ws.source[i]['title'][:60]}")
        create_tc = st.form_submit_button('Create synthetic test case')
    if create_tc:
        try:
            new_tc = create_synthetic_test_case(ws, title=tc_title, action=tc_action,
                                                expected=tc_expected, requirement_id=req_id)
            st.success(f'Created simulated test case {new_tc}; sync to make it available to agent knowledge.')
        except ValueError as exc:
            st.error(str(exc))
    st.divider()
    st.subheader('Change a synthetic graph link')
    with st.form('edit-link'):
        src = st.number_input('Source ID', min_value=1, max_value=max(ids), value=3, step=1)
        dst = st.number_input('Target ID', min_value=1, max_value=max(ids), value=391 if max(ids)>=391 else max(ids), step=1)
        kind = st.selectbox('Relation', ['tested-by', 'related', 'parent-child'])
        operation = st.radio('Action', ['Add', 'Remove'], horizontal=True)
        submit = st.form_submit_button('Apply change to simulated source')
    if submit:
        try:
            did_change = change_synthetic_link(ws, int(src), int(dst), kind, add=operation=='Add')
            st.success('Simulated source graph updated; sync to publish knowledge.' if did_change else 'No change needed; link already in requested state.')
        except ValueError as exc:
            st.error(str(exc))
