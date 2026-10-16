"""Native GitHub actor evidence must retain a real event or source identity."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evidence import timestamp  # noqa: E402
from extended_evidence import (  # noqa: E402
    historical_head_hashes, repository_aliases, validate_github, validated_timeline_events,
)


class ExtendedGithubEvidenceTest(unittest.TestCase):
    def test_related_only_keeps_native_name_lead_separate_from_read_failure(self):
        data, evidence = self.capture()
        url = 'https://github.com/org/project/pull/7'
        item = {'repo': 'org/project', 'number': 7, 'url': url, 'title': 'Work', 'body': '', 'state': 'open',
                'created_at': '2026-01-10T12:00:00Z', 'updated_at': '2026-01-11T12:00:00Z',
                'closed_at': None, 'merged_at': None, 'head_sha': 'a' * 40, 'merge_sha': None, 'head_ref': 'work'}
        data['github']['pr_index'] = [item]
        data['metadata'].update(github_status='ok', repositories=[{'path': '/work/local',
            'remotes': [{'kind': 'github', 'host': 'github.com', 'repo': 'org/project'}]}],
            github_discovery={'head_history:' + url: {'status': 'ok', 'pages': 1, 'count': 0}})
        data['branches'] = [{'repo': '/work/local', 'refs': ['refs/heads/work'], 'pr_status': 'related_only',
            'prs': [], 'related_prs': [{**{key: item[key] for key in ('repo', 'number', 'url', 'state')},
                                      'proof': 'head_name_only'}],
            'association_lookups': [{'repo': 'org/project', 'status': 'ok'}]}]
        self.validate(data, evidence)
        for mutation in ('no_candidate', 'wrong_name', 'failed_sha', 'failed_history', 'wrong_state', 'unrouted'):
            broken = copy.deepcopy(data)
            if mutation == 'no_candidate':
                broken['branches'][0]['related_prs'] = []
            elif mutation == 'wrong_name':
                broken['branches'][0]['refs'] = ['refs/heads/other-work']
            elif mutation == 'failed_sha':
                broken['branches'][0]['association_lookups'][0]['status'] = 'partial'
            elif mutation == 'failed_history':
                broken['metadata'].update(github_status='partial', warnings=['native history failed'])
                broken['metadata']['github_discovery']['head_history:' + url]['status'] = 'partial'
            elif mutation == 'wrong_state':
                broken['branches'][0]['related_prs'][0]['state'] = 'merged'
            else:
                broken['metadata']['repositories'][0]['remotes'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(broken, evidence)

    def test_html_head_requires_complete_page_replay_and_native_api_binding(self):
        from github_timeline import collect_timeline
        item = {'repo': 'org/project', 'number': 12, 'url': 'https://github.com/org/project/pull/12'}
        before, after, at = 'a' * 40, 'b' * 40, '2026-01-10T12:00:00Z'
        rest = {'id': 77, 'node_id': 'native77', 'event': 'head_ref_force_pushed',
                'url': 'https://api.github.com/repos/org/project/issues/events/77',
                'created_at': at, 'commit_id': after}
        graph = {'__typename': 'HeadRefForcePushedEvent', 'id': 'native77', 'createdAt': at,
                 'beforeCommit': None, 'afterCommit': None, 'pullRequest': {'url': item['url']}}
        hydro = {'event_type': 'force_push_timeline_diff.click', 'payload': {
            'event_id': 77, 'originating_url': item['url'], 'repository_id': 1, 'pull_request_id': 2}}
        body = ('<div class="TimelineItem js-targetable-element" id="event-77">'
                f'<a href="/org/project/commit/{before}">before</a>'
                f'<a href="/org/project/commit/{after}">after</a>'
                f'<a href="/org/project/compare/{before}..{after}" '
                f"data-hydro-click='{json.dumps(hydro)}'>diff</a>"
                f'<a href="#event-77"><relative-time datetime="{at}"></relative-time></a></div>')
        timeline = collect_timeline(item, [rest], [77], read=lambda url: {
            'url': url, 'status': 200, 'headers': {}, 'body': body})
        self.assertEqual(timeline['status'], 'ok')
        base = {key: item[key] for key in ('repo', 'number')}
        api_heads = [{**base, 'item_url': item['url'], 'kind': 'rest_force_push', 'source': rest,
                      'source_path': 'repos/org/project/issues/12/timeline'},
                     {**base, 'item_url': item['url'], 'kind': 'graphql_force_push', 'source': graph}]
        html_head = {**base, 'item_url': item['url'], 'kind': 'html_force_push', 'source': timeline['events'][0]}
        github = {'head_events': api_heads + [html_head], 'timeline_html': [timeline]}
        known = {item['url']: item}
        events = validated_timeline_events(github, known)
        self.assertEqual(historical_head_hashes(html_head, known, html_events=events), {before, after})
        with self.assertRaises(ValueError):
            historical_head_hashes(html_head, known)
        forged = copy.deepcopy(html_head)
        forged['source']['before_sha'] = 'c' * 40
        with self.assertRaises(ValueError):
            historical_head_hashes(forged, known, html_events=events)
        later = copy.deepcopy(github)
        later['head_events'].append({**api_heads[1], 'source': {
            **graph, 'beforeCommit': {'oid': before}, 'afterCommit': {'oid': after}}})
        self.assertEqual(validated_timeline_events(later, known), events)
        for mutation in ('no_rest', 'different_graph_id', 'forged_pair', 'hidden_target'):
            broken = copy.deepcopy(github)
            if mutation == 'no_rest':
                del broken['head_events'][0]
            elif mutation == 'different_graph_id':
                broken['head_events'][1]['source']['id'] = 'unrelated'
            elif mutation == 'forged_pair':
                broken['timeline_html'][0]['events'][0]['before_sha'] = 'c' * 40
            else:
                broken['timeline_html'][0]['target_event_ids'] = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validated_timeline_events(broken, known)

    def alias(self):
        return {'host':'github.com', 'requested_repo':'old/project', 'canonical_repo':'current/project',
                'requested':{'path':'repos/old/project', 'id':123, 'full_name':'current/project',
                             'html_url':'https://github.com/current/project'},
                'canonical':{'path':'repos/current/project', 'id':123, 'full_name':'current/project',
                             'html_url':'https://github.com/current/project'}}

    def test_repository_alias_requires_independent_native_id_bound_routes(self):
        record = self.alias()
        self.assertEqual(repository_aliases({'repository_aliases':[record]}, 'github.com'),
                         {('github.com','old/project'):'current/project'})
        mutations = [(('canonical','id'),124), (('requested','id'),True),
                     (('requested','path'),'repos/wrong/project'),
                     (('canonical','path'),'repos/old/project'),
                     (('canonical','full_name'),'other/project'),
                     (('requested','html_url'),'https://other.example/current/project'),
                     (('host',),'other.example'), (('requested_repo',),'../project')]
        for path,value in mutations:
            with self.subTest(path=path):
                row=copy.deepcopy(record)
                target=row
                for key in path[:-1]:
                    target=target[key]
                target[path[-1]]=value
                with self.assertRaises(ValueError):
                    repository_aliases({'repository_aliases':[row]},'github.com')
        contradictory=copy.deepcopy(record)
        contradictory['requested_repo']='another/project'
        contradictory['requested'].update(path='repos/another/project',id=999)
        contradictory['canonical']['id']=999
        with self.assertRaises(ValueError):
            repository_aliases({'repository_aliases':[record,contradictory]},'github.com')

    def capture(self):
        item = {'repo': 'example/target', 'number': 12,
                'url': 'https://github.com/example/target/issues/12'}
        event = {'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
                 'actor': 'person', 'action': 'cross-referenced', 'at': '2026-01-10T12:00:00Z',
                 'url': item['url'], 'id': None, 'body': '',
                 'source': {'type': 'issue', 'issue': {'id': 1234, 'number': 7,
                            'html_url': 'https://github.com/another/source/pull/7'}}}
        return {'metadata': {'github_user': 'person', 'github_host': 'github.com'},
                'github': {'activity': [event]}}, {item['url']: item}

    def validate(self, data, evidence):
        validate_github(data, evidence, timestamp('2026-01-08T12:00:00Z'),
                        timestamp('2026-01-15T12:00:00Z'))

    def test_idless_cross_reference_retains_uncaptured_native_source(self):
        data, evidence = self.capture()
        self.validate(data, evidence)
        self.assertIsNone(data['github']['activity'][0]['id'])
        self.assertEqual(len(evidence), 1)  # The source need not be a captured work item.
        data['github']['activity'][0]['source']['issue']['html_url'] = (
            'https://github.com/another/source/issues/7')
        self.validate(data, evidence)

    def test_local_repository_scope_is_not_a_failed_github_read(self):
        data, evidence = self.capture()
        data['metadata'].update(github_status='ok', repositories=[{'path': '/work/local'}],
            github_discovery={'unrouted:/work/local': {'status': 'unsupported',
                'scope': 'github_global_without_repository', 'reason': 'No configured GitHub repository',
                'pages': 0, 'count': 0}})
        data['branches'] = [{'repo': '/work/local', 'pr_status': 'unsupported',
            'association_scope': 'github_global_without_repository',
            'prs': [], 'association_lookups': [{'scope': 'github_global', 'status': 'ok'}]}]
        self.validate(data, evidence)
        for mutation in ('failed_lookup', 'missing_lookup', 'routed_branch', 'unsupported_api_failure',
                         'configured_remote', 'unknown_repository'):
            broken = copy.deepcopy(data)
            if mutation == 'failed_lookup':
                broken['branches'][0]['association_lookups'][0]['status'] = 'partial'
            elif mutation == 'missing_lookup':
                broken['branches'][0]['association_lookups'] = []
            elif mutation == 'routed_branch':
                broken['branches'][0]['association_scope'] = 'configured_repository'
            elif mutation == 'unsupported_api_failure':
                broken['metadata']['github_discovery']['activity:org/repo#1'] = (
                    broken['metadata']['github_discovery'].pop('unrouted:/work/local'))
            elif mutation == 'configured_remote':
                broken['metadata']['repositories'][0]['remotes'] = [
                    {'kind': 'github', 'host': 'github.com', 'repo': 'org/project'}]
            else:
                broken['branches'][0]['repo'] = '/unrecorded/path'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(broken, evidence)
    def test_cross_reference_source_rejects_forged_identity_and_routes(self):
        baseline, evidence = self.capture()
        invalid = [('id', None), ('id', True), ('id', 0), ('number', 0), ('number', True)]
        invalid += [('html_url', url) for url in (
            'http://github.com/another/source/pull/7',
            'https://other.example/another/source/pull/7',
            'https://user@github.com/another/source/pull/7',
            'https://github.com/another/source/pull/8',
            'https://github.com/another/source/pull/7#issuecomment-1',
            'https://github.com/another/source/pull/7?x=1',
            'https://github.com/another/source/pull/7/',
            'https://github.com/another/../pull/7',
            'https://github.com/another/%2e%2e/pull/7',
            'https://github.com/another/source/issues/07',
        )]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['source']['issue'][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        for source in (None, {}, {'type': 'pull', 'issue': {}},
                       {'type': 'issue', 'issue': {}, 'invented': 1}):
            with self.subTest(source=source):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['source'] = source
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))

    def test_idless_evidence_requires_exact_captured_target_and_cross_reference_action(self):
        baseline, evidence = self.capture()
        for field, value in [('item_url', 'https://github.com/example/target/issues/99'),
                             ('number', 99), ('repo', 'other/target'),
                             ('action', 'closed'), ('url', next(iter(evidence)) + '#event-1')]:
            with self.subTest(field=field):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        data = copy.deepcopy(baseline)
        del data['github']['activity'][0]['id']
        with self.assertRaises(ValueError):
            self.validate(data, copy.deepcopy(evidence))

    def test_other_actor_events_require_native_positive_id(self):
        baseline, evidence = self.capture()
        event = baseline['github']['activity'][0]
        event.pop('source')
        event.update(action='closed', id=42)
        self.validate(baseline, copy.deepcopy(evidence))
        for value in (None, 0, -1, True, '42'):
            with self.subTest(value=value):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['id'] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))

    def test_recovered_comment_editor_is_bound_to_native_identity_and_edit_time(self):
        baseline, evidence = self.capture()
        event = baseline['github']['activity'][0]
        event.pop('source')
        event.update(action='issue_comment_edited', id=42,
                     url=event['item_url'] + '#issuecomment-42')
        event['editor_evidence'] = {
            'id': 'native-node', 'databaseId': 42, 'url': event['url'],
            'author': {'login': 'another-author'}, 'editor': {'login': 'person'},
            'createdAt': '2026-01-01T00:00:00Z',
            'lastEditedAt': event['at'], 'updatedAt': '2026-01-10T12:00:01Z'}
        self.validate(baseline, copy.deepcopy(evidence))
        mutations = [('databaseId', 43), ('databaseId', True), ('id', ''),
                     ('url', event['item_url'] + '#issuecomment-43'),
                     ('editor', {'login': 'another-editor'}), ('author', {'login': ''}),
                     ('createdAt', '2026-01-11T00:00:00Z'),
                     ('lastEditedAt', '2026-01-10T12:00:01Z'),
                     ('updatedAt', '2026-01-10T11:59:59Z')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['editor_evidence'][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        deleted_author = copy.deepcopy(baseline)
        deleted_author['github']['activity'][0]['editor_evidence']['author'] = None
        self.validate(deleted_author, copy.deepcopy(evidence))
        baseline['github']['activity'][0]['action'] = 'closed'
        with self.assertRaises(ValueError):
            self.validate(baseline, copy.deepcopy(evidence))

    def test_context_comments_require_native_target_identity_and_dates(self):
        baseline, evidence = self.capture()
        item = next(iter(evidence.values()))
        baseline['github']['reference_comments'] = [{
            'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
            'kind': 'issue_comment', 'id': 42, 'node_id': 'native-node',
            'url': item['url'] + '#issuecomment-42', 'body': 'Another author mentions TASK-42.',
            'author_login': 'someone-else', 'created_at': '2025-01-01T00:00:00Z',
            'updated_at': '2025-01-02T00:00:00Z'}]
        self.validate(baseline, copy.deepcopy(evidence))
        for field, value in [('item_url', 'https://github.com/other/repo/issues/12'),
                             ('repo', 'other/repo'), ('number', 13), ('kind', 'invented'),
                             ('id', True), ('url', item['url'] + '#issuecomment-43'),
                             ('body', None), ('author_login', ''), ('node_id', ''),
                             ('created_at', '2026-01-01T00:00:00Z')]:
            with self.subTest(field=field):
                data = copy.deepcopy(baseline)
                data['github']['reference_comments'][0][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        baseline['github']['reference_comments'] *= 2
        with self.assertRaises(ValueError):
            self.validate(baseline, copy.deepcopy(evidence))

    def test_editor_proof_cannot_contradict_retained_native_comment(self):
        baseline, evidence = self.capture()
        event = baseline['github']['activity'][0]
        event.pop('source')
        event.update(action='issue_comment_edited', id=42, body='Native body',
                     url=event['item_url'] + '#issuecomment-42')
        event['editor_evidence'] = {
            'id': 'native-comment', 'databaseId': 42, 'url': event['url'],
            'author': {'id': 'native-author', 'login': 'native-bot'}, 'editor': {'login': 'person'},
            'createdAt': '2026-01-01T00:00:00Z', 'lastEditedAt': event['at'],
            'updatedAt': '2026-01-10T12:00:01Z'}
        baseline['github']['reference_comments'] = [{
            'item_url': event['item_url'], 'repo': event['repo'], 'number': event['number'],
            'id': 42, 'kind': 'issue_comment', 'node_id': 'native-comment', 'url': event['url'],
            'author_login': 'native-bot[bot]', 'author_node_id': 'native-author', 'body': event['body'],
            'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-10T12:00:01Z'}]
        self.validate(baseline, copy.deepcopy(evidence))  # Native IDs bridge REST/GraphQL bot login forms.
        for field, value in [('id', 'other-comment'), ('author', None),
                             ('author', {'id': 'other-author', 'login': 'native-bot[bot]'}),
                             ('createdAt', '2025-01-01T00:00:00Z'),
                             ('updatedAt', '2026-01-10T12:00:02Z')]:
            with self.subTest(field=field):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['editor_evidence'][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        data = copy.deepcopy(baseline)
        data['github']['activity'][0]['body'] = 'Contradictory native body'
        with self.assertRaises(ValueError):
            self.validate(data, copy.deepcopy(evidence))
        data = copy.deepcopy(baseline)
        changed = data['github']['activity'][0]
        changed['url'] = changed['editor_evidence']['url'] = event['item_url'] + '#discussion_r999'
        with self.assertRaises(ValueError):
            self.validate(data, copy.deepcopy(evidence))
        event['editor_evidence']['author'] = None
        baseline['github']['reference_comments'][0].update(author_login=None, author_node_id=None)
        self.validate(baseline, copy.deepcopy(evidence))


if __name__ == '__main__':
    unittest.main()
