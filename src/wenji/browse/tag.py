import json
import logging
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

class TagBrowser:
    def __init__(self, db_path: str, source_filter: str | None = None):
        self.db_path = db_path
        self.source_filter = source_filter
        self._tag_to_articles: Dict[str, Set[str]] = {}
        self._article_to_meta: Dict[str, Dict[str, Any]] = {}
        self._tag_counts: List[Tuple[str, int]] = []
        self._last_load = 0

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _refresh_if_needed(self):
        # In a real app, we might check file mtime. For now, we load once or cache for 5 min.
        # But since this is a transient instance in FastAPI, we'll just load it.
        if self._tag_to_articles:
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
                except:
                    tags = []
                
                article_to_meta[aid] = {
                    "article_id": aid,
                    "title": title,
                    "pub_date": pdate,
                    "source_type": stype,
                    "tags": tags
                }
                
                for t in tags:
                    if t:
                        tag_to_articles[t].add(aid)
            
            self._tag_to_articles = dict(tag_to_articles)
            self._article_to_meta = article_to_meta
            
            # Pre-calculate counts
            counts = Counter()
            for t, aids in self._tag_to_articles.items():
                counts[t] = len(aids)
            self._tag_counts = counts.most_common()
            
        finally:
            conn.close()

    def list_tags(self) -> List[Tuple[str, int]]:
        """Return list of (tag_name, count) sorted by count desc."""
        self._refresh_if_needed()
        return self._tag_counts

    def get_tag_detail(self, name: str) -> Dict[str, Any]:
        """Return articles and stats for a specific tag."""
        self._refresh_if_needed()
        if name not in self._tag_to_articles:
            return None
        
        article_ids = self._tag_to_articles[name]
        articles = [self._article_to_meta[aid] for aid in article_ids]
        # Sort articles by date desc
        articles.sort(key=lambda x: x["pub_date"] or "", reverse=True)
        
        # Source type distribution
        stype_counts = Counter(a["source_type"] for a in articles)
        
        return {
            "name": name,
            "article_count": len(article_ids),
            "articles": articles,
            "source_type_distribution": dict(stype_counts.most_common())
        }

    def get_related_tags(self, name: str, k: int = 10) -> List[Tuple[str, float]]:
        """Calculate related tags using Jaccard similarity."""
        self._refresh_if_needed()
        if name not in self._tag_to_articles:
            return []
        
        target_articles = self._tag_to_articles[name]
        results = []
        
        for other_tag, other_articles in self._tag_to_articles.items():
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
