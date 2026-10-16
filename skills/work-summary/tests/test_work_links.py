"""Cross-source relations require captured, explicit references."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from work_links import associate_work, complete_references, referenced_keys, validate_links  # noqa: E402


class WorkLinksTest(unittest.TestCase):
    def capture(self):
        issue = {'key': 'TEST-123', 'url': 'https://jira.example.test/browse/TEST-123', 'sensitive': False,
                 'detail': {'fields': {'description': '', 'issuelinks': [], 'comment': {'comments': []}}}}
        pr = {'url': 'https://github.com/org/repo/pull/42', 'title': 'TEST-123: repair checks', 'body': ''}
        return {'metadata': {'github_host': 'github.com'},
                'github': {'prs': [pr], 'issues': [], 'pr_index': []},
                'jira': {'metadata': {'site': 'jira.example.test'}, 'issues': [issue]}}

    def test_native_key_reference_links_both_directions(self):
        data = self.capture()
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(data['github']['prs'][0]['jira_links'][0]['key'], 'TEST-123')
        self.assertEqual(data['jira']['issues'][0]['pr_links'][0]['url'], data['github']['prs'][0]['url'])

    def test_native_jira_description_links_even_when_pr_is_inaccessible(self):
        data = self.capture()
        url = data['github']['prs'][0]['url']
        issue = data['jira']['issues'][0]
        issue['detail']['fields']['description'] = 'Change: ' + url
        issue['pr_links'] = [{'url': url, 'proof': 'description', 'evidence_url': issue['url']}]
        data['github']['prs'] = []
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(len(issue['pr_links']), 1)
        issue['detail']['fields']['description'] = 'No PR mentioned'
        with self.assertRaises(ValueError):
            validate_links(data)

    def test_foreign_jira_url_does_not_become_bare_key(self):
        for text in ('https://other-jira.example/browse/TEST-123',
                     '[TEST-123](https://other-jira.example/browse/TEST-123)'):
            data = self.capture()
            pr = data['github']['prs'][0]
            pr['title'], pr['body'] = '', text
            associate_work(data['jira'], data['github'])
            self.assertEqual(pr['jira_links'], [])
            self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), [])

    def test_matching_jira_url_and_bare_branch_keys_are_supported(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr['title'], pr['body'] = '', '[ticket](https://jira.example.test/browse/TEST-123)'
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['TEST-123'])

    def test_browse_links_keep_keys_with_sentence_punctuation_or_code_quotes(self):
        for suffix in ('.', ', then verify.', '`', ';', '!', '?', "'.", '’', '”'):
            for host in ('jira.example.test', 'other-jira.example'):
                data = self.capture()
                pr = data['github']['prs'][0]
                pr['title'], pr['body'] = '', 'See `https://' + host + '/browse/TEST-123' + suffix
                expected = ['TEST-123'] if host == 'jira.example.test' else []
                self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), expected)

    def test_selected_index_head_ref_seeds_native_ticket_lookup(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr['title'], pr['body'] = '', ''
        data['github']['pr_index'] = [{**pr, 'head_ref': 'fix/test-123-retry'},
                                      {'url': 'https://github.com/org/repo/pull/99', 'head_ref': 'OLD-77'}]
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['TEST-123'])

    def test_newly_fetched_pr_ticket_frontiers_complete_and_cycles_terminate(self):
        data = self.capture()
        github, jira = data['github'], data['jira']
        github['prs'][0]['title'] = 'TEST-2'
        jira['linked_prs'] = []
        calls = []
        def tickets(keys):
            calls.extend(('jira', key) for key in keys)
            for key in keys:
                if key == 'TEST-2':
                    jira['linked_prs'].append('https://github.com/org/repo/pull/99')
        def prs(urls):
            calls.extend(('pr', url) for url in urls)
            github.setdefault('context_prs', []).append({'url': urls[0], 'body': 'TEST-3 and TEST-2'})
        result = complete_references(jira, github, tickets, prs)
        self.assertEqual(result['attempted_jira_keys'], ['TEST-2', 'TEST-3'])
        self.assertEqual(calls.count(('jira', 'TEST-2')), 1)
        self.assertEqual(calls.count(('jira', 'TEST-3')), 1)
        self.assertEqual(len([call for call in calls if call[0] == 'pr']), 1)

    def test_restricted_ticket_links_remain_marked_private(self):
        data = self.capture()
        data['jira']['issues'][0]['sensitive'] = True
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertTrue(data['jira']['issues'][0]['pr_links'][0]['sensitive'])
        self.assertTrue(data['github']['prs'][0]['jira_links'][0]['sensitive'])

    def test_explicit_jira_link_promotes_index_only_pr_and_expands_its_ticket(self):
        data = self.capture()
        github, jira = data['github'], data['jira']
        github['pr_index'] = [{'url': github['prs'][0]['url'], 'body': 'TEST-2'}]
        github['prs'] = []
        jira['linked_prs'] = [github['pr_index'][0]['url']]
        tickets, urls = [], []
        def read_prs(values):
            urls.extend(values)
            github['context_prs'] = list(github['pr_index'])
        complete_references(jira, github, tickets.extend, read_prs)
        self.assertEqual(urls, jira['linked_prs'])
        self.assertEqual(tickets, ['TEST-2'])

    def test_forged_proof_and_unsourced_comment_links_fail(self):
        data = self.capture()
        associate_work(data['jira'], data['github'])
        for proof in ('invented', 'description', 'comment', 'issue_link'):
            broken = copy.deepcopy(data)
            broken['jira']['issues'][0]['pr_links'][0]['proof'] = proof
            with self.assertRaises(ValueError):
                validate_links(broken)

    def test_sensitive_comment_cannot_prove_public_relation(self):
        data = self.capture()
        issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
        issue['detail']['fields']['comment']['comments'] = [{'id': '77', 'body': pr['url'], 'visibility': {'type':'group'}}]
        issue['pr_links'] = [{'url': pr['url'], 'proof': 'comment', 'evidence_url': issue['url'] + '?focusedCommentId=77'}]
        with self.assertRaises(ValueError):
            validate_links(data)


if __name__ == '__main__':
    unittest.main()
