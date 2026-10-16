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
        report = ws.render(source)
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
                   'TEST-1', 'release-4.20']
        for body in invalid:
            with self.subTest(body=body):
                pr['body']=body
                self.assertEqual(ws.contextual_jira_links(pr),[])
                self.assertNotIn('Jira state is unverified',ws.render(source))

    def test_foreign_site_context_never_joins_a_same_key_native_ticket(self):
        source = self.capture()
        issue = self.add_jira(source)
        source['github']['prs'][0]['body']='https://foreign.example/browse/TEST-123'
        report = ws.render(source)
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
        report=ws.render(source)
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
        self.assertIn(issue['url'],report)
        self.assertNotIn('Merged:',report)

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
        self.assertIn('GitHub partial; local Git unavailable',report)
        self.assertNotIn('Private failure',report)
        self.assertLess(len(report.splitlines()),8)

    def test_native_mr_context_is_raw_and_markdown_sensitive_url_stays_literal(self):
        source=self.capture()
        issue=self.add_jira(source)
        url='https://git.example.test/group/project~~not~~/-/merge_requests/17'
        issue['detail']['fields']['description'] += '\nContext '+url
        associate_work(source['jira'],source['github'])
        report=ws.render(source)
        self.assertIn('  - <'+url+'>',report)
        self.assertNotIn('MR merged',report)
        self.assertEqual(issue['review_links'][0]['proof'],'description')

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
