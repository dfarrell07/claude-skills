"""References are followed one hop from the user's own work and nowhere else."""

from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from references import follow_references  # noqa: E402

SITE = 'jira.example.test'
MR = 'https://gitlab.example.test/group/project/-/merge_requests/5'


def pr(number, body, author='me'):
    return {'repo': 'org/repo', 'number': number, 'title': f'Change {number}', 'body': body, 'head_ref': '',
            'url': f'https://github.com/org/repo/pull/{number}', 'state': 'open', 'author_login': author}


def ticket(key, roles, links=()):
    return {'key': key, 'url': f'https://{SITE}/browse/{key}', 'roles': list(roles), 'sensitive': False,
            'detail': {'fields': {'description': ' '.join(links)}}, 'pr_links': [], 'review_links': []}


class FollowReferencesTest(unittest.TestCase):
    def run_follow(self, github, jira, gitlab_items=()):
        reads = {'tickets': [], 'gitlab': [], 'history': []}

        def read_tickets(keys):
            reads['tickets'].append(list(keys))
            for key in keys:
                if key == 'NET-2':
                    jira['issues'].append(ticket(key, ['referenced_by_github'], [MR]))

        def read_gitlab(urls):
            reads['gitlab'].append(list(urls))
            return {'items': list(gitlab_items), 'metadata': {}}

        gitlab, summary = follow_references(jira, github, [], read_tickets, read_gitlab,
                                            lambda tickets: reads['history'].append([t['key'] for t in tickets]))
        return reads, gitlab, summary

    def test_one_hop_from_active_work_reaches_ticket_then_mr(self):
        github = {'prs': [pr(1, 'Fixes NET-2')], 'issues': [], 'activity': [],
                  'context_prs': [pr(9, 'Old OLD-1 reference')]}
        jira = {'metadata': {'site': SITE}, 'issues': [], 'activity': []}
        mr = {'url': MR, 'host': 'gitlab.example.test', 'source': {'title': 'MR', 'description': 'See NET-3'},
              'notes': []}
        reads, gitlab, summary = self.run_follow(github, jira, [mr])
        self.assertEqual(reads['tickets'], [['NET-2'], ['NET-3']])
        self.assertEqual(reads['gitlab'], [[MR]])
        self.assertEqual(reads['history'], [['NET-2']])
        self.assertEqual(summary['jira_keys'], ['NET-2', 'NET-3'])
        self.assertNotIn('OLD-1', summary['jira_keys'])

    def test_role_only_tickets_and_inactive_items_are_not_crawled(self):
        github = {'prs': [], 'issues': [], 'activity': [], 'context_prs': [pr(9, 'NET-7', author='other')]}
        jira = {'metadata': {'site': SITE}, 'activity': [],
                'issues': [ticket('NET-8', ['watcher'], [MR]), ticket('NET-9', ['updated_by'])]}
        reads, _, summary = self.run_follow(github, jira)
        self.assertEqual(reads['tickets'], [])
        self.assertEqual(reads['gitlab'], [[]])
        self.assertEqual(reads['history'], [['NET-9']])
        self.assertEqual(summary['gitlab_urls'], [])

    def test_failed_updated_by_search_falls_back_to_recently_updated_tickets(self):
        github = {'prs': [], 'issues': [], 'activity': []}
        jira = {'metadata': {'site': SITE, 'coverage': {'updated_by': {'status': 'partial'}}}, 'activity': [],
                'issues': [ticket('NET-8', ['watcher', 'recent']), ticket('NET-9', ['watcher'])]}
        reads, _, _ = self.run_follow(github, jira)
        self.assertEqual(reads['history'], [['NET-8']])

    def test_comments_on_active_items_count_but_other_items_do_not(self):
        github = {'prs': [pr(1, '')], 'issues': [], 'activity': [], 'context_prs': [pr(9, '', author='other')],
                  'reference_comments': [
                      {'item_url': 'https://github.com/org/repo/pull/1', 'body': 'Tracked in NET-4'},
                      {'item_url': 'https://github.com/org/repo/pull/9', 'body': 'Tracked in NET-5'}]}
        jira = {'metadata': {'site': SITE}, 'issues': [], 'activity': []}
        reads, _, _ = self.run_follow(github, jira)
        self.assertEqual(reads['tickets'], [['NET-4']])


if __name__ == '__main__':
    unittest.main()
