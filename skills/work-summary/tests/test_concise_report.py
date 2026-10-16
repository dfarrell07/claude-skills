"""Offline primary-output contract: ranked work bullets and raw source URLs."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import work_summary as ws  # noqa: E402
from work_links import associate_work  # noqa: E402
from capabilities import empty_local  # noqa: E402


class ConciseReportTest(unittest.TestCase):
    def capture(self):
        item = {'repo':'org/repo', 'number':7, 'url':'https://github.com/org/repo/pull/7',
                'title':'Repair the edge case', 'body':'', 'state':'merged',
                'created_at':'2026-01-10T12:00:00Z', 'updated_at':'2026-01-11T12:00:00Z',
                'closed_at':'2026-01-11T12:00:00Z', 'merged_at':'2026-01-11T12:00:00Z'}
        return {'metadata':{'days':7, 'since_date':'2026-01-08', 'to_date':'2026-01-15',
                'since_at':'2026-01-08T12:00:00Z', 'to_at':'2026-01-15T12:00:00Z',
                'repos_searched':0, 'repos_with_commits':0, 'github_host':'github.com',
                'github_user':'person', 'github_status':'ok', 'local_status':'ok', 'warnings':[]},
                'github':{'prs':[item], 'issues':[]}, 'commits':{}}

    def reference(self, item):
        return {key:item[key] for key in ('repo','number','url')}

    def test_mutating_pr_heads_preserve_both_native_observations_for_exact_branch_proof(self):
        data = self.capture()
        item = {**data['github']['prs'][0], 'head_sha': 'c' * 40, 'merge_sha': None}
        later = {**item, 'head_sha': 'b' * 40, 'body': 'A later native observation.'}
        data['github'].update(pr_index=[item], pr_index_observations=[later])
        repo = '/example/repo'
        data['metadata'].update(repos_searched=1, repositories=[{
            'path': repo, 'common_dir': repo + '/.git', 'status': 'ok', 'identities': [],
            'paths': [repo], 'refs': [{'name': 'refs/heads/work', 'hash': 'b' * 40}]}])
        data['branches'] = [{'repo': repo, 'hash': 'b' * 40, 'object_type': 'commit',
                             'refs': ['refs/heads/work'], 'pr_status': 'matched', 'prs': [{
                                 **self.reference(item), 'state': item['state'], 'proof': 'exact_head_or_merge_sha'}]}]
        ws.validate(data)
        self.assertEqual(data['github']['pr_index'][0]['head_sha'], 'c' * 40)
        for field, value in [('head_sha', 'a' * 40), ('head_sha', 'invalid'),
                             ('url', 'https://github.com/org/repo/pull/999')]:
            with self.subTest(field=field):
                broken = copy.deepcopy(data)
                broken['github']['pr_index_observations'][0][field] = value
                with self.assertRaises(ValueError):
                    ws.validate(broken)

    def test_historical_heads_need_native_pr_and_sha_binding_without_weekly_credit(self):
        data = self.capture()
        item = {**data['github']['prs'][0], 'head_sha': 'c' * 40, 'merge_sha': None}
        repo, head = '/example/repo', 'b' * 40
        data['github']['pr_index'] = [item]
        event = {'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
                 'kind': 'graphql_force_push', 'source': {
                     '__typename': 'HeadRefForcePushedEvent', 'id': 'native-force-push',
                     'createdAt': '2025-01-01T00:00:00Z', 'beforeCommit': {'oid': head},
                     'afterCommit': {'oid': 'c' * 40}, 'pullRequest': {'url': item['url']}}}
        data['github']['head_events'] = [event]
        data['metadata'].update(repos_searched=1, repositories=[{
            'path': repo, 'common_dir': repo + '/.git', 'status': 'ok', 'identities': [],
            'paths': [repo], 'refs': [{'name': 'refs/heads/backup', 'hash': head}]}])
        data['branches'] = [{'repo': repo, 'hash': head, 'object_type': 'commit',
                             'refs': ['refs/heads/backup'], 'pr_status': 'matched', 'prs': [{
                                 **self.reference(item), 'state': item['state'], 'proof': 'historical_head_event',
                                 'head_event_id': 'native-force-push'}]}]
        ws.validate(data)
        event['source']['pullRequest']['url'] = item['url'].upper()
        event['source']['afterCommit'] = None
        ws.validate(data)
        del event['source']['afterCommit']
        with self.assertRaises(ValueError):
            ws.validate(data)
        event['source']['afterCommit'] = {'oid': 'c' * 40}
        for field, value in [('id', 'other-event'), ('beforeCommit', {'oid': 'd' * 40}),
                             ('pullRequest', {'url': 'https://github.com/org/repo/pull/999'}),
                             ('beforeCommit', {'oid': 'invalid'})]:
            with self.subTest(field=field):
                bad = copy.deepcopy(data)
                bad['github']['head_events'][0]['source'][field] = value
                with self.assertRaises(ValueError):
                    ws.validate(bad)
        event.update(kind='rest_force_push', source_path=f"repos/{item['repo']}/issues/{item['number']}/timeline", source={
            'event': 'head_ref_force_pushed', 'id': 42, 'commit_id': head,
            'node_id': 'native-rest-node', 'url': f"https://api.github.com/repos/{item['repo']}/issues/events/42",
            'created_at': '2025-01-01T00:00:00Z'})
        data['branches'][0]['prs'][0]['head_event_id'] = 42
        ws.validate(data)
        del event['source']['commit_id']
        with self.assertRaises(ValueError):
            ws.validate(data)
        event['source']['commit_id'] = head
        for field, value in [('url', 'https://api.github.com/repos/foreign/project/issues/events/42'),
                             ('url', f"https://api.github.com/repos/{item['repo']}/issues/events/999"),
                             ('node_id', '')]:
            with self.subTest(rest_field=field, value=value):
                bad = copy.deepcopy(data)
                bad['github']['head_events'][0]['source'][field] = value
                with self.assertRaises(ValueError):
                    ws.validate(bad)
        bad = copy.deepcopy(data)
        bad['github']['head_events'][0]['source_path'] = f"repos/{item['repo']}/issues/999/timeline"
        with self.assertRaises(ValueError):
            ws.validate(bad)
        self.assertNotIn('activity', data['github'])

    def test_stale_worktree_reflog_snapshot_requires_exact_native_head_evidence(self):
        data = self.capture()
        head = 'a' * 40
        repo, tree = '/example/repo', '/missing/worktree'
        data['metadata'].update(repos_searched=1, repositories=[{
            'path': repo, 'common_dir': repo + '/.git', 'status': 'ok',
            'identities': ['person@example.test'], 'paths': [repo], 'refs': [],
            'worktrees': [{'path': tree, 'head': 'b' * 40, 'recovery': {
                'reflog_heads': [head], 'reflog_output': head + '\n'}}]}])
        data['branches'] = [{'repo': repo, 'hash': head, 'object_type': 'commit',
                             'refs': ['worktree-reflog:' + tree + ':' + head],
                             'pr_status': 'unsupported'}]
        ws.validate(data)
        for field, value in [('reflog_heads', []), ('reflog_output', 'c' * 40 + '\n')]:
            with self.subTest(field=field):
                bad = copy.deepcopy(data)
                bad['metadata']['repositories'][0]['worktrees'][0]['recovery'][field] = value
                with self.assertRaises(ValueError):
                    ws.validate(bad)
        recovery = data['metadata']['repositories'][0]['worktrees'][0]['recovery']
        recovery.update(reflog_output='', reflog_file='0' * 40 + ' ' + head + ' Native actor\n')
        ws.validate(data)  # Git may omit unreadable objects; the raw admin log still proves their identity.
        recovery['reflog_file'] = 'malformed row'
        with self.assertRaises(ValueError):
            ws.validate(data)
        recovery['reflog_file'] = '0' * 40 + ' ' + head + ' Native actor\n'
        recovery['reflog_file'] += 'malformed native tail\n'
        recovery.update(status='partial', reflog_parse_errors=[{'line': 2, 'reason': 'invalid record'}])
        ws.validate(data)  # Readable native positives survive a separately qualified malformed tail.
        recovery['reflog_output'] = 'native failure diagnostic\n'
        with self.assertRaises(ValueError):
            ws.validate(data)
        recovery['errors'] = ['reflog_output']
        ws.validate(data)
        data['branches'][0]['hash'] = 'd' * 40
        with self.assertRaises(ValueError):
            ws.validate(data)

    def enrich(self, source, descriptions=('Fix the important behavior',)):
        enriched = copy.deepcopy(source)
        enriched['analysis'] = {'themes':[{'name':'Work', 'description':text,
            'items':[self.reference(source['github']['prs'][0])]} for text in descriptions],
            'in_progress':[]}
        return enriched

    def add_jira(self, data, sensitive=False):
        pr = data['github']['prs'][0]
        issue = {'key':'TEST-123', 'url':'https://jira.example.test/browse/TEST-123',
                 'title':'Native ticket', 'status':'Active', 'status_category':'indeterminate',
                 'roles':['assigned'], 'sensitive':sensitive,
                 'detail':{'fields':{'description':pr['url'], 'comment':{'comments':[]}}},
                 'pr_links':[{'url':pr['url'], 'proof':'description',
                              'evidence_url':'https://jira.example.test/browse/TEST-123',
                              'sensitive':sensitive}]}
        data['jira'] = {'metadata':{'status':'ok', 'site':'jira.example.test'},
                        'issues':[issue], 'activity':[]}
        associate_work(data['jira'],data['github'])
        return issue

    def test_ranked_descriptions_and_raw_paired_urls_are_the_entire_primary_output(self):
        source = self.capture()
        issue = self.add_jira(source)
        enriched = self.enrich(source,('Most important result','Less important follow-up'))
        for theme in enriched['analysis']['themes']:
            theme['links'] = [issue['url'], source['github']['prs'][0]['url']]
        original = copy.deepcopy(enriched)
        report = ws.render(enriched,source)
        self.assertEqual(report,
            '- Most important result\n  - '+issue['url']+'\n  - '+source['github']['prs'][0]['url']+
            '\n- Less important follow-up\n  - '+issue['url']+'\n  - '+source['github']['prs'][0]['url'])
        self.assertNotIn('# Work Summary',report)
        self.assertNotIn('Activity Stats',report)
        self.assertNotIn('](',report)
        self.assertEqual(enriched,original)

    def test_cli_defaults_to_bullets_and_detailed_remains_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'data.json'
            path.write_text(json.dumps(self.capture()))
            env = {**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
            command = ['bash',str(SCRIPTS/'render-report.sh'),str(path)]
            brief = subprocess.run(command,text=True,capture_output=True,env=env,check=True).stdout
            detailed = subprocess.run(command+['--detailed'],text=True,capture_output=True,env=env,check=True).stdout
            self.assertTrue(brief.startswith('- Merged: '))
            self.assertNotIn('##',brief)
            self.assertIn('## Executive Summary',detailed)

    def test_native_context_urls_survive_no_jira_auth_without_becoming_verified_relations(self):
        source = self.capture()
        pr = source['github']['prs'][0]
        pr['body'] = 'Context https://other-jira.example/browse/CONTEXT-9?focusedCommentId=17#note'
        source['jira'] = {'metadata':{'status':'unavailable','site':''},'issues':[],'activity':[]}
        enriched = self.enrich(source)
        enriched['analysis']['themes'][0]['links'] = ws.work_urls(source, enriched['analysis']['themes'][0])
        report = ws.render(enriched, source)
        self.assertIn('  - https://other-jira.example/browse/CONTEXT-9',report)
        self.assertIn('Jira state is unverified',report)
        self.assertIn('Jira unavailable',report)
        self.assertNotIn('jira_links',pr)
        self.assertEqual(source['jira']['issues'],[])
        proof = ws.contextual_jira_links(pr)[0]
        self.assertEqual(proof['proof'],'github_body_url')
        self.assertEqual(proof['evidence_url'],pr['url'])
        self.assertTrue(proof['source_url'].endswith('#note'))

    def test_context_url_rejects_unsafe_authority_paths_suffixes_and_bare_keys(self):
        source = self.capture()
        pr = source['github']['prs'][0]
        invalid = ['http://jira.example/browse/TEST-1', 'https://user:secret@jira.example/browse/TEST-1',
                   'https://jira.example/browse/TEST-1oops', 'https://jira.example/browse/TEST-1.20',
                   'https://jira.example/browse/TEST-1/extra', 'https://jira.example/../browse/TEST-1',
                   'https://jira.example/browse/%54EST-1', 'https://jira.example:999999/browse/TEST-1',
                   'https://jira.example／evil/browse/TEST-1',
                   'https://jira.example：443/browse/TEST-1',
                   'TEST-1', 'release-4.20']
        for body in invalid:
            with self.subTest(body=body):
                pr['body']=body
                self.assertEqual(ws.contextual_jira_links(pr),[])
                self.assertNotIn('Jira state is unverified',ws.render(source))

    def test_comment_only_context_is_selectable_without_jira_access(self):
        source = self.capture()
        pr = source['github']['prs'][0]
        ticket = 'https://jira.example.test/browse/CONTEXT-9'
        comment = {'item_url': pr['url'], 'repo': pr['repo'], 'number': pr['number'],
                   'kind': 'issue_comment', 'id': 42, 'node_id': None,
                   'url': pr['url'] + '#issuecomment-42', 'body': 'Core context: ' + ticket,
                   'author_login': 'another-author', 'created_at': '2025-01-01T00:00:00Z',
                   'updated_at': '2025-01-02T00:00:00Z'}
        source['github']['reference_comments'] = [comment]
        source['jira'] = {'metadata': {'status': 'unavailable', 'site': ''}, 'issues': [], 'activity': []}
        enriched = self.enrich(source)
        enriched['analysis']['themes'][0]['links'] = [ticket, pr['url']]
        report = ws.render(enriched, source)
        self.assertIn('  - ' + ticket, report)
        self.assertIn('Jira state is unverified', report)
        proof = ws.contextual_jira_links(pr, [comment])[0]
        self.assertEqual(proof['proof'], 'github_comment_url')
        self.assertEqual(proof['evidence_url'], comment['url'])
        self.assertNotIn('activity', source['github'])
        self.assertNotIn('jira_links', pr)
        self.assertEqual(ws.contextual_jira_links({**pr, 'url': pr['url'] + '9'}, [comment]), [])

    def test_foreign_site_context_never_joins_a_same_key_native_ticket(self):
        source = self.capture()
        issue = self.add_jira(source)
        source['github']['prs'][0]['body']='https://foreign.example/browse/TEST-123'
        enriched = self.enrich(source)
        enriched['analysis']['themes'][0]['links'] = ws.work_urls(source, enriched['analysis']['themes'][0])
        report = ws.render(enriched, source)
        self.assertIn('  - '+issue['url'],report)
        self.assertIn('  - https://foreign.example/browse/TEST-123',report)
        self.assertIn('Jira state is unverified',report)
        self.assertEqual(len(source['jira']['issues']),1)

    def test_context_url_case_normalization_deduplicates_a_known_native_ticket(self):
        source=self.capture()
        issue=self.add_jira(source)
        original='https://JIRA.EXAMPLE.TEST/browse/test-123'
        pr=source['github']['prs'][0]
        pr['body']=original
        enriched=self.enrich(source)
        enriched['analysis']['themes'][0]['links']=ws.work_urls(source,enriched['analysis']['themes'][0])
        report=ws.render(enriched,source)
        self.assertEqual(report.count('  - '+issue['url']),1)
        self.assertNotIn('state is unverified',report)
        proof=ws.contextual_jira_links(pr)[0]
        self.assertEqual(proof['url'],issue['url'])
        self.assertEqual(proof['source_url'],original)

    def test_untrusted_descriptions_cannot_create_work_rows_html_or_formatting(self):
        source = self.capture()
        payload='Shipped ~~not~~\n# Forged\n  - https://evil.example <img src=x> [label](javascript:evil)\u202e'
        report=ws.render(self.enrich(source,(payload,)),source)
        self.assertEqual(len([line for line in report.splitlines() if line.startswith('- ')]),1)
        self.assertNotIn('\n#',report)
        self.assertNotIn('<img',report)
        self.assertNotIn('\u202e',report)
        self.assertIn(r'\~\~not\~\~',report)
        self.assertNotIn('  - https://evil.example',report)

    def test_sentence_periods_and_versions_remain_plain_in_the_work_list(self):
        source=self.capture()
        description='Updated Kubernetes 1.37.1. Release 0.23.4 remains pending.'
        enriched=self.enrich(source,(description,))
        self.assertEqual(ws.render(enriched,source).splitlines()[0],'- '+description)

    def test_forged_citations_and_source_tampering_still_fail_before_output(self):
        source=self.capture()
        enriched=self.enrich(source)
        enriched['analysis']['themes'][0]['items'][0]['number']=99
        with self.assertRaises(ValueError):
            ws.render(enriched,source)
        enriched=self.enrich(source)
        enriched['github']['prs'][0]['title']='Changed source'
        with self.assertRaises(ValueError):
            ws.render(enriched,source)

    def test_fallback_uses_native_merge_time_and_events_not_backlog_roles(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(merged_at='2025-01-01T00:00:00Z',closed_at='2025-01-01T00:00:00Z')
        issue=self.add_jira(source)
        self.assertEqual(ws.render(source),'No supported work found in the available evidence.')
        source['github']['activity']=[{'item_url':pr['url'],'repo':pr['repo'],'number':pr['number'],
            'actor':'person','action':'review_comment','at':'2026-01-12T12:00:00Z',
            'url':pr['url']+'#discussion_r1','id':1,'body':'Native review'}]
        report=ws.render(source)
        self.assertTrue(report.startswith('- Discussed: '))
        self.assertNotIn(issue['url'],report)
        self.assertIn(issue['url'],ws.work_urls(source,ws.fallback_topics(source)[0]))
        self.assertNotIn('Merged:',report)

    def test_passive_native_actor_events_are_context_until_substantive_work_exists(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(state='open',created_at='2025-01-01T00:00:00Z',merged_at=None,closed_at=None)
        passive=['mentioned','subscribed','unsubscribed','assigned','unassigned',
                 'review_requested','review_request_removed','cross-referenced','labeled','unknown_action']
        for action in passive:
            with self.subTest(action=action):
                source['github']['activity']=[{'item_url':pr['url'],'repo':pr['repo'],
                    'number':pr['number'],'actor':'person','action':action,
                    'at':'2026-01-12T12:00:00Z','url':pr['url'],'id':1,'body':''}]
                original=copy.deepcopy(source)
                self.assertEqual(ws.render(source),'No supported work found in the available evidence.')
                audit=ws.render(source,detailed=True)
                self.assertIn('Verified GitHub Actor Events (1)',audit)
                self.assertIn(ws.markdown(action),audit)
                self.assertIn(pr['url'],audit)
                self.assertEqual(source,original)
        source['github']['activity'].append({'item_url':pr['url'],'repo':pr['repo'],
            'number':pr['number'],'actor':'person','action':'issue_comment',
            'at':'2026-01-12T13:00:00Z','url':pr['url']+'#issuecomment-2',
            'id':2,'body':'Explained the proposed implementation'})
        report=ws.render(source)
        self.assertEqual(report,'- Discussed: '+pr['title']+'\n  - '+pr['url'])
        self.assertEqual(len(source['github']['activity']),2)

    def test_curated_ongoing_work_is_kept_without_duplicate_topic(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(state='open',merged_at=None,closed_at=None)
        enriched=copy.deepcopy(source)
        enriched['analysis']={'themes':[],'in_progress':[{**self.reference(pr),'description':'Continue the proposal'}]}
        self.assertTrue(ws.render(enriched,source).startswith('- Ongoing: Continue the proposal'))
        enriched=self.enrich(source)
        enriched['analysis']['in_progress']=[{**self.reference(pr),'description':'Same proposal'}]
        self.assertEqual(len([line for line in ws.render(enriched,source).splitlines() if line.startswith('- ')]),1)

    def test_ongoing_links_are_validated_selected_and_deduplicated(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(state='open',merged_at=None,closed_at=None,
                  body='https://unknown.example/browse/TEST-1')
        context='https://unknown.example/browse/TEST-1'
        enriched=copy.deepcopy(source)
        ongoing={**self.reference(pr),'description':'Continue work','links':[]}
        enriched['analysis']={'themes':[],'in_progress':[ongoing]}
        self.assertEqual(ws.render(enriched,source),'- Ongoing: Continue work')
        ongoing['links']=[context,pr['url'],context]
        report=ws.render(enriched,source)
        self.assertIn('- Ongoing: Continue work\n  - '+context+'\n  - '+pr['url'],report)
        self.assertEqual(report.count(context),1)
        self.assertIn('Jira state is unverified',report)
        detailed=ws.render(enriched,source,detailed=True).split('### Activity Stats',1)[0]
        self.assertIn('- Continue work\n  - '+context+'\n  - '+pr['url'],detailed)
        for selected in (['https://forged.example/browse/TEST-1'],None,'invalid'):
            with self.subTest(selected=selected):
                ongoing['links']=selected
                with self.assertRaises(ValueError):
                    ws.render(enriched,source)

    def test_local_only_fallback_is_grounded_and_keeps_private_proof_out_of_primary_output(self):
        source=self.capture()
        source['github']['prs']=[]
        repo='/private/work/checker'
        source['metadata'].update(repos_searched=1,repos_with_commits=1,repositories=[{
            'path':repo,'common_dir':repo+'/.git','paths':[repo],'status':'ok','identities':['me@example.test']}])
        source['commits']={repo:[{'hash':'a'*40,'message':'Fix local edge case',
            'author_email':'me@example.test','committed_at':'2026-01-12T12:00:00Z'}]}
        report=ws.render(source)
        self.assertEqual(report,'- Local change in checker: Fix local edge case')
        self.assertNotIn('/private',report)
        self.assertNotIn('a'*12,report)
        source['metadata']['repositories'][0]['identities']=[]
        with self.assertRaises(ValueError):
            ws.render(source)

    def test_coverage_note_names_sources_without_dumping_warning_text(self):
        source=self.capture()
        source['metadata'].update(github_status='partial',local_status='unavailable',
                                  warnings=['Private failure detail '+str(n) for n in range(2000)])
        report=ws.render(source)
        self.assertIn('local Git unavailable',report)
        self.assertIn('Some history or cross-references could not be fully verified',report)
        self.assertNotIn('GitHub unavailable',report)
        self.assertNotIn('Private failure',report)
        self.assertLess(len(report.splitlines()),8)

    def local_capture(self):
        source=self.capture()
        source['github']['prs']=[]
        source['metadata'].update(github_status='unavailable',warnings=['No remote tools'],
            repos_searched=1,repos_with_commits=1,repositories=[{
                'path':'/private/checker','common_dir':'/private/checker/.git',
                'paths':['/private/checker'],'status':'ok','identities':['me@example.test']}])
        source['jira']={'metadata':{'status':'unavailable','site':''},'issues':[],'activity':[]}
        source['commits']={'/private/checker':[{'hash':'b'*40,'message':'Fix local behavior',
            'author_email':'me@example.test','committed_at':'2026-01-12T12:00:00Z'}]}
        return source

    def test_local_native_subject_retains_explicit_links_without_remote_auth_or_join(self):
        source=self.local_capture()
        entry=source['commits']['/private/checker'][0]
        entry['message']='Fix https://JIRA.EXAMPLE/browse/test-123 and https://github.com/org/repo/pull/8#note'
        original=copy.deepcopy(source)
        topic=ws.fallback_topics(source)[0]
        enriched=copy.deepcopy(source)
        topic.update(name='Local work',links=ws.work_urls(source,topic))
        enriched['analysis']={'themes':[topic],'in_progress':[]}
        report=ws.render(enriched,source)
        self.assertIn('  - https://jira.example/browse/TEST-123',report)
        self.assertIn('  - https://github.com/org/repo/pull/8',report)
        self.assertIn('Jira state is unverified',report)
        self.assertIn('GitHub state is unverified',report)
        self.assertIn('GitHub unavailable; Jira unavailable',report)
        self.assertEqual(source,original)
        proofs=ws.local_context_links(source,topic)
        self.assertEqual({proof['hash'] for proof in proofs},{entry['hash']})
        self.assertTrue(all(proof['proof']=='local_commit_subject_url' for proof in proofs))
        self.assertTrue(any(proof['source_url'].endswith('#note') for proof in proofs))

    def test_local_context_requires_exact_selected_author_verified_subject(self):
        source=self.local_capture()
        entry=source['commits']['/private/checker'][0]
        entry['message']='Context https://jira.example/browse/TEST-1'
        topic={'commits':[{'repo':'/private/checker','hash':'c'*40}]}
        self.assertEqual(ws.local_context_links(source,topic),[])
        topic['commits'][0]['hash']=entry['hash']
        source['metadata']['repositories'][0]['identities']=[]
        self.assertEqual(ws.local_context_links(source,topic),[])

    def test_local_context_rejects_unsafe_or_unsupported_urls_and_honors_enterprise_host(self):
        source=self.local_capture()
        source['metadata']['github_host']='git.example:8443'
        entry=source['commits']['/private/checker'][0]
        topic={'commits':[{'repo':'/private/checker','hash':entry['hash']}]}
        invalid=['https://git.example:8443/../repo/pull/1',
            'https://other.example/org/repo/pull/1','https://github.com/org/repo/pull/1',
            'https://git.example:8443/org/repo/pull/1/extra',
            'https://git.example:8443/org/repo/pull/1oops',
            'https://user:secret@git.example:8443/org/repo/pull/1',
            'https://git.example:999999/org/repo/pull/1',
            'https://git.example／evil/org/repo/pull/1',
            'https://git.example：8443/org/repo/pull/1',
            'http://jira.example/browse/TEST-1','https://jira.example/browse/TEST-1.20',
            'https://jira.example/browse/%54EST-1','TEST-1']
        for value in invalid:
            with self.subTest(value=value):
                entry['message']=value
                self.assertEqual(ws.local_context_links(source,topic),[])
        entry['message']='https://GIT.EXAMPLE:8443/org/repo/pull/8'
        self.assertEqual(ws.work_urls(source,topic),['https://git.example:8443/org/repo/pull/8'])

    def test_native_mr_context_is_raw_and_markdown_sensitive_url_stays_literal(self):
        source=self.capture()
        issue=self.add_jira(source)
        url='https://git.example.test/group/project~~not~~/-/merge_requests/17'
        issue['detail']['fields']['description'] += '\nContext '+url
        associate_work(source['jira'],source['github'])
        enriched=self.enrich(source)
        enriched['analysis']['themes'][0]['links']=[url]
        report=ws.render(enriched,source)
        self.assertIn('  - <'+url+'>',report)
        self.assertNotIn('MR merged',report)
        self.assertEqual(issue['review_links'][0]['proof'],'description')

    def test_normal_gitlab_url_uses_plain_text_despite_intraword_underscore(self):
        source=self.capture()
        issue=self.add_jira(source)
        url='https://git.example.test/group/project/-/merge_requests/17'
        issue['detail']['fields']['description']+='\n'+url
        associate_work(source['jira'],source['github'])
        enriched=self.enrich(source)
        enriched['analysis']['themes'][0]['links']=[url]
        report=ws.render(enriched,source)
        self.assertIn('  - '+url,report)
        self.assertNotIn('<'+url+'>',report)

    def test_partial_verification_is_distinct_from_unavailable_sources(self):
        source=self.capture()
        source['metadata'].update(github_status='partial',local_status='partial',
                                  warnings=['Historical reference could not be verified.'])
        source['jira']={'metadata':{'status':'partial','site':'jira.example.test'},
                        'issues':[],'activity':[]}
        report=ws.render(source)
        self.assertIn('Some history or cross-references could not be fully verified',report)
        self.assertNotIn('unavailable',report)
        self.assertNotIn('Coverage is incomplete:',report)
        self.assertEqual(source['metadata']['github_status'],'partial')

    def test_repository_name_cannot_turn_a_raw_url_into_active_emphasis(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(repo='org/_not_',url='https://github.com/org/_not_/pull/7')
        self.assertIn('  - <'+pr['url']+'>',ws.render(source))

    def test_fallback_is_bounded_deterministic_and_preserves_all_capture_rows(self):
        source=self.capture()
        template=source['github']['prs'][0]
        source['github']['prs']=[{**template,'number':number,'url':f'https://github.com/org/repo/pull/{number}',
            'title':f'Change {number}','merged_at':f'2026-01-11T12:00:{number:02d}Z',
            'closed_at':f'2026-01-11T12:00:{number:02d}Z','updated_at':f'2026-01-11T12:00:{number:02d}Z'}
            for number in range(1,21)]
        original=copy.deepcopy(source)
        report=ws.render(source)
        self.assertEqual(len([line for line in report.splitlines() if line.startswith('- ')]),12)
        self.assertTrue(report.startswith('- Merged: Change 20'))
        self.assertEqual(ws.render(source),report)
        self.assertEqual(source,original)

    def selective_capture(self):
        source=self.capture()
        template=source['github']['prs'][0]
        source['github']['prs'] += [{**template,'number':number,
            'url':f'https://github.com/org/repo/pull/{number}', 'title':f'Peripheral change {number}'}
            for number in (8,9)]
        issue=self.add_jira(source)
        mr='https://git.example.test/group/project/-/merge_requests/17'
        issue['detail']['fields']['description']=' '.join(
            [item['url'] for item in source['github']['prs']]+[mr])
        issue['pr_links']=[{'url':item['url'],'proof':'description',
            'evidence_url':issue['url'],'sensitive':False} for item in source['github']['prs']]
        associate_work(source['jira'],source['github'])
        return source,issue,mr

    def test_explicit_links_select_core_work_without_losing_full_audit(self):
        source,issue,mr=self.selective_capture()
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        theme['jira']=[{key:issue[key] for key in ('key','url')}]
        theme['links']=[issue['url'],source['github']['prs'][0]['url']]
        original=copy.deepcopy(enriched)
        candidates=ws.work_urls(source,theme)
        self.assertIn(mr,candidates)
        self.assertEqual(len(candidates),5)
        brief=ws.render(enriched,source)
        for url in candidates:
            self.assertEqual(url in brief,url in theme['links'])
        detailed=ws.render(enriched,source,detailed=True)
        summary,audit=detailed.split('## Detailed Work Log',1)
        for url in candidates:
            self.assertEqual(url in summary,url in theme['links'])
            self.assertIn(url,audit)
        self.assertEqual(enriched,original)

    def test_legacy_links_are_direct_citations_even_with_many_native_relations(self):
        source,issue,mr=self.selective_capture()
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        report=ws.render(enriched,source)
        self.assertEqual(ws.display_urls(source,theme),[theme['items'][0]['url']])
        self.assertNotIn(issue['url'],report)
        self.assertNotIn(mr,report)
        theme['items']=[]
        theme['jira']=[{key:issue[key] for key in ('key','url')}]
        self.assertEqual(ws.render(enriched,source),'- Fix the important behavior\n  - '+issue['url'])

    def test_explicit_empty_suppresses_links_and_duplicate_ongoing_citation(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        pr.update(state='open',merged_at=None,closed_at=None,
                  body='https://unknown.example/browse/TEST-8')
        enriched=self.enrich(source)
        enriched['analysis']['themes'][0]['links']=[]
        enriched['analysis']['in_progress']=[{**self.reference(pr),'description':'Same work'}]
        self.assertEqual(ws.render(enriched,source),'- Fix the important behavior')

    def test_omitted_candidate_does_not_hide_distinct_ongoing_work(self):
        source,issue,_=self.selective_capture()
        other=source['github']['prs'][1]
        other.update(state='open',merged_at=None,closed_at=None)
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        theme['jira']=[{key:issue[key] for key in ('key','url')}]
        theme['links']=[]
        enriched['analysis']['in_progress']=[{**self.reference(other),'description':'Distinct work'}]
        report=ws.render(enriched,source)
        self.assertIn('- Ongoing: Distinct work',report)
        self.assertIn('  - '+other['url'],report)

    def test_selected_links_reject_sibling_global_forged_and_wrong_types(self):
        source=self.capture()
        first=source['github']['prs'][0]
        second={**first,'number':8,'url':first['url'].replace('/7','/8')}
        source['github']['prs'].append(second)
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        enriched['analysis']['themes'].append({'name':'Other','description':'Other work',
            'items':[self.reference(second)]})
        for selected in ([second['url']], [first['url']+'#forged'],
                         ['https://forged.example/browse/TEST-1'], [None], first['url']):
            with self.subTest(selected=selected):
                theme['links']=selected
                with self.assertRaises(ValueError):
                    ws.render(enriched,source)

    def test_nested_topics_have_independent_links_and_valid_markdown_nesting(self):
        try:
            from markdown_it import MarkdownIt
        except ImportError:
            self.skipTest('Markdown parser is not installed')
        source=self.capture()
        pr=source['github']['prs'][0]
        enriched=copy.deepcopy(source)
        enriched['analysis']={'themes':[{'name':'Group','description':'Main result','items':[],
            'links':[], 'subtopics':[{'name':'Child','description':'Substantive detail',
                'items':[self.reference(pr)],'links':[pr['url'],pr['url']],
                'subtopics':[{'name':'Further','description':'Further detail',
                    'items':[self.reference(pr)],'links':[pr['url']]}]}]}], 'in_progress':[]}
        original=copy.deepcopy(enriched)
        report=ws.render(enriched,source)
        self.assertEqual(report,'- Main result\n  - Substantive detail\n    - '+pr['url']+
            '\n    - Further detail\n      - '+pr['url'])
        tokens=MarkdownIt().parse(report)
        contents=[(token.level,token.content) for token in tokens if token.type=='inline']
        self.assertEqual(contents,[(3,'Main result'),(5,'Substantive detail'),
            (7,pr['url']),(7,'Further detail'),(9,pr['url'])])
        self.assertEqual(enriched,original)
        for description in ('1. Main result.','1.'):
            enriched['analysis']['themes'][0]['description']=description
            numbered=MarkdownIt().parse(ws.render(enriched,source))
            self.assertNotIn('ordered_list_open',[token.type for token in numbered])
            self.assertEqual(sum(token.type=='bullet_list_open' for token in numbered),
                             sum(token.type=='bullet_list_open' for token in tokens))
        group=enriched['analysis']['themes'][0]
        group['links']=[pr['url']]
        with self.assertRaises(ValueError):
            ws.render(enriched,source)

    def test_child_cannot_select_parent_or_sibling_artifacts_without_own_citation(self):
        source=self.capture()
        first=source['github']['prs'][0]
        second={**first,'number':8,'url':first['url'].replace('/7','/8')}
        source['github']['prs'].append(second)
        enriched=self.enrich(source)
        parent=enriched['analysis']['themes'][0]
        child={'name':'Child','description':'Child work','items':[self.reference(second)]}
        parent['subtopics']=[child,{'name':'Sibling','description':'Sibling work',
            'items':[self.reference(first)]}]
        child['links']=[first['url']]
        with self.assertRaises(ValueError):
            ws.render(enriched,source)
        child['links']=[second['url']]
        parent['links']=[]
        report=ws.render(enriched,source)
        self.assertIn('  - Child work\n    - '+second['url'],report)
        self.assertIn('  - Sibling work\n    - '+first['url'],report)

    def test_parent_and_children_preserve_deliberately_reused_selected_url(self):
        source=self.capture()
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        url=source['github']['prs'][0]['url']
        theme['links']=[url,url]
        theme['subtopics']=[{**copy.deepcopy(theme),'description':'Child work'}]
        report=ws.render(enriched,source)
        self.assertEqual(report.count(url),2)
        self.assertIn('  - Child work\n    - '+url,report)

    def test_no_auth_context_is_selectable_but_never_automatic_or_noted_if_omitted(self):
        source=self.capture()
        pr=source['github']['prs'][0]
        urls=[f'https://unknown.example/browse/TEST-{number}' for number in range(1,15)]
        pr['body']=' '.join(urls)
        enriched=self.enrich(source)
        theme=enriched['analysis']['themes'][0]
        candidates=ws.work_urls(source,theme)
        self.assertTrue(set(urls)<=set(candidates))
        for capture,original in ((source,None),(enriched,source)):
            report=ws.render(capture,original)
            self.assertNotIn('unknown.example',report)
            self.assertNotIn('Jira state is unverified',report)
        theme['links']=list(reversed(urls))
        report=ws.render(enriched,source)
        self.assertEqual([line.strip()[2:] for line in report.splitlines() if line.startswith('  - ')],
                         list(reversed(urls)))
        self.assertIn('Jira state is unverified',report)

    def test_grouping_nodes_require_evidenced_children(self):
        source=self.capture()
        enriched=self.enrich(source)
        group=enriched['analysis']['themes'][0]
        group.update(items=[],links=[])
        for children in ([],{},[{'name':'Empty','description':'Empty','items':[]}]):
            with self.subTest(children=children):
                group['subtopics']=children
                with self.assertRaises(ValueError):
                    ws.render(enriched,source)

    def test_bad_github_configuration_does_not_block_other_capable_sources(self):
        jira={'metadata':{'status':'ok','site':'jira.example.test'},'issues':[],'activity':[],'linked_prs':[]}
        with patch.dict(os.environ,{'GH_HOST':'invalid@host'}), \
             patch.object(ws,'collect_local_safely',return_value=(empty_local(),'ok',{'status':'ok'})), \
             patch.object(ws,'collect_jira',return_value=jira), \
             patch.object(ws,'command',side_effect=AssertionError('No GitHub calls allowed')):
            source=ws.collect('7',[])
        ws.validate(source)
        self.assertEqual(source['metadata']['github_status'],'unavailable')
        self.assertEqual(source['metadata']['local_status'],'ok')
        self.assertEqual(source['jira']['metadata']['status'],'ok')
        self.assertIn('GitHub unavailable',ws.render(source))

    def test_no_execution_capabilities_returns_honest_empty_note_and_named_sources(self):
        with patch.object(ws.shutil,'which',return_value=None), patch.dict(os.environ,{'GH_HOST':'github.com'}):
            source=ws.collect('7',[])
        ws.validate(source)
        report=ws.render(source)
        self.assertIn('No supported work found',report)
        self.assertIn('GitHub unavailable',report)
        self.assertIn('Jira unavailable',report)
        self.assertIn('local Git unavailable',report)


if __name__=='__main__':
    unittest.main()
