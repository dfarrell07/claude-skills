"""Preserve explicit cross-source references without inferring delivery or ownership."""

import re
from urllib.parse import quote, urlsplit

from evidence import HOST, is_pr_url

KEY = re.compile(r'(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*-[1-9][0-9]*)(?![A-Za-z0-9_]|\.[0-9])', re.I)
NON_JIRA = {'CVE', 'GHSA', 'RFC', 'ISO'}
# A backslash never appears unescaped in a URL; JSON-escaped text ("\\n") would otherwise join two.
URL = re.compile(r'https?://[^\s<>"\[\]()\\]+')
MARKDOWN_LINK = re.compile(r'\[[^\]\n]*\]\((https?://[^\s<>"()]+)\)')
UUID = re.compile(r'(?<![0-9a-f])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![0-9a-f])', re.I)
# A PR link may point into the PR (/files, /commits/...); GitLab project paths allow only [A-Za-z0-9_.-].
GITHUB_PR = re.compile(r'(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*)(?:/.*)?')
GITLAB_MR = re.compile(r'/(?:[A-Za-z0-9_.-]+/){2,}-/merge_requests/[1-9][0-9]*')


def urls(text):
    """Each URL in text with trailing punctuation stripped, as (original, parts); bad authorities skipped."""
    for match in URL.finditer(text or ''):
        original = match.group().rstrip('.,;:!?`\'’”')
        try:
            parts = urlsplit(original)
            # Accessing port also rejects invalid and out-of-range authorities.
            if parts.port is not None and not 1 <= parts.port <= 65535:
                continue
        except ValueError:
            continue
        yield original, parts


def code_urls(text, github_host=None):
    """HTTPS GitHub PR (on the configured host) and GitLab MR links: (provider, url, original)."""
    for original, parts in urls(text):
        if (parts.scheme != 'https' or not HOST.fullmatch(parts.netloc) or parts.username or parts.password
                or any(part in {'.', '..'} for part in parts.path.split('/'))):
            continue
        pull = GITHUB_PR.fullmatch(parts.path)
        if github_host and parts.netloc.casefold() == github_host.casefold() and pull:
            yield 'github', 'https://' + parts.netloc.lower() + pull.group(1), original
        elif GITLAB_MR.fullmatch(parts.path):
            yield 'gitlab', 'https://' + parts.netloc.lower() + parts.path, original


def jira_strings(value, sensitive=False):
    """Every string in a Jira value as (text, sensitive); a restricted object marks what it contains.

    Wiki markup separates link text from the URL with '|', so it becomes a space.
    """
    if isinstance(value, str):
        yield value.replace('|', ' '), sensitive
    elif isinstance(value, dict):
        fields = value.get('fields')
        for scope in [value] + ([fields] if isinstance(fields, dict) else []):
            labels = scope.get('labels')
            sensitive = sensitive or bool(scope.get('security') or scope.get('visibility')) or (
                isinstance(labels, list) and any(isinstance(label, str) and (
                    'security' in label.casefold() or label.upper().startswith('CVE-')) for label in labels))
        for child in value.values():
            yield from jira_strings(child, sensitive)
    elif isinstance(value, list):
        for child in value:
            yield from jira_strings(child, sensitive)


def github_reference_comments(github):
    """Yield saved discussion bodies and identity-bound current native bodies."""
    yield from github.get('reference_comments', [])
    for record in github.get('reference_reviews', []):
        source = record['source']
        yield {'item_url': record['item_url'], 'repo': record['repo'], 'number': record['number'],
               'kind': 'review', 'id': source['id'], 'node_id': source.get('node_id'),
               'url': source['html_url'], 'body': source['body'] or '',
               'author_login': (source['user'] or {}).get('login'),
               'author_node_id': (source['user'] or {}).get('node_id'), 'submitted_at': source['submitted_at']}


