from __future__ import annotations

import logging
import os
import random
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    OperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)

ROOT = Path(__file__).resolve().parents[2]
api_path = ROOT / "services" / "api"
sys.path.insert(0, str(api_path))
sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.models import Deployment, DeploymentEvent, DeploymentJob
from sqlalchemy import or_

from services.worker.kubernetes_adapter import (
    DeploymentObservation,
    KubernetesAuthenticationError,
    KubernetesConfigurationError,
    KubernetesDeploymentAdapter,
    KubernetesReconcileError,
    build_kubernetes_adapter,
)

LEASE_SECONDS = 300
MAX_JOBS_PER_POLL = 20
AUTHENTICATION_RETRY_DELAY_SECONDS = 30
AUTHENTICATION_ERROR_PREFIX = "Kubernetes authentication failed"
DATABASE_BACKOFF_BASE_SECONDS = 1.0
DATABASE_BACKOFF_MAX_SECONDS = 60.0
DATABASE_BACKOFF_JITTER_FRACTION = 0.5
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_seconds(now: datetime, then: datetime) -> float:
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()


def _event(
    db,
    deployment_id: str,
    event_type: str,
    message: str,
    request_id: str | None = None,
) -> None:
    db.add(
        DeploymentEvent(
            deployment_id=deployment_id,
            event_type=event_type,
            message=message,
            request_id=request_id,
        )
    )


def _job_request_id(job: DeploymentJob) -> str:
    return job.request_id or "not_recorded"


def _rollout_started_at(job: DeploymentJob, deployment: Deployment) -> datetime:
    return job.started_at or job.created_at


def _release(job: DeploymentJob) -> None:
    job.lease_expires_at = None


def _authentication_status_path() -> Path | None:
    configured_path = os.getenv("WORKER_AUTH_STATUS_FILE")
    return Path(configured_path) if configured_path else None


def _set_worker_status_blocked(setting_name: str, blocked: bool, status_name: str) -> None:
    configured_path = os.getenv(setting_name)
    path = Path(configured_path) if configured_path else None
    if path is None:
        return
    try:
        if blocked:
            path.write_text(f"Worker {status_name} is unavailable.\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to update worker %s health status.", status_name)


def _set_authentication_blocked(blocked: bool) -> None:
    _set_worker_status_blocked("WORKER_AUTH_STATUS_FILE", blocked, "Kubernetes authentication")


def _set_database_blocked(blocked: bool) -> None:
    _set_worker_status_blocked("WORKER_DATABASE_STATUS_FILE", blocked, "database connectivity")


def _is_authentication_error(message: str | None) -> bool:
    return bool(message and message.startswith(AUTHENTICATION_ERROR_PREFIX))


def _fail_job(db, job: DeploymentJob, deployment: Deployment, message: str) -> None:
    job.state = "failed"
    job.last_error = message
    deployment.status = "failed"
    deployment.last_error = message
    _release(job)
    _event(db, deployment.id, "failed", message, request_id=job.request_id)
    logger.error(
        "Deployment %s failed: %s request_id=%s",
        deployment.id,
        message,
        _job_request_id(job),
    )


def _claim_jobs(db, now: datetime) -> list[tuple[str, datetime]]:
    jobs = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.state.in_(["queued", "applying", "progressing", "timed_out"]),
            or_(DeploymentJob.lease_expires_at.is_(None), DeploymentJob.lease_expires_at < now),
        )
        .order_by(DeploymentJob.created_at.asc())
        .limit(MAX_JOBS_PER_POLL)
        .with_for_update(skip_locked=True)
        .all()
    )
    claimed_jobs = []
    for job in jobs:
        lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.lease_expires_at = lease_expires_at
        claimed_jobs.append((job.id, lease_expires_at))
    if jobs:
        db.commit()
    return claimed_jobs


def _lock_claimed_job(db, job_id: str, lease_expires_at: datetime) -> DeploymentJob | None:
    return (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.id == job_id,
            DeploymentJob.lease_expires_at == lease_expires_at,
            DeploymentJob.lease_expires_at > _utc_now(),
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )


