\set ON_ERROR_STOP on

BEGIN;

DO $preflight$
DECLARE
    runtime_role_oid oid;
    worker_role_oid oid;
    maintenance_role_oid oid;
    database_oid oid;
    required_relation text;
    required_relation_kind "char";
BEGIN
    IF pg_catalog.current_database() <> 'minicloud' THEN
        RAISE EXCEPTION 'Expected database minicloud; refusing to grant runtime privileges';
    END IF;
    SELECT oid INTO database_oid
    FROM pg_catalog.pg_database
    WHERE datname = pg_catalog.current_database();

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'minicloud_runtime'
    ) THEN
        RAISE EXCEPTION
            'Legacy shared minicloud_runtime role exists; review its grants and credentials separately';
    END IF;

    IF pg_catalog.to_regclass('public.alembic_version') IS NULL THEN
        RAISE EXCEPTION 'Alembic history is missing; run the approved migrations first';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.alembic_version
        WHERE version_num = '20261114_resource_policies'
    ) THEN
        RAISE EXCEPTION
            'Expected Alembic revision 20261114_resource_policies before role grants';
    END IF;

    FOREACH required_relation IN ARRAY ARRAY[
        'public.users',
        'public.projects',
        'public.project_memberships',
        'public.deployments',
        'public.deployment_jobs',
        'public.deployment_events',
        'public.deployment_revisions',
        'public.resource_policies',
        'public.resource_policy_mutations',
        'public.audit_events',
        'public.token_revocations',
        'public.alembic_version'
    ] LOOP
        SELECT relation.relkind
        INTO required_relation_kind
        FROM pg_catalog.pg_class AS relation
        WHERE relation.oid = pg_catalog.to_regclass(required_relation);

        IF required_relation_kind IS NULL THEN
            RAISE EXCEPTION
                'Required relation % is missing; refusing to grant runtime privileges',
                required_relation;
        END IF;
        IF required_relation_kind NOT IN ('r', 'p') THEN
            RAISE EXCEPTION
                'Required relation % is not a table; refusing to grant runtime privileges',
                required_relation;
        END IF;
    END LOOP;

    SELECT oid INTO runtime_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'minicloud_api_runtime';

    IF runtime_role_oid IS NULL THEN
        EXECUTE
            'CREATE ROLE minicloud_api_runtime LOGIN NOSUPERUSER NOCREATEDB '
            || 'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS';
        SELECT oid INTO runtime_role_oid
        FROM pg_catalog.pg_roles
        WHERE rolname = 'minicloud_api_runtime';
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE oid = runtime_role_oid AND rolcanlogin
              AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit
        ) THEN
            RAISE EXCEPTION
                'minicloud_api_runtime has unexpected role attributes; refusing to alter it';
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members
            WHERE roleid = runtime_role_oid OR member = runtime_role_oid
        ) THEN
            RAISE EXCEPTION
                'minicloud_api_runtime has unexpected memberships; refusing to alter it';
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_shdepend
            WHERE refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
              AND refobjid = runtime_role_oid
              AND deptype = 'o'
        ) THEN
            RAISE EXCEPTION
                'minicloud_api_runtime owns database objects; refusing to alter it';
        END IF;

    END IF;

    SELECT oid INTO worker_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'minicloud_worker_runtime';

    IF worker_role_oid IS NULL THEN
        EXECUTE
            'CREATE ROLE minicloud_worker_runtime LOGIN NOSUPERUSER NOCREATEDB '
            || 'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS';
        SELECT oid INTO worker_role_oid
        FROM pg_catalog.pg_roles
        WHERE rolname = 'minicloud_worker_runtime';
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles
            WHERE oid = worker_role_oid AND rolcanlogin
              AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit
        ) THEN
            RAISE EXCEPTION
                'minicloud_worker_runtime has unexpected role attributes; refusing to alter it';
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_auth_members
            WHERE roleid = worker_role_oid OR member = worker_role_oid
        ) THEN
            RAISE EXCEPTION
                'minicloud_worker_runtime has unexpected memberships; refusing to alter it';
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_shdepend
            WHERE refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
              AND refobjid = worker_role_oid
              AND deptype = 'o'
        ) THEN
            RAISE EXCEPTION
                'minicloud_worker_runtime owns database objects; refusing to alter it';
        END IF;
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
          AND (
              namespace.nspname <> 'public'
              OR relation.relname NOT IN (
                  'users', 'projects', 'project_memberships', 'deployments',
                  'deployment_jobs', 'deployment_events', 'deployment_revisions',
                  'resource_policies', 'resource_policy_mutations',
                  'audit_events',
                  'token_revocations',
                  'alembic_version'
              )
          )
          AND pg_catalog.has_table_privilege(
              runtime_role_oid, relation.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION
            'minicloud_api_runtime has privileges on an unexpected non-system relation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS seq_rel
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = seq_rel.relnamespace
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS priv(name)
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND seq_rel.relkind = 'S'
          AND pg_catalog.has_sequence_privilege(
              runtime_role_oid, seq_rel.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION
            'minicloud_api_runtime has unexpected non-system sequence privileges';
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
              AND relation.relname IN (
                  'deployments', 'deployment_jobs', 'deployment_events'
              )
          )
          AND pg_catalog.has_table_privilege(
              worker_role_oid, relation.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION
            'minicloud_worker_runtime has privileges on an unexpected non-system relation';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS seq_rel
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = seq_rel.relnamespace
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS priv(name)
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND seq_rel.relkind = 'S'
          AND pg_catalog.has_sequence_privilege(
              worker_role_oid, seq_rel.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION
            'minicloud_worker_runtime has unexpected non-system sequence privileges';
    END IF;

    SELECT oid INTO maintenance_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'minicloud_revocation_maintenance';
    IF maintenance_role_oid IS NULL OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE oid = maintenance_role_oid
          AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
               OR rolreplication OR rolbypassrls OR rolinherit)
    ) THEN
        RAISE EXCEPTION
            'minicloud_revocation_maintenance is missing or has unexpected attributes';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members
        WHERE roleid = maintenance_role_oid OR member = maintenance_role_oid
    ) THEN
        RAISE EXCEPTION
            'minicloud_revocation_maintenance has unexpected memberships';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_shdepend AS dependency
        WHERE dependency.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          AND dependency.refobjid = maintenance_role_oid
          AND dependency.deptype = 'o'
          AND NOT (
              dependency.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
              AND dependency.objid =
                  'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
          )
    ) THEN
        RAISE EXCEPTION
            'Maintenance role owns unexpected objects; review them separately';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_db_role_setting AS role_setting
        CROSS JOIN LATERAL pg_catalog.unnest(role_setting.setconfig) AS setting(value)
        WHERE role_setting.setrole IN (
            0, runtime_role_oid, worker_role_oid, maintenance_role_oid
        )
          AND role_setting.setdatabase IN (0, database_oid)
          AND setting.value LIKE 'search_path=%'
    ) THEN
        RAISE EXCEPTION
            'An applicable database or role search_path is configured; review it separately';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS ns
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'CREATE']) AS priv(name)
        WHERE ns.nspname !~ '^pg_'
          AND ns.nspname <> 'information_schema'
          AND (
              (
                  ns.nspname = 'public'
                  AND (
                      (
                          priv.name = 'USAGE'
                          AND pg_catalog.has_schema_privilege(
                              maintenance_role_oid, ns.oid, priv.name
                          ) IS DISTINCT FROM true
                      )
                      OR (
                          priv.name = 'CREATE'
                          AND pg_catalog.has_schema_privilege(
                              maintenance_role_oid, ns.oid, priv.name
                          )
                      )
                      OR (
                          priv.name = 'USAGE'
                          AND pg_catalog.has_schema_privilege(
                              worker_role_oid, ns.oid, priv.name
                          ) IS DISTINCT FROM true
                      )
                      OR (
                          priv.name = 'CREATE'
                          AND pg_catalog.has_schema_privilege(
                              worker_role_oid, ns.oid, priv.name
                          )
                      )
                  )
              )
              OR (
                  ns.nspname <> 'public'
                  AND (
                      pg_catalog.has_schema_privilege(
                          runtime_role_oid, ns.oid, priv.name
                      )
                      OR pg_catalog.has_schema_privilege(
                          worker_role_oid, ns.oid, priv.name
                      )
                      OR pg_catalog.has_schema_privilege(
                          maintenance_role_oid, ns.oid, priv.name
                      )
                  )
              )
          )
    ) THEN
        RAISE EXCEPTION
            'Runtime or maintenance role has unexpected effective schema privileges';
    END IF;

    -- PUBLIC TEMP is harmless to this NOLOGIN role without memberships; direct
    -- Maintenance-role grants are rejected. Effective TEMP is checked below.
    IF pg_catalog.has_database_privilege(
        maintenance_role_oid, database_oid, 'CREATE'
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database AS database_entry
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                database_entry.datacl,
                pg_catalog.acldefault('d', database_entry.datdba)
            )
        ) AS acl
        WHERE database_entry.oid = database_oid
          AND acl.grantee = maintenance_role_oid
          AND acl.privilege_type IN ('CREATE', 'TEMPORARY')
    ) THEN
        RAISE EXCEPTION
            'Maintenance role has direct database CREATE or TEMP; review ACLs separately';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_default_acl AS future_acl
        CROSS JOIN LATERAL pg_catalog.aclexplode(future_acl.defaclacl) AS acl
        WHERE future_acl.defaclobjtype IN ('r', 'S', 'f', 'T', 'n')
          AND acl.grantee IN (
              0, runtime_role_oid, worker_role_oid, maintenance_role_oid
          )
    ) THEN
        RAISE EXCEPTION
            'Default privileges expose future objects to PUBLIC or a target role; review them separately';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND pg_catalog.has_function_privilege(
              runtime_role_oid, proc.oid, 'EXECUTE'
          )
          AND proc.oid <>
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND pg_catalog.has_function_privilege(
              maintenance_role_oid, proc.oid, 'EXECUTE'
          )
          AND proc.oid <>
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND pg_catalog.has_function_privilege(
              worker_role_oid, proc.oid, 'EXECUTE'
          )
          AND proc.oid <>
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
    ) THEN
        RAISE EXCEPTION
            'Runtime or maintenance role can execute an unexpected non-system function';
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
              WHERE acl.grantee = 0
                AND acl.privilege_type = 'EXECUTE'
          )
    ) THEN
        RAISE EXCEPTION
            'Expired-revocation cleanup function is missing or has unsafe owner, configuration, or ACL';
    END IF;

    IF NOT pg_catalog.has_table_privilege(
        maintenance_role_oid, 'public.token_revocations', 'DELETE'
    ) THEN
        RAISE EXCEPTION
            'Maintenance role lacks DELETE on public.token_revocations';
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
            'Maintenance role has unexpected public relation privileges';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS seq_rel
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = seq_rel.relnamespace
        CROSS JOIN LATERAL pg_catalog.unnest(
            ARRAY['USAGE', 'SELECT', 'UPDATE']
        ) AS priv(name)
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND seq_rel.relkind = 'S'
          AND pg_catalog.has_sequence_privilege(
              maintenance_role_oid, seq_rel.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION
            'Maintenance role has unexpected non-system sequence privileges';
    END IF;
END
$preflight$;

REVOKE ALL PRIVILEGES ON TABLE
    public.users,
    public.projects,
    public.project_memberships,
    public.deployments,
    public.deployment_jobs,
    public.deployment_events,
    public.deployment_revisions,
    public.resource_policies,
    public.resource_policy_mutations,
    public.audit_events,
    public.token_revocations,
    public.alembic_version
FROM minicloud_api_runtime;
REVOKE CREATE ON SCHEMA public FROM minicloud_api_runtime;
REVOKE CREATE, TEMPORARY ON DATABASE minicloud FROM minicloud_api_runtime;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.minicloud_cleanup_expired_token_revocations()
FROM minicloud_api_runtime;

REVOKE ALL PRIVILEGES ON TABLE
    public.users,
    public.projects,
    public.project_memberships,
    public.deployments,
    public.deployment_jobs,
    public.deployment_events,
    public.deployment_revisions,
    public.resource_policies,
    public.resource_policy_mutations,
    public.audit_events,
    public.token_revocations,
    public.alembic_version
FROM minicloud_worker_runtime;
REVOKE CREATE ON SCHEMA public FROM minicloud_worker_runtime;
REVOKE CREATE, TEMPORARY ON DATABASE minicloud FROM minicloud_worker_runtime;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.minicloud_cleanup_expired_token_revocations()
FROM minicloud_worker_runtime;

GRANT CONNECT ON DATABASE minicloud TO minicloud_api_runtime;
GRANT USAGE ON SCHEMA public TO minicloud_api_runtime;
GRANT SELECT, INSERT ON TABLE public.users TO minicloud_api_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE public.projects TO minicloud_api_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.project_memberships TO minicloud_api_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE public.deployments TO minicloud_api_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE public.deployment_jobs TO minicloud_api_runtime;
GRANT SELECT, INSERT ON TABLE public.deployment_events TO minicloud_api_runtime;
GRANT SELECT, INSERT ON TABLE public.deployment_revisions TO minicloud_api_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE public.resource_policies TO minicloud_api_runtime;
GRANT SELECT, INSERT ON TABLE public.resource_policy_mutations TO minicloud_api_runtime;
GRANT INSERT ON TABLE public.audit_events TO minicloud_api_runtime;
GRANT SELECT, INSERT ON TABLE public.token_revocations TO minicloud_api_runtime;
GRANT EXECUTE ON FUNCTION
    public.minicloud_cleanup_expired_token_revocations()
TO minicloud_api_runtime;

GRANT CONNECT ON DATABASE minicloud TO minicloud_worker_runtime;
GRANT USAGE ON SCHEMA public TO minicloud_worker_runtime;
GRANT SELECT, UPDATE ON TABLE public.deployments TO minicloud_worker_runtime;
GRANT SELECT, UPDATE ON TABLE public.deployment_jobs TO minicloud_worker_runtime;
GRANT INSERT ON TABLE public.deployment_events TO minicloud_worker_runtime;

DO $verify$
BEGIN
    IF pg_catalog.has_schema_privilege(
        'minicloud_api_runtime', 'public', 'CREATE'
    ) OR pg_catalog.has_schema_privilege(
        'minicloud_worker_runtime', 'public', 'CREATE'
    ) THEN
        RAISE EXCEPTION 'Runtime role can CREATE in public';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace AS ns
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'CREATE']) AS priv(name)
        WHERE ns.nspname !~ '^pg_'
          AND ns.nspname <> 'information_schema'
          AND (
              (
                  ns.nspname = 'public'
                  AND (
                      (
                          priv.name = 'USAGE'
                          AND pg_catalog.has_schema_privilege(
                              'minicloud_api_runtime', ns.oid, priv.name
                          ) IS DISTINCT FROM true
                      )
                      OR (
                          priv.name = 'USAGE'
                          AND pg_catalog.has_schema_privilege(
                          'minicloud_worker_runtime', ns.oid, priv.name
                          ) IS DISTINCT FROM true
                      )
                      OR (
                          priv.name = 'CREATE'
                          AND pg_catalog.has_schema_privilege(
                              'minicloud_api_runtime', ns.oid, priv.name
                          )
                      )
                      OR (
                          priv.name = 'CREATE'
                          AND pg_catalog.has_schema_privilege(
                              'minicloud_worker_runtime', ns.oid, priv.name
                          )
                      )
                      OR (
                          priv.name = 'USAGE'
                          AND pg_catalog.has_schema_privilege(
                              'minicloud_revocation_maintenance',
                              ns.oid,
                              priv.name
                          ) IS DISTINCT FROM true
                      )
                      OR (
                          priv.name = 'CREATE'
                          AND pg_catalog.has_schema_privilege(
                              'minicloud_revocation_maintenance',
                              ns.oid,
                              priv.name
                          )
                      )
                  )
              )
              OR (
                  ns.nspname <> 'public'
                  AND (
                      pg_catalog.has_schema_privilege(
                          'minicloud_api_runtime', ns.oid, priv.name
                      )
                      OR pg_catalog.has_schema_privilege(
                          'minicloud_worker_runtime', ns.oid, priv.name
                      )
                      OR pg_catalog.has_schema_privilege(
                          'minicloud_revocation_maintenance',
                          ns.oid,
                          priv.name
                      )
                  )
              )
          )
    ) THEN
        RAISE EXCEPTION
            'Runtime or maintenance role has an unexpected effective schema privilege';
    END IF;

    IF pg_catalog.has_database_privilege(
        'minicloud_api_runtime', 'minicloud', 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        'minicloud_api_runtime', 'minicloud', 'TEMP'
    ) OR pg_catalog.has_database_privilege(
        'minicloud_worker_runtime', 'minicloud', 'CREATE'
    ) OR pg_catalog.has_database_privilege(
        'minicloud_worker_runtime', 'minicloud', 'TEMP'
    ) THEN
        RAISE EXCEPTION 'Runtime role can CREATE or TEMP in minicloud';
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
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname IN (
              'users', 'projects', 'project_memberships', 'deployments',
              'deployment_jobs', 'deployment_events', 'deployment_revisions',
              'resource_policies', 'resource_policy_mutations',
              'audit_events',
              'token_revocations',
              'alembic_version'
          )
          AND pg_catalog.has_table_privilege(
              'minicloud_api_runtime', relation.oid, priv.name
          ) IS DISTINCT FROM CASE
              WHEN relation.relname = 'users'
                   AND priv.name IN ('SELECT', 'INSERT')
                THEN true
              WHEN relation.relname = 'projects'
                   AND priv.name IN ('SELECT', 'INSERT', 'UPDATE')
                THEN true
              WHEN relation.relname = 'project_memberships'
                   AND priv.name IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
                THEN true
              WHEN relation.relname IN ('deployments', 'deployment_jobs')
                   AND priv.name IN ('SELECT', 'INSERT', 'UPDATE')
                THEN true
              WHEN relation.relname = 'deployment_events'
                   AND priv.name IN ('SELECT', 'INSERT')
                THEN true
              WHEN relation.relname = 'deployment_revisions'
                   AND priv.name IN ('SELECT', 'INSERT')
                THEN true
              WHEN relation.relname = 'resource_policies'
                   AND priv.name IN ('SELECT', 'INSERT', 'UPDATE')
                THEN true
              WHEN relation.relname = 'resource_policy_mutations'
                   AND priv.name IN ('SELECT', 'INSERT')
                THEN true
              WHEN relation.relname = 'audit_events'
                   AND priv.name = 'INSERT'
                THEN true
              WHEN relation.relname = 'token_revocations'
                   AND priv.name IN ('SELECT', 'INSERT')
                THEN true
              ELSE false
          END
    ) THEN
        RAISE EXCEPTION 'Runtime role table privileges differ from the approved matrix';
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
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND relation.relname IN (
              'users', 'projects', 'project_memberships', 'deployments',
              'deployment_jobs', 'deployment_events', 'deployment_revisions',
              'resource_policies', 'resource_policy_mutations',
              'audit_events',
              'token_revocations',
              'alembic_version'
          )
          AND pg_catalog.has_table_privilege(
              'minicloud_worker_runtime', relation.oid, priv.name
          ) IS DISTINCT FROM CASE
              WHEN relation.relname IN ('deployments', 'deployment_jobs')
                   AND priv.name IN ('SELECT', 'UPDATE')
                THEN true
              WHEN relation.relname = 'deployment_events'
                   AND priv.name = 'INSERT'
                THEN true
              ELSE false
          END
    ) THEN
        RAISE EXCEPTION
            'Worker role table privileges differ from the approved matrix';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS seq_rel
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = seq_rel.relnamespace
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS priv(name)
        WHERE namespace.nspname = 'public'
          AND seq_rel.relkind = 'S'
          AND pg_catalog.has_sequence_privilege(
              'minicloud_api_runtime', seq_rel.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION 'Runtime role has unexpected public sequence privileges';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class AS seq_rel
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = seq_rel.relnamespace
        CROSS JOIN pg_catalog.unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS priv(name)
        WHERE namespace.nspname = 'public'
          AND seq_rel.relkind = 'S'
          AND pg_catalog.has_sequence_privilege(
              'minicloud_worker_runtime', seq_rel.oid, priv.name
          )
    ) THEN
        RAISE EXCEPTION 'Worker role has unexpected public sequence privileges';
    END IF;

    IF NOT pg_catalog.has_function_privilege(
        'minicloud_api_runtime',
        'public.minicloud_cleanup_expired_token_revocations()',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'Runtime role cannot execute expired-revocation cleanup';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                proc.proacl,
                pg_catalog.acldefault('f', proc.proowner)
            )
        ) AS acl
        WHERE proc.oid =
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
          AND acl.privilege_type = 'EXECUTE'
          AND (
              acl.grantee NOT IN (
                  proc.proowner,
                  'minicloud_api_runtime'::pg_catalog.regrole
              )
              OR (
                  acl.grantee = 'minicloud_api_runtime'::pg_catalog.regrole
                  AND acl.is_grantable
              )
          )
    ) THEN
        RAISE EXCEPTION
            'Cleanup function EXECUTE is granted to an unexpected role';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND pg_catalog.has_function_privilege(
              'minicloud_api_runtime', proc.oid, 'EXECUTE'
          )
          AND proc.oid <>
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname !~ '^pg_'
          AND namespace.nspname <> 'information_schema'
          AND pg_catalog.has_function_privilege(
              'minicloud_revocation_maintenance', proc.oid, 'EXECUTE'
          )
          AND proc.oid <>
              'public.minicloud_cleanup_expired_token_revocations()'::pg_catalog.regprocedure
    ) THEN
        RAISE EXCEPTION
            'Runtime or maintenance role can execute an unexpected non-system function';
    END IF;

    IF pg_catalog.has_function_privilege(
        'minicloud_worker_runtime',
        'public.minicloud_cleanup_expired_token_revocations()',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'Worker role can execute the revocation cleanup function';
    END IF;
END
$verify$;

COMMIT;
