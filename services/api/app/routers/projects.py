from __future__ import annotations

import hashlib
import json
import re

from app.core.audit import add_audit_event
from app.core.db import get_db
from app.core.models import (
    Project,
    ProjectMembership,
    ProjectResourcePolicy,
    ProjectResourcePolicyMutation,
    User,
)
from app.core.pagination import decode_created_at_cursor, encode_created_at_cursor
from app.core.resource_policy import (
    InvalidProjectQuota,
    new_default_project_policy,
    parse_cpu_limit,
    parse_memory_limit,
    project_quota_out,
    validate_project_policy,
)
from app.core.security import get_current_user
from app.schemas import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectMemberRoleUpdate,
    ProjectOut,
    ProjectPageOut,
    ProjectQuotaOut,
    ProjectQuotaUpdate,
    ProjectUpdate,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1", tags=["projects"])


def _project_or_404(db: Session, project_id: str) -> Project:
    project = db.query(Project).filter(Project.id == project_id).one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


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


def _membership(db: Session, project_id: str, user_id: str) -> ProjectMembership | None:
    return (
        db.query(ProjectMembership)
        .filter(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id)
        .one_or_none()
    )


def _require_owner(db: Session, project_id: str, user_id: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .with_for_update()
        .one_or_none()
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    membership = _membership(db, project.id, user_id)
    if membership is None or membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners can manage membership",
        )
    return project


def _require_project_owner(db: Session, project_id: str, user_id: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .with_for_update()
        .one_or_none()
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    membership = _membership(db, project.id, user_id)
    if membership is None or membership.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners can update the resource policy",
        )
    return project


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    slug = payload.slug.strip().lower()
    if db.query(Project).filter(Project.slug == slug).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project slug already exists")

    project = Project(name=payload.name.strip(), slug=slug, owner_id=user.id)
    db.add(project)
    db.flush()

    membership = ProjectMembership(project_id=project.id, user_id=user.id, role="owner")
    db.add(membership)
    db.add(new_default_project_policy(project.id, user.id))
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
    )
    db.commit()
    db.refresh(project)
    return ProjectOut(id=project.id, name=project.name, slug=project.slug, status=project.status, owner_id=project.owner_id)