def _record_retry(
    db,
    job: DeploymentJob,
    deployment: Deployment,
    error: KubernetesReconcileError,
    *,
    applying: bool,
    timed_out: bool = False,
) -> None:
    if timed_out and error.retryable:
        job.state = "timed_out"
        job.last_error = str(error)
        deployment.status = "timed_out"
        deployment.last_error = str(error)
        job.updated_at = _utc_now()
        _release(job)
        _event(
            db,
            deployment.id,
            "reconciliation_unavailable",
            str(error),
            request_id=job.request_id,
        )
        logger.warning(
            "Timed-out deployment %s remains under observation after a transient Kubernetes error: %s "
            "request_id=%s",
            deployment.id,
            error,
            _job_request_id(job),
        )
        return
    if not error.retryable:
        _fail_job(db, job, deployment, str(error))
        return

    job.attempts += 1
    if job.attempts >= job.max_attempts:
        _fail_job(db, job, deployment, f"{error} Retry limit reached.")
        return

    job.state = "timed_out" if timed_out else "queued" if applying else "progressing"
    job.last_error = str(error)
    deployment.status = "timed_out" if timed_out else "progressing"
    deployment.last_error = str(error)
    job.updated_at = _utc_now()
    _release(job)
    _event(db, deployment.id, "retrying", str(error), request_id=job.request_id)
    logger.warning(
        "Deployment %s will retry: %s request_id=%s",
        deployment.id,
        error,
        _job_request_id(job),
    )


def _pause_for_authentication(
    db,
    job: DeploymentJob,
    deployment: Deployment,
    error: KubernetesAuthenticationError,
    *,
    applying: bool,
    timed_out: bool = False,
) -> None:
    job.state = "timed_out" if timed_out else "queued" if applying else "progressing"
    job.last_error = str(error)
    job.updated_at = _utc_now()
    deployment.status = "timed_out" if timed_out else "progressing"
    deployment.last_error = str(error)
    deployment.updated_at = job.updated_at
    _release(job)
    _event(
        db,
        deployment.id,
        "authentication_blocked",
        str(error),
        request_id=job.request_id,
    )
    _set_authentication_blocked(True)
    logger.error(
        "Deployment %s paused because Kubernetes rejected worker authentication; "
        "the job remains recoverable and its retry budget is unchanged. request_id=%s",
        deployment.id,
        _job_request_id(job),
    )


def _pause_available_jobs_for_authentication(
    db,
    error: KubernetesAuthenticationError,
) -> None:
    claimed_jobs = _claim_jobs(db, _utc_now())
    for job_id, lease_expires_at in claimed_jobs:
        job = _lock_claimed_job(db, job_id, lease_expires_at)
        if job is None or job.state not in {"queued", "applying", "progressing", "timed_out"}:
            continue
        deployment = db.query(Deployment).filter(Deployment.id == job.deployment_id).one_or_none()
        if deployment is None:
            continue
        if _is_authentication_error(job.last_error):
            _release(job)
            continue
        _pause_for_authentication(
            db,
            job,
            deployment,
            error,
            applying=job.state in {"queued", "applying"},
            timed_out=job.state == "timed_out",
        )
    if claimed_jobs:
        db.commit()


