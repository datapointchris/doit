"""The open pull requests on every account doit's GitHub App is installed on.

The App signs an assertion with its private key, lists its installations, and
mints a token for each that asks for `pull_requests: read` and `metadata: read`
and nothing more. One issue search per installation then finds every open pull
request on the account's repositories, a page of a hundred at a time, however
many repositories it holds.

The rows carry the fields the `prs` adapter reads. A pull request someone opened
on a repository the App is not installed on is not among them, because the App
can read only where it is installed. An account whose search came back
incomplete, or past the thousand results search answers, is named in the
sentence beside the rows, so the lane reads as partial rather than whole.

The answer is kept for `CACHE_SECONDS`, however often a dashboard asks.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import httpx2
import jwt

from ..settings import USER_AGENT
from ..settings import GitHubSettings
from .apps import ReadFailed

API = 'https://api.github.com'
TIMEOUT_SECONDS = 15.0
PERMISSIONS = {'pull_requests': 'read', 'metadata': 'read'}
CACHE_SECONDS = 300
PAGE_SIZE = 100
# The search API answers at most a thousand results, ten pages of a hundred.
MOST_PAGES = 10


def age_days(created_at: str, now: dt.datetime) -> int:
    created = dt.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    return max(int((now - created).total_seconds() // 86400), 0)


def pull_request_row(item: dict, now: dt.datetime) -> dict:
    """One search result as a lane reads it: oldest first by `age_days`, named by repository."""
    slug = '/'.join(str(item.get('repository_url', '')).rsplit('/', 2)[-2:])
    user = item.get('user') or {}
    return {
        'repo': slug.rsplit('/', 1)[-1],
        'slug': slug,
        'number': item.get('number'),
        'title': item.get('title', ''),
        'author': user.get('login', ''),
        'bot': user.get('type') == 'Bot',
        'draft': bool(item.get('draft')),
        'created_at': item.get('created_at'),
        'age_days': age_days(item['created_at'], now) if item.get('created_at') else 0,
        'url': item.get('html_url', ''),
    }


class GitHub:
    """GitHub, reached as doit's App."""

    def __init__(self, settings: GitHubSettings, client: httpx2.Client | None = None, clock=time.time) -> None:
        self.settings = settings
        self.client = client or httpx2.Client(timeout=TIMEOUT_SECONDS)
        self.clock = clock
        self.lock = threading.Lock()
        self.kept: tuple[float, list[dict], str] | None = None

    def call(self, method: str, path: str, bearer: str, what: str, **keywords) -> dict | list:
        headers = {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {bearer}',
            'User-Agent': USER_AGENT,
            'X-GitHub-Api-Version': '2022-11-28',
        }
        try:
            response = self.client.request(method, API + path, headers=headers, timeout=TIMEOUT_SECONDS, **keywords)
        except httpx2.TimeoutException as error:
            raise ReadFailed(f'GitHub did not answer {what} within {TIMEOUT_SECONDS:g}s', timed_out=True) from error
        except httpx2.HTTPError as error:
            raise ReadFailed(f'GitHub could not be reached for {what}: {error}') from error
        if response.status_code >= 400:
            try:
                message = response.json().get('message', '')
            except ValueError:
                message = ''
            raise ReadFailed(f'GitHub answered {response.status_code} to {what}' + (f': {message}' if message else ''))
        try:
            return response.json()
        except ValueError as error:
            raise ReadFailed(f'GitHub answered {what} with a body that is not JSON') from error

    def assertion(self) -> str:
        issued = int(self.clock())
        try:
            return jwt.encode({'iat': issued - 60, 'exp': issued + 540, 'iss': self.settings.client_id}, self.settings.private_key, 'RS256')
        except (ValueError, TypeError) as error:
            raise ReadFailed(f'DOIT_API_GITHUB_PRIVATE_KEY does not hold a key that signs RS256: {error}') from error

    def open_pull_requests(self, now: dt.datetime) -> tuple[list[dict], str]:
        """Every open pull request the App can see, oldest first, and a sentence naming any account whose list was cut."""
        if self.settings.missing:
            raise ReadFailed(f'GitHub is not configured on the service: {self.settings.missing} unset')
        with self.lock:
            if self.kept is not None and self.clock() - self.kept[0] < CACHE_SECONDS:
                return self.kept[1], self.kept[2]
            assertion = self.assertion()
            rows: list[dict] = []
            cut: list[str] = []
            for installation in self.installations(assertion):
                account = str((installation.get('account') or {}).get('login', ''))
                minted = self.call(
                    'POST',
                    f'/app/installations/{installation["id"]}/access_tokens',
                    assertion,
                    f'the token request for {account}',
                    json={'permissions': PERMISSIONS},
                )
                if not isinstance(minted, dict) or not isinstance(minted.get('token'), str):
                    raise ReadFailed(f'GitHub answered the token request for {account} with no token')
                items, whole = self.search(minted['token'], account)
                rows.extend(pull_request_row(item, now) for item in items)
                if not whole:
                    cut.append(account)
            rows.sort(key=lambda row: -row['age_days'])
            said = f'the pull request search on {", ".join(cut)} came back cut, so older ones exist' if cut else ''
            self.kept = (self.clock(), rows, said)
            return rows, said

    def installations(self, assertion: str) -> list[dict]:
        """Every installation of the App, page by page, each checked for the id a token is minted against."""
        found: list[dict] = []
        page = 1
        while True:
            answer = self.call(
                'GET', '/app/installations', assertion, 'the installation list', params={'per_page': PAGE_SIZE, 'page': page}
            )
            if not isinstance(answer, list) or not all(isinstance(entry, dict) and isinstance(entry.get('id'), int) for entry in answer):
                raise ReadFailed('GitHub answered the installation list with something other than a list of installations')
            found.extend(answer)
            if len(answer) < PAGE_SIZE:
                return found
            page += 1

    def search(self, token: str, account: str) -> tuple[list[dict], bool]:
        """Every open pull request on one account, and whether the search returned all of them."""
        found: list[dict] = []
        total, incomplete = 0, False
        for page in range(1, MOST_PAGES + 1):
            answer = self.call(
                'GET',
                '/search/issues',
                token,
                f'the pull request search on {account}',
                params={'q': f'is:pr is:open archived:false user:{account}', 'per_page': PAGE_SIZE, 'page': page},
            )
            if not isinstance(answer, dict) or not isinstance(answer.get('items'), list):
                raise ReadFailed(f'GitHub answered the pull request search on {account} with no items')
            items = answer['items']
            found.extend(items)
            total = int(answer.get('total_count') or 0)
            incomplete = incomplete or bool(answer.get('incomplete_results'))
            if len(items) < PAGE_SIZE or len(found) >= total:
                break
        return found, not incomplete and len(found) >= total
