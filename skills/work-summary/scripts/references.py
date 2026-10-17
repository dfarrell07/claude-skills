"""Follow links one hop out from the user's own work.

The tickets named by PRs and issues the user worked on, the GitLab MRs those
tickets and PRs list, and the tickets those MRs name. Nothing else is read:
references in old or unrelated items are context, not reasons to crawl further.
"""

from extended_evidence import github_reference_comments
from provider_references import associate_gitlab, code_urls, gitlab_texts
from work_links import discovery_keys, github_items, native_review_links


def active_items(github):
    """GitHub items with the user's own events in the window, or authored and updated in it."""
    urls = {event['item_url'] for event in github.get('activity', [])}
    urls.update(item['url'] for item in github.get('prs', []) + github.get('issues', []))
    return [item for item in github_items(github) if item['url'] in urls]


def item_texts(github, items):
    urls = {item['url'] for item in items}
    texts = [item.get(field) or '' for item in items for field in ('title', 'body', 'head_ref')]
    texts += [comment['body'] for comment in github_reference_comments(github) if comment.get('item_url') in urls]
    return texts


def referenced_keys(github, site=None, items=None):
    """Ticket keys named by the given items (default: all selected) and their discussion."""
    items = github_items(github) if items is None else items
    return sorted({key for text in item_texts(github, items) for key in discovery_keys(text, site)})


def active_tickets(jira):
    """Tickets the user commented on, updated, or that their PRs name."""
    commented = {event['key'] for event in jira.get('activity', [])}
    return [issue for issue in jira.get('issues', [])
            if issue['key'] in commented or {'updated_by', 'referenced_by_github'} & set(issue.get('roles', []))]


def follow_references(jira, github, branches, read_tickets, read_gitlab, read_history=None, github_host='github.com'):
    site = jira.get('metadata', {}).get('site')
    items = active_items(github)
    texts = item_texts(github, items)
    keys = referenced_keys(github, site, items)
    if keys:
        read_tickets(keys)
    tickets = active_tickets(jira)
    if read_history is not None:
        read_history(tickets)
    targets = {link['url'] for issue in tickets for link in native_review_links(issue) if link['provider'] == 'gitlab'}
    targets.update(link['url'] for issue in tickets for link in issue.get('history_review_links', [])
                   if link['provider'] == 'gitlab')
    targets.update(mr['url'] for branch in branches for mr in branch.get('merge_requests', []))
    targets.update(url for text in texts for provider, url in code_urls(text, github_host) if provider == 'gitlab')
    gitlab = read_gitlab(sorted(targets))
    known = {issue['key'] for issue in jira.get('issues', [])}
    mr_keys = sorted({key for _, _, _, text, _ in gitlab_texts(gitlab)
                      for key in discovery_keys(text, site)} - known - set(keys))
    if mr_keys:
        read_tickets(mr_keys)
    associate_gitlab(jira, gitlab)
    return gitlab, {'jira_keys': sorted(set(keys) | set(mr_keys)), 'gitlab_urls': sorted(targets)}
