'use client';

import { useEffect, useRef, useState } from 'react';
import {
  apiFetch,
  apiFetchNoContent,
  type Deployment,
  type DeploymentPage,
  type DeploymentEvent,
  type DeploymentEventPage,
  type DeploymentRevision,
  type DeploymentRevisionPage,
  type KubernetesEvent,
  type KubernetesEventPage,
  type KubernetesPodLog,
  type KubernetesPodLogs,
  type Operation,
  type Project,
  type ProjectPage,
  type ProjectQuota,
  type ProjectMember,
  type User,
} from '@/lib/api';

const MAX_PROJECT_REPLICAS = 4;

export default function HomePage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string>('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [nextProjectCursor, setNextProjectCursor] = useState<string | null>(null);
  const [activeProjectId, setActiveProjectId] = useState('');
  const [projectMembers, setProjectMembers] = useState<ProjectMember[]>([]);
  const [projectQuota, setProjectQuota] = useState<ProjectQuota | null>(null);
  const [quotaProjectId, setQuotaProjectId] = useState('');
  const [quotaMaxReplicas, setQuotaMaxReplicas] = useState('4');
  const [quotaCpuLimit, setQuotaCpuLimit] = useState('2');
  const [quotaMemoryLimit, setQuotaMemoryLimit] = useState('2Gi');
  const [quotaIdempotencyKey, setQuotaIdempotencyKey] = useState('');
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [nextDeploymentCursor, setNextDeploymentCursor] = useState<string | null>(null);
  const [selectedDeploymentId, setSelectedDeploymentId] = useState('');
  const [deploymentEvents, setDeploymentEvents] = useState<DeploymentEvent[]>([]);
  const [nextEventCursor, setNextEventCursor] = useState<string | null>(null);
  const [podLogs, setPodLogs] = useState<KubernetesPodLog[]>([]);
  const [podLogsDeploymentId, setPodLogsDeploymentId] = useState('');
  const [podLogsSourceTime, setPodLogsSourceTime] = useState('');
  const [podLogsTruncated, setPodLogsTruncated] = useState(false);
  const [kubernetesEvents, setKubernetesEvents] = useState<KubernetesEvent[]>([]);
  const [kubernetesEventsDeploymentId, setKubernetesEventsDeploymentId] = useState('');
  const [kubernetesEventsSourceTime, setKubernetesEventsSourceTime] = useState('');
  const [kubernetesEventsTruncated, setKubernetesEventsTruncated] = useState(false);
  const [diagnosticsError, setDiagnosticsError] = useState('');
  const [diagnosticsErrorDeploymentId, setDiagnosticsErrorDeploymentId] = useState('');
  const [diagnosticsRequest, setDiagnosticsRequest] = useState<{
    deploymentId: string;
    kind: 'logs' | 'events';
    requestId: number;
  } | null>(null);
  const diagnosticsRequestId = useRef(0);
  const activeProjectIdRef = useRef('');
  const [deploymentRevisions, setDeploymentRevisions] = useState<DeploymentRevision[]>([]);
  const [nextRevisionCursor, setNextRevisionCursor] = useState<string | null>(null);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [isLoadingMoreProjects, setIsLoadingMoreProjects] = useState(false);
  const [isLoadingMembers, setIsLoadingMembers] = useState(false);
  const [isLoadingDeployments, setIsLoadingDeployments] = useState(false);
  const [isLoadingMoreDeployments, setIsLoadingMoreDeployments] = useState(false);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [isLoadingRevisions, setIsLoadingRevisions] = useState(false);
  const [isLoadingMoreRevisions, setIsLoadingMoreRevisions] = useState(false);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSubmittingMember, setIsSubmittingMember] = useState(false);
  const [isLoadingQuota, setIsLoadingQuota] = useState(false);
  const [isUpdatingQuota, setIsUpdatingQuota] = useState(false);
  const [isScaling, setIsScaling] = useState(false);
  const [isRollingBack, setIsRollingBack] = useState(false);
  const [rollbackRequest, setRollbackRequest] = useState<{
    deploymentId: string;
    revisionId: string;
    idempotencyKey: string;
  } | null>(null);
  const [scalingDeploymentId, setScalingDeploymentId] = useState('');
  const [scaleReplicas, setScaleReplicas] = useState('');
  const [scaleIdempotencyKey, setScaleIdempotencyKey] = useState('');
  const [projectName, setProjectName] = useState('demo-project');
  const [projectSlug, setProjectSlug] = useState('demo-project');
  const [memberEmail, setMemberEmail] = useState('');
  const [memberRole, setMemberRole] = useState<ProjectMember['role']>('member');
  const [deploymentName, setDeploymentName] = useState('web');
  const [image, setImage] = useState('nginx:1.27-alpine');
  const [deploymentPort, setDeploymentPort] = useState('80');
  const [deploymentReplicas, setDeploymentReplicas] = useState('2');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!token || !activeProjectId) return;

    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      setIsLoadingQuota(true);
      try {
        const quota = await apiFetch<ProjectQuota>(`/api/v1/projects/${activeProjectId}/quota`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (cancelled) return;
        setProjectQuota(quota);
        setQuotaProjectId(activeProjectId);
        setQuotaMaxReplicas(String(quota.desired?.max_replicas ?? 4));
        setQuotaCpuLimit(quota.desired?.cpu_limit ?? '2');
        setQuotaMemoryLimit(quota.desired?.memory_limit ?? '2Gi');
        setQuotaIdempotencyKey('');
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setIsLoadingQuota(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, token]);

  useEffect(() => {
    if (!token || !user) return;
    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      setIsLoadingProjects(true);
      try {
        const page = await apiFetch<ProjectPage>('/api/v1/projects?limit=50', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (cancelled) return;
        setProjects(page.items);
        setNextProjectCursor(page.next_cursor);
        const nextProjectId = page.items.some((project) => project.id === activeProjectIdRef.current)
          ? activeProjectIdRef.current
          : page.items[0]?.id ?? '';
        activeProjectIdRef.current = nextProjectId;
        setActiveProjectId(nextProjectId);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setIsLoadingProjects(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, user]);

  useEffect(() => {
    if (!token || !activeProjectId) return;

    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      setProjectMembers([]);
      setIsLoadingMembers(true);
      try {
        const members = await apiFetch<ProjectMember[]>(`/api/v1/projects/${activeProjectId}/members`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!cancelled) setProjectMembers(members);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setIsLoadingMembers(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, token]);

  useEffect(() => {
    if (!token || !activeProjectId) return;
    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      setIsLoadingDeployments(true);
      try {
        const page = await apiFetch<DeploymentPage>(
          `/api/v1/projects/${activeProjectId}/deployments?limit=50`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (!cancelled) {
          setDeployments(page.items);
          setNextDeploymentCursor(page.next_cursor);
          setSelectedDeploymentId((current) => page.items.some((deployment) => deployment.id === current)
            ? current
            : page.items[0]?.id ?? '');
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setIsLoadingDeployments(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeProjectId, token]);

  const hasPendingDeployments = deployments.some((deployment) => !['ready', 'failed'].includes(deployment.status));
  const selectedDeploymentStatus = deployments.find((deployment) => deployment.id === selectedDeploymentId)?.status ?? '';
  const isLoadingDiagnostics = diagnosticsRequest?.deploymentId === selectedDeploymentId;
  const loadingDiagnosticsKind = isLoadingDiagnostics ? diagnosticsRequest?.kind : null;
  const activeProjectMembership = projectMembers.find((member) => member.user_id === user?.id);
  const canManageMembers = activeProjectMembership?.role === 'owner';

  useEffect(() => {
    if (!token || !activeProjectId || !hasPendingDeployments) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void apiFetch<DeploymentPage>(
        `/api/v1/projects/${activeProjectId}/deployments?limit=50`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
        .then((page) => {
          if (!cancelled) {
            setDeployments((current) => [
              ...page.items,
              ...current.filter((item) => !page.items.some((fresh) => fresh.id === item.id)),
            ]);
            setNextDeploymentCursor(page.next_cursor);
          }
        })
        .catch((err: Error) => {
          if (!cancelled) setError(err.message);
        });
    }, 5000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeProjectId, deployments, hasPendingDeployments, token]);

  useEffect(() => {
    if (!token || !activeProjectId || !selectedDeploymentId) return;

    let cancelled = false;
    void apiFetch<DeploymentEventPage>(
      `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/events?limit=20`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
      .then((page) => {
        if (cancelled) return;
        setDeploymentEvents(page.items);
        setNextEventCursor(page.next_cursor);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, selectedDeploymentId, selectedDeploymentStatus, token]);

  useEffect(() => {
    if (!token || !activeProjectId || !selectedDeploymentId) return;

    let cancelled = false;
    void (async () => {
      await Promise.resolve();
      if (cancelled) return;
      setIsLoadingRevisions(true);
      try {
        const page = await apiFetch<DeploymentRevisionPage>(
          `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/revisions?limit=20`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        if (cancelled) return;
        setDeploymentRevisions(page.items);
        setNextRevisionCursor(page.next_cursor);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setIsLoadingRevisions(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, selectedDeploymentId, selectedDeploymentStatus, token]);

  useEffect(() => {
    if (
      !token
      || !activeProjectId
      || !selectedDeploymentId
      || !diagnosticsRequest
      || diagnosticsRequest.deploymentId !== selectedDeploymentId
    ) return;

    let cancelled = false;
    const { kind } = diagnosticsRequest;

    void (async () => {
      try {
        if (kind === 'logs') {
          const logs = await apiFetch<KubernetesPodLogs>(
            `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/logs?container=app&tail_lines=100`,
            { headers: { Authorization: `Bearer ${token}` } },
          );
          if (cancelled) return;
          setPodLogs(logs.items);
          setPodLogsDeploymentId(selectedDeploymentId);
          setPodLogsSourceTime(logs.source_time);
          setPodLogsTruncated(logs.truncated);
        } else {
          const events = await apiFetch<KubernetesEventPage>(
            `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/kubernetes-events`,
            { headers: { Authorization: `Bearer ${token}` } },
          );
          if (cancelled) return;
          setKubernetesEvents(events.items);
          setKubernetesEventsDeploymentId(selectedDeploymentId);
          setKubernetesEventsSourceTime(events.source_time);
          setKubernetesEventsTruncated(events.truncated);
        }
      } catch (err) {
        if (!cancelled) {
          setDiagnosticsError(err instanceof Error ? err.message : 'Kubernetes diagnostics request failed.');
          setDiagnosticsErrorDeploymentId(selectedDeploymentId);
        }
      } finally {
        if (!cancelled) {
          setDiagnosticsRequest((current) => (
            current?.requestId === diagnosticsRequest.requestId ? null : current
          ));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, diagnosticsRequest, selectedDeploymentId, token]);

  const clearKubernetesDiagnostics = () => {
    setPodLogs([]);
    setPodLogsDeploymentId('');
    setPodLogsSourceTime('');
    setPodLogsTruncated(false);
    setKubernetesEvents([]);
    setKubernetesEventsDeploymentId('');
    setKubernetesEventsSourceTime('');
    setKubernetesEventsTruncated(false);
    setDiagnosticsError('');
    setDiagnosticsErrorDeploymentId('');
    setDiagnosticsRequest(null);
  };

  const handleAuth = async (mode: 'login' | 'register') => {
    if (isAuthenticating) return;
    setError('');
    setStatus('');
    setIsAuthenticating(true);
    try {
      const result = await apiFetch<{ user: User; access_token: string; expires_in: number }>(`/api/v1/auth/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      setUser(result.user);
      setToken(result.access_token);
      setStatus(`${mode === 'login' ? 'Signed in' : 'Registered'} successfully.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPassword('');
      setIsAuthenticating(false);
    }
  };

  const handleCreateProject = async () => {
    if (!token || !projectName.trim() || !projectSlug.trim() || isSubmitting) return;
    setIsSubmitting(true);
    setError('');
    try {
      const newProject = await apiFetch<Project>('/api/v1/projects', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ name: projectName.trim(), slug: projectSlug.trim() }),
      });
      setProjects((current) => [newProject, ...current]);
      setProjectMembers([]);
      setProjectQuota(null);
      setQuotaProjectId('');
      setQuotaIdempotencyKey('');
      setDeployments([]);
      setNextDeploymentCursor(null);
      setSelectedDeploymentId('');
      clearKubernetesDiagnostics();
      setDeploymentRevisions([]);
      setNextRevisionCursor(null);
      setScalingDeploymentId('');
      setScaleIdempotencyKey('');
      activeProjectIdRef.current = newProject.id;
      setActiveProjectId(newProject.id);
      setStatus(`Created project ${newProject.name}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateProjectQuota = async () => {
    if (!token || !activeProjectId || !canManageMembers || isUpdatingQuota) return;
    const maxReplicas = Number(quotaMaxReplicas);
    if (!Number.isInteger(maxReplicas) || maxReplicas < 1 || maxReplicas > MAX_PROJECT_REPLICAS) {
      setError(`Maximum replicas must be an integer between 1 and ${MAX_PROJECT_REPLICAS}.`);
      return;
    }
    if (!quotaCpuLimit.trim() || !quotaMemoryLimit.trim()) {
      setError('CPU and memory limits are required.');
      return;
    }

    const projectId = activeProjectId;
    const idempotencyKey = quotaIdempotencyKey || crypto.randomUUID();
    setQuotaIdempotencyKey(idempotencyKey);
    setIsUpdatingQuota(true);
    setError('');
    try {
      const updated = await apiFetch<ProjectQuota>(`/api/v1/projects/${projectId}/quota`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'If-Match': `"${projectQuota?.version ?? 0}"`,
          'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({
          max_replicas: maxReplicas,
          cpu_limit: quotaCpuLimit.trim(),
          memory_limit: quotaMemoryLimit.trim(),
        }),
      });
      if (activeProjectIdRef.current !== projectId) return;
      setProjectQuota(updated);
      setQuotaProjectId(projectId);
      setQuotaMaxReplicas(String(updated.desired?.max_replicas ?? maxReplicas));
      setQuotaCpuLimit(updated.desired?.cpu_limit ?? quotaCpuLimit.trim());
      setQuotaMemoryLimit(updated.desired?.memory_limit ?? quotaMemoryLimit.trim());
      setQuotaIdempotencyKey('');
      setStatus(`Project resource policy saved (version ${updated.version ?? 'unknown'}; ${updated.state}).`);
    } catch (err) {
      if (activeProjectIdRef.current !== projectId) return;
      const message = (err as Error).message;
      setError(message);
      if (message === 'Resource policy changed; refresh the project quota before retrying') {
        try {
          const current = await apiFetch<ProjectQuota>(`/api/v1/projects/${projectId}/quota`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (activeProjectIdRef.current !== projectId) return;
          setProjectQuota(current);
          setQuotaProjectId(projectId);
          setQuotaMaxReplicas(String(current.desired?.max_replicas ?? 4));
          setQuotaCpuLimit(current.desired?.cpu_limit ?? '2');
          setQuotaMemoryLimit(current.desired?.memory_limit ?? '2Gi');
          setQuotaIdempotencyKey('');
        } catch (refreshError) {
          if (activeProjectIdRef.current !== projectId) return;
          setError(`${message} Quota state could not be refreshed: ${(refreshError as Error).message}`);
        }
      }
    } finally {
      setIsUpdatingQuota(false);
    }
  };

  const handleLoadMoreProjects = async () => {
    if (!token || !nextProjectCursor || isLoadingMoreProjects) return;
    setIsLoadingMoreProjects(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '50', cursor: nextProjectCursor });
      const page = await apiFetch<ProjectPage>(`/api/v1/projects?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setProjects((current) => [
        ...current,
        ...page.items.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setNextProjectCursor(page.next_cursor);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoadingMoreProjects(false);
    }
  };

  const handleLoadMoreDeployments = async () => {
    if (!token || !activeProjectId || !nextDeploymentCursor || isLoadingMoreDeployments) return;
    setIsLoadingMoreDeployments(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '50', cursor: nextDeploymentCursor });
      const page = await apiFetch<DeploymentPage>(
        `/api/v1/projects/${activeProjectId}/deployments?${params}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setDeployments((current) => [
        ...current,
        ...page.items.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setNextDeploymentCursor(page.next_cursor);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoadingMoreDeployments(false);
    }
  };

  const handleAddMember = async () => {
    if (!token || !activeProjectId || !memberEmail.trim() || isSubmittingMember) return;
    setIsSubmittingMember(true);
    setError('');
    try {
      const member = await apiFetch<ProjectMember>(`/api/v1/projects/${activeProjectId}/members`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: JSON.stringify({ email: memberEmail.trim(), role: memberRole }),
      });
      setProjectMembers((current) => [...current.filter((item) => item.user_id !== member.user_id), member]);
      setMemberEmail('');
      setStatus(`Added ${member.email} to the project.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsSubmittingMember(false);
    }
  };

  const handleUpdateMemberRole = async (member: ProjectMember, role: ProjectMember['role']) => {
    if (!token || !activeProjectId || isSubmittingMember || member.role === role) return;
    setIsSubmittingMember(true);
    setError('');
    try {
      const updated = await apiFetch<ProjectMember>(
        `/api/v1/projects/${activeProjectId}/members/${member.user_id}`,
        {
          method: 'PATCH',
          headers: { Authorization: `Bearer ${token}` },
          body: JSON.stringify({ role }),
        },
      );
      setProjectMembers((current) => current.map((item) => item.user_id === updated.user_id ? updated : item));
      setStatus(`Updated ${updated.email} to ${updated.role}.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsSubmittingMember(false);
    }
  };

  const handleRemoveMember = async (member: ProjectMember) => {
    if (!token || !activeProjectId || isSubmittingMember) return;
    if (!window.confirm(`Remove ${member.email} from this project?`)) return;
    setIsSubmittingMember(true);
    setError('');
    try {
      await apiFetchNoContent(
        `/api/v1/projects/${activeProjectId}/members/${member.user_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        },
      );
      setProjectMembers((current) => current.filter((item) => item.user_id !== member.user_id));
      if (member.user_id === user?.id) {
        const remainingProjects = projects.filter((project) => project.id !== activeProjectId);
        setProjects(remainingProjects);
        const nextProjectId = remainingProjects[0]?.id ?? '';
        activeProjectIdRef.current = nextProjectId;
        setActiveProjectId(nextProjectId);
        setDeployments([]);
        setProjectQuota(null);
        setQuotaProjectId('');
        setQuotaIdempotencyKey('');
        setSelectedDeploymentId('');
        clearKubernetesDiagnostics();
        setScalingDeploymentId('');
        setScaleIdempotencyKey('');
        setDeploymentEvents([]);
        setNextEventCursor(null);
        setDeploymentRevisions([]);
        setNextRevisionCursor(null);
        setRollbackRequest(null);
      }
      setStatus(`Removed ${member.email} from the project.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsSubmittingMember(false);
    }
  };

  const handleCreateDeployment = async () => {
    if (!token || !activeProjectId || !deploymentName.trim() || !image.trim() || isSubmitting) return;
    const port = Number(deploymentPort);
    const replicas = Number(deploymentReplicas);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setError('Container port must be an integer between 1 and 65535.');
      return;
    }
    if (!Number.isInteger(replicas) || replicas < 1 || replicas > MAX_PROJECT_REPLICAS) {
      setError(`Replicas must be an integer between 1 and ${MAX_PROJECT_REPLICAS}.`);
      return;
    }
    setIsSubmitting(true);
    setError('');
    try {
      const created = await apiFetch<Operation>(
        `/api/v1/projects/${activeProjectId}/deployments`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            name: deploymentName.trim(),
            image: image.trim(),
            port,
            replicas,
            idempotency_key: crypto.randomUUID(),
          }),
        },
      );
      setStatus(`Deployment ${created.deployment_id}: ${created.job_state ?? created.status} (${created.status})`);
      const updated = await apiFetch<DeploymentPage>(
        `/api/v1/projects/${activeProjectId}/deployments?limit=50`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setDeployments(updated.items);
      setNextDeploymentCursor(updated.next_cursor);
      setDeploymentEvents([]);
      setNextEventCursor(null);
      clearKubernetesDiagnostics();
      setDeploymentRevisions([]);
      setNextRevisionCursor(null);
      setSelectedDeploymentId(updated.items[0]?.id ?? created.deployment_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleScaleDeployment = async (deployment: Deployment) => {
    if (!token || !activeProjectId || scalingDeploymentId !== deployment.id || isScaling) return;
    const replicas = Number(scaleReplicas);
    if (!Number.isInteger(replicas) || replicas < 1 || replicas > MAX_PROJECT_REPLICAS) {
      setError(`Replicas must be an integer between 1 and ${MAX_PROJECT_REPLICAS}.`);
      return;
    }
    if (replicas === deployment.replicas) {
      setError('Choose a replica count different from the current count.');
      return;
    }
    if (!scaleIdempotencyKey) {
      setError('Scale request is missing its idempotency key. Close and reopen the scale form.');
      return;
    }

    setIsScaling(true);
    setError('');
    try {
      const operation = await apiFetch<Operation>(
        `/api/v1/projects/${activeProjectId}/deployments/${deployment.id}/scale`,
        {
          method: 'PATCH',
          headers: { Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            replicas,
            expected_replicas: deployment.replicas,
            idempotency_key: scaleIdempotencyKey,
          }),
        },
      );
      if (operation.deployment_id !== deployment.id) {
        throw new Error('MiniCloud API returned a mismatched scale operation.');
      }
      setDeployments((current) => current.map((item) => item.id === deployment.id
        ? {
          ...item,
          replicas,
          status: operation.status,
          last_error: operation.last_error,
        }
        : item));
      clearKubernetesDiagnostics();
      setSelectedDeploymentId(deployment.id);
      setStatus(`Scale request accepted for ${replicas} replicas; waiting for readiness.`);
      setScalingDeploymentId('');
      setScaleIdempotencyKey('');
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      if (message === 'Deployment replica count changed; refresh before retrying') {
        try {
          const refreshed = await apiFetch<DeploymentPage>(
            `/api/v1/projects/${activeProjectId}/deployments?limit=50`,
            { headers: { Authorization: `Bearer ${token}` } },
          );
          setDeployments((current) => [
            ...refreshed.items,
            ...current.filter((item) => !refreshed.items.some((fresh) => fresh.id === item.id)),
          ]);
          setNextDeploymentCursor(refreshed.next_cursor);
        } catch (refreshError) {
          setError(`${message} Current deployment state could not be refreshed: ${(refreshError as Error).message}`);
        }
      }
    } finally {
      setIsScaling(false);
    }
  };

  const handleLogout = async () => {
    if (isAuthenticating) return;
    setIsAuthenticating(true);
    let revocationFailed = false;
    try {
      await apiFetch<{ logged_out: boolean }>('/api/v1/auth/logout', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      revocationFailed = true;
    }
    setUser(null);
    setToken('');
    setPassword('');
    setProjects([]);
    setNextProjectCursor(null);
    activeProjectIdRef.current = '';
    setActiveProjectId('');
    setProjectMembers([]);
    setProjectQuota(null);
    setQuotaProjectId('');
    setQuotaIdempotencyKey('');
    setDeployments([]);
    setNextDeploymentCursor(null);
    setSelectedDeploymentId('');
    clearKubernetesDiagnostics();
    setScalingDeploymentId('');
    setScaleIdempotencyKey('');
    setDeploymentEvents([]);
    setNextEventCursor(null);
    setDeploymentRevisions([]);
    setNextRevisionCursor(null);
    setRollbackRequest(null);
    setIsLoadingProjects(false);
    setIsLoadingDeployments(false);
    setStatus(revocationFailed ? 'Signed out locally; server-side revocation could not be confirmed.' : 'Signed out.');
    setError('');
    setIsAuthenticating(false);
  };

  const handleLoadKubernetesDiagnostics = (kind: 'logs' | 'events') => {
    if (!token || !activeProjectId || !selectedDeploymentId || isLoadingDiagnostics) return;

    const request = {
      deploymentId: selectedDeploymentId,
      kind,
      requestId: ++diagnosticsRequestId.current,
    };
    setDiagnosticsError('');
    setDiagnosticsErrorDeploymentId('');
    if (kind === 'logs') {
      setPodLogs([]);
      setPodLogsDeploymentId('');
      setPodLogsSourceTime('');
      setPodLogsTruncated(false);
    } else {
      setKubernetesEvents([]);
      setKubernetesEventsDeploymentId('');
      setKubernetesEventsSourceTime('');
      setKubernetesEventsTruncated(false);
    }
    setDiagnosticsRequest(request);
  };

  const handleLoadOlderEvents = async () => {
    if (!token || !activeProjectId || !selectedDeploymentId || !nextEventCursor || isLoadingEvents) return;
    setIsLoadingEvents(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '20', cursor: nextEventCursor });
      const page = await apiFetch<DeploymentEventPage>(
        `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/events?${params}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setDeploymentEvents((current) => [...current, ...page.items]);
      setNextEventCursor(page.next_cursor);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoadingEvents(false);
    }
  };

  const handleLoadMoreRevisions = async () => {
    if (!token || !activeProjectId || !selectedDeploymentId || !nextRevisionCursor || isLoadingMoreRevisions) return;
    setIsLoadingMoreRevisions(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: '20', cursor: nextRevisionCursor });
      const page = await apiFetch<DeploymentRevisionPage>(
        `/api/v1/projects/${activeProjectId}/deployments/${selectedDeploymentId}/revisions?${params}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setDeploymentRevisions((current) => [...current, ...page.items]);
      setNextRevisionCursor(page.next_cursor);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoadingMoreRevisions(false);
    }
  };

  const handleRollbackToRevision = async (target: DeploymentRevision) => {
    const deployment = deployments.find((item) => item.id === selectedDeploymentId);
    const currentRevision = deploymentRevisions[0];
    if (!token || !activeProjectId || !deployment || deployment.status !== 'ready' || isRollingBack) return;
    if (!currentRevision || target.id === currentRevision.id) {
      setError('The current deployment revision cannot be rolled back to itself.');
      return;
    }

    const idempotencyKey = rollbackRequest?.deploymentId === deployment.id
      && rollbackRequest.revisionId === target.id
      ? rollbackRequest.idempotencyKey
      : crypto.randomUUID();
    setRollbackRequest({
      deploymentId: deployment.id,
      revisionId: target.id,
      idempotencyKey,
    });
    setIsRollingBack(true);
    setError('');
    try {
      const operation = await apiFetch<Operation>(
        `/api/v1/projects/${activeProjectId}/deployments/${deployment.id}/rollback`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: JSON.stringify({
            revision_id: target.id,
            expected_revision_id: currentRevision.id,
            idempotency_key: idempotencyKey,
          }),
        },
      );
      if (operation.deployment_id !== deployment.id) {
        throw new Error('MiniCloud API returned a mismatched rollback operation.');
      }
      setDeployments((current) => current.map((item) => item.id === deployment.id
        ? {
          ...item,
          name: target.name,
          image: target.image,
          port: target.port,
          replicas: target.replicas,
          status: operation.status,
          last_error: operation.last_error,
        }
        : item));
      setStatus(`Rollback to revision ${target.revision_number} accepted; waiting for readiness.`);
      setRollbackRequest(null);
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      if (message === 'Deployment revision changed; refresh before retrying') {
        try {
          const [deploymentsPage, revisionsPage] = await Promise.all([
            apiFetch<DeploymentPage>(
              `/api/v1/projects/${activeProjectId}/deployments?limit=50`,
              { headers: { Authorization: `Bearer ${token}` } },
            ),
            apiFetch<DeploymentRevisionPage>(
              `/api/v1/projects/${activeProjectId}/deployments/${deployment.id}/revisions?limit=20`,
              { headers: { Authorization: `Bearer ${token}` } },
            ),
          ]);
          setDeployments((current) => [
            ...deploymentsPage.items,
            ...current.filter((item) => !deploymentsPage.items.some((fresh) => fresh.id === item.id)),
          ]);
          setNextDeploymentCursor(deploymentsPage.next_cursor);
          setDeploymentRevisions(revisionsPage.items);
          setNextRevisionCursor(revisionsPage.next_cursor);
        } catch (refreshError) {
          setError(`${message} Current deployment state could not be refreshed: ${(refreshError as Error).message}`);
        }
      }
    } finally {
      setIsRollingBack(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-6xl space-y-8">
        <header className="flex items-center justify-between border-b border-slate-800 pb-5">
          <div>
            <p className="text-xs uppercase tracking-[0.25em] text-sky-300">MiniCloud</p>
            <h1 className="mt-2 text-3xl font-semibold">Developer Console</h1>
          </div>
          {user ? (
            <button onClick={handleLogout} disabled={isAuthenticating} className="px-4 py-2 text-sm text-slate-200 disabled:opacity-50">
              {isAuthenticating ? 'Signing out...' : 'Sign out'}
            </button>
          ) : null}
        </header>

        <div className="grid gap-6 lg:grid-cols-[350px_1fr]">
          <section className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 shadow-lg shadow-slate-950/30">
            <div className="mb-5 flex rounded-lg border border-slate-700 bg-slate-950 p-1 text-sm">
              <button
                onClick={() => setActiveTab('login')}
                disabled={isAuthenticating}
                className={`flex-1 rounded-md px-3 py-2 ${activeTab === 'login' ? 'bg-sky-500 text-slate-950' : 'text-slate-300'}`}
              >
                Login
              </button>
              <button
                onClick={() => setActiveTab('register')}
                disabled={isAuthenticating}
                className={`flex-1 rounded-md px-3 py-2 ${activeTab === 'register' ? 'bg-sky-500 text-slate-950' : 'text-slate-300'}`}
              >
                Register
              </button>
            </div>

            <div className="space-y-4">
              <label className="block text-sm text-slate-300">
                Email
                <input value={email} onChange={(e) => setEmail(e.target.value)} disabled={isAuthenticating} className="mt-2 w-full px-3 py-2 text-slate-950 disabled:opacity-50" />
              </label>
              <label className="block text-sm text-slate-300">
                Password
                <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" disabled={isAuthenticating} className="mt-2 w-full px-3 py-2 text-slate-950 disabled:opacity-50" />
              </label>
              <button onClick={() => handleAuth(activeTab)} disabled={isAuthenticating} className="w-full bg-sky-500 px-4 py-2 font-medium text-slate-950 disabled:opacity-50">
                {isAuthenticating ? 'Please wait...' : activeTab === 'login' ? 'Sign in' : 'Create account'}
              </button>
            </div>

            {user ? (
              <div className="mt-6 rounded-lg border border-emerald-700 bg-emerald-950/30 p-3 text-sm text-emerald-200">
                Signed in as <span className="font-medium">{user.email}</span>
              </div>
            ) : null}
          </section>

          <section className="space-y-6">
            {user ? <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
              <h2 className="mb-4 text-lg font-semibold">Create project</h2>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-sm text-slate-300">
                  Project name
                  <input value={projectName} onChange={(e) => setProjectName(e.target.value)} required minLength={2} maxLength={120} className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
                <label className="text-sm text-slate-300">
                  Project slug
                  <input value={projectSlug} onChange={(e) => setProjectSlug(e.target.value)} required minLength={2} maxLength={120} className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
              </div>
              <button onClick={handleCreateProject} disabled={isSubmitting || !projectName.trim() || !projectSlug.trim()} className="mt-4 bg-slate-100 px-4 py-2 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50">
                Create project
              </button>
            </div> : null}

            {user ? <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
              <h2 className="mb-4 text-lg font-semibold">Queue deployment</h2>
              <label className="mb-4 block text-sm text-slate-300">
                Project
                <select
                  value={activeProjectId}
                  onChange={(event) => {
                    setProjectMembers([]);
                    setProjectQuota(null);
                    setQuotaProjectId('');
                    setQuotaIdempotencyKey('');
                    setDeployments([]);
                    setNextDeploymentCursor(null);
                    setDeploymentEvents([]);
                    setNextEventCursor(null);
                    setDeploymentRevisions([]);
                    setNextRevisionCursor(null);
                    setSelectedDeploymentId('');
                    clearKubernetesDiagnostics();
                    setScalingDeploymentId('');
                    setScaleIdempotencyKey('');
                    setRollbackRequest(null);
                    activeProjectIdRef.current = event.target.value;
                    setActiveProjectId(event.target.value);
                  }}
                  disabled={projects.length === 0 || isLoadingProjects}
                  className="mt-2 w-full px-3 py-2 text-slate-950"
                >
                  {projects.length === 0 ? <option value="">Create a project first</option> : null}
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
              </label>
              <div className="grid gap-4 md:grid-cols-4">
                <label className="text-sm text-slate-300">
                  Deployment name
                  <input value={deploymentName} onChange={(e) => setDeploymentName(e.target.value)} required minLength={2} maxLength={120} pattern="[a-z0-9](?:[-a-z0-9]*[a-z0-9])?" className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
                <label className="text-sm text-slate-300">
                  Container image
                  <input value={image} onChange={(e) => setImage(e.target.value)} required minLength={3} maxLength={255} className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
                <label className="text-sm text-slate-300">
                  Container port
                  <input type="number" value={deploymentPort} onChange={(e) => setDeploymentPort(e.target.value)} required min={1} max={65535} className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
                <label className="text-sm text-slate-300">
                  Replicas
                  <input type="number" value={deploymentReplicas} onChange={(e) => setDeploymentReplicas(e.target.value)} required min={1} max={MAX_PROJECT_REPLICAS} className="mt-2 w-full px-3 py-2 text-slate-950" />
                </label>
              </div>
              <button onClick={handleCreateDeployment} disabled={isSubmitting || !activeProjectId || !deploymentName.trim() || !image.trim()} className="mt-4 bg-sky-500 px-4 py-2 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50">
                Queue deployment
              </button>
            </div> : null}

            {status && (
              <div className="rounded-lg border border-blue-700 bg-blue-950/30 p-3 text-sm text-blue-200">
                {status}
              </div>
            )}
            {error && (
              <div className="rounded-lg border border-red-700 bg-red-950/30 p-3 text-sm text-red-200">
                {error}
              </div>
            )}

            {user && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-4 text-lg font-semibold">Projects</h3>
                {isLoadingProjects ? <p className="text-sm text-slate-400">Loading projects…</p> : projects.length === 0 ? <p className="text-sm text-slate-400">No projects yet. Create one to get started.</p> : <div className="space-y-2">
                  {projects.map((p) => (
                    <div key={p.id} className="rounded border border-slate-700 bg-slate-800/50 p-3 text-sm">
                      <p className="font-medium">{p.name}</p>
                      <p className="text-xs text-slate-400">{p.slug}</p>
                    </div>
                  ))}
                </div>}
                {nextProjectCursor && (
                  <button
                    type="button"
                    onClick={() => void handleLoadMoreProjects()}
                    disabled={isLoadingMoreProjects}
                    className="mt-3 border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:opacity-50"
                  >
                    {isLoadingMoreProjects ? 'Loading…' : 'Load more projects'}
                  </button>
                )}
              </div>
            )}

            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-2 text-lg font-semibold">Project resource policy</h3>
                {isLoadingQuota || quotaProjectId !== activeProjectId ? (
                  <p className="text-sm text-slate-400">Loading project policy…</p>
                ) : projectQuota ? (
                  <>
                    <p className="text-sm text-slate-300">
                      Policy {projectQuota.state}
                      {projectQuota.version !== null ? ` · version ${projectQuota.version}` : ''}
                    </p>
                    {projectQuota.desired ? (
                      <p className="mt-2 text-xs text-slate-400">
                        Maximum {projectQuota.desired.max_replicas} replicas · CPU {projectQuota.desired.cpu_request} requested / {projectQuota.desired.cpu_limit} limit · memory {projectQuota.desired.memory_request} requested / {projectQuota.desired.memory_limit} limit
                      </p>
                    ) : (
                      <p className="mt-2 text-sm text-amber-300">
                        An owner must configure this project policy before deployments can be queued.
                      </p>
                    )}
                    {projectQuota.state === 'pending' && (
                      <p className="mt-2 text-xs text-amber-300">
                        This policy is stored and enforced by API admission, but has not been applied to Kubernetes.
                      </p>
                    )}
                    {canManageMembers ? (
                      <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 sm:grid-cols-4">
                        <label className="text-sm text-slate-300">
                          Maximum replicas
                          <input
                            type="number"
                            min={1}
                            max={MAX_PROJECT_REPLICAS}
                            value={quotaMaxReplicas}
                            onChange={(event) => {
                              setQuotaMaxReplicas(event.target.value);
                              setQuotaIdempotencyKey(crypto.randomUUID());
                            }}
                            disabled={isUpdatingQuota}
                            className="mt-2 w-full px-3 py-2 text-slate-950 disabled:opacity-50"
                          />
                        </label>
                        <label className="text-sm text-slate-300">
                          CPU limit
                          <input
                            type="text"
                            value={quotaCpuLimit}
                            onChange={(event) => {
                              setQuotaCpuLimit(event.target.value);
                              setQuotaIdempotencyKey(crypto.randomUUID());
                            }}
                            maxLength={32}
                            disabled={isUpdatingQuota}
                            aria-describedby="quota-limits-help"
                            className="mt-2 w-full px-3 py-2 text-slate-950 disabled:opacity-50"
                          />
                        </label>
                        <label className="text-sm text-slate-300">
                          Memory limit
                          <input
                            type="text"
                            value={quotaMemoryLimit}
                            onChange={(event) => {
                              setQuotaMemoryLimit(event.target.value);
                              setQuotaIdempotencyKey(crypto.randomUUID());
                            }}
                            maxLength={32}
                            disabled={isUpdatingQuota}
                            aria-describedby="quota-limits-help"
                            className="mt-2 w-full px-3 py-2 text-slate-950 disabled:opacity-50"
                          />
                        </label>
                        <button
                          type="button"
                          onClick={() => void handleUpdateProjectQuota()}
                          disabled={isUpdatingQuota || isLoadingQuota || isLoadingMembers}
                          className="self-end bg-slate-100 px-4 py-2 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isUpdatingQuota ? 'Saving…' : 'Save policy'}
                        </button>
                        <p id="quota-limits-help" className="text-xs text-slate-400 sm:col-span-4">
                          Limits accept Kubernetes quantities (CPU 500m–2, memory 512Mi–2Gi). Limits must cover the selected replica maximum. Kubernetes enforcement is pending.
                        </p>
                      </div>
                    ) : isLoadingMembers ? (
                      <p className="mt-3 text-xs text-slate-400">Checking project permissions…</p>
                    ) : (
                      <p className="mt-3 text-xs text-slate-400">Only project owners can change this policy.</p>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-slate-400">Project policy is unavailable until it can be loaded successfully.</p>
                )}
              </div>
            )}

            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-4 text-lg font-semibold">Project members</h3>
                {isLoadingMembers ? <p className="text-sm text-slate-400">Loading members…</p>
                  : projectMembers.length === 0 ? <p className="text-sm text-slate-400">No members found.</p>
                    : <div className="space-y-2" aria-live="polite">
                      {projectMembers.map((member) => (
                        <div key={member.user_id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-slate-700 bg-slate-800/50 p-3 text-sm">
                          <div>
                            <p className="font-medium">{member.email}</p>
                            <p className="text-xs text-slate-400">Project {member.role}</p>
                          </div>
                          {canManageMembers && (
                            <div className="flex items-center gap-2">
                              <label className="sr-only" htmlFor={`member-role-${member.user_id}`}>
                                Role for {member.email}
                              </label>
                              <select
                                id={`member-role-${member.user_id}`}
                                value={member.role}
                                onChange={(event) => {
                                  const role = event.target.value;
                                  if (role === 'owner' || role === 'member') {
                                    void handleUpdateMemberRole(member, role);
                                  }
                                }}
                                disabled={isSubmittingMember || isLoadingMembers}
                                className="px-2 py-1 text-slate-950 disabled:opacity-50"
                              >
                                <option value="member">Member</option>
                                <option value="owner">Owner</option>
                              </select>
                              <button
                                type="button"
                                onClick={() => void handleRemoveMember(member)}
                                disabled={isSubmittingMember || isLoadingMembers}
                                aria-label={`Remove ${member.email}`}
                                className="border border-slate-600 px-2 py-1 text-slate-200 disabled:opacity-50"
                              >
                                Remove
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>}
                {canManageMembers && (
                  <div className="mt-5 grid gap-3 border-t border-slate-800 pt-4 sm:grid-cols-[1fr_auto_auto]">
                    <label className="text-sm text-slate-300">
                      Member email
                      <input
                        type="email"
                        value={memberEmail}
                        onChange={(event) => setMemberEmail(event.target.value)}
                        required
                        maxLength={255}
                        className="mt-2 w-full px-3 py-2 text-slate-950"
                      />
                    </label>
                    <label className="text-sm text-slate-300">
                      Role
                      <select
                        value={memberRole}
                        onChange={(event) => {
                          const role = event.target.value;
                          if (role === 'owner' || role === 'member') setMemberRole(role);
                        }}
                        className="mt-2 block px-3 py-2 text-slate-950"
                      >
                        <option value="member">Member</option>
                        <option value="owner">Owner</option>
                      </select>
                    </label>
                    <button
                      type="button"
                      onClick={() => void handleAddMember()}
                      disabled={isSubmittingMember || isLoadingMembers || !memberEmail.trim()}
                      className="self-end bg-slate-100 px-4 py-2 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      Add member
                    </button>
                  </div>
                )}
              </div>
            )}

            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-4 text-lg font-semibold">Deployments</h3>
                {isLoadingDeployments ? <p className="text-sm text-slate-400">Loading deployments…</p> : deployments.length === 0 ? <p className="text-sm text-slate-400">No deployments in this project.</p> : <div className="space-y-2" aria-live="polite">
                  {deployments.map((d) => (
                    <div key={d.id} className="rounded border border-slate-700 bg-slate-800/50 p-3 text-sm">
                      <p className="font-medium">{d.name}</p>
                      <p className="text-xs text-slate-400">{d.image} (replicas: {d.replicas})</p>
                      <p className={`mt-1 text-xs font-semibold ${d.status === 'ready' ? 'text-emerald-400' : 'text-yellow-400'}`}>{d.status}</p>
                      <p className="text-xs text-slate-500">{d.kubernetes_resource_name}</p>
                      {d.last_error && <p className="text-xs text-red-400">{d.last_error}</p>}
                      {d.status === 'ready' && (scalingDeploymentId === d.id ? (
                        <div className="mt-3 flex flex-wrap items-end gap-2">
                          <label htmlFor={`scale-replicas-${d.id}`} className="text-xs text-slate-300">
                            Replicas
                            <input
                              id={`scale-replicas-${d.id}`}
                              type="number"
                              min={1}
                              max={MAX_PROJECT_REPLICAS}
                              value={scaleReplicas}
                              onChange={(event) => {
                                setScaleReplicas(event.target.value);
                                setScaleIdempotencyKey(crypto.randomUUID());
                              }}
                              disabled={isScaling}
                              className="ml-2 w-20 px-2 py-1 text-slate-950"
                            />
                          </label>
                          <button
                            type="button"
                            onClick={() => void handleScaleDeployment(d)}
                            disabled={isScaling}
                            className="border border-slate-600 px-3 py-1 text-xs text-slate-100 disabled:opacity-50"
                          >
                            {isScaling ? 'Scaling…' : 'Apply scale'}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setScalingDeploymentId('');
                              setScaleIdempotencyKey('');
                            }}
                            disabled={isScaling}
                            className="border border-slate-700 px-3 py-1 text-xs text-slate-300 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setScalingDeploymentId(d.id);
                            setScaleReplicas(String(d.replicas));
                            setScaleIdempotencyKey(crypto.randomUUID());
                          }}
                          className="mt-3 border border-slate-700 px-3 py-1 text-xs text-slate-200"
                        >
                          Scale
                        </button>
                      ))}
                    </div>
                  ))}
                </div>}
                {nextDeploymentCursor && (
                  <button
                    type="button"
                    onClick={() => void handleLoadMoreDeployments()}
                    disabled={isLoadingMoreDeployments}
                    className="mt-3 border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:opacity-50"
                  >
                    {isLoadingMoreDeployments ? 'Loading…' : 'Load more deployments'}
                  </button>
                )}
              </div>
            )}
            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-4 text-lg font-semibold">Deployment revisions</h3>
                {!selectedDeploymentId ? <p className="text-sm text-slate-400">Select a deployment to view its revisions.</p>
                  : isLoadingRevisions ? <p className="text-sm text-slate-400">Loading revisions…</p>
                    : deploymentRevisions.length === 0 ? <p className="text-sm text-slate-400">No revision history is available.</p>
                      : <ol className="space-y-3" aria-live="polite">
                        {deploymentRevisions.map((revision) => (
                          <li key={revision.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3 last:border-b-0">
                            <div>
                              <p className="text-sm font-medium">
                                Revision {revision.revision_number} · {revision.image} · {revision.replicas} replicas
                              </p>
                              <p className="text-xs text-slate-400">
                                Port {revision.port}
                                {revision.rollback_of_revision_id ? ` · rolled back from ${revision.rollback_of_revision_id}` : ''}
                              </p>
                              <time className="text-xs text-slate-500" dateTime={revision.created_at}>
                                {new Date(revision.created_at).toLocaleString()}
                              </time>
                            </div>
                            {revision.id !== deploymentRevisions[0]?.id && (
                              <button
                                type="button"
                                onClick={() => void handleRollbackToRevision(revision)}
                                disabled={isRollingBack || selectedDeploymentStatus !== 'ready'}
                                className="border border-slate-600 px-3 py-1 text-xs text-slate-200 disabled:opacity-50"
                              >
                                {isRollingBack ? 'Rolling back…' : `Roll back to revision ${revision.revision_number}`}
                              </button>
                            )}
                          </li>
                        ))}
                      </ol>}
                {nextRevisionCursor && (
                  <button
                    type="button"
                    onClick={() => void handleLoadMoreRevisions()}
                    disabled={isLoadingMoreRevisions}
                    className="mt-4 border border-slate-700 px-3 py-2 text-sm text-slate-200 disabled:opacity-50"
                  >
                    {isLoadingMoreRevisions ? 'Loading…' : 'Load older revisions'}
                  </button>
                )}
              </div>
            )}
            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="mb-4 text-lg font-semibold">Deployment events</h3>
                <label className="mb-4 block text-sm text-slate-300">
                  Deployment
                  <select
                    value={selectedDeploymentId}
                    onChange={(event) => {
                      setDeploymentEvents([]);
                      setNextEventCursor(null);
                      setDeploymentRevisions([]);
                      setNextRevisionCursor(null);
                      setRollbackRequest(null);
                      clearKubernetesDiagnostics();
                      setSelectedDeploymentId(event.target.value);
                    }}
                    disabled={deployments.length === 0 || isLoadingDeployments}
                    className="mt-2 w-full px-3 py-2 text-slate-950"
                  >
                    {deployments.length === 0 ? <option value="">No deployments available</option> : null}
                    {deployments.map((deployment) => (
                      <option key={deployment.id} value={deployment.id}>{deployment.name}</option>
                    ))}
                  </select>
                </label>
                {isLoadingEvents ? <p className="text-sm text-slate-400">Loading events…</p>
                  : deploymentEvents.length === 0 ? <p className="text-sm text-slate-400">No recorded lifecycle events.</p>
                    : <ol className="space-y-3" aria-live="polite">
                      {deploymentEvents.map((event) => (
                        <li key={event.id} className="border-l border-slate-700 pl-3">
                          <p className="text-sm font-medium">{event.event_type}</p>
                          <p className="text-sm text-slate-300">{event.message}</p>
                          <time className="text-xs text-slate-500" dateTime={event.created_at}>
                            {new Date(event.created_at).toLocaleString()}
                          </time>
                        </li>
                      ))}
                    </ol>}
                {nextEventCursor && (
                  <button
                    type="button"
                    onClick={handleLoadOlderEvents}
                    disabled={isLoadingEvents}
                    className="mt-4 border border-slate-700 px-3 py-2 text-sm text-slate-200 disabled:opacity-50"
                  >
                    Load older events
                  </button>
                )}
              </div>
            )}
            {user && activeProjectId && (
              <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
                <h3 className="text-lg font-semibold">Kubernetes diagnostics</h3>
                <p className="mt-2 text-sm text-slate-400">
                  Reads live Pod logs and Kubernetes Events using the separately configured API diagnostics identity.
                </p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={() => handleLoadKubernetesDiagnostics('logs')}
                    disabled={!selectedDeploymentId || isLoadingDiagnostics}
                    className="border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isLoadingDiagnostics && loadingDiagnosticsKind === 'logs' ? 'Loading Pod logs…' : 'Load Pod logs'}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleLoadKubernetesDiagnostics('events')}
                    disabled={!selectedDeploymentId || isLoadingDiagnostics}
                    className="border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isLoadingDiagnostics && loadingDiagnosticsKind === 'events' ? 'Loading Kubernetes Events…' : 'Load Kubernetes Events'}
                  </button>
                </div>
                {diagnosticsError && diagnosticsErrorDeploymentId === selectedDeploymentId && (
                  <p className="mt-4 text-sm text-red-300" role="alert">{diagnosticsError}</p>
                )}
                {podLogsSourceTime && podLogsDeploymentId === selectedDeploymentId && (
                  <section className="mt-5" aria-label="Pod logs">
                    <h4 className="text-sm font-semibold text-slate-200">Pod logs</h4>
                    <p className="mt-1 text-xs text-slate-500">
                      Retrieved <time dateTime={podLogsSourceTime}>{new Date(podLogsSourceTime).toLocaleString()}</time>
                      {podLogsTruncated ? ' · results truncated by API limits' : ''}
                    </p>
                    {podLogs.length === 0 ? <p className="mt-3 text-sm text-slate-400">No Pod logs were returned.</p>
                      : <ul className="mt-3 space-y-3">
                        {podLogs.map((entry) => (
                          <li key={`${entry.pod_name}:${entry.container}`} className="border-t border-slate-800 pt-3">
                            <p className="mb-2 text-xs text-slate-400">{entry.pod_name} · {entry.container}{entry.truncated ? ' · truncated' : ''}</p>
                            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-xs text-slate-200">{entry.log}</pre>
                          </li>
                        ))}
                      </ul>}
                  </section>
                )}
                {kubernetesEventsSourceTime && kubernetesEventsDeploymentId === selectedDeploymentId && (
                  <section className="mt-5" aria-label="Kubernetes Events">
                    <h4 className="text-sm font-semibold text-slate-200">Kubernetes Events</h4>
                    <p className="mt-1 text-xs text-slate-500">
                      Retrieved <time dateTime={kubernetesEventsSourceTime}>{new Date(kubernetesEventsSourceTime).toLocaleString()}</time>
                      {kubernetesEventsTruncated ? ' · results truncated by API limits' : ''}
                    </p>
                    {kubernetesEvents.length === 0 ? <p className="mt-3 text-sm text-slate-400">No Kubernetes Events were returned.</p>
                      : <ol className="mt-3 space-y-3" aria-live="polite">
                        {kubernetesEvents.map((event, index) => (
                          <li key={`${event.resource_kind}:${event.resource_name}:${event.created_at}:${index}`} className="border-l border-slate-700 pl-3">
                            <p className="text-sm font-medium">{event.event_type}{event.reason ? ` · ${event.reason}` : ''}</p>
                            <p className="text-xs text-slate-400">{event.resource_kind} · {event.resource_name}</p>
                            <p className="text-sm text-slate-300">{event.message}</p>
                            <time className="text-xs text-slate-500" dateTime={event.created_at}>
                              {new Date(event.created_at).toLocaleString()}
                            </time>
                          </li>
                        ))}
                      </ol>}
                  </section>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
