"""Native GitHub actor evidence must retain a real event or source identity."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evidence import timestamp  # noqa: E402
from extended_evidence import repository_aliases, validate_github  # noqa: E402


class ExtendedGithubEvidenceTest(unittest.TestCase):
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
                     ('editor', {'login': 'another-editor'}), ('author', None),
                     ('createdAt', '2026-01-11T00:00:00Z'),
                     ('lastEditedAt', '2026-01-10T12:00:01Z'),
                     ('updatedAt', '2026-01-10T11:59:59Z')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(baseline)
                data['github']['activity'][0]['editor_evidence'][field] = value
                with self.assertRaises(ValueError):
                    self.validate(data, copy.deepcopy(evidence))
        baseline['github']['activity'][0]['action'] = 'closed'
        with self.assertRaises(ValueError):
            self.validate(baseline, copy.deepcopy(evidence))


if __name__ == '__main__':
    unittest.main()