def _process_job(
    db,
    job_id: str,
    lease_expires_at: datetime,
    adapter: KubernetesDeploymentAdapter,
    now: datetime,
) -> None:
    job = _lock_claimed_job(db, job_id, lease_expires_at)
    if job is None or job.state not in {"queued", "applying", "progressing", "timed_out"}:
        db.rollback()
        return
    deployment = db.query(Deployment).filter(Deployment.id == job.deployment_id).one_or_none()
    if deployment is None:
        job.state = "failed"
        job.last_error = "Deployment record no longer exists."
        _release(job)
        db.commit()
        return

    if job.target_replicas is not None and job.target_replicas != deployment.replicas:
        _fail_job(db, job, deployment, "Operation target no longer matches the deployment replica count.")
        db.commit()
        return

    retry_delay = min(2 ** job.attempts, 60)
    if job.attempts and _elapsed_seconds(now, deployment.updated_at) < retry_delay:
        _release(job)
        db.commit()
        return
    if (
        _is_authentication_error(job.last_error)
        and _elapsed_seconds(now, deployment.updated_at) < AUTHENTICATION_RETRY_DELAY_SECONDS
    ):
        _release(job)
        db.commit()
        return

    applying = job.state in {"queued", "applying"}
    recovering_timeout = job.state == "timed_out" or deployment.status == "timed_out"
    recovering_authentication = _is_authentication_error(job.last_error)
    logger.info(
        "Processing job %s for deployment %s from state %s request_id=%s",
        job.id,
        deployment.id,
        job.state,
        _job_request_id(job),
    )
    try:
        if applying:
            job.state = "applying"
            deployment.status = "applying"
            deployment.last_error = None
            job.started_at = job.started_at or now
            message = (
                f"Applying the requested scale to {job.target_replicas} replicas."
                if job.operation_type == "scale"
                else "Reconciling Deployment and Service in Kubernetes."
            )
            _event(db, deployment.id, "applying", message, request_id=job.request_id)
            db.commit()
            job = _lock_claimed_job(db, job_id, lease_expires_at)
            if job is None or job.state not in {"applying", "progressing"}:
                db.rollback()
                return
            deployment = db.query(Deployment).filter(Deployment.id == job.deployment_id).one_or_none()
            if deployment is None:
                db.rollback()
                return
            adapter.apply(deployment)
            job.state = "progressing"
            deployment.status = "progressing"
            _event(
                db,
                deployment.id,
                "progressing",
                "Kubernetes accepted the workload; waiting for observed readiness.",
                request_id=job.request_id,
            )

        observation: DeploymentObservation = adapter.observe(deployment)
        if recovering_authentication:
            _event(
                db,
                deployment.id,
                "authentication_recovered",
                "Kubernetes accepted the renewed worker credential.",
                request_id=job.request_id,
            )
            logger.info(
                "Kubernetes authentication recovered for deployment %s request_id=%s",
                deployment.id,
                _job_request_id(job),
            )
            job.last_error = None
            _set_authentication_blocked(False)
        if observation.ready:
            if recovering_timeout:
                _event(
                    db,
                    deployment.id,
                    "recovered_after_timeout",
                    "Kubernetes readiness was observed after the MiniCloud rollout timeout.",
                    request_id=job.request_id,
                )
            job.state = "completed"
            job.last_error = None
            deployment.status = "ready"
            deployment.last_error = None
            _release(job)
            _event(db, deployment.id, "ready", observation.message, request_id=job.request_id)
            db.commit()
            logger.info(
                "Deployment %s reached ready: %s request_id=%s",
                deployment.id,
                observation.message,
                _job_request_id(job),
            )
            return

        if observation.terminal:
            _fail_job(db, job, deployment, observation.message)
            db.commit()
            return

        rollout_started_at = _rollout_started_at(job, deployment)
        if recovering_timeout or _elapsed_seconds(now, rollout_started_at) >= adapter.rollout_timeout_seconds:
            message = "Kubernetes rollout exceeded MiniCloud's configured readiness timeout."
            if not recovering_timeout:
                job.state = "timed_out"
                deployment.status = "timed_out"
                job.updated_at = now
                deployment.updated_at = now
                _event(db, deployment.id, "timed_out", message, request_id=job.request_id)
            job.last_error = message
            deployment.last_error = message
            _release(job)
            db.commit()
            return

        job.state = "progressing"
        deployment.status = "progressing"
        deployment.last_error = observation.message
        job.last_error = observation.message
        _release(job)
        db.commit()
        logger.info(
            "Deployment %s remains progressing: %s request_id=%s",
            deployment.id,
            observation.message,
            _job_request_id(job),
        )
    except KubernetesAuthenticationError as exc:
        _pause_for_authentication(
            db,
            job,
            deployment,
            exc,
            applying=applying,
            timed_out=recovering_timeout,
        )
        db.commit()
    except KubernetesReconcileError as exc:
        _record_retry(db, job, deployment, exc, applying=applying, timed_out=recovering_timeout)
        db.commit()


