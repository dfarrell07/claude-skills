"""Preserve explicit cross-source references without inferring delivery or ownership."""

import re
from urllib.parse import quote, urlsplit

from evidence import is_pr_url
from extended_evidence import github_reference_comments, jira_key_aliases

KEY = re.compile(r'(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*-[1-9][0-9]*)(?![A-Za-z0-9_]|\.[0-9])', re.I)
NON_JIRA = {'CVE', 'GHSA', 'RFC', 'ISO'}
URL = re.compile(r'https?://[^\s<>"\[\]()]+')
MARKDOWN_LINK = re.compile(r'\[[^\]\n]*\]\((https?://[^\s<>"()]+)\)')
UUID = re.compile(r'(?<![0-9a-f])[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}(?![0-9a-f])', re.I)


def _url_keys(text, site, confirmed=False):
    """An explicit authority is stronger evidence than a project-name heuristic."""
    explicit = set()
    for match in URL.finditer(text or ''):
        try:
            parts = urlsplit(match.group().rstrip('.,;:!?`\'’”'))
            # Accessing port also rejects invalid and out-of-range authorities.
            if parts.port is not None and not 1 <= parts.port <= 65535:
                continue
        except ValueError:
            continue
        key = parts.path.removeprefix('/browse/')
        if (site and parts.netloc.casefold() == site.casefold()
                and (not confirmed or parts.scheme == 'https')
                and parts.path.startswith('/browse/') and KEY.fullmatch(key)):
            explicit.add(key.upper())
    return explicit


def _key_mentions(text, site, confirmed, *, numeric_fragments=False):
    """Separate discovery syntax from stronger native reference evidence."""
    text = text or ''
    explicit = _url_keys(text, site, confirmed)
    bare = UUID.sub(' ', URL.sub(' ', MARKDOWN_LINK.sub(' ', text)))
    for match in KEY.finditer(bare):
        key = match.group(1)
        # A numeric continuation belongs to the original identifier, such as
        # GO-2026-6354 or product-5-0; its truncated prefix is not a ticket lead.
        if ((not numeric_fragments and re.match(r'[.-][0-9]', bare[match.end():]))
                or (confirmed and key != key.upper())):
            continue
        explicit.add(key.upper())
    return explicit


def confirmed_keys(text, site):
    """Accept native browse URLs or conventional uppercase ticket references."""
    return _key_mentions(text, site, confirmed=True)


def discovery_keys(text, site, *, include_numeric_fragments=False):
    """Keep broad ticket leads while recognizing explicit native URL authorities."""
    explicit = _url_keys(text, site)
    mentions = _key_mentions(text, site, confirmed=False, numeric_fragments=include_numeric_fragments)
    return {key for key in mentions
            if key.rsplit('-', 1)[0].upper() not in NON_JIRA or key in explicit}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def pr_identity(url):
    """GitHub treats owner and repository names case-insensitively."""
    parts = urlsplit(url)
    return parts.netloc.casefold(), parts.path.casefold()


def native_review_links(issue):
    """Keep explicit GitLab MR references as context without querying that host."""
    def values(value, sensitive):
        if isinstance(value, str):
            yield value, sensitive
        elif isinstance(value, dict):
            labels = value.get('labels')
            private = sensitive or bool(value.get('security') or value.get('visibility')) or (
                isinstance(labels, list) and any(isinstance(label, str) and (
                    'security' in label.casefold() or label.upper().startswith('CVE-')) for label in labels))
            for child in value.values():
                yield from values(child, private)
        elif isinstance(value, list):
            for child in value:
                yield from values(child, sensitive)

    fields = (issue.get('detail') or {}).get('fields', {})
    sources = [('description', issue['url'], fields.get('description'), issue['sensitive'], {}),
               ('issue_link', issue['url'], fields.get('issuelinks'), issue['sensitive'], {})]
    for source_kind in ('detail', 'full_detail'):
        for field_id, value in (issue.get(source_kind) or {}).get('fields', {}).items():
            if re.fullmatch(r'customfield_[1-9][0-9]*', field_id):
                sources.append(('custom_field', issue['url'], value, issue['sensitive'],
                                {'field_id': field_id, 'source_kind': source_kind}))
    comments = list((fields.get('comment') or {}).get('comments', []))
    for page in issue.get('comment_pages', []):
        comments.extend(page.get('comments', []))
    for comment in comments:
        if isinstance(comment, dict) and isinstance(comment.get('id'), str):
            sources.append(('comment', issue['url'] + '?focusedCommentId=' + quote(comment['id'], safe=''),
                            comment.get('body'), issue['sensitive'] or bool(comment.get('visibility'))
                            or not isinstance(comment.get('author'), dict), {}))
    result = {}
    for proof, evidence_url, value, sensitive, extra in sources:
        for text, private in values(value, sensitive):
            for match in URL.finditer(text.replace('|', ' ')):
                original = match.group().rstrip('.,;:!?`\'’”')
                try:
                    parts = urlsplit(original)
                    if parts.port is not None and not 1 <= parts.port <= 65535:
                        continue
                except ValueError:
                    continue
                if (parts.scheme != 'https' or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?', parts.netloc)
                        or not re.fullmatch(r'/(?:[A-Za-z0-9_.~-]+/){2,}-/merge_requests/[1-9][0-9]*', parts.path)
                        or any(part in {'.', '..'} for part in parts.path.split('/'))):
                    continue
                url = 'https://' + parts.netloc + parts.path
                relation = {'url': url, 'source_url': original, 'provider': 'gitlab', 'proof': proof,
                            'evidence_url': evidence_url, 'sensitive': private, **extra}
                result[(url, proof, evidence_url, private, tuple(sorted(extra.items())))] = relation
    return [result[key] for key in sorted(result)]


def github_items(github):
    buckets = ('prs', 'issues', 'context_prs', 'context_issues', 'context_observations')
    return [item for bucket in buckets for item in github.get(bucket, [])]


def associate_work(jira, github):
    """Add symmetric links, retaining provenance of each explicit mention."""
    issues = {issue['key']: issue for issue in jira['issues']}
    site = jira.get('metadata', {}).get('site')
    ticket_aliases = jira_key_aliases(jira)
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


