"""Native GitHub actor evidence must retain a real event or source identity."""

import copy
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from extended_evidence import repository_aliases  # noqa: E402


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
        pass

    def test_idless_cross_reference_retains_uncaptured_native_source(self):
        data, evidence = self.capture()
        self.validate(data, evidence)
        self.assertIsNone(data['github']['activity'][0]['id'])
        self.assertEqual(len(evidence), 1)  # The source need not be a captured work item.
        data['github']['activity'][0]['source']['issue']['html_url'] = (
            'https://github.com/another/source/issues/7')
        self.validate(data, evidence)

if __name__ == '__main__':
    unittest.main()
