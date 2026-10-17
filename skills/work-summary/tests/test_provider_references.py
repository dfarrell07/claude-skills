"""Cross-provider reference closure must retain leads without inventing activity."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provider_references import associate_gitlab, verified_aliases  # noqa: E402


class ProviderReferencesTest(unittest.TestCase):
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
        relation['note_id'] = 8

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
        relation = gitlab['items'][0]['jira_links'][0]
        self.assertEqual(relation['url'], 'https://jira.example.test/browse/NEW-3')
        self.assertEqual(relation['source_key'], 'OLD-2')
        self.assertEqual(relation['jira_source_url'], 'https://jira.example.test/browse/OLD-2')
        broken = copy.deepcopy(data)
        broken['gitlab']['items'][0]['jira_links'][0]['source_key'] = 'UNMENTIONED-9'

    def test_only_native_account_aliases_can_supply_public_identity_bridge(self):
        self.assertEqual(verified_aliases({'author_aliases': [
            {'email': 'own@example.test', 'source': 'authenticated_github_profile'},
            {'email': 'guessed@example.test', 'source': 'configured_git_email'},
            {'email': 'historical@example.test', 'source': 'github_commit_account'}]}),
            ['historical@example.test', 'own@example.test'])


if __name__ == '__main__':
    unittest.main()
