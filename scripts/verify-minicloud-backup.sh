#!/bin/sh
set -eu

: > /tmp/minicloud-postgres.log
docker-entrypoint.sh postgres >/tmp/minicloud-postgres.log 2>&1 &
server_pid=$!
cleanup() {
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

ready=0
attempt=0
while [ "$attempt" -lt 90 ]; do
    if grep -q 'PostgreSQL init process complete; ready for start up.' /tmp/minicloud-postgres.log \
        && pg_isready -q -h 127.0.0.1 -U postgres -d postgres; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    printf '%s\n' 'isolated_postgres_ready=false' >&2
    exit 1
fi

createdb -U postgres minicloud
pg_restore --exit-on-error --no-owner --no-privileges -U postgres -d minicloud /backup.dump

printf '%s\n' 'restore_status=success'
psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d minicloud \
    -c "SELECT 'revision=' || version_num FROM public.alembic_version"
psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d minicloud \
    -c "SELECT 'core_counts=' || (SELECT count(*)::text FROM public.users) || ',' || (SELECT count(*)::text FROM public.projects) || ',' || (SELECT count(*)::text FROM public.project_memberships) || ',' || (SELECT count(*)::text FROM public.deployments) || ',' || (SELECT count(*)::text FROM public.deployment_jobs) || ',' || (SELECT count(*)::text FROM public.deployment_events)"
psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d minicloud \
    -c "SELECT 'token_revocations_present=' || (pg_catalog.to_regclass('public.token_revocations') IS NOT NULL)::text"
psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d minicloud \
    -c "SELECT 'cleanup_function_present=' || (pg_catalog.to_regprocedure('public.minicloud_cleanup_expired_token_revocations()') IS NOT NULL)::text"
psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d minicloud \
    -c "SELECT 'validated_foreign_keys=' || count(*)::text FROM pg_catalog.pg_constraint WHERE contype = 'f' AND convalidated"
