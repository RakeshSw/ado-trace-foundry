"""ADOTraceFoundry public-safe demo (run: streamlit run playground.py).

Uses an isolated in-memory synthetic ADO workspace. No Azure DevOps writes.
All live LLM calls are gated by Redis; credentials are server-side only.
"""
import json
import os
import uuid
from pathlib import Path

import streamlit as st
from adotracefoundry.playground_core import load_export, Workspace
from adotracefoundry.playground_ai import generate_story, QuotaExceeded
from adotracefoundry.traceability import analyze

st.set_page_config(page_title='ADOTraceFoundry Playground', page_icon='🔗', layout='wide')


def initial_workspace():
    configured = os.environ.get('ADOTRACE_DEMO_EXPORT')
    candidates = ([Path(configured)] if configured else []) + [
        Path('samples/playground-snapshot'), Path('output/live-v03-complete')]
    for folder in candidates:
        if (folder / 'workitems.jsonl').exists() and (folder / 'relationships.json').exists():
            return load_export(folder), str(folder)
    raise FileNotFoundError('No synthetic demo snapshot found. Run tools/build_demo_snapshot.py first.')


if 'workspace' not in st.session_state:
    try:
        st.session_state.workspace, st.session_state.dataset_path = initial_workspace()
    except Exception as exc:
        st.error(str(exc))
        st.stop()
if 'visitor_id' not in st.session_state:
    st.session_state.visitor_id = uuid.uuid4().hex
if 'draft' not in st.session_state:
    st.session_state.draft = None
if 'draft_version' not in st.session_state:
    st.session_state.draft_version = 0

ws: Workspace = st.session_state.workspace
st.title('🔗 ADOTraceFoundry | Engineering Intelligence Playground')
st.caption('Synthetic SampleCommerce sandbox · No real Azure DevOps changes · AI generation requires server-side Redis limits')

if st.sidebar.button('Reset my demo session'):
    st.session_state.workspace, _ = initial_workspace()
    st.session_state.draft = None
    st.rerun()

page = st.sidebar.radio('Explore', ['Overview', 'Generate & Edit', 'Incremental Sync', 'Knowledge & Coverage'])
st.sidebar.caption('Rate limits are enforced in Upstash Redis, not in browser state.')

if page == 'Overview':
    delta = ws.diff()
    a, b, c, d = st.columns(4)
    a.metric('Source work items', len(ws.source))
    b.metric('Synced knowledge records', len(ws.synced))
    c.metric('Synced relationships', len(ws.synced_edges))
    d.metric('Pending work-item changes', len(delta['created_ids']) + len(delta['modified_ids']))
    st.info('Source work items and synced knowledge are intentionally separate. Save a story, then run Sync to see the difference.')
    st.json(delta)
    st.caption(f'Demo dataset: {st.session_state.dataset_path}')

