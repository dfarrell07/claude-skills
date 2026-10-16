"""Cross-provider reference closure must retain leads without inventing activity."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provider_references import (  # noqa: E402
    complete_provider_references, gitlab_seed_urls, validate_gitlab_links, verified_aliases,
)


class ProviderReferencesTest(unittest.TestCase):
    def test_selected_native_alternate_bodies_supply_mr_leads_without_historical_index_crawl(self):
        selected = 'https://github.com/org/project/pull/42'
        root = 'https://gitlab.example.test/team/project/-/merge_requests/'
        github = {'prs': [{'url': selected, 'body': ''}], 'issues': [],
                  'pr_index': [{'url': selected, 'body': root + '2'},
                               {'url': 'https://github.com/org/other/pull/9', 'body': root + '99'}],
                  'pr_index_observations': [{'url': selected, 'body': root + '3'}]}
        self.assertEqual(gitlab_seed_urls({'issues': []}, github, []), {root + '2', root + '3'})

    def test_native_descriptions_and_other_author_notes_close_cross_provider_frontier(self):
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
        self.assertEqual(expansion['attempted_gitlab_urls'], [root + str(n) for n in (1, 2, 3)])
        self.assertEqual(key_reads, ['TEST-2', 'TEST-3', 'MISSING-9'])
        self.assertEqual(pr_reads, ['https://github.com/org/project/pull/42'])
        self.assertEqual(len(native_reads), 4)  # Empty scope, then three expanding native URL sets.
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
