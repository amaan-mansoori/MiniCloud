"""Use a narrowly privileged database function for expired-token cleanup.

Revision ID: 20261012_revocation_cleanup
Revises: 20261012_token_revocations
"""

from __future__ import annotations

from alembic import op

revision = "20261012_revocation_cleanup"
down_revision = "20261012_token_revocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $role$
        BEGIN
            IF pg_catalog.to_regclass('public.token_revocations') IS NULL THEN
                RAISE EXCEPTION
                    'public.token_revocations is missing; apply its migration first';
            END IF;

            IF pg_catalog.to_regprocedure(
                'public.minicloud_cleanup_expired_token_revocations()'
            ) IS NOT NULL THEN
                RAISE EXCEPTION
                    'The expired-revocation cleanup function already exists; review it before migration';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_roles
                WHERE rolname = 'minicloud_revocation_maintenance'
            ) THEN
                RAISE EXCEPTION
                    'minicloud_revocation_maintenance already exists; review it before migration';
            END IF;

            EXECUTE
                'CREATE ROLE minicloud_revocation_maintenance NOLOGIN '
                || 'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
                || 'NOREPLICATION NOBYPASSRLS';
        END
        $role$;
        """
    )
    op.execute(
        "GRANT USAGE, CREATE ON SCHEMA public "
        "TO minicloud_revocation_maintenance"
    )
    op.execute(
        "GRANT DELETE ON TABLE public.token_revocations "
        "TO minicloud_revocation_maintenance"
    )
    op.execute(
        "GRANT SELECT (expires_at) ON TABLE public.token_revocations "
        "TO minicloud_revocation_maintenance"
    )
    op.execute(
        """
        CREATE FUNCTION public.minicloud_cleanup_expired_token_revocations()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            DELETE FROM public.token_revocations
            WHERE expires_at <= pg_catalog.now();
        END;
        $function$;
        """
    )
    op.execute(
        "ALTER FUNCTION public.minicloud_cleanup_expired_token_revocations() "
        "OWNER TO minicloud_revocation_maintenance"
    )
    op.execute(
        "REVOKE CREATE ON SCHEMA public "
        "FROM minicloud_revocation_maintenance"
    )
    op.execute(
        """
        DO $verify$
        BEGIN
            IF pg_catalog.has_schema_privilege(
                'minicloud_revocation_maintenance', 'public', 'CREATE'
            ) THEN
                RAISE EXCEPTION
                    'minicloud_revocation_maintenance can CREATE in public';
            END IF;
        END
        $verify$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "public.minicloud_cleanup_expired_token_revocations() FROM PUBLIC"
    )
    op.execute(
        """
        DO $runtime_acl$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles
                WHERE rolname = 'minicloud_runtime'
            ) THEN
                EXECUTE
                    'REVOKE ALL ON FUNCTION '
                    || 'public.minicloud_cleanup_expired_token_revocations() '
                    || 'FROM minicloud_runtime';
            END IF;
        END
        $runtime_acl$;
        """
    )
    op.execute(
        """
        DO $verify$
        DECLARE
            maintenance_role_oid oid;
        BEGIN
            SELECT oid INTO maintenance_role_oid
            FROM pg_catalog.pg_roles
            WHERE rolname = 'minicloud_revocation_maintenance'
              AND NOT rolcanlogin
              AND NOT rolsuper
              AND NOT rolcreatedb
              AND NOT rolcreaterole
              AND NOT rolinherit
              AND NOT rolreplication
              AND NOT rolbypassrls;

            IF maintenance_role_oid IS NULL OR EXISTS (
                SELECT 1
                FROM pg_catalog.pg_auth_members
                WHERE roleid = maintenance_role_oid
                   OR member = maintenance_role_oid
            ) THEN
                RAISE EXCEPTION
                    'Maintenance role attributes or memberships are unsafe';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_proc AS proc
                WHERE proc.oid =
                    'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
                  AND proc.proowner = maintenance_role_oid
                  AND proc.prosecdef
                  AND proc.proconfig @> ARRAY['search_path=pg_catalog']
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_catalog.aclexplode(
                          COALESCE(
                              proc.proacl,
                              pg_catalog.acldefault('f', proc.proowner)
                          )
                      ) AS acl
                      WHERE acl.grantee <> proc.proowner
                  )
            ) THEN
                RAISE EXCEPTION
                    'Cleanup function owner, search_path, or execution ACL is unsafe';
            END IF;

            IF NOT pg_catalog.has_column_privilege(
                maintenance_role_oid,
                'public.token_revocations',
                'expires_at',
                'SELECT'
            ) OR EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS attribute
                WHERE attribute.attrelid = 'public.token_revocations'::pg_catalog.regclass
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                  AND attribute.attname <> 'expires_at'
                  AND pg_catalog.has_column_privilege(
                      maintenance_role_oid,
                      attribute.attrelid,
                      attribute.attname,
                      'SELECT'
                  )
            ) THEN
                RAISE EXCEPTION
                    'Maintenance role must have SELECT only on token_revocations.expires_at';
            END IF;

            IF pg_catalog.has_schema_privilege(
                maintenance_role_oid, 'public', 'CREATE'
            ) IS TRUE THEN
                RAISE EXCEPTION
                    'Maintenance role can CREATE in public after migration';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                CROSS JOIN pg_catalog.unnest(ARRAY[
                    'SELECT', 'INSERT', 'UPDATE', 'DELETE',
                    'TRUNCATE', 'REFERENCES', 'TRIGGER'
                ]) AS priv(name)
                WHERE namespace.nspname !~ '^pg_'
                  AND namespace.nspname <> 'information_schema'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND NOT (
                      namespace.nspname = 'public'
                      AND relation.relname = 'token_revocations'
                      AND priv.name = 'DELETE'
                  )
                  AND pg_catalog.has_table_privilege(
                      maintenance_role_oid, relation.oid, priv.name
                  )
            ) THEN
                RAISE EXCEPTION
                    'Maintenance role has unexpected relation privileges';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_catalog.pg_class AS sequence
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = sequence.relnamespace
                CROSS JOIN pg_catalog.unnest(
                    ARRAY['USAGE', 'SELECT', 'UPDATE']
                ) AS priv(name)
                WHERE namespace.nspname !~ '^pg_'
                  AND namespace.nspname <> 'information_schema'
                  AND sequence.relkind = 'S'
                  AND pg_catalog.has_sequence_privilege(
                      maintenance_role_oid, sequence.oid, priv.name
                  )
            ) THEN
                RAISE EXCEPTION
                    'Maintenance role has unexpected sequence privileges';
            END IF;
        END
        $verify$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION "
        "public.minicloud_cleanup_expired_token_revocations()"
    )
    op.execute(
        "REVOKE DELETE ON TABLE public.token_revocations "
        "FROM minicloud_revocation_maintenance"
    )
    op.execute(
        "REVOKE SELECT (expires_at) ON TABLE public.token_revocations "
        "FROM minicloud_revocation_maintenance"
    )
    op.execute(
        "REVOKE USAGE, CREATE ON SCHEMA public "
        "FROM minicloud_revocation_maintenance"
    )
    op.execute("DROP ROLE minicloud_revocation_maintenance")
