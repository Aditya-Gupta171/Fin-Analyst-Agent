"""lock down public tables: enable row-level security, revoke Data API role grants

Supabase exposes every table in the ``public`` schema through its auto-generated REST API (PostgREST), and by
default grants the ``anon`` and ``authenticated`` roles full access to them. With row-level security off, anyone
holding the project's anon key — which Supabase treats as publishable, not secret — could read, edit and delete
every row (Supabase's ``rls_disabled_in_public`` finding).

This app never uses that API: it connects straight to Postgres as the table owner, which RLS does not restrict.
So enabling RLS with *no* policies denies the API roles everything while the app keeps working unchanged. The
grants are revoked too, as a second layer, and future tables are kept from inheriting them. Postgres only: SQLite
has neither RLS nor these roles, and the role statements are skipped on a Postgres without Supabase's roles.

Revision ID: 5f3c9a1e7d24
Revises: acd04122d257
Create Date: 2026-09-26 23:10:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5f3c9a1e7d24"
down_revision: Union[str, Sequence[str], None] = "acd04122d257"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "documents",
    "analyses",
    "findings",
    "feedback",
    "cohort_baselines",
    "rule_versions",
    "alembic_version",
)
API_ROLES = ("anon", "authenticated")


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _for_each_api_role(statement: str) -> None:
    """Run ``statement`` (with ``{role}``) for each Supabase API role that exists on this server."""
    for role in API_ROLES:
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"EXECUTE '{statement.format(role=role)}'; END IF; END $$"
        )


def upgrade() -> None:
    if not _is_postgres():
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        _for_each_api_role(f"REVOKE ALL ON TABLE public.{table} FROM {{role}}")
    # tables a later migration creates would otherwise inherit Supabase's default grants to the API roles
    _for_each_api_role("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}")


def downgrade() -> None:
    # Restores Supabase's defaults exactly, which reopens the tables to the Data API — only for rolling back.
    if not _is_postgres():
        return
    _for_each_api_role("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {role}")
    for table in TABLES:
        _for_each_api_role(f"GRANT ALL ON TABLE public.{table} TO {{role}}")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
