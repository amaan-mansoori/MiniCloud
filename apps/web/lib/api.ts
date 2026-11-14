export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8001';

export type User = {
  id: string;
  email: string;
};

export type Project = {
  id: string;
  name: string;
  slug: string;
  status: string;
  owner_id: string;
};

export type ProjectPage = {
  items: Project[];
  next_cursor: string | null;
};

export type ProjectQuota = {
  state: 'unconfigured' | 'pending' | 'applied';
  version: number | null;
  desired: {
    max_replicas: number;
    cpu_request: string;
    cpu_limit: string;
    memory_request: string;
    memory_limit: string;
  } | null;
  applied_version: number | null;
  updated_at: string | null;
};

export type ProjectMember = {
  user_id: string;
  email: string;
  role: 'owner' | 'member';
};

export type Deployment = {
  id: string;
  project_id: string;
  owner_id: string;
  name: string;
  image: string;
  port: number;
  replicas: number;
  kubernetes_resource_name: string;
  status: string;
  last_error: string | null;
};

export type DeploymentPage = {
  items: Deployment[];
  next_cursor: string | null;
};

export type Operation = {
  deployment_id: string;
  kubernetes_resource_name: string;
  revision_id: string | null;
  job_state: string | null;
  status: string;
  last_error: string | null;
};

export type DeploymentRevision = {
  id: string;
  revision_number: number;
  name: string;
  image: string;
  port: number;
  replicas: number;
  created_by_user_id: string;
  rollback_of_revision_id: string | null;
  created_at: string;
};

export type DeploymentRevisionPage = {
  items: DeploymentRevision[];
  next_cursor: string | null;
};

export type DeploymentEvent = {
  id: string;
  event_type: string;
  message: string;
  created_at: string;
};

export type DeploymentEventPage = {
  items: DeploymentEvent[];
  next_cursor: string | null;
};

export type KubernetesPodLog = {
  pod_name: string;
  container: string;
  log: string;
  truncated: boolean;
};

export type KubernetesPodLogs = {
  items: KubernetesPodLog[];
  source_time: string;
  truncated: boolean;
};

export type KubernetesEvent = {
  resource_kind: string;
  resource_name: string;
  event_type: string;
  reason: string | null;
  message: string;
  created_at: string;
};

export type KubernetesEventPage = {
  items: KubernetesEvent[];
  source_time: string;
  truncated: boolean;
};

function getErrorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item !== 'object' || item === null || !('msg' in item)) return null;
        return typeof item.msg === 'string' ? item.msg : null;
      })
      .filter((message): message is string => message !== null);
    if (messages.length > 0) return messages.join(' ');
  }
  return `Request failed with ${status}`;
}

async function apiResponse(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE_URL}${input}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const data: unknown = await response.json().catch(() => undefined);
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : undefined;
    throw new Error(getErrorMessage(detail, response.status));
  }

  return response;
}

export async function apiFetch<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await apiResponse(input, init);
  return (await response.json()) as T;
}

export async function apiFetchNoContent(input: string, init?: RequestInit): Promise<void> {
  const response = await apiResponse(input, init);
  if (response.status !== 204) {
    throw new Error(`Expected an empty response, received ${response.status}.`);
  }
}
