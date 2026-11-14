from __future__ import annotations

from app.core.audit import add_audit_event
from app.core.db import get_db
from app.core.models import (
    Deployment,
    DeploymentEvent,
    DeploymentJob,
    DeploymentRevision,
    Project,
    ProjectMembership,
    User,
)
from app.core.pagination import decode_created_at_cursor, encode_created_at_cursor
from app.core.resource_policy import ProjectQuotaError, enforce_project_replica_policy
from app.core.security import get_current_user
from app.kubernetes_diagnostics import (
    KubernetesDiagnosticsAdapter,
    KubernetesDiagnosticsUnavailable,
    get_kubernetes_diagnostics_adapter,
)
from app.kubernetes_names import deployment_resource_name
from app.schemas import (
    DeploymentCreate,
    DeploymentEventOut,
    DeploymentEventPageOut,
    DeploymentOut,
    DeploymentPageOut,
    DeploymentRevisionOut,
    DeploymentRevisionPageOut,
    DeploymentRollback,
    DeploymentScale,
    KubernetesEventOut,
    KubernetesEventPageOut,
    KubernetesPodLogOut,
    KubernetesPodLogsOut,
    OperationOut,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from kubernetes.client.exceptions import ApiException
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1", tags=["deployments"])


def _diagnostics_adapter() -> KubernetesDiagnosticsAdapter:
    try:
        return get_kubernetes_diagnostics_adapter()
    except KubernetesDiagnosticsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _project_for_member(db: Session, project_id: str, user_id: str) -> Project:
    project = (
        db.query(Project)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .filter(Project.id == project_id, ProjectMembership.user_id == user_id)
        .one_or_none()
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _enforce_project_quota(db: Session, project_id: str, replicas: int) -> None:
    try:
        enforce_project_replica_policy(db, project_id, replicas)
    except ProjectQuotaError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _is_replayed_scale(
    job: DeploymentJob,
    deployment_id: str,
    target_replicas: int,
) -> bool:
    return (
        job.operation_type == "scale"
        and job.deployment_id == deployment_id
        and job.target_replicas == target_replicas
    )


def _next_revision_number(db: Session, deployment_id: str) -> int:
    latest = (
        db.query(DeploymentRevision.revision_number)
        .filter(DeploymentRevision.deployment_id == deployment_id)
        .order_by(DeploymentRevision.revision_number.desc())
        .first()
    )
    return 1 if latest is None else latest[0] + 1


def _replayed_rollback_revision(
    db: Session,
    job: DeploymentJob,
    deployment_id: str,
    target_revision_id: str,
) -> DeploymentRevision | None:
    if (
        job.operation_type != "rollback"
        or job.deployment_id != deployment_id
        or not job.revision_id
    ):
        return None
    revision = (
        db.query(DeploymentRevision).filter(DeploymentRevision.id == job.revision_id).one_or_none()
    )
    if revision and revision.rollback_of_revision_id == target_revision_id:
        return revision
    return None


@router.post(
    "/projects/{project_id}/deployments",
    response_model=OperationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_deployment(
    project_id: str,
    payload: DeploymentCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationOut:
    project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .with_for_update()
        .one_or_none()
    )
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    membership = (
        db.query(ProjectMembership)
        .filter(ProjectMembership.project_id == project.id, ProjectMembership.user_id == user.id)
        .one_or_none()
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to manage deployments"
        )

    if not payload.name.strip() or not payload.image.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Deployment name and image are required",
        )

    idempotency_key = payload.idempotency_key.strip() if payload.idempotency_key else None
    if idempotency_key:
        existing_job = (
            db.query(DeploymentJob)
            .filter(
                DeploymentJob.project_id == project.id,
                DeploymentJob.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing_job:
            existing_deployment = (
                db.query(Deployment)
                .filter(Deployment.id == existing_job.deployment_id)
                .one_or_none()
            )
            if (
                existing_deployment
                and existing_job.operation_type == "deploy"
                and existing_job.target_replicas == payload.replicas
                and existing_deployment.project_id == project.id
                and existing_deployment.owner_id == user.id
                and existing_deployment.name == payload.name.strip()
                and existing_deployment.image == payload.image.strip()
                and existing_deployment.port == payload.port
                and existing_deployment.replicas == payload.replicas
            ):
                return OperationOut(
                    deployment_id=existing_deployment.id,
                    kubernetes_resource_name=deployment_resource_name(existing_deployment.id),
                    revision_id=existing_job.revision_id,
                    job_state=existing_job.state,
                    status=existing_deployment.status,
                    last_error=existing_deployment.last_error,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key has already been used for a different deployment request",
            )

    _enforce_project_quota(db, project.id, payload.replicas)

    if payload.port < 1 or payload.port > 65535:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Port must be between 1 and 65535",
        )

    existing = (
        db.query(Deployment)
        .filter(
            Deployment.project_id == project.id,
            Deployment.name == payload.name.strip(),
            Deployment.image == payload.image.strip(),
        )
        .order_by(Deployment.created_at.desc())
        .first()
    )
    if existing and existing.status in {"accepted", "queued", "applying", "progressing", "ready"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deployment with the same name and image already exists for this project",
        )

    deployment = Deployment(
        project_id=project.id,
        owner_id=user.id,
        name=payload.name.strip(),
        image=payload.image.strip(),
        port=payload.port,
        replicas=payload.replicas,
        status="accepted",
    )
    db.add(deployment)
    db.flush()

    revision = DeploymentRevision(
        deployment_id=deployment.id,
        revision_number=1,
        name=deployment.name,
        image=deployment.image,
        port=deployment.port,
        replicas=deployment.replicas,
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()

    job = DeploymentJob(
        deployment_id=deployment.id,
        operation_type="deploy",
        target_replicas=payload.replicas,
        revision_id=revision.id,
        state="queued",
        attempts=0,
        max_attempts=3,
        project_id=project.id,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    db.add(job)

    event = DeploymentEvent(
        deployment_id=deployment.id,
        event_type="accepted",
        message="Deployment request accepted",
        request_id=request.state.request_id,
    )
    db.add(event)
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="deployment.created",
        resource_type="deployment",
        resource_id=deployment.id,
        details={"port": payload.port, "replicas": payload.replicas, "revision_id": revision.id},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key:
            existing_job = (
                db.query(DeploymentJob)
                .filter(
                    DeploymentJob.project_id == project.id,
                    DeploymentJob.idempotency_key == idempotency_key,
                )
                .one_or_none()
            )
            if existing_job:
                existing_deployment = (
                    db.query(Deployment)
                    .filter(Deployment.id == existing_job.deployment_id)
                    .one_or_none()
                )
                if (
                    existing_deployment
                    and existing_job.operation_type == "deploy"
                    and existing_job.target_replicas == payload.replicas
                    and existing_deployment.project_id == project.id
                    and existing_deployment.owner_id == user.id
                    and existing_deployment.name == payload.name.strip()
                    and existing_deployment.image == payload.image.strip()
                    and existing_deployment.port == payload.port
                    and existing_deployment.replicas == payload.replicas
                ):
                    return OperationOut(
                        deployment_id=existing_deployment.id,
                        kubernetes_resource_name=deployment_resource_name(existing_deployment.id),
                        revision_id=existing_job.revision_id,
                        job_state=existing_job.state,
                        status=existing_deployment.status,
                        last_error=existing_deployment.last_error,
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key has already been used for a different deployment request",
                ) from exc
        raise
    db.refresh(deployment)
    db.refresh(job)

    return OperationOut(
        deployment_id=deployment.id,
        kubernetes_resource_name=deployment_resource_name(deployment.id),
        revision_id=revision.id,
        job_state=job.state,
        status=deployment.status,
        last_error=deployment.last_error,
    )


@router.patch(
    "/projects/{project_id}/deployments/{deployment_id}/scale",
    response_model=OperationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def scale_deployment(
    project_id: str,
    deployment_id: str,
    payload: DeploymentScale,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationOut:
    project = _project_for_member(db, project_id, user.id)
    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .with_for_update()
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    existing_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.project_id == project.id,
            DeploymentJob.idempotency_key == payload.idempotency_key,
        )
        .one_or_none()
    )
    if existing_job:
        if _is_replayed_scale(existing_job, deployment.id, payload.replicas):
            return OperationOut(
                deployment_id=deployment.id,
                kubernetes_resource_name=deployment_resource_name(deployment.id),
                revision_id=existing_job.revision_id,
                job_state=existing_job.state,
                status=deployment.status,
                last_error=existing_job.last_error,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key has already been used for a different operation",
        )

    if deployment.replicas != payload.expected_replicas:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment replica count changed; refresh before retrying",
        )

    if deployment.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment must be ready before it can be scaled",
        )

    active_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.deployment_id == deployment.id,
            DeploymentJob.state.in_(["queued", "applying", "progressing", "timed_out"]),
        )
        .with_for_update()
        .first()
    )
    if active_job is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another deployment operation is already in progress",
        )
    if deployment.replicas == payload.replicas:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment already has the requested replica count",
        )

    _enforce_project_quota(db, project.id, payload.replicas)

    previous_replicas = deployment.replicas
    revision = DeploymentRevision(
        deployment_id=deployment.id,
        revision_number=_next_revision_number(db, deployment.id),
        name=deployment.name,
        image=deployment.image,
        port=deployment.port,
        replicas=payload.replicas,
        created_by_user_id=user.id,
    )
    db.add(revision)
    db.flush()

    deployment.replicas = payload.replicas
    deployment.status = "accepted"
    deployment.last_error = None
    job = DeploymentJob(
        deployment_id=deployment.id,
        project_id=project.id,
        operation_type="scale",
        target_replicas=payload.replicas,
        revision_id=revision.id,
        state="queued",
        attempts=0,
        max_attempts=3,
        idempotency_key=payload.idempotency_key,
        request_id=request.state.request_id,
    )
    event = DeploymentEvent(
        deployment_id=deployment.id,
        event_type="scale_accepted",
        message=f"Scaling requested from {previous_replicas} to {payload.replicas} replicas.",
        request_id=request.state.request_id,
    )
    db.add_all([job, event])
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="deployment.scale_requested",
        resource_type="deployment",
        resource_id=deployment.id,
        details={
            "from_replicas": previous_replicas,
            "to_replicas": payload.replicas,
            "revision_id": revision.id,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing_job = (
            db.query(DeploymentJob)
            .filter(
                DeploymentJob.project_id == project.id,
                DeploymentJob.idempotency_key == payload.idempotency_key,
            )
            .one_or_none()
        )
        if existing_job and _is_replayed_scale(existing_job, deployment_id, payload.replicas):
            current_deployment = (
                db.query(Deployment)
                .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
                .one_or_none()
            )
            if current_deployment:
                return OperationOut(
                    deployment_id=current_deployment.id,
                    kubernetes_resource_name=deployment_resource_name(current_deployment.id),
                    revision_id=existing_job.revision_id,
                    job_state=existing_job.state,
                    status=current_deployment.status,
                    last_error=existing_job.last_error,
                )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment scale request conflicted with another operation",
        ) from exc

    db.refresh(deployment)
    db.refresh(job)
    return OperationOut(
        deployment_id=deployment.id,
        kubernetes_resource_name=deployment_resource_name(deployment.id),
        revision_id=revision.id,
        job_state=job.state,
        status=deployment.status,
        last_error=job.last_error,
    )


