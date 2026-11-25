"""What the service reads from ingestion and passes on to it: the items that wait on a person, and that person's placement or drop of one.

The records stay in ingestion. A placement names the person's login in its
body, because the service's token carries no person, and ingestion records the
decision as theirs beside the service's client id.
"""

from __future__ import annotations

from urllib.parse import quote

from .apps import App


def review(ingestion: App) -> object:
    """`GET /review`: each item that waits on a person, the longest waiting first."""
    return ingestion.get('/review')


def decide(ingestion: App, verb: str, key: str, body: dict[str, object]) -> object:
    """`POST /review/<key>/<verb>`, which answers the decisions it made.

    A key holds `/` and may hold `#`, so it is escaped whole into one path
    segment, which ingestion resolves back to the key.
    """
    return ingestion.post(f'/review/{quote(key, safe="")}/{verb}', body, f'POST /review/{key}/{verb}')
