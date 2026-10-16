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

    def test_authenticated_custom_field_counterparts_keep_exact_native_field_provenance(self):
        from jira_sources import _custom_pr_links
        from work_links import native_review_links
        data = self.capture()
        issue = data['jira']['issues'][0]
        pr = data['github']['prs'][0]
        pr['title'] = 'Repair checks'
        mr = 'https://gitlab.example.test/team/project/-/merge_requests/7'
        issue['full_detail'] = {'fields': {'customfield_123': {
            'content': [{'text': pr['url'], 'marks': [{'attrs': {'href': mr}}]}]}}}
        issue['pr_links'] = _custom_pr_links(issue, issue['full_detail']['fields'], 'full_detail', 'github.com')
        associate_work(data['jira'], data['github'])
        validate_links(data)
        relation = pr['jira_links'][0]
        self.assertEqual((relation['field_id'], relation['source_kind']), ('customfield_123', 'full_detail'))
        review = native_review_links(issue)[0]
        self.assertEqual((review['url'], review['field_id'], review['source_kind']),
                         (mr, 'customfield_123', 'full_detail'))
        for field, value in [('field_id', 'customfield_999'), ('source_kind', 'detail')]:
            broken = copy.deepcopy(data)
            broken['jira']['issues'][0]['pr_links'][0][field] = value
            with self.assertRaises(ValueError):
                validate_links(broken)

    def test_moved_native_jira_key_links_canonical_ticket_and_retains_original_reference(self):
        data = self.capture()
        native = {'key': 'TEST-123', 'id': '123', 'self': 'https://jira.example.test/rest/api/3/issue/123',
                  'fields': {'summary': 'Canonical ticket', 'status': {'name': 'Active',
                             'statusCategory': {'key': 'indeterminate'}}}}
        data['jira']['issues'][0]['source'] = native
        data['jira']['key_aliases'] = [{'requested_key': 'OLD-7', 'canonical_key': 'TEST-123',
                                      'requested': copy.deepcopy(native), 'canonical': copy.deepcopy(native)}]
        data['github']['prs'][0]['title'] = 'OLD-7: repair checks'
        associate_work(data['jira'], data['github'])
        validate_links(data)
        relation = data['github']['prs'][0]['jira_links'][0]
        self.assertEqual(relation['key'], 'TEST-123')
        self.assertEqual(relation['source_key'], 'OLD-7')
        self.assertEqual(relation['jira_source_url'], 'https://jira.example.test/browse/OLD-7')
        broken = copy.deepcopy(data)
        broken['jira']['key_aliases'][0]['canonical']['id'] = '999'
        with self.assertRaises(ValueError):
            validate_links(broken)
        broken = copy.deepcopy(data)
        broken['github']['prs'][0]['jira_links'][0]['source_key'] = 'OTHER-8'
        with self.assertRaises(ValueError):
            validate_links(broken)

    def test_later_selected_native_observation_keeps_new_ticket_reference(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr['title'] = 'First native observation'
        data['github']['pr_index_observations'] = [
            {**pr, 'title': 'TEST-123: later native observation'},
            {**pr, 'url': 'https://github.com/org/repo/pull/999', 'title': 'UNRELATED-999'}]
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['TEST-123'])
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(pr['jira_links'][0]['key'], 'TEST-123')
        self.assertEqual(pr['title'], 'First native observation')

    def test_deliberately_captured_historical_comment_references_join_frontier(self):
        data = self.capture()
        url = 'https://github.com/org/other/pull/99'
        data['github']['pr_index'] = [{'url': url, 'body': 'UNSCANNED-9'}]
        data['github']['reference_comments'] = [{
            'item_url': url, 'body': 'Historical native comment refers to TASK-7.'}]
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['TASK-7', 'TEST-123'])
        self.assertNotIn('activity', data['github'])

    def test_explicit_native_jira_authority_overrides_non_ticket_prefix_guess(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr.update(title='Advisory CVE-2026-1234 and RFC-9999',
                  body='Track https://jira.example.test/browse/CVE-123 and '
                       'https://jira.example.test/browse/ISO-9001')
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['CVE-123', 'ISO-9001'])
        self.assertEqual(referenced_keys(data['github'], 'other.example.test'), [])

    def test_complete_uuid_fragments_are_not_ticket_mentions_but_explicit_tickets_survive(self):
        data = self.capture()
        data['github']['prs'][0].update(
            title='Trace 8a0ba0b1-4636-4f0a-b3e3-411612345678',
            body='Uppercase 8A0BA0B1-4636-4F0A-B3E3-411612345678; '
                 'https://jira.example.test/browse/A0B1-4636 and TEST-123.')
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['A0B1-4636', 'TEST-123'])
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual([i['key'] for i in data['github']['prs'][0]['jira_links']], ['TEST-123'])

    def test_comment_only_ticket_is_discovered_and_bound_without_crediting_its_author(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr['title'] = 'Repair checks'
        comment = {'item_url': pr['url'], 'url': pr['url'] + '#issuecomment-77',
                   'id': 77, 'body': 'The core task is TEST-123.', 'author_login': 'another-person'}
        data['github']['reference_comments'] = [comment]
        self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['TEST-123'])
        associate_work(data['jira'], data['github'])
        validate_links(data)
        relation = data['jira']['issues'][0]['pr_links'][0]
        self.assertEqual(relation['proof'], 'github_comment_key')
        self.assertEqual(relation['evidence_url'], comment['url'])
        self.assertNotIn('activity', data['github'])
        for field, value in [('body', 'Different task TEST-999'),
                             ('url', pr['url'] + '#issuecomment-88'),
                             ('item_url', 'https://github.com/other/repo/pull/42')]:
            with self.subTest(field=field):
                broken = copy.deepcopy(data)
                broken['github']['reference_comments'][0][field] = value
                with self.assertRaises(ValueError):
                    validate_links(broken)

    def test_comment_discovery_reaches_fixed_point_and_preserves_weak_leads(self):
        data = self.capture()
        pr = data['github']['prs'][0]
        pr['title'] = 'No ticket'
        data['github']['reference_comments'] = [
            {'item_url': pr['url'], 'url': pr['url'] + '#issuecomment-1', 'id': 1,
             'body': 'Investigate branch task-99 and https://jira.example.test/browse/TEST-123'}]
        attempted = []
        complete_references(data['jira'], data['github'], lambda keys: attempted.extend(keys))
        self.assertEqual(attempted, ['TASK-99', 'TEST-123'])
        associate_work(data['jira'], data['github'])
        self.assertEqual(pr['jira_links'][0]['key'], 'TEST-123')
        self.assertEqual(len(pr['jira_links']), 1)

    def test_issue_in_a_repository_named_pull_is_not_a_pr_link(self):
        data = self.capture()
        item = data['github']['prs'].pop()
        item['url'] = 'https://github.com/org/pull/issues/42'
        data['github']['issues'] = [item]
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(data['jira']['issues'][0]['pr_links'], [])

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

    def test_github_repository_casing_keeps_native_url_and_jira_provenance(self):
        data = self.capture()
        pr, issue = data['github']['prs'][0], data['jira']['issues'][0]
        pr['title'] = 'No ticket key here'
        original_url = 'https://github.com/ORG/Repo/pull/42'
        issue['detail']['fields']['description'] = original_url
        issue['pr_links'] = [{'url': original_url, 'proof': 'description', 'evidence_url': issue['url']}]
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(issue['pr_links'][0]['url'], pr['url'])
        self.assertEqual(issue['pr_links'][0]['source_url'], original_url)
        self.assertEqual(pr['jira_links'][0]['key'], issue['key'])

    def test_native_repository_alias_pairs_historical_jira_url_without_rewriting_proof(self):
        data = self.capture()
        pr,issue = data['github']['prs'][0],data['jira']['issues'][0]
        pr['title']='No ticket key in this PR'
        original='https://github.com/previous/repo/pull/42'
        issue['detail']['fields']['description']={'marks':[{'attrs':{'href':original}}]}
        issue['pr_links']=[{'url':original,'proof':'description','evidence_url':issue['url']}]
        record={'host':'github.com','requested_repo':'previous/repo','canonical_repo':'org/repo',
                'requested':{'path':'repos/previous/repo','id':123,'full_name':'org/repo',
                             'html_url':'https://github.com/org/repo'},
                'canonical':{'path':'repos/org/repo','id':123,'full_name':'org/repo',
                             'html_url':'https://github.com/org/repo'}}
        # Different repository names alone must never establish identity.
        associate_work(data['jira'],data['github'])
        self.assertEqual(pr['jira_links'],[])
        data['github']['repository_aliases']=[record]
        associate_work(data['jira'],data['github'])
        validate_links(data)
        relation=issue['pr_links'][0]
        self.assertEqual(relation['url'],pr['url'])
        self.assertEqual(relation['source_url'],original)
        self.assertEqual(pr['jira_links'][0]['key'],issue['key'])
        associate_work(data['jira'],data['github'])
        validate_links(data)
        self.assertEqual(relation['source_url'],original)
        for tamper in ('missing-alias','wrong-number','wrong-native-source','invalid-native-suffix','different-id'):
            with self.subTest(tamper=tamper):
                broken=copy.deepcopy(data)
                if tamper=='missing-alias':
                    broken['github'].pop('repository_aliases')
                if tamper=='wrong-number':
                    broken['jira']['issues'][0]['pr_links'][0]['source_url']=original.replace('/42','/43')
                if tamper=='wrong-native-source':
                    broken['jira']['issues'][0]['detail']['fields']['description']='Unrelated content'
                if tamper=='invalid-native-suffix':
                    broken['jira']['issues'][0]['detail']['fields']['description']=original+'oops'
                if tamper=='different-id':
                    broken['github']['repository_aliases'][0]['canonical']['id']=124
                with self.assertRaises(ValueError):
                    validate_links(broken)

    def test_native_gitlab_mr_references_keep_href_comment_and_private_proof(self):
        data = self.capture()
        issue = data['jira']['issues'][0]
        issue['detail']['fields']['description'] = {'marks':[{'attrs':{'href':'https://git.example.test/group/project/-/merge_requests/17'}}]}
        issue['detail']['fields']['comment']['comments'] = [{'id':'81', 'author':{'accountId':'native-id'},
            'visibility':{'type':'group'}, 'body':'See https://git.example.test/group/sub/project/-/merge_requests/18#note_1'}]
        associate_work(data['jira'], data['github'])
        validate_links(data)
        links = issue['review_links']
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]['provider'], 'gitlab')
        self.assertTrue(any(link['proof']=='comment' and link['sensitive'] for link in links))
        self.assertTrue(any(link['proof']=='description' and not link['sensitive'] for link in links))
        self.assertTrue(any(link['source_url'].endswith('#note_1') and not link['url'].endswith('#note_1') for link in links))
        for field, value in (('url','https://git.example.test/group/project/-/merge_requests/99'),
                             ('proof','invented'), ('sensitive',False)):
            broken = copy.deepcopy(data)
            comment = next(link for link in broken['jira']['issues'][0]['review_links'] if link['proof']=='comment')
            comment[field] = value
            with self.assertRaises(ValueError):
                validate_links(broken)

    def test_native_mr_links_reject_unsafe_identity_and_keep_nested_label_privacy(self):
        for label in ('security-review','CVE-2099-1234'):
            data = self.capture()
            issue = data['jira']['issues'][0]
            issue['detail']['fields']['issuelinks'] = [{'outwardIssue':{'fields':{'labels':[label],
                'description':'https://git.example.test/group/project/-/merge_requests/17'}}}]
            associate_work(data['jira'], data['github'])
            self.assertTrue(issue['review_links'][0]['sensitive'])
        for url in ('http://git.example.test/group/project/-/merge_requests/17',
                    'https://user:password@git.example.test/group/project/-/merge_requests/17',
                    'https://git.example.test／evil/group/project/-/merge_requests/17',
                    'https://git.example.test：443/group/project/-/merge_requests/17',
                    'https://git.example.test:0/group/project/-/merge_requests/17',
                    'https://git.example.test:65536/group/project/-/merge_requests/17',
                    'https://git.example.test/group/../project/-/merge_requests/17',
                    'https://git.example.test/group/%2e%2e/project/-/merge_requests/17',
                    'https://git.example.test/group/project/-/merge_requests/17suffix'):
            data = self.capture()
            issue = data['jira']['issues'][0]
            issue['detail']['fields']['description'] = url
            associate_work(data['jira'], data['github'])
            self.assertEqual(issue['review_links'], [])

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

    def test_lowercase_product_paths_and_branches_are_discovery_only(self):
        samples = [('head_ref', 'product-5-support'), ('head_ref', 'product-5-tekton'),
                   ('body', 'plans/product-5-0-rollout.md'), ('body', 'plans/PRODUCT-5-0-rollout.md'),
                   ('body', 'product-5.0 rollout'), ('title', 'product-5 support')]
        for field, text in samples:
            with self.subTest(field=field, text=text):
                data = self.capture()
                issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                issue.update(key='PRODUCT-5', url='https://jira.example.test/browse/PRODUCT-5',
                             title='Unrelated old disk validation work')
                pr.update(title='', body='', head_ref='')
                pr[field] = text
                expected = [] if '.0' in text else ['PRODUCT-5']
                self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), expected)
                associate_work(data['jira'], data['github'])
                validate_links(data)
                self.assertEqual(pr['jira_links'], [])
                self.assertEqual(issue['pr_links'], [])

    def test_conventional_uppercase_and_explicit_browse_references_confirm_pairing(self):
        for field, text in [('title', 'ACM-123: fix checks'), ('head_ref', 'fix/ACM-123-checks'),
                            ('body', 'See https://jira.example.test/browse/acm-123')]:
            with self.subTest(field=field, text=text):
                data = self.capture()
                issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                issue.update(key='ACM-123', url='https://jira.example.test/browse/ACM-123')
                pr.update(title='', body='', head_ref='')
                pr[field] = text
                associate_work(data['jira'], data['github'])
                validate_links(data)
                self.assertEqual(pr['jira_links'][0]['key'], 'ACM-123')

    def test_native_jira_backreference_confirms_lowercase_branch_and_preserves_proof(self):
        for proof in ('description', 'comment'):
            with self.subTest(proof=proof):
                data = self.capture()
                issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                pr.update(title='', body='', head_ref='fix/test-123-checks')
                if proof == 'description':
                    issue['detail']['fields']['description'] = pr['url']
                    evidence = issue['url']
                else:
                    issue['detail']['fields']['comment']['comments'] = [{'id': '7', 'body': pr['url']}]
                    evidence = issue['url'] + '?focusedCommentId=7'
                issue['pr_links'] = [
                    {'url': pr['url'], 'proof': 'github_head_ref_key', 'evidence_url': pr['url'], 'sensitive': False},
                    {'url': pr['url'], 'proof': proof, 'evidence_url': evidence, 'sensitive': False}]
                associate_work(data['jira'], data['github'])
                validate_links(data)
                self.assertEqual(pr['jira_links'][0]['proof'], proof)
                self.assertEqual(issue['pr_links'][0]['evidence_url'], evidence)
                before = copy.deepcopy(data)
                associate_work(data['jira'], data['github'])
                self.assertEqual(data, before)

    def test_stale_lowercase_or_numeric_derived_proof_is_rejected_and_rebuilt(self):
        for field, text in [('head_ref', 'product-5-support'), ('body', 'plans/product-5-0-rollout.md'),
                            ('body', 'PRODUCT-5-0 rollout'), ('body', 'PRODUCT-5.0 rollout')]:
            with self.subTest(field=field, text=text):
                data = self.capture()
                issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                issue.update(key='PRODUCT-5', url='https://jira.example.test/browse/PRODUCT-5')
                pr.update(title='', body='', head_ref='')
                pr[field] = text
                issue['pr_links'] = [{'url': pr['url'], 'proof': 'github_' + field + '_key',
                                      'evidence_url': pr['url'], 'sensitive': False}]
                pr['jira_links'] = [{'key': issue['key'], 'url': issue['url'], 'proof': 'github_' + field + '_key',
                                     'evidence_url': pr['url'], 'sensitive': False}]
                with self.assertRaises(ValueError):
                    validate_links(data)
                associate_work(data['jira'], data['github'])
                self.assertEqual(issue['pr_links'], [])
                self.assertEqual(pr['jira_links'], [])
                validate_links(data)
                before = copy.deepcopy(data)
                associate_work(data['jira'], data['github'])
                self.assertEqual(data, before)

    def test_derived_reference_requires_known_proof_and_native_github_provenance(self):
        for field, value in [('proof', 'github_title'),
                             ('evidence_url', 'https://jira.example.test/browse/TEST-123')]:
            with self.subTest(field=field):
                data = self.capture()
                associate_work(data['jira'], data['github'])
                data['jira']['issues'][0]['pr_links'][0][field] = value
                data['github']['prs'][0]['jira_links'][0][field] = value
                with self.assertRaises(ValueError):
                    validate_links(data)

    def test_confirmed_browse_reference_requires_safe_configured_https_authority(self):
        for url in ('http://jira.example.test/browse/TEST-123',
                    'https://person@jira.example.test/browse/TEST-123',
                    'https://person:secret@jira.example.test/browse/TEST-123',
                    'https://jira.example.test:bad/browse/TEST-123',
                    'https://jira.example.test:65536/browse/TEST-123',
                    'https://jira.example.test/browse/../TEST-123',
                    'https://jira.example.test/browse/%2e%2e/TEST-123'):
            with self.subTest(url=url):
                data = self.capture()
                pr = data['github']['prs'][0]
                pr.update(title='', body=url)
                associate_work(data['jira'], data['github'])
                validate_links(data)
                self.assertEqual(pr['jira_links'], [])
        data = self.capture()
        issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
        data['jira']['metadata']['site'] = 'jira.example.test:8443'
        issue['url'] = 'https://jira.example.test:8443/browse/TEST-123'
        pr.update(title='', body='https://JIRA.example.test:8443/browse/test-123?view=compact#details')
        associate_work(data['jira'], data['github'])
        validate_links(data)
        self.assertEqual(pr['jira_links'][0]['key'], 'TEST-123')
        data['jira']['metadata']['site'] = 'jira.example.test:0'
        pr['body'] = 'https://jira.example.test:0/browse/TEST-123'
        associate_work(data['jira'], data['github'])
        self.assertEqual(pr['jira_links'], [])

    def test_version_tokens_do_not_seed_or_pair_a_real_ticket_with_the_same_prefix(self):
        for field in ('title', 'body', 'head_ref', 'indexed_head_ref'):
            for text in ('VERSION-4.20', 'version-4.20.1', 'fix/version-4.20-next'):
                with self.subTest(field=field, text=text):
                    data = self.capture()
                    issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                    issue.update(key='VERSION-4', url='https://jira.example.test/browse/VERSION-4')
                    pr.update(title='', body='', head_ref='')
                    if field == 'indexed_head_ref':
                        data['github']['pr_index'] = [{**pr, 'head_ref': text}]
                    else:
                        pr[field] = text
                    self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), [])
                    associate_work(data['jira'], data['github'])
                    validate_links(data)
                    self.assertEqual(pr['jira_links'], [])
                    self.assertEqual(issue['pr_links'], [])
                    proof_field = 'head_ref' if field == 'indexed_head_ref' else field
                    issue['pr_links'] = [{'url': pr['url'], 'proof': 'github_' + proof_field + '_key',
                                          'evidence_url': pr['url'], 'sensitive': False}]
                    with self.assertRaises(ValueError):
                        validate_links(data)
        for text in ('VERSION-4.', 'VERSION-4. More context.',
                     'VERSION-4/followup', 'VERSION-4.followup', 'VERSION-4: update',
                     'version-4.20; VERSION-4: genuine ticket'):
            with self.subTest(genuine_key=text):
                data = self.capture()
                issue, pr = data['jira']['issues'][0], data['github']['prs'][0]
                issue.update(key='VERSION-4', url='https://jira.example.test/browse/VERSION-4')
                pr.update(title=text, body='')
                self.assertEqual(referenced_keys(data['github'], 'jira.example.test'), ['VERSION-4'])
                associate_work(data['jira'], data['github'])
                validate_links(data)
                self.assertEqual(pr['jira_links'][0]['key'], 'VERSION-4')

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

    def test_missing_pr_reader_retains_context_without_claiming_a_native_attempt(self):
        data = self.capture()
        github, jira = data['github'], data['jira']
        known, unread = github['prs'][0]['url'], 'https://github.com/org/repo/pull/99'
        issue = jira['issues'][0]
        issue['detail']['fields']['description'] = 'Review context ' + unread
        issue['pr_links'] = [{'url': unread, 'proof': 'description', 'evidence_url': issue['url']}]
        jira['linked_prs'] = [known, unread, unread]
        before = copy.deepcopy(github)
        keys = []
        result = complete_references(jira, github, keys.extend)
        self.assertEqual(result['attempted_jira_keys'], ['TEST-123'])
        self.assertEqual(result['attempted_pr_urls'], [known])
        self.assertEqual(result['unread_pr_urls'], [unread])
        self.assertEqual(keys, ['TEST-123'])
        self.assertEqual(github, before)
        associate_work(jira, github)
        validate_links(data)
        self.assertIn(unread, [relation['url'] for relation in issue['pr_links']])
        self.assertFalse(any(item['url'] == unread for item in github['prs']))

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