@router.post(
    "/projects/{project_id}/deployments/{deployment_id}/rollback",
    response_model=OperationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def rollback_deployment(
    project_id: str,
    deployment_id: str,
    payload: DeploymentRollback,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationOut:
    project = _project_for_member(db, project_id, user.id)
    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .with_for_update()
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    existing_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.project_id == project.id,
            DeploymentJob.idempotency_key == payload.idempotency_key,
        )
        .one_or_none()
    )
    if existing_job:
        replayed_revision = _replayed_rollback_revision(
            db,
            existing_job,
            deployment.id,
            payload.revision_id,
        )
        if replayed_revision:
            return OperationOut(
                deployment_id=deployment.id,
                kubernetes_resource_name=deployment_resource_name(deployment.id),
                revision_id=replayed_revision.id,
                job_state=existing_job.state,
                status=deployment.status,
                last_error=existing_job.last_error,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key has already been used for a different operation",
        )

    current_revision = (
        db.query(DeploymentRevision)
        .filter(DeploymentRevision.deployment_id == deployment.id)
        .order_by(DeploymentRevision.revision_number.desc())
        .first()
    )
    if current_revision is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment revision history is unavailable",
        )
    target_revision = (
        db.query(DeploymentRevision)
        .filter(
            DeploymentRevision.deployment_id == deployment.id,
            DeploymentRevision.id == payload.revision_id,
        )
        .one_or_none()
    )
    if target_revision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Deployment revision not found"
        )
    if current_revision.id != payload.expected_revision_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment revision changed; refresh before retrying",
        )
    if target_revision.id == current_revision.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment is already at the selected revision",
        )
    if deployment.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment must be ready before it can be rolled back",
        )

    _enforce_project_quota(db, project.id, target_revision.replicas)

    active_job = (
        db.query(DeploymentJob)
        .filter(
            DeploymentJob.deployment_id == deployment.id,
            DeploymentJob.state.in_(["queued", "applying", "progressing", "timed_out"]),
        )
        .with_for_update()
        .first()
    )
    if active_job is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another deployment operation is already in progress",
        )

    revision = DeploymentRevision(
        deployment_id=deployment.id,
        revision_number=_next_revision_number(db, deployment.id),
        name=target_revision.name,
        image=target_revision.image,
        port=target_revision.port,
        replicas=target_revision.replicas,
        created_by_user_id=user.id,
        rollback_of_revision_id=target_revision.id,
    )
    db.add(revision)
    db.flush()

    previous_revision_id = current_revision.id
    deployment.name = revision.name
    deployment.image = revision.image
    deployment.port = revision.port
    deployment.replicas = revision.replicas
    deployment.status = "accepted"
    deployment.last_error = None

    job = DeploymentJob(
        deployment_id=deployment.id,
        project_id=project.id,
        operation_type="rollback",
        target_replicas=revision.replicas,
        revision_id=revision.id,
        state="queued",
        attempts=0,
        max_attempts=3,
        idempotency_key=payload.idempotency_key,
        request_id=request.state.request_id,
    )
    event = DeploymentEvent(
        deployment_id=deployment.id,
        event_type="rollback_accepted",
        message=f"Rollback to revision {target_revision.revision_number} accepted as revision {revision.revision_number}.",
        request_id=request.state.request_id,
    )
    db.add_all([job, event])
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="deployment.rollback_requested",
        resource_type="deployment",
        resource_id=deployment.id,
        details={
            "from_revision_id": previous_revision_id,
            "revision_id": revision.id,
            "rollback_of_revision_id": target_revision.id,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing_job = (
            db.query(DeploymentJob)
            .filter(
                DeploymentJob.project_id == project.id,
                DeploymentJob.idempotency_key == payload.idempotency_key,
            )
            .one_or_none()
        )
        if existing_job:
            replayed_revision = _replayed_rollback_revision(
                db,
                existing_job,
                deployment_id,
                payload.revision_id,
            )
            if replayed_revision:
                current_deployment = (
                    db.query(Deployment)
                    .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
                    .one_or_none()
                )
                if current_deployment:
                    return OperationOut(
                        deployment_id=current_deployment.id,
                        kubernetes_resource_name=deployment_resource_name(current_deployment.id),
                        revision_id=replayed_revision.id,
                        job_state=existing_job.state,
                        status=current_deployment.status,
                        last_error=existing_job.last_error,
                    )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deployment rollback request conflicted with another operation",
        ) from exc

    db.refresh(deployment)
    db.refresh(job)
    return OperationOut(
        deployment_id=deployment.id,
        kubernetes_resource_name=deployment_resource_name(deployment.id),
        revision_id=revision.id,
        job_state=job.state,
        status=deployment.status,
        last_error=job.last_error,
    )