elif page == 'Generate & Edit':
    st.subheader('✨ AI Story Generator')
    features = sorted((n for n in ws.source.values() if n['type'] == 'Feature'), key=lambda n: n['id'])
    if not features:
        st.warning('No Features in demo dataset; import a SampleCommerce snapshot with Features.')
        st.stop()
    feature_ids = [x['id'] for x in features]
    selected = st.selectbox('Parent Feature', feature_ids,
                           format_func=lambda i: f"{i} — {ws.source[i]['title']}")
    if st.button('✨ Generate with Azure Foundry', type='primary'):
        try:
            with st.spinner('Generating a new synthetic User Story via Foundry...'):
                st.session_state.draft = generate_story(ws.source[selected],
                                                        visitor_id=st.session_state.visitor_id)
                st.session_state.draft_version += 1
            st.success('New LLM-generated draft. Review it before saving.')
        except (QuotaExceeded, RuntimeError, ValueError) as exc:
            st.error(str(exc))

    existing_ids = [i for i, n in ws.source.items() if n['type'] == 'User Story']
    selected_story = st.selectbox('Edit existing story (optional)', [None] + sorted(existing_ids),
                                 format_func=lambda i: 'Create a new story' if i is None else f"{i} — {ws.source[i]['title']}")
    draft = st.session_state.draft
    if selected_story is not None:
        node = ws.source[selected_story]
        defaults = (node['title'], str(node.get('fields', {}).get('System.Description', '')),
                    str(node.get('fields', {}).get('Microsoft.VSTS.Common.AcceptanceCriteria', '')))
    elif draft:
        defaults = (draft['title'], draft['description'], draft['acceptance_criteria'])
    else:
        defaults = ('', '', '')

    # Changing feature or selecting a different story refreshes the form's widget keys.
    nonce = f'{st.session_state.draft_version}-{selected_story}'
    with st.form('story_editor_' + nonce):
        title = st.text_input('Title', value=defaults[0], key=f'title-{nonce}')
        description = st.text_area('Description', value=defaults[1], height=140, key=f'desc-{nonce}')
        criteria = st.text_area('Acceptance criteria', value=defaults[2], height=180, key=f'ac-{nonce}')
        saved = st.form_submit_button('💾 Save to simulated ADO source')
    if saved:
        try:
            new_id = ws.save_story(title=title, description=description, acceptance_criteria=criteria,
                                   parent_id=selected if selected_story is None else None, item_id=selected_story)
            st.session_state.draft = None
            st.session_state.draft_version += 1
            st.success(f'Saved work item {new_id} in simulated ADO. Knowledge remains unchanged until you sync.')
        except ValueError as exc:
            st.error(str(exc))
    delta = ws.diff()
    st.warning(f"Pending: {len(delta['created_ids'])} new, {len(delta['modified_ids'])} modified. Synced view remains unchanged.")

elif page == 'Incremental Sync':
    st.subheader('🔄 Source vs Knowledge: before and after')
    delta = ws.diff()
    cols = st.columns(3)
    cols[0].metric('Source records', len(ws.source))
    cols[1].metric('Knowledge records', len(ws.synced))
    cols[2].metric('Pending changes', len(delta['created_ids']) + len(delta['modified_ids']))
    st.json(delta)
    if st.button('🔄 Run genuine incremental sync', type='primary'):
        with st.status('Synchronizing changed synthetic work items', expanded=True) as progress:
            for update in ws.sync():
                st.write(f"✅ {update['stage']}")
                st.caption(json.dumps(update['detail'], ensure_ascii=False))
            progress.update(label='Sync complete', state='complete')
    if ws.last_report:
        st.subheader('Last synchronization — measured results')
        before, after = ws.last_report['before'], ws.last_report['after']
        st.dataframe([{'metric': k, 'before': before[k], 'after': after[k]} for k in before], hide_index=True)
        st.json({k: v for k, v in ws.last_report.items() if k not in ('before', 'after')})
        st.caption('Only changed work items and edge endpoints regenerate Markdown. No embeddings are refreshed in v0.4a.')

else:
    st.subheader('📚 Synced Knowledge Explorer')
    st.caption('This page reads the last synchronized state, not unsynced source edits.')
    stories = sorted((n for n in ws.synced.values() if n['type'] == 'User Story'), key=lambda n: n['id'])
    if not stories:
        st.warning('No User Stories in the current knowledge snapshot.')
        st.stop()
    ids = [n['id'] for n in stories]
    chosen = st.selectbox('User Story', ids, format_func=lambda i: f"{i} — {ws.synced[i]['title']}")
    doc = ws.documents[chosen]
    st.markdown(doc)
    related = [e for e in ws.synced_edges if int(e['source']) == chosen or int(e['target']) == chosen]
    st.subheader('Connected work items')
    st.json(related)
    result = analyze(list(ws.synced.values()), ws.synced_edges)
    summary = result['summary']
    rec = next((x for x in result['requirements'] if x['requirement_id'] == chosen), None)
    if rec:
        st.metric('Linked test cases (structural only)', rec['test_count'])
        if not rec['has_linked_tests']:
            st.warning('No linked test cases. This is a structural coverage gap; it is not an AI assessment.')
    st.caption(f"Across synced project: {summary['requirements_with_tests']}/{summary['requirements']} requirements have linked tests.")
    st.download_button('⬇️ Download this agent-ready Markdown', doc,
                       file_name=f'workitem-{chosen}.md', mime='text/markdown')
