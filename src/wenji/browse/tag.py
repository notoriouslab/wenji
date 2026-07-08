import json
import logging
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Cache lifetime: articles ingested while `wenji serve` keeps running become
# visible on /tags within this window (previously the cache never refreshed).
REFRESH_TTL_SECONDS = 300


class TagBrowser:
    def __init__(self, db_path: str, source_filter: str | None = None):
        self.db_path = db_path
        self.source_filter = source_filter
        self._tag_to_articles: dict[str, set[str]] = {}
        self._article_to_meta: dict[str, dict[str, Any]] = {}
        self._tag_counts: list[tuple[str, int]] = []
        self._last_load = 0.0
        self._lock = threading.Lock()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _refresh_if_needed(self):
        # TagBrowser is a process-lifetime singleton (web/app.py state), hit
        # concurrently by threadpool threads. Reload when the TTL expires;
        # build into locals and swap all three maps atomically under the lock
        # so readers never observe a mixed pair.
        if self._tag_to_articles and time.monotonic() - self._last_load < REFRESH_TTL_SECONDS:
            return

        conn = self._get_conn()
        try:
            # We only need article_id and tags
            sql = "SELECT article_id, title, pub_date, source_type, tags FROM articles_meta"
            params = []
            if self.source_filter:
                sql += " WHERE source_type = ?"
                params.append(self.source_filter)

            rows = conn.execute(sql, params).fetchall()

            tag_to_articles = defaultdict(set)
            article_to_meta = {}

            for row in rows:
                aid, title, pdate, stype, tags_raw = row
                try:
                    tags = json.loads(tags_raw) if tags_raw else []
                except (json.JSONDecodeError, TypeError):
                    tags = []

                article_to_meta[aid] = {
                    "article_id": aid,
                    "title": title,
                    "pub_date": pdate,
                    "source_type": stype,
                    "tags": tags,
                }

                for t in tags:
                    if t:
                        tag_to_articles[t].add(aid)

            # Pre-calculate counts from the local build (not self.*)
            counts = Counter()
            for t, aids in tag_to_articles.items():
                counts[t] = len(aids)

            with self._lock:
                self._tag_to_articles = dict(tag_to_articles)
                self._article_to_meta = article_to_meta
                self._tag_counts = counts.most_common()
                self._last_load = time.monotonic()

        finally:
            conn.close()

    def list_tags(self) -> list[tuple[str, int]]:
        """Return list of (tag_name, count) sorted by count desc."""
        self._refresh_if_needed()
        return self._tag_counts

    def get_tag_detail(self, name: str) -> dict[str, Any]:
        """Return articles and stats for a specific tag."""
        self._refresh_if_needed()
        # Snapshot both maps as a consistent pair — a TTL swap between the two
        # attribute reads must not mix generations (KeyError otherwise).
        with self._lock:
            tag_map = self._tag_to_articles
            meta_map = self._article_to_meta
        if name not in tag_map:
            return None

        article_ids = tag_map[name]
        articles = [meta_map[aid] for aid in article_ids]
        # Sort articles by date desc
        articles.sort(key=lambda x: x["pub_date"] or "", reverse=True)

        # Source type distribution
        stype_counts = Counter(a["source_type"] for a in articles)

        return {
            "name": name,
            "article_count": len(article_ids),
            "articles": articles,
            "source_type_distribution": dict(stype_counts.most_common()),
        }

    def get_related_tags(self, name: str, k: int = 10) -> list[tuple[str, float]]:
        """Calculate related tags using Jaccard similarity."""
        self._refresh_if_needed()
        with self._lock:
            tag_map = self._tag_to_articles
        if name not in tag_map:
            return []

        target_articles = tag_map[name]
        results = []

        for other_tag, other_articles in tag_map.items():
            if other_tag == name:
                continue

            intersection = len(target_articles & other_articles)
            if intersection == 0:
                continue

            union = len(target_articles | other_articles)
            score = intersection / union
            results.append((other_tag, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]
