"""Persistence. One SQLite file, shared by the web app and the MCP server (E8).

Parameterized SQL only, everywhere in this package. Repository-derived strings
are untrusted data and are never formatted into a query (E4).
"""

from necropsy.store.analyses import (
    AnalysisRow,
    cache_key,
    get_by_cache_key,
    get_latest_for_url,
    load_analysis,
    save_analysis,
)
from necropsy.store.cache import (
    VOLATILE_FIELDS,
    AnalysisCache,
    CacheLookup,
    CacheResult,
    CacheStatus,
    SingleFlight,
    persisted_view,
    served_now,
)
from necropsy.store.db import Database, open_database, utc_now_iso

__all__ = [
    "VOLATILE_FIELDS",
    "AnalysisCache",
    "AnalysisRow",
    "CacheLookup",
    "CacheResult",
    "CacheStatus",
    "Database",
    "SingleFlight",
    "cache_key",
    "get_by_cache_key",
    "get_latest_for_url",
    "load_analysis",
    "open_database",
    "persisted_view",
    "save_analysis",
    "served_now",
    "utc_now_iso",
]