@router.get("/projects", response_model=ProjectPageOut)
def list_projects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> ProjectPageOut:
    query = (
        db.query(Project)
        .join(ProjectMembership, Project.id == ProjectMembership.project_id)
        .filter(ProjectMembership.user_id == user.id)
    )
    if cursor:
        try:
            cursor_time, cursor_id = decode_created_at_cursor(cursor)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project cursor",
            ) from exc
        query = query.filter(
            or_(
                Project.created_at < cursor_time,
                and_(Project.created_at == cursor_time, Project.id < cursor_id),
            )
        )

    rows = (
        query.order_by(Project.created_at.desc(), Project.id.desc())
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    projects = rows[:limit]
    return ProjectPageOut(
        items=[
            ProjectOut(id=p.id, name=p.name, slug=p.slug, status=p.status, owner_id=p.owner_id)
            for p in projects
        ],
        next_cursor=(
            encode_created_at_cursor(projects[-1].created_at, projects[-1].id)
            if has_more and projects
            else None
        ),
    )


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = _project_for_member(db, project_id, user.id)

    return ProjectOut(id=project.id, name=project.name, slug=project.slug, status=project.status, owner_id=project.owner_id)


@router.get("/projects/{project_id}/quota", response_model=ProjectQuotaOut)
def get_project_quota(
    project_id: str,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectQuotaOut:
    project = _project_for_member(db, project_id, user.id)
    policy = (
        db.query(ProjectResourcePolicy)
        .filter(ProjectResourcePolicy.project_id == project.id)
        .one_or_none()
    )
    result = project_quota_out(policy)
    if result.version is not None:
        response.headers["ETag"] = f'"{result.version}"'
    return result


@router.patch("/projects/{project_id}/quota", response_model=ProjectQuotaOut)
def update_project_quota(
    project_id: str,
    payload: ProjectQuotaUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectQuotaOut:
    project = _require_project_owner(db, project_id, user.id)
    version_match = re.fullmatch(r'"(0|[1-9][0-9]*)"', if_match or "")
    if version_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail='If-Match must contain the quoted resource-policy version, or "0" for an unconfigured project',
        )
    expected_version = int(version_match.group(1))

    try:
        cpu_limit_millicores = parse_cpu_limit(payload.cpu_limit)
        memory_limit_bytes = parse_memory_limit(payload.memory_limit)
        cpu_request_millicores, memory_request_bytes = validate_project_policy(
            max_replicas=payload.max_replicas,
            cpu_limit_millicores=cpu_limit_millicores,
            memory_limit_bytes=memory_limit_bytes,
        )
    except InvalidProjectQuota as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    request_hash = hashlib.sha256(
        json.dumps(
            {
                "expected_version": expected_version,
                "max_replicas": payload.max_replicas,
                "cpu_limit_millicores": cpu_limit_millicores,
                "memory_limit_bytes": memory_limit_bytes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    prior_mutation = (
        db.query(ProjectResourcePolicyMutation)
        .filter(
            ProjectResourcePolicyMutation.project_id == project.id,
            ProjectResourcePolicyMutation.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if prior_mutation is not None:
        if prior_mutation.request_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key has already been used for a different quota request",
            )
        result = ProjectQuotaOut.model_validate(prior_mutation.response)
        if result.version is not None:
            response.headers["ETag"] = f'"{result.version}"'
        return result

    policy = (
        db.query(ProjectResourcePolicy)
        .filter(ProjectResourcePolicy.project_id == project.id)
        .with_for_update()
        .one_or_none()
    )
    current_version = policy.version if policy is not None else 0
    if expected_version != current_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource policy changed; refresh the project quota before retrying",
        )

    if policy is None:
        policy = ProjectResourcePolicy(
            project_id=project.id,
            max_replicas=payload.max_replicas,
            cpu_request_millicores=cpu_request_millicores,
            cpu_limit_millicores=cpu_limit_millicores,
            memory_request_bytes=memory_request_bytes,
            memory_limit_bytes=memory_limit_bytes,
            version=1,
            applied_version=None,
            updated_by_user_id=user.id,
        )
        db.add(policy)
    else:
        policy.max_replicas = payload.max_replicas
        policy.cpu_request_millicores = cpu_request_millicores
        policy.cpu_limit_millicores = cpu_limit_millicores
        policy.memory_request_bytes = memory_request_bytes
        policy.memory_limit_bytes = memory_limit_bytes
        policy.version += 1
        policy.updated_by_user_id = user.id

    db.flush()
    result = project_quota_out(policy)
    mutation = ProjectResourcePolicyMutation(
        project_id=project.id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        policy_version=result.version or 1,
        response=result.model_dump(mode="json"),
    )
    db.add(mutation)
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="project.quota_updated",
        resource_type="resource_policy",
        resource_id=policy.id,
        details={
            "version": policy.version,
            "max_replicas": policy.max_replicas,
            "cpu_limit": payload.cpu_limit,
            "memory_limit": payload.memory_limit,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        prior_mutation = (
            db.query(ProjectResourcePolicyMutation)
            .filter(
                ProjectResourcePolicyMutation.project_id == project.id,
                ProjectResourcePolicyMutation.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if prior_mutation is not None and prior_mutation.request_hash == request_hash:
            result = ProjectQuotaOut.model_validate(prior_mutation.response)
            if result.version is not None:
                response.headers["ETag"] = f'"{result.version}"'
            return result
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resource policy update conflicted with another request",
        ) from exc

    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: str,
    payload: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    project = _project_or_404(db, project_id)

    membership = _membership(db, project.id, user.id)
    if not membership or membership.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project owners can update the project")

    if payload.name is not None:
        updated_name = payload.name.strip()
        if updated_name != project.name:
            project.name = updated_name
            add_audit_event(
                db,
                actor_user_id=user.id,
                project_id=project.id,
                action="project.updated",
                resource_type="project",
                resource_id=project.id,
                details={"changed_fields": ["name"]},
            )
    project.updated_at = project.updated_at
    db.commit()
    db.refresh(project)
    return ProjectOut(id=project.id, name=project.name, slug=project.slug, status=project.status, owner_id=project.owner_id)


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberOut])
def list_project_members(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProjectMemberOut]:
    project = _project_for_member(db, project_id, user.id)

    rows = (
        db.query(ProjectMembership, User.email)
        .join(User, User.id == ProjectMembership.user_id)
        .filter(ProjectMembership.project_id == project.id)
        .order_by(User.email)
        .all()
    )
    return [
        ProjectMemberOut(user_id=membership.user_id, email=email, role=membership.role)
        for membership, email in rows
    ]


@router.post(
    "/projects/{project_id}/members",
    response_model=ProjectMemberOut,
    status_code=status.HTTP_201_CREATED,
)
def add_project_member(
    project_id: str,
    payload: ProjectMemberCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectMemberOut:
    project = _require_owner(db, project_id, user.id)
    invitee = db.query(User).filter(User.email == payload.email).one_or_none()
    if invitee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if _membership(db, project.id, invitee.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already a project member")

    membership = ProjectMembership(
        project_id=project.id,
        user_id=invitee.id,
        role=payload.role,
    )
    db.add(membership)
    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="project.member_added",
        resource_type="project_membership",
        resource_id=invitee.id,
        details={"target_user_id": invitee.id, "role": payload.role},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a project member",
        ) from exc
    return ProjectMemberOut(user_id=invitee.id, email=invitee.email, role=membership.role)


@router.patch(
    "/projects/{project_id}/members/{member_user_id}",
    response_model=ProjectMemberOut,
)
def update_project_member_role(
    project_id: str,
    member_user_id: str,
    payload: ProjectMemberRoleUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectMemberOut:
    project = _require_owner(db, project_id, user.id)
    membership = _membership(db, project.id, member_user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project member not found")

    if membership.role == "owner" and payload.role != "owner":
        replacement_owner = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.project_id == project.id,
                ProjectMembership.role == "owner",
                ProjectMembership.user_id != member_user_id,
            )
            .order_by(ProjectMembership.created_at, ProjectMembership.user_id)
            .first()
        )
        if replacement_owner is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must retain at least one owner")
        if project.owner_id == member_user_id:
            project.owner_id = replacement_owner.user_id

    previous_role = membership.role
    membership.role = payload.role
    invitee = db.query(User).filter(User.id == member_user_id).one()
    if previous_role != payload.role:
        add_audit_event(
            db,
            actor_user_id=user.id,
            project_id=project.id,
            action="project.member_role_updated",
            resource_type="project_membership",
            resource_id=member_user_id,
            details={
                "target_user_id": member_user_id,
                "old_role": previous_role,
                "new_role": payload.role,
            },
        )
    db.commit()
    return ProjectMemberOut(user_id=invitee.id, email=invitee.email, role=membership.role)


@router.delete(
    "/projects/{project_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_project_member(
    project_id: str,
    member_user_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    project = _require_owner(db, project_id, user.id)
    membership = _membership(db, project.id, member_user_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project member not found")

    if membership.role == "owner":
        replacement_owner = (
            db.query(ProjectMembership)
            .filter(
                ProjectMembership.project_id == project.id,
                ProjectMembership.role == "owner",
                ProjectMembership.user_id != member_user_id,
            )
            .order_by(ProjectMembership.created_at, ProjectMembership.user_id)
            .first()
        )
        if replacement_owner is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must retain at least one owner")
        if project.owner_id == member_user_id:
            project.owner_id = replacement_owner.user_id

    add_audit_event(
        db,
        actor_user_id=user.id,
        project_id=project.id,
        action="project.member_removed",
        resource_type="project_membership",
        resource_id=member_user_id,
        details={"target_user_id": member_user_id, "role": membership.role},
    )
    db.delete(membership)
    db.commit()
