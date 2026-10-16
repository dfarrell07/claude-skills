"""Preserve explicit cross-source references without inferring delivery or ownership."""

import re
from urllib.parse import quote, urlsplit

from evidence import is_pr_url, require

KEY = re.compile(r'(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*-[1-9][0-9]*)(?![A-Za-z0-9_])', re.I)
NON_JIRA = {'CVE', 'GHSA', 'RFC', 'ISO'}
URL = re.compile(r'https?://[^\s<>"\[\]()]+')
MARKDOWN_LINK = re.compile(r'\[[^\]\n]*\]\((https?://[^\s<>"()]+)\)')


def mentioned_keys(text, site):
    """A foreign browse URL is not a bare-key reference to this Jira site."""
    text = text or ''
    explicit = set()
    for match in URL.finditer(text):
        parts = urlsplit(match.group().rstrip('.,;:!?`\'’”'))
        key = parts.path.removeprefix('/browse/')
        if parts.hostname == site and parts.path.startswith('/browse/') and KEY.fullmatch(key):
            explicit.add(key.upper())
    bare = URL.sub(' ', MARKDOWN_LINK.sub(' ', text))
    return explicit | {key.upper() for key in KEY.findall(bare)}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def native_pr_mention(value, url):
    return any(re.search(re.escape(url) + r'(?![0-9])', text, re.I) for text in strings(value))


def pr_identity(url):
    parts = urlsplit(url)
    return parts.netloc.casefold(), parts.path.casefold()


def github_items(github, include_index=True):
    buckets = ('prs', 'issues', 'context_prs', 'context_issues') + (('pr_index',) if include_index else ())
    return [item for bucket in buckets for item in github.get(bucket, [])]


def referenced_keys(github, site=None):
    # Old indexed snapshots are association context, not a reason to retrieve
    # every historical ticket mentioned across an account's entire PR history.
    result = set()
    selected = github_items(github, include_index=False)
    selected_urls = {item['url'] for item in selected}
    selected += [item for item in github.get('pr_index', []) if item['url'] in selected_urls]
    for item in selected:
        for field in ('title', 'body', 'head_ref'):
            for key in mentioned_keys(item.get(field), site):
                if key.rsplit('-', 1)[0].upper() not in NON_JIRA:
                    result.add(key.upper())
    return sorted(result)


def complete_references(jira, github, read_tickets, read_prs=None):
    """Follow explicit captured references to exhaustion, attempting failures once."""
    attempted_keys, attempted_urls = set(), {item['url'] for item in github_items(github, include_index=False)}
    while True:
        keys = set(referenced_keys(github, jira.get('metadata', {}).get('site'))) - attempted_keys
        urls = set(jira.get('linked_prs', [])) - attempted_urls
        if not keys and not urls:
            break
        if keys:
            attempted_keys.update(keys)
            read_tickets(sorted(keys))
        urls = set(jira.get('linked_prs', [])) - attempted_urls
        attempted_urls.update(urls)
        if urls and read_prs is not None:
            read_prs(sorted(urls))
    return {'attempted_jira_keys': sorted(attempted_keys), 'attempted_pr_urls': sorted(attempted_urls)}


def associate_work(jira, github):
    """Add symmetric links, retaining provenance of each explicit mention."""
    issues = {issue['key']: issue for issue in jira['issues']}
    site = jira.get('metadata', {}).get('site')
    items = github_items(github)
    canonical = {pr_identity(item['url']): item['url'] for item in reversed(items) if is_pr_url(item['url'])}
    relations = {}
    for issue in issues.values():
        for pr in issue.get('pr_links', []):
            url = canonical.get(pr_identity(pr['url']), pr['url'])
            relation = {**pr, 'url': url}
            if pr['url'] != url:
                relation['source_url'] = pr['url']
            relations.setdefault((issue['key'], url), relation)
    for item in items:
        if not is_pr_url(item['url']):
            continue
        for field in ('title', 'body', 'head_ref'):
            for key in mentioned_keys(item.get(field), site):
                key = key.upper()
                issue = issues.get(key)
                if issue is None:
                    continue
                relations.setdefault((key, item['url']), {'url': item['url'], 'proof': 'github_' + field + '_key',
                                                         'evidence_url': item['url'], 'sensitive': issue['sensitive']})
    # The same PR may have several observed snapshots. Attach the same linked
    # ticket context to each while leaving original fields and states intact.
    for item in items:
        item['jira_links'] = [{'key': key, 'url': issues[key]['url'], 'proof': proof['proof'],
                               'evidence_url': proof['evidence_url'], 'sensitive': proof.get('sensitive', issues[key]['sensitive'])}
                              for (key, url), proof in sorted(relations.items())
                              if pr_identity(url) == pr_identity(item['url'])]
    for issue in issues.values():
        issue['pr_links'] = [proof for (key, url), proof in sorted(relations.items()) if key == issue['key']]


