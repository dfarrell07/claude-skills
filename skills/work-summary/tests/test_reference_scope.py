"""Reference scope follows personal work counterparts without a graph-size cap."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reference_scope import ReferenceScope  # noqa: E402
from work_links import complete_references  # noqa: E402
from jira_public_history import collect_public_history  # noqa: E402


START, END = '2026-07-01T00:00:00Z', '2026-09-30T23:59:59Z'
ROOT = 'https://github.com/org/repo/pull/1'
CONTEXT = 'https://github.com/org/repo/pull/2'


def capture():
    return ({'metadata': {'site': 'jira.example.test'}, 'issues': []},
            {'prs': [{'url': ROOT, 'body': 'TEST-1'}], 'issues': []})


class ReferenceScopeTest(unittest.TestCase):
    def test_pair_completion_stops_at_terminal_ticket(self):
        jira, github = capture()
        scope = ReferenceScope(jira, github)
        self.assertTrue(scope.allow('review', ROOT, 'ticket', 'TEST-1'))
        self.assertTrue(scope.allow('ticket', 'TEST-1', 'review', CONTEXT))
        self.assertTrue(scope.allow('review', CONTEXT, 'ticket', 'TEST-2'))
        self.assertFalse(scope.allow('ticket', 'TEST-2', 'review', 'https://github.com/org/repo/pull/3'))
        self.assertFalse(scope.allow('review', CONTEXT, 'review', 'https://gitlab.test/group/repo/-/merge_requests/9'))
        self.assertEqual(len(scope.snapshot()['deferred_targets']), 2)
        self.assertEqual(scope.attempted_keys, set())

    def test_no_cap_on_independent_work_roots(self):
        jira, github = capture()
        github['prs'] = [{'url': f'https://github.com/org/repo/pull/{i}'} for i in range(1, 301)]
        scope = ReferenceScope(jira, github)
        for i in range(1, 301):
            self.assertTrue(scope.allow('review', github['prs'][i - 1]['url'], 'ticket', f'TEST-{i}'))

    def test_terminal_node_upgrades_on_later_direct_root_path(self):
        jira, github = capture()
        scope = ReferenceScope(jira, github)
        scope.allow('review', ROOT, 'ticket', 'TEST-1')
        scope.allow('ticket', 'TEST-1', 'review', CONTEXT)
        scope.allow('review', CONTEXT, 'ticket', 'TEST-2')
        self.assertFalse(scope.allow('ticket', 'TEST-2', 'review', CONTEXT + '0'))
        scope.allow('review', ROOT, 'ticket', 'TEST-2')
        self.assertTrue(scope.allow('ticket', 'TEST-2', 'review', CONTEXT + '0'))
        self.assertFalse(scope.snapshot()['deferred_targets'])

    def test_native_promotion_revisits_retained_context_but_passive_events_do_not(self):
        jira, github = capture()
        github['context_prs'] = [{'url': CONTEXT, 'body': 'TEST-9', 'roles': ['jira_link']}]
        scope = ReferenceScope(jira, github, since=START, until=END)
        self.assertFalse(scope.allow('review', CONTEXT, 'ticket', 'TEST-9'))
        for action in ('assigned', 'mentioned', 'subscribed', 'cross-referenced'):
            github['activity'] = [{'action': action, 'item_url': CONTEXT, 'at': '2026-08-01T00:00:00Z'}]
            scope.refresh(jira, github)
            self.assertFalse(scope.node('review', CONTEXT)['root'])
        github['activity'] = [{'action': 'review', 'item_url': CONTEXT, 'at': '2026-08-01T00:00:00Z'}]
        result = complete_references(jira, github, lambda keys: None, scope=scope)
        self.assertIn('TEST-9', result['attempted_jira_keys'])
        self.assertFalse(result['scope']['deferred_targets'])

    def test_resuming_preserves_terminal_scope_and_dates(self):
        jira, github = capture()
        scope = ReferenceScope(jira, github, since=START, until=END)
        scope.allow('review', ROOT, 'ticket', 'TEST-1')
        scope.allow('ticket', 'TEST-1', 'review', CONTEXT)
        scope.allow('review', CONTEXT, 'ticket', 'TEST-2')
        jira['issues'] = [{'key': 'TEST-2'}]
        github['context_prs'] = [{'url': CONTEXT, 'roles': ['jira_link']}]
        restored = ReferenceScope(jira, github, state=copy.deepcopy(scope.snapshot()))
        self.assertEqual(restored.since, START)
        self.assertFalse(restored.node('ticket', 'TEST-2')['root'])
        self.assertFalse(restored.allow('ticket', 'TEST-2', 'review', CONTEXT + '0'))

    def test_completion_accepts_persisted_scope_without_refreezing_context(self):
        jira, github = capture()
        github['prs'][0]['body'] = ''
        first = complete_references(jira, github, lambda keys: None)
        github['context_prs'] = [{'url': CONTEXT, 'roles': ['jira_link'], 'body': 'OTHER-9'}]
        calls = []
        resumed = complete_references(jira, github, calls.append, scope=first['scope'])
        self.assertEqual(calls, [])
        self.assertEqual(resumed['attempted_jira_keys'], [])
        self.assertEqual(resumed['scope']['deferred_targets'][0]['target'], 'OTHER-9')

    def test_initial_partial_personal_role_context_is_a_root(self):
        jira, github = capture()
        github['context_prs'] = [{'url': CONTEXT, 'roles': ['initial_dated_search_context']}]
        scope = ReferenceScope(jira, github)
        self.assertTrue(scope.node('review', CONTEXT)['root'])

    def test_contains_own_history_branch_association_is_a_root(self):
        jira, github = capture()
        branches = [{'attribution': 'contains_configured_or_verified_author',
                     'prs': [{'url': CONTEXT, 'proof': 'head_ref_oid'}]}]
        scope = ReferenceScope(jira, github, branches)
        self.assertTrue(scope.node('review', CONTEXT)['root'])

    def test_provider_route_case_and_verified_repository_alias(self):
        jira, github = capture()
        github['repository_aliases'] = [{'host': 'github.com', 'requested_repo': 'org/old',
            'canonical_repo': 'org/new', 'requested': {'path': 'repos/org/old', 'id': 7,
                'full_name': 'org/new', 'html_url': 'https://github.com/org/new'},
            'canonical': {'path': 'repos/org/new', 'id': 7,
                'full_name': 'org/new', 'html_url': 'https://github.com/org/new'}}]
        scope = ReferenceScope(jira, github)
        self.assertEqual(scope.identity('review', 'https://GITHUB.com/ORG/OLD/pull/7'),
                         'https://github.com/org/new/pull/7')
        self.assertNotEqual(scope.identity('review', 'https://gitlab.test/Group/Repo/-/merge_requests/7'),
                            scope.identity('review', 'https://gitlab.test/group/repo/-/merge_requests/7'))

    def test_gitlab_late_personal_inventory_and_typed_note_promotions(self):
        jira, github = capture()
        scope = ReferenceScope(jira, github, since=START, until=END)
        url = 'https://gitlab.test/Group/Repo/-/merge_requests/7'
        gitlab = {'metadata': {'identities': [{'host': 'gitlab.test', 'user_id': 42}]},
            'items': [{'url': url, 'source': {'author': {'id': 99}, 'state': 'closed'},
                'notes': [{'source_kind': 'graphql', 'system': False,
                    'author': {'id': 'gid://gitlab/User/42'}, 'created_at': '2026-08-01T00:00:00Z'}]}]}
        scope.refresh(jira, github, gitlab)
        self.assertTrue(scope.node('review', url)['root'])
        edit_url = url + '0'
        note = {'source_kind': 'graphql', 'system': False, 'author': {'id': 'gid://gitlab/User/99'},
                'created_at': '2025-01-01T00:00:00Z', 'last_edited_by': {'id': 'gid://gitlab/User/42'},
                'last_edited_at': '2026-08-01T00:00:00Z'}
        gitlab['items'] = [{'url': edit_url, 'source': {}, 'notes': [note]}]
        scope.refresh(jira, github, gitlab)
        self.assertTrue(scope.node('review', edit_url)['root'])
        for bad in ('gid://gitlab/Note/42', 'gid://gitlab/User/99', 42):
            foreign_url = url + str(bad).replace('/', '')
            note = {**note, 'last_edited_by': {'id': bad}}
            gitlab['items'] = [{'url': foreign_url, 'source': {}, 'notes': [note]}]
            scope.refresh(jira, github, gitlab)
            self.assertFalse(scope.node('review', foreign_url)['root'])
        inventory_url = url + '1'
        gitlab['items'] = [{'url': inventory_url, 'source': {'author': {'id': 42},
            'state': 'opened', 'updated_at': '2025-01-01T00:00:00Z'}}]
        scope.refresh(jira, github, gitlab)
        self.assertTrue(scope.node('review', inventory_url)['root'])

    def test_deferred_lookups_do_not_become_attempts_or_failures(self):
        jira, github = capture()
        github['prs'][0]['body'] = ''
        github['context_prs'] = [{'url': CONTEXT, 'roles': ['jira_link'], 'body': 'OTHER-9'}]
        calls = []
        result = complete_references(jira, github, calls.append)
        self.assertEqual(calls, [])
        self.assertEqual(result['attempted_jira_keys'], [])
        self.assertEqual(result['scope']['deferred_targets'][0]['target'], 'OTHER-9')

    def test_new_verified_jira_field_activity_promotes_context_ticket(self):
        jira, github = capture()
        scope = ReferenceScope(jira, github, since=START, until=END)
        site = 'jira.example.test'
        issue = {'key': 'TEST-9', 'url': f'https://{site}/browse/TEST-9',
                 'roles': ['referenced_by_github'], 'source': {
                     'id': '9', 'key': 'TEST-9', 'fields': {}}}
        jira['issues'].append(issue)
        jira['metadata'].update(identity_verified=True, account_id='own-id')
        own = {'id': '1', 'created': '2026-08-01T12:00:00Z', 'author': {'accountId': 'own-id'},
               'items': [{'field': 'description', 'toString': 'Design context'}]}
        def read(url, receipt=None):
            if '?fields=' in url:
                return {'id': '9', 'key': 'TEST-9', 'self': f'https://{site}/rest/api/3/issue/9',
                        'fields': {'summary': 'Context'}}
            return {'self': f'https://{site}/rest/api/3/issue/9/changelog?maxResults=100&startAt=0',
                    'startAt': 0, 'maxResults': 100, 'total': 1, 'isLast': True, 'values': [own]}
        collect_public_history(jira, lambda message: None, workers=1, reader=read)
        scope.refresh(jira, github)
        self.assertTrue(scope.node('ticket', 'TEST-9')['root'])
        self.assertIn('native_personal_field_activity', scope.node('ticket', 'TEST-9')['reasons'])


if __name__ == '__main__':
    unittest.main()
