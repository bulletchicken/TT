from supabase import Client, create_client

from tt.config import SUPABASE_KEY, SUPABASE_URL


def create_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


supabase = create_supabase_client()