@router.get("/projects/{project_id}/deployments", response_model=DeploymentPageOut)
def list_deployments(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> DeploymentPageOut:
    project = _project_for_member(db, project_id, user.id)

    query = db.query(Deployment).filter(Deployment.project_id == project.id)
    if cursor:
        try:
            cursor_time, cursor_id = decode_created_at_cursor(cursor)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid deployment cursor",
            ) from exc
        query = query.filter(
            or_(
                Deployment.created_at < cursor_time,
                and_(Deployment.created_at == cursor_time, Deployment.id < cursor_id),
            )
        )

    rows = query.order_by(Deployment.created_at.desc(), Deployment.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    deployments = rows[:limit]
    return DeploymentPageOut(
        items=[
            DeploymentOut(
                id=d.id,
                project_id=d.project_id,
                owner_id=d.owner_id,
                name=d.name,
                image=d.image,
                port=d.port,
                replicas=d.replicas,
                kubernetes_resource_name=deployment_resource_name(d.id),
                status=d.status,
                last_error=d.last_error,
            )
            for d in deployments
        ],
        next_cursor=(
            encode_created_at_cursor(deployments[-1].created_at, deployments[-1].id)
            if has_more and deployments
            else None
        ),
    )


@router.get("/projects/{project_id}/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    project_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeploymentOut:
    project = _project_for_member(db, project_id, user.id)

    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .one_or_none()
    )
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    return DeploymentOut(
        id=deployment.id,
        project_id=deployment.project_id,
        owner_id=deployment.owner_id,
        name=deployment.name,
        image=deployment.image,
        port=deployment.port,
        replicas=deployment.replicas,
        kubernetes_resource_name=deployment_resource_name(deployment.id),
        status=deployment.status,
        last_error=deployment.last_error,
    )


@router.get(
    "/projects/{project_id}/deployments/{deployment_id}/revisions",
    response_model=DeploymentRevisionPageOut,
)
def list_deployment_revisions(
    project_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> DeploymentRevisionPageOut:
    project = _project_for_member(db, project_id, user.id)
    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    query = db.query(DeploymentRevision).filter(
        DeploymentRevision.deployment_id == deployment.id,
    )
    if cursor:
        try:
            cursor_time, cursor_id = decode_created_at_cursor(cursor)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid deployment revision cursor",
            ) from exc
        query = query.filter(
            or_(
                DeploymentRevision.created_at < cursor_time,
                and_(
                    DeploymentRevision.created_at == cursor_time,
                    DeploymentRevision.id < cursor_id,
                ),
            )
        )

    rows = (
        query.order_by(DeploymentRevision.created_at.desc(), DeploymentRevision.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    revisions = rows[:limit]
    return DeploymentRevisionPageOut(
        items=[
            DeploymentRevisionOut(
                id=revision.id,
                revision_number=revision.revision_number,
                name=revision.name,
                image=revision.image,
                port=revision.port,
                replicas=revision.replicas,
                created_by_user_id=revision.created_by_user_id,
                rollback_of_revision_id=revision.rollback_of_revision_id,
                created_at=revision.created_at,
            )
            for revision in revisions
        ],
        next_cursor=(
            encode_created_at_cursor(revisions[-1].created_at, revisions[-1].id)
            if has_more and revisions
            else None
        ),
    )


@router.get(
    "/projects/{project_id}/deployments/{deployment_id}/events",
    response_model=DeploymentEventPageOut,
)
def list_deployment_events(
    project_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> DeploymentEventPageOut:
    project = _project_for_member(db, project_id, user.id)

    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .one_or_none()
    )
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    query = db.query(DeploymentEvent).filter(DeploymentEvent.deployment_id == deployment.id)
    if cursor:
        try:
            cursor_time, cursor_id = decode_created_at_cursor(cursor)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid event cursor",
            ) from exc
        query = query.filter(
            or_(
                DeploymentEvent.created_at < cursor_time,
                and_(
                    DeploymentEvent.created_at == cursor_time,
                    DeploymentEvent.id < cursor_id,
                ),
            )
        )

    rows = (
        query.order_by(DeploymentEvent.created_at.desc(), DeploymentEvent.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    events = rows[:limit]
    return DeploymentEventPageOut(
        items=[
            DeploymentEventOut(
                id=event.id,
                event_type=event.event_type,
                message=event.message,
                created_at=event.created_at,
            )
            for event in events
        ],
        next_cursor=(
            encode_created_at_cursor(events[-1].created_at, events[-1].id)
            if has_more and events
            else None
        ),
    )


@router.get(
    "/projects/{project_id}/deployments/{deployment_id}/logs",
    response_model=KubernetesPodLogsOut,
)
def get_deployment_logs(
    project_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    container: str = Query(
        "app", min_length=1, max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
    ),
    since_seconds: int | None = Query(default=None, ge=1, le=86400),
    tail_lines: int = Query(default=100, ge=1, le=1000),
) -> KubernetesPodLogsOut:
    project = _project_for_member(db, project_id, user.id)
    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    adapter = _diagnostics_adapter()
    try:
        items, truncated, source_time = adapter.read_pod_logs(
            project.id,
            deployment.id,
            container,
            since_seconds,
            tail_lines,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except KubernetesDiagnosticsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ApiException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kubernetes log source is unavailable.",
        ) from exc
    return KubernetesPodLogsOut(
        items=[
            KubernetesPodLogOut(
                pod_name=item.pod_name,
                container=item.container,
                log=item.log,
                truncated=item.truncated,
            )
            for item in items
        ],
        source_time=source_time,
        truncated=truncated,
    )


@router.get(
    "/projects/{project_id}/deployments/{deployment_id}/kubernetes-events",
    response_model=KubernetesEventPageOut,
)
def list_kubernetes_events(
    project_id: str,
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KubernetesEventPageOut:
    project = _project_for_member(db, project_id, user.id)
    deployment = (
        db.query(Deployment)
        .filter(Deployment.id == deployment_id, Deployment.project_id == project.id)
        .one_or_none()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    adapter = _diagnostics_adapter()
    try:
        items, truncated, source_time = adapter.list_kubernetes_events(
            project.id,
            deployment.id,
        )
    except KubernetesDiagnosticsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ApiException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kubernetes event source is unavailable.",
        ) from exc
    return KubernetesEventPageOut(
        items=[
            KubernetesEventOut(
                resource_kind=item.resource_kind,
                resource_name=item.resource_name,
                event_type=item.event_type,
                reason=item.reason,
                message=item.message,
                created_at=item.created_at,
            )
            for item in items
        ],
        source_time=source_time,
        truncated=truncated,
    )


@router.get("/operations/{deployment_id}", response_model=OperationOut)
def get_operation_state(
    deployment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OperationOut:
    deployment = (
        db.query(Deployment)
        .join(ProjectMembership, ProjectMembership.project_id == Deployment.project_id)
        .filter(Deployment.id == deployment_id, ProjectMembership.user_id == user.id)
        .one_or_none()
    )
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    job = (
        db.query(DeploymentJob)
        .filter(DeploymentJob.deployment_id == deployment.id)
        .order_by(DeploymentJob.created_at.desc())
        .first()
    )
    return OperationOut(
        deployment_id=deployment.id,
        kubernetes_resource_name=deployment_resource_name(deployment.id),
        revision_id=job.revision_id if job else None,
        job_state=job.state if job else None,
        status=deployment.status,
        last_error=deployment.last_error,
    )