def _url_keys(text, site, confirmed=False):
    """An explicit authority is stronger evidence than a project-name heuristic."""
    explicit = set()
    for _, parts in urls(text):
        key = parts.path.removeprefix('/browse/')
        if (site and parts.netloc.casefold() == site.casefold()
                and (not confirmed or parts.scheme == 'https')
                and parts.path.startswith('/browse/') and KEY.fullmatch(key)):
            explicit.add(key.upper())
    return explicit


def _key_mentions(text, site, confirmed):
    """Separate discovery syntax from stronger native reference evidence."""
    text = text or ''
    explicit = _url_keys(text, site, confirmed)
    bare = UUID.sub(' ', URL.sub(' ', MARKDOWN_LINK.sub(' ', text)))
    for match in KEY.finditer(bare):
        key = match.group(1)
        # A numeric continuation belongs to the original identifier, such as
        # GO-2026-6354 or product-5-0; its truncated prefix is not a ticket lead.
        if (re.match(r'[.-][0-9]', bare[match.end():])
                or (confirmed and key != key.upper())):
            continue
        explicit.add(key.upper())
    return explicit


def confirmed_keys(text, site):
    """Accept native browse URLs or conventional uppercase ticket references."""
    return _key_mentions(text, site, confirmed=True)


def discovery_keys(text, site):
    """Keep broad ticket leads while recognizing explicit native URL authorities."""
    explicit = _url_keys(text, site)
    mentions = _key_mentions(text, site, confirmed=False)
    return {key for key in mentions
            if key.rsplit('-', 1)[0].upper() not in NON_JIRA or key in explicit}


def pr_identity(url):
    """GitHub treats owner and repository names case-insensitively."""
    parts = urlsplit(url)
    return parts.netloc.casefold(), parts.path.casefold()


def native_review_links(issue):
    """Keep explicit GitLab MR references as context without querying that host."""
    fields = (issue.get('detail') or {}).get('fields', {})
    sources = [('description', issue['url'], fields.get('description'), issue['sensitive'], {}),
               ('issue_link', issue['url'], fields.get('issuelinks'), issue['sensitive'], {})]
    for source_kind in ('detail', 'full_detail'):
        for field_id, value in (issue.get(source_kind) or {}).get('fields', {}).items():
            if re.fullmatch(r'customfield_[1-9][0-9]*', field_id):
                sources.append(('custom_field', issue['url'], value, issue['sensitive'],
                                {'field_id': field_id, 'source_kind': source_kind}))
    native = fields.get('comment')
    comments = native.get('comments') if isinstance(native, dict) else None
    comments = comments if isinstance(comments, list) else []
    for comment in comments:
        if isinstance(comment, dict) and isinstance(comment.get('id'), str):
            sources.append(('comment', issue['url'] + '?focusedCommentId=' + quote(comment['id'], safe=''),
                            comment.get('body'), issue['sensitive'] or bool(comment.get('visibility'))
                            or not isinstance(comment.get('author'), dict), {}))
    result = {}
    for proof, evidence_url, value, sensitive, extra in sources:
        for text, private in jira_strings(value, sensitive):
            for provider, url, original in code_urls(text):
                relation = {'url': url, 'source_url': original, 'provider': provider, 'proof': proof,
                            'evidence_url': evidence_url, 'sensitive': private, **extra}
                result[(url, proof, evidence_url, private, tuple(sorted(extra.items())))] = relation
    return [result[key] for key in sorted(result)]


def gitlab_texts(gitlab):
    for item in gitlab.get('items', []):
        for field in ('title', 'description'):
            yield item, field, item['url'], item['source'].get(field) or ''


def github_items(github):
    buckets = ('prs', 'issues', 'context_prs', 'context_issues', 'context_observations')
    return [item for bucket in buckets for item in github.get(bucket, [])]