def _fail_unconfigured_jobs(
    db,
    claimed_jobs: list[tuple[str, datetime]],
    message: str,
) -> None:
    for job_id, lease_expires_at in claimed_jobs:
        job = _lock_claimed_job(db, job_id, lease_expires_at)
        if job is None or job.state not in {"queued", "applying", "progressing", "timed_out"}:
            continue
        deployment = db.query(Deployment).filter(Deployment.id == job.deployment_id).one_or_none()
        if deployment is None:
            job.state = "failed"
            job.last_error = message
            _release(job)
            continue
        if job.state == "queued":
            _fail_job(db, job, deployment, message)
            continue

        previous_error = job.last_error
        job.state = "timed_out" if job.state == "timed_out" else "progressing"
        deployment.status = job.state
        job.last_error = message
        deployment.last_error = message
        job.updated_at = _utc_now()
        deployment.updated_at = job.updated_at
        _release(job)
        if previous_error != message:
            _event(
                db,
                deployment.id,
                "reconciliation_unavailable",
                message,
                request_id=job.request_id,
            )
    db.commit()


def process_jobs(adapter: KubernetesDeploymentAdapter | None = None) -> None:
    db = SessionLocal()
    try:
        active_adapter = adapter
        if active_adapter is None:
            try:
                active_adapter = build_kubernetes_adapter(settings)
            except KubernetesConfigurationError as exc:
                now = _utc_now()
                claimed_jobs = _claim_jobs(db, now)
                if claimed_jobs:
                    logger.error("Kubernetes configuration unavailable: %s", exc)
                    _fail_unconfigured_jobs(db, claimed_jobs, str(exc))
                return

            try:
                active_adapter.verify_credentials()
            except KubernetesAuthenticationError as exc:
                _set_authentication_blocked(True)
                logger.error("Kubernetes credential check failed: %s", exc)
                _pause_available_jobs_for_authentication(db, exc)
                return
            except KubernetesReconcileError as exc:
                if exc.retryable:
                    logger.warning("Kubernetes credential check temporarily unavailable: %s", exc)
                else:
                    _set_authentication_blocked(True)
                    logger.error("Kubernetes worker access check failed: %s", exc)
                return

            status_path = _authentication_status_path()
            if status_path is not None and status_path.exists():
                _set_authentication_blocked(False)
                logger.info("Kubernetes worker access recovered; the kubeconfig was reloaded.")

        now = _utc_now()
        claimed_jobs = _claim_jobs(db, now)
        if not claimed_jobs:
            return
        logger.info("Claimed %d deployment job(s)", len(claimed_jobs))

        for job_id, lease_expires_at in claimed_jobs:
            _process_job(db, job_id, lease_expires_at, active_adapter, now)
    finally:
        db.close()


def _database_retry_delay(failure_count: int) -> float:
    exponent = min(max(failure_count - 1, 0), 6)
    ceiling = min(
        DATABASE_BACKOFF_BASE_SECONDS * (2**exponent),
        DATABASE_BACKOFF_MAX_SECONDS,
    )
    return random.uniform(
        ceiling * (1 - DATABASE_BACKOFF_JITTER_FRACTION),
        ceiling,
    )


def run_worker(stop_event: threading.Event | None = None) -> None:
    shutdown_event = stop_event if stop_event is not None else threading.Event()
    database_failures = 0
    interval = max(1, int(os.getenv("WORKER_INTERVAL_SECONDS", str(settings.worker_interval_seconds))))
    while not shutdown_event.is_set():
        try:
            process_jobs()
        except (
            DisconnectionError,
            InterfaceError,
            OperationalError,
            SQLAlchemyTimeoutError,
        ) as exc:
            database_failures += 1
            delay = _database_retry_delay(database_failures)
            _set_database_blocked(True)
            logger.error(
                "Worker database operation failed (%s); retrying in %.1f seconds.",
                type(exc).__name__,
                delay,
            )
        else:
            database_failures = 0
            _set_database_blocked(False)
            delay = interval
        shutdown_event.wait(delay)
    logger.info("MiniCloud worker shutdown requested.")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("MiniCloud worker starting for namespace %s", settings.kubernetes_namespace)
    stop_event = threading.Event()

    def request_shutdown(signum: int, _: object) -> None:
        logger.info("MiniCloud worker received shutdown signal %s.", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    run_worker(stop_event)


if __name__ == "__main__":
    main()