def validate_links(data):
    jira = data.get('jira', {'issues': []})
    issues = {issue['key']: issue for issue in jira['issues']}
    items = github_items(data['github'])
    by_url = {}
    by_identity = {}
    for item in items:
        by_url.setdefault(item['url'], []).append(item)
        by_identity.setdefault(pr_identity(item['url']), []).append(item)
    for issue in issues.values():
        links = issue.get('pr_links', [])
        require(isinstance(links, list), 'Invalid Jira PR links')

        for relation in links:
            require(isinstance(relation, dict) and isinstance(relation.get('url'), str), 'Invalid linked PR')
            require(not issue['sensitive'] or relation.get('sensitive') is True, 'Restricted relation must be marked private')
            parts = urlsplit(relation['url'])
            require(parts.scheme == 'https' and parts.netloc == data['metadata'].get('github_host', 'github.com')
                    and re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*', parts.path)
                    and not parts.query and not parts.fragment, 'Invalid Jira PR URL')
            require(isinstance(relation.get('proof'), str) and relation['proof'], 'Missing cross-source proof')
            if 'source_url' in relation:
                require(isinstance(relation['source_url'], str)
                        and pr_identity(relation['source_url']) == pr_identity(relation['url']), 'PR source identity mismatch')
            evidence_url = relation.get('evidence_url')
            require(isinstance(evidence_url, str) and (evidence_url == issue['url']
                    or evidence_url.startswith(issue['url'] + '?focusedCommentId=')
                    or evidence_url == relation['url']), 'Invalid cross-source provenance')
            if relation['proof'].startswith('github_'):
                field = relation['proof'].removeprefix('github_').removesuffix('_key')
                require(field in {'title', 'body', 'head_ref'} and any(issue['key'] in
                    mentioned_keys(item.get(field), jira.get('metadata', {}).get('site'))
                    for item in by_identity.get(pr_identity(relation['url']), [])), 'GitHub key mention absent from evidence')
            else:
                fields = (issue.get('detail') or {}).get('fields', {})
                proof = relation['proof']
                if proof in {'description', 'issue_link'}:
                    value = fields.get('description' if proof == 'description' else 'issuelinks')
                    require(evidence_url == issue['url'] and native_pr_mention(value, relation['url']),
                            'Jira PR mention absent from native evidence')
                elif proof == 'comment':
                    comments = list((fields.get('comment') or {}).get('comments', []))
                    for page in issue.get('comment_pages', []):
                        comments.extend(page.get('comments', []))
                    require(any(isinstance(comment, dict) and (not comment.get('visibility') or relation.get('sensitive') is True)
                                and isinstance(comment.get('id'), str)
                                and evidence_url == issue['url'] + '?focusedCommentId=' + quote(comment['id'], safe='')
                                and native_pr_mention(comment.get('body'), relation['url']) for comment in comments),
                            'Jira PR mention absent from native comment')
                else:
                    require(False, 'Unknown cross-source proof')
    for item in items:
        require(isinstance(item.get('jira_links', []), list), 'Invalid GitHub Jira links')
        for relation in item.get('jira_links', []):
            issue = issues.get(relation.get('key'))
            require(issue is not None and relation.get('url') == issue['url'] and any(
                    pr_identity(pr['url']) == pr_identity(item['url']) and pr['proof'] == relation.get('proof')
                    and pr['evidence_url'] == relation.get('evidence_url')
                    and pr.get('sensitive', issue['sensitive']) == relation.get('sensitive', issue['sensitive'])
                    for pr in issue.get('pr_links', [])),
                    'GitHub Jira relation is absent from source issue')