def associate_work(jira, github):
    """Add symmetric links, retaining provenance of each explicit mention."""
    issues = {issue['key']: issue for issue in jira['issues']}
    site = jira.get('metadata', {}).get('site')
    ticket_aliases = jira.get('key_aliases', {})
    items = github_items(github)
    canonical = {pr_identity(item['url']): item['url'] for item in reversed(items) if is_pr_url(item['url'])}
    relations = {}
    for issue in issues.values():
        for pr in issue.get('pr_links', []):
            # Derived links must be rebuilt from current native observations.
            # Reusing them would preserve obsolete or weaker inferred proofs.
            if isinstance(pr.get('proof'), str) and pr['proof'].startswith('github_'):
                continue
            url = canonical.get(pr_identity(pr['url']), pr['url'])
            relation = {**pr, 'url': url, 'sensitive': bool(pr.get('sensitive', issue['sensitive']) or issue['sensitive'])}
            if pr['url'] != url:
                relation['source_url'] = pr['url']
            relations.setdefault((issue['key'], url), relation)
    for item in items:
        if not is_pr_url(item['url']):
            continue
        for field in ('title', 'body'):
            for key in confirmed_keys(item.get(field), site):
                source_key = key.upper()
                key = ticket_aliases.get(source_key, source_key)
                issue = issues.get(key)
                if issue is None:
                    continue
                relation = {'url': item['url'], 'proof': 'github_' + field + '_key',
                            'evidence_url': item['url'], 'sensitive': issue['sensitive']}
                if key != source_key:
                    relation.update(source_key=source_key, jira_source_url=f'https://{site}/browse/{source_key}')
                relations.setdefault((key, item['url']), relation)
    for comment in github_reference_comments(github):
        url = canonical.get(pr_identity(comment['item_url']))
        if url is None:
            continue
        for source_key in confirmed_keys(comment['body'], site):
            key = ticket_aliases.get(source_key, source_key)
            issue = issues.get(key)
            if issue is not None:
                relation = {'url': url, 'proof': 'github_comment_key', 'evidence_url': comment['url'],
                            'sensitive': issue['sensitive']}
                if key != source_key:
                    relation.update(source_key=source_key, jira_source_url=f'https://{site}/browse/{source_key}')
                relations.setdefault((key, url), relation)
    # The same PR may have several observed snapshots. Attach the same linked
    # ticket context to each while leaving original fields and states intact.
    for item in items:
        item['jira_links'] = [{'key': key, 'url': issues[key]['url'], 'proof': proof['proof'],
                               'evidence_url': proof['evidence_url'], 'sensitive': proof.get('sensitive', issues[key]['sensitive']),
                               **{field: proof[field] for field in ('source_key', 'jira_source_url', 'field_id', 'source_kind') if field in proof}}
                              for (key, url), proof in sorted(relations.items())
                              if pr_identity(url) == pr_identity(item['url'])]
    for issue in issues.values():
        issue['pr_links'] = [proof for (key, url), proof in sorted(relations.items()) if key == issue['key']]
        issue['review_links'] = native_review_links(issue)


def associate_gitlab(jira, gitlab):
    issues = {issue['key']: issue for issue in jira.get('issues', [])}
    site = jira.get('metadata', {}).get('site')
    aliases = jira.get('key_aliases', {})
    relations = {item['url']: {} for item in gitlab.get('items', [])}
    for item, field, url, text in gitlab_texts(gitlab):
        for source_key in confirmed_keys(text, site):
            key = aliases.get(source_key, source_key)
            if key in issues:
                relation = {'key': key, 'url': issues[key]['url'], 'proof': 'gitlab_' + field + '_key',
                            'evidence_url': url, 'sensitive': issues[key]['sensitive']}
                if key != source_key:
                    relation.update(source_key=source_key, jira_source_url=f'https://{site}/browse/{source_key}')
                relations[item['url']].setdefault(key, relation)
    for item in gitlab.get('items', []):
        item['jira_links'] = [relations[item['url']][key] for key in sorted(relations[item['url']])]
