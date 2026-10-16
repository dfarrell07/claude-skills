"""Cross-provider reference closure must retain leads without inventing activity."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provider_references import (  # noqa: E402
    associate_gitlab, complete_provider_references, gitlab_seed_urls, validate_gitlab_links, verified_aliases,
)
from reference_scope import ReferenceScope  # noqa: E402


class ProviderReferencesTest(unittest.TestCase):
    def test_resumed_provider_keeps_native_positives_and_recovers_unread_github(self):
        for persisted in (False, True):
            with self.subTest(persisted=persisted):
                mr = 'https://gitlab.example.test/group/project/-/merge_requests/7'
                pr = 'https://github.com/org/project/pull/9'
                jira = {'metadata': {'site': 'jira.example.test'}, 'issues': []}
                github = {'prs': [], 'issues': []}
                branches = [{'merge_requests': [{'url': mr}]}]
                scope = ReferenceScope(jira, github, branches)
                native_reads = []
                def gitlab(urls):
                    native_reads.append(list(urls))
                    return {'items': [{'url': url, 'source': {'description': pr}} for url in urls]}
                captured, first = complete_provider_references(jira, github, branches,
                    lambda keys: None, None, gitlab, scope=scope)
                self.assertEqual([row['url'] for row in captured['items']], [mr])
                self.assertEqual(first['unread_pr_urls'], [pr])
                calls = []
                captured, second = complete_provider_references(jira, github, branches,
                    lambda keys: None, calls.extend, gitlab,
                    scope=first['scope'] if persisted else scope)
                self.assertEqual([row['url'] for row in captured['items']], [mr])
                self.assertEqual(calls, [pr])
                self.assertEqual(second['unread_pr_urls'], [])
                self.assertEqual(second['attempted_gitlab_urls'], [mr])
                self.assertEqual(len(second['scope']['nodes']), 2)
                captured, third = complete_provider_references(jira, github, branches,
                    lambda keys: None, calls.extend, gitlab,
                    scope=second['scope'] if persisted else scope)
                self.assertEqual([row['url'] for row in captured['items']], [mr])
                self.assertEqual(calls, [pr])
                self.assertEqual(third['unread_pr_urls'], [])

    def test_native_graphql_note_keeps_its_url_and_global_id(self):
        mr = 'https://gitlab.example.test/team/project/-/merge_requests/7'
        jira = {'metadata': {'site': 'jira.example.test'}, 'issues': [{
            'key': 'TEST-1', 'url': 'https://jira.example.test/browse/TEST-1', 'sensitive': False}]}
        gitlab = {'items': [{'url': mr, 'source': {'description': ''}, 'notes': [{
            'source_kind': 'graphql', 'id': 'gid://gitlab/Note/8', 'url': mr + '#note_8',
            'body': 'Follow TEST-1'}]}]}
        associate_gitlab(jira, gitlab)
        relation = gitlab['items'][0]['jira_links'][0]
        self.assertEqual(relation['evidence_url'], mr + '#note_8')
        self.assertEqual(relation['note_id'], 'gid://gitlab/Note/8')
        validate_gitlab_links({'jira': jira, 'gitlab': gitlab})
        relation['note_id'] = 8
        with self.assertRaises(ValueError):
            validate_gitlab_links({'jira': jira, 'gitlab': gitlab})

    def test_unresolved_gitlab_ticket_url_is_not_only_a_lexical_lead(self):
        mr = 'https://gitlab.example.test/team/project/-/merge_requests/7'
        jira = {'metadata': {'site': 'jira.example.test', 'coverage': {
            'referenced_by_github': {'status': 'partial', 'requested_keys': ['NEWPROJECT-7'],
                'returned_keys': [], 'failures': {'NEWPROJECT-7': {'message': 'native 404'}}}}},
            'issues': [], 'linked_prs': []}
        github = {'prs': [], 'issues': []}
        gitlab = {'items': [{'url': mr, 'source': {
            'description': 'See https://jira.example.test/browse/NEWPROJECT-7'}}]}
        complete_provider_references(jira, github, [], lambda keys: None, None, lambda urls: gitlab)
        classification = jira['metadata']['coverage']['referenced_by_github']['failure_classification']
        self.assertEqual(classification['NEWPROJECT-7'], {
            'kind': 'explicit_jira_url', 'evidence_urls': [mr]})
        self.assertEqual(jira['metadata']['coverage']['referenced_by_github']['status'], 'partial')

    def test_new_ticket_histories_join_reference_closure_without_reviving_old_pairings(self):
        old_pr = 'https://github.com/org/project/pull/9'
        mr = 'https://gitlab.example.test/team/project/-/merge_requests/7'
        def issue(key):
            return {'key': key, 'url': 'https://jira.example.test/browse/' + key,
                    'sensitive': False, 'pr_links': []}
        jira = {'metadata': {'site': 'jira.example.test'}, 'linked_prs': [], 'issues': [issue('TEST-1')]}
        github, histories, pr_reads = {'prs': [], 'issues': []}, [], []
        def history():
            for item in jira['issues']:
                if 'public_history' in item:
                    continue
                histories.append(item['key'])
                item['public_history'] = {'status': 'ok'}
                item['history_review_links'] = ([{'provider': 'github', 'url': old_pr,
                    'history_side': 'fromString'}] if item['key'] == 'TEST-1' else
                    [{'provider': 'gitlab', 'url': mr, 'history_side': 'toString'}]
                    if item['key'] == 'TEST-2' else [])
        def tickets(keys):
            known = {item['key'] for item in jira['issues']}
            jira['issues'].extend(issue(key) for key in keys if key not in known)
            # This newly discovered ticket has independently attributed work;
            # its history may therefore start another direct-counterpart lookup.
            jira.setdefault('activity', []).extend({'key': key, 'kind': 'comment_created',
                'occurred_at': '2026-07-10T12:00:00Z'} for key in keys if key == 'TEST-2')
        def prs(urls):
            pr_reads.extend(urls)
            github.setdefault('context_prs', []).extend({'url': url, 'body': 'TEST-2'} for url in urls)
        def gitlab(urls):
            return {'items': [{'url': url, 'source': {'description': 'TEST-3'}} for url in urls]}
        captured, expansion = complete_provider_references(
            jira, github, [], tickets, prs, gitlab, read_history=history)
        self.assertEqual(pr_reads, [old_pr])
        self.assertEqual(histories, ['TEST-1', 'TEST-2', 'TEST-3'])
        self.assertEqual(expansion['attempted_gitlab_urls'], [mr])
        self.assertEqual(captured['items'][0]['jira_links'][0]['key'], 'TEST-3')
        self.assertTrue(all(not item['pr_links'] for item in jira['issues']))

    def test_later_identity_bound_native_comment_body_keeps_cross_provider_reference(self):
        url = 'https://github.com/org/repo/pull/42#issuecomment-7'
        created, updated = '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z'
        rest = {'id': 7, 'node_id': 'native-comment', 'html_url': url, 'created_at': created,
                'updated_at': created, 'user': {'login': 'bot', 'node_id': 'native-bot'}}
        mr = 'https://gitlab.example.test/team/project/-/merge_requests/9'
        native = {'id': 'native-comment', 'databaseId': 7, 'url': url, 'createdAt': created,
                  'updatedAt': updated, 'body': mr, 'author': {'login': 'bot', 'id': 'native-bot'}}
        github = {'prs': [{'url': url.split('#')[0], 'body': ''}],
                  'comment_edit_histories': [{'item_url': url.split('#')[0], 'repo': 'org/repo',
                      'number': 42, 'kind': 'issue_comment', 'rest_observation': rest,
                      'pages': [{'after': None, 'response': {'data': {'node': native}}}]}]}
        self.assertEqual(gitlab_seed_urls({'issues': []}, github, []), {mr})
        github['comment_edit_histories'][0]['pages'][0]['response']['data']['node']['databaseId'] = 8
        self.assertEqual(gitlab_seed_urls({'issues': []}, github, []), set())

    def test_moved_ticket_in_native_gitlab_description_keeps_original_key_provenance(self):
        native = {'key': 'NEW-3', 'id': '123', 'self': 'https://jira.example.test/rest/api/3/issue/123',
                  'fields': {'summary': 'Canonical ticket', 'status': {'name': 'Active',
                             'statusCategory': {'key': 'indeterminate'}}}}
        jira = {'metadata': {'site': 'jira.example.test'}, 'issues': [{
            'key': 'NEW-3', 'url': 'https://jira.example.test/browse/NEW-3',
            'sensitive': False, 'source': native}], 'key_aliases': [{
                'requested_key': 'OLD-2', 'canonical_key': 'NEW-3',
                'requested': copy.deepcopy(native), 'canonical': copy.deepcopy(native)}]}
        gitlab = {'items': [{'url': 'https://gitlab.example.test/team/repo/-/merge_requests/7',
                            'source': {'description': 'Fixes OLD-2'}}]}
        associate_gitlab(jira, gitlab)
        data = {'jira': jira, 'gitlab': gitlab}
        validate_gitlab_links(data)
        relation = gitlab['items'][0]['jira_links'][0]
        self.assertEqual(relation['url'], 'https://jira.example.test/browse/NEW-3')
        self.assertEqual(relation['source_key'], 'OLD-2')
        self.assertEqual(relation['jira_source_url'], 'https://jira.example.test/browse/OLD-2')
        broken = copy.deepcopy(data)
        broken['gitlab']['items'][0]['jira_links'][0]['source_key'] = 'UNMENTIONED-9'
        with self.assertRaises(ValueError):
            validate_gitlab_links(broken)

    def test_selected_native_alternate_bodies_supply_mr_leads_without_historical_index_crawl(self):
        selected = 'https://github.com/org/project/pull/42'
        root = 'https://gitlab.example.test/team/project/-/merge_requests/'
        github = {'prs': [{'url': selected, 'body': ''}], 'issues': [],
                  'pr_index': [{'url': selected, 'body': root + '2'},
                               {'url': 'https://github.com/org/other/pull/9', 'body': root + '99'}],
                  'pr_index_observations': [{'url': selected, 'body': root + '3'}],
                  'reference_comments': [{'item_url': 'https://github.com/org/other/pull/9',
                                          'body': root + '4'}]}
        self.assertEqual(gitlab_seed_urls({'issues': []}, github, []), {root + '2', root + '3', root + '4'})

    def test_context_pair_completion_does_not_crawl_arbitrary_cross_provider_frontier(self):
        root = 'https://gitlab.example.test/group/project/-/merge_requests/'
        jira = {'metadata': {'site': 'jira.example.test'}, 'linked_prs': [], 'issues': [{
            'key': 'TEST-1', 'url': 'https://jira.example.test/browse/TEST-1', 'sensitive': False,
            'detail': {'fields': {'description': root + '1'}}}]}
        github = {'prs': [], 'issues': []}
        key_reads, pr_reads, native_reads = [], [], []
        def tickets(keys):
            key_reads.extend(keys)
            for key in keys:
                if key.startswith('TEST-'):
                    jira['issues'].append({'key': key, 'url': 'https://jira.example.test/browse/' + key,
                        'sensitive': False, 'detail': {'fields': {'description': root + '2' if key == 'TEST-2' else ''}}})
        def prs(urls):
            pr_reads.extend(urls)
            github.setdefault('context_prs', []).extend({'url': url, 'title': 'Follow-up', 'body': 'TEST-3'}
                                                        for url in urls)
        def gitlab(urls):
            native_reads.append(list(urls))
            return {'items': [{'url': url, 'source': {'title': 'Catalog', 'description': (
                'TEST-2 https://github.com/org/project/pull/42' if url.endswith('/1') else 'TEST-3')},
                'notes': ([{'id': 77, 'body': 'MISSING-9 ' + root + '3'}] if url.endswith('/2') else [])}
                for url in urls], 'metadata': {'status': 'ok'}}
        captured, expansion = complete_provider_references(jira, github, [], tickets, prs, gitlab)
        self.assertEqual(expansion['attempted_gitlab_urls'], [root + '1'])
        self.assertEqual(key_reads, ['TEST-2'])
        self.assertEqual(pr_reads, [])
        self.assertEqual(len(native_reads), 2)  # Empty scope, then the direct work counterpart.
        deferred = expansion['scope']['deferred_targets']
        self.assertTrue(any(row['target'] == root + '2' for row in deferred))
        self.assertTrue(any(row['target'] == 'https://github.com/org/project/pull/42' for row in deferred))
        self.assertEqual(captured['items'][0]['jira_links'][0]['key'], 'TEST-2')
        data = {'jira': jira, 'gitlab': captured}
        validate_gitlab_links(data)
        broken = copy.deepcopy(data)
        broken['gitlab']['items'][0]['jira_links'][0]['key'] = 'TEST-3'
        with self.assertRaises(ValueError):
            validate_gitlab_links(broken)
        self.assertNotIn('activity', captured)

    def test_only_native_account_aliases_can_supply_public_identity_bridge(self):
        self.assertEqual(verified_aliases({'author_aliases': [
            {'email': 'own@example.test', 'source': 'authenticated_github_profile'},
            {'email': 'guessed@example.test', 'source': 'configured_git_email'},
            {'email': 'historical@example.test', 'source': 'github_commit_account'}]}),
            ['historical@example.test', 'own@example.test'])


if __name__ == '__main__':
    unittest.main()
