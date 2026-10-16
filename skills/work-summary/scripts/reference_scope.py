"""Keep reference reads near personal work without limiting the number of work roots.

Root reviews may discover tickets and their implementation reviews. Context reviews
may resolve their own tickets, but those final tickets do not start another crawl.
Existing attributed activity can promote any context node to an independent root.
"""

from urllib.parse import urlsplit

from evidence import timestamp
from extended_evidence import jira_key_aliases, repository_aliases
from jira_public_history import public_field_events


DERIVED_ROLES = {'jira_link', 'referenced_by_github'}
PERSONAL_ACTIONS = {'opened', 'closed', 'reopened', 'merged', 'committed',
                    'issue_comment', 'issue_comment_edited', 'review',
                    'review_comment', 'review_comment_edited'}


class ReferenceScope:
    def __init__(self, jira, github, branches=(), *, state=None, since=None, until=None,
                 github_host=None):
        self.site = jira.get('metadata', {}).get('site') or ''
        self.since = since if since is not None else (state or {}).get('since')
        self.until = until if until is not None else (state or {}).get('until')
        samples = github.get('prs', []) + github.get('issues', []) + github.get('context_prs', [])
        self.github_host = (github_host or (state or {}).get('github_host') or jira.get('metadata', {}).get('github_host')
                            or (urlsplit(samples[0]['url']).netloc if samples else 'github.com')).casefold()
        self.nodes, self.edges = {}, {}
        self.revision = 0
        self.review_aliases, self.ticket_aliases, self.repo_aliases = {}, {}, {}
        self.attempted_keys, self.attempted_prs = set(), set()
        self.unread_prs, self.attempted_gitlab = set(), set()
        if state:
            for node in state.get('nodes', []):
                self.nodes[(node['kind'], node['identity'])] = dict(node)
            for edge in state.get('edges', []):
                self.edges[self._edge_key(edge)] = dict(edge)
            for name in ('attempted_keys', 'attempted_prs', 'unread_prs', 'attempted_gitlab'):
                getattr(self, name).update(state.get(name, []))
        self.refresh(jira, github)
        # These are native discovery roots, not accomplishment claims. Partial
        # event reads do not disqualify independently captured role candidates.
        for bucket in ('prs', 'issues', 'context_prs', 'context_issues'):
            for item in github.get(bucket, []):
                if bucket in ('prs', 'issues') or set(item.get('roles', [])) - DERIVED_ROLES:
                    self.root('review', item['url'], 'personal_inventory')
        for issue in jira.get('issues', []):
            roles = set(issue.get('roles', []))
            if (not roles and not state) or roles - DERIVED_ROLES:
                self.root('ticket', issue['key'], 'personal_inventory')
        for branch in branches:
            for item in branch.get('merge_requests', []):
                self.root('review', item['url'], 'local_branch_association')
            for item in branch.get('prs', []):
                if (item.get('proof') not in {'name_only', 'head_name_only'}
                        and branch.get('attribution') in {'configured_or_verified_author',
                                                          'contains_configured_or_verified_author'}):
                    self.root('review', item['url'], 'local_branch_association')

    @staticmethod
    def _edge_key(edge):
        return (edge['source_kind'], edge['source'], edge['target_kind'],
                edge['target'], edge['evidence_url'])

    def identity(self, kind, value):
        if kind == 'ticket':
            key = value.upper()
            return self.ticket_aliases.get(key, key)
        parts = urlsplit(value)
        path = parts.path
        if parts.netloc.casefold() == self.github_host:
            pieces = path.split('/')
            if len(pieces) >= 3:
                pieces[1:3] = [part.casefold() for part in pieces[1:3]]
                repo = '/'.join(pieces[1:3])
                seen = set()
                while (parts.netloc.casefold(), repo) in self.repo_aliases and repo not in seen:
                    seen.add(repo)
                    repo = self.repo_aliases[(parts.netloc.casefold(), repo)]
                pieces[1:3] = repo.split('/')
            path = '/'.join(pieces)
        url = 'https://' + parts.netloc.casefold() + path
        seen = set()
        while url in self.review_aliases and url not in seen:
            seen.add(url)
            url = self.review_aliases[url]
        return url

    def node(self, kind, value):
        identity = self.identity(kind, value)
        return self.nodes.setdefault((kind, identity), {'kind': kind, 'identity': identity,
            'root': False, 'can_read_reviews': False, 'can_read_tickets': False, 'reasons': []})

    def root(self, kind, value, reason):
        node = self.node(kind, value)
        if not node['root']:
            self.revision += 1
        node['root'] = True
        if reason not in node['reasons']:
            node['reasons'].append(reason)

    def _in_window(self, value):
        try:
            when = timestamp(value)
            def boundary(value):
                return timestamp(value.isoformat() if hasattr(value, 'isoformat') else value)
            return ((self.since is None or boundary(self.since) <= when)
                    and (self.until is None or when <= boundary(self.until)))
        except (ValueError, TypeError, AttributeError):
            return False

    def refresh(self, jira, github, gitlab=None):
        self.ticket_aliases.update(jira_key_aliases(jira))
        records = github.get('repository_aliases', [])
        host = records[0]['host'] if records else self.github_host
        self.repo_aliases.update(repository_aliases(github, host))
        for item in (gitlab or {}).get('items', []):
            for requested in item.get('requested_urls', []):
                original = self.identity('review', requested)
                canonical = self.identity('review', item['url'])
                if original != canonical:
                    self.review_aliases[original] = canonical
        # Alias resolution changes identity, never consumes a counterpart stage.
        for key, old in list(self.nodes.items()):
            new_identity = self.identity(old['kind'], old['identity'])
            if new_identity == old['identity']:
                continue
            del self.nodes[key]
            new = self.node(old['kind'], new_identity)
            for flag in ('root', 'can_read_reviews', 'can_read_tickets'):
                new[flag] |= old[flag]
            new['reasons'] = sorted(set(new['reasons']) | set(old['reasons']))
        for old_key, edge in list(self.edges.items()):
            del self.edges[old_key]
            edge['source'] = self.identity(edge['source_kind'], edge['source'])
            edge['target'] = self.identity(edge['target_kind'], edge['target'])
            self.edges[self._edge_key(edge)] = edge
        # These activity arrays are already actor- and window-qualified by their
        # native collectors. Passive timeline events must not promote a node.
        for event in github.get('activity', []):
            if event.get('action') in PERSONAL_ACTIONS and self._in_window(event.get('at')):
                self.root('review', event['item_url'], 'native_personal_activity')
        for event in jira.get('activity', []):
            if event.get('kind') in {'comment_created', 'comment_edited'} and self._in_window(event.get('occurred_at')):
                self.root('ticket', event['key'], 'native_personal_activity')
        if self.since is not None and self.until is not None:
            def boundary(value):
                return timestamp(value.isoformat() if hasattr(value, 'isoformat') else value)
            candidates = {**jira, 'issues': [issue for issue in jira.get('issues', [])
                if not self.node('ticket', issue['key'])['root']]}
            for event in public_field_events(candidates, boundary(self.since), boundary(self.until)):
                self.root('ticket', event['key'], 'native_personal_field_activity')
            identity = jira.get('metadata', {})
            if identity.get('identity_verified') is True and identity.get('account_id'):
                for issue in candidates['issues']:
                    fields = (issue.get('detail') or issue.get('source') or {}).get('fields', {})
                    if ((fields.get('creator') or {}).get('accountId') == identity['account_id']
                            and self._in_window(fields.get('created'))):
                        self.root('ticket', issue['key'], 'native_personal_creation')
        self._gitlab_roots(gitlab or {})

    def _gitlab_roots(self, gitlab):
        identities = {(row['host'].casefold(), row['user_id'])
                      for row in gitlab.get('metadata', {}).get('identities', [])}
        for item in gitlab.get('items', []):
            source = item.get('source', {})
            host = item.get('host') or urlsplit(item['url']).netloc
            actors = {actor for native_host, actor in identities if native_host == host.casefold()}
            if not actors:
                continue
            author = (source.get('author') or {}).get('id')
            assigned = {row.get('id') for row in source.get('assignees', [])}
            reviewers = {row.get('id') for row in source.get('reviewers', [])}
            try:
                updated = timestamp(source.get('updated_at'))
                since = (timestamp(self.since.isoformat() if hasattr(self.since, 'isoformat') else self.since)
                         if self.since is not None else None)
                authored_candidate = since is None or updated >= since
            except (ValueError, TypeError):
                authored_candidate = False
            if ((author in actors and (authored_candidate or source.get('state') == 'opened'))
                    or source.get('state') == 'opened' and actors & (assigned | reviewers)):
                self.root('review', item['url'], 'native_personal_inventory')
            for note in item.get('notes', []):
                principals = ({f'gid://gitlab/User/{actor}' for actor in actors}
                              if note.get('source_kind') == 'graphql' else actors)
                actor = (note.get('author') or {}).get('id')
                if (self.since is not None and self.until is not None
                        and note.get('system') is False and actor in principals
                        and self._in_window(note.get('created_at'))):
                    self.root('review', item['url'], 'native_personal_activity')
                if (self.since is not None and self.until is not None
                        and note.get('source_kind') == 'graphql' and note.get('system') is False
                        and (note.get('last_edited_by') or {}).get('id') in principals
                        and self._in_window(note.get('last_edited_at'))):
                    self.root('review', item['url'], 'native_personal_edit')

    def allow(self, source_kind, source, target_kind, target, evidence_url=None):
        origin = self.node(source_kind, source)
        destination = self.node(target_kind, target)
        allowed = False
        if origin['root']:
            allowed, reason = True, 'direct_work_reference'
            if target_kind == 'ticket':
                if not destination['can_read_reviews']:
                    self.revision += 1
                destination['can_read_reviews'] = True
            else:
                if not destination['can_read_tickets']:
                    self.revision += 1
                destination['can_read_tickets'] = True
        elif source_kind == 'ticket' and origin['can_read_reviews'] and target_kind == 'review':
            allowed, reason = True, 'ticket_review_counterpart'
            if not destination['can_read_tickets']:
                self.revision += 1
            destination['can_read_tickets'] = True
        elif source_kind == 'review' and origin['can_read_tickets'] and target_kind == 'ticket':
            allowed, reason = True, 'review_ticket_pair_completion'
        elif destination['root']:
            allowed, reason = True, 'independent_work_root'
        else:
            reason = 'context_reference_outside_work_counterpart_scope'
        edge = {'source_kind': source_kind, 'source': origin['identity'],
                'target_kind': target_kind, 'target': destination['identity'],
                'evidence_url': evidence_url or source,
                'disposition': 'eligible' if allowed else 'deferred', 'reason': reason}
        self.edges[self._edge_key(edge)] = edge
        return allowed

    def snapshot(self):
        edges = sorted(self.edges.values(), key=self._edge_key)
        return {'policy': 'personal_work_and_direct_counterpart_pairs',
                'since': self.since.isoformat() if hasattr(self.since, 'isoformat') else self.since,
                'until': self.until.isoformat() if hasattr(self.until, 'isoformat') else self.until,
                'github_host': self.github_host,
                'nodes': sorted(self.nodes.values(), key=lambda n: (n['kind'], n['identity'])),
                'edges': edges, 'deferred_targets': [row for row in edges if row['disposition'] == 'deferred'],
                **{name: sorted(getattr(self, name)) for name in
                   ('attempted_keys', 'attempted_prs', 'unread_prs', 'attempted_gitlab')}}
