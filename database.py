"""
database.py – Supabase client (service-role) used across all services.
The service-role key bypasses Row Level Security so the backend can
read/write any row.  Never expose this key to the frontend.
"""

import logging
from functools import lru_cache

from supabase import create_client, Client

from config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    """Return a cached Supabase service-role client."""
    logger.info("Initialising Supabase client → %s", settings.SUPABASE_URL)
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


# Convenience alias used throughout the app
supabase: Client = get_supabase()
