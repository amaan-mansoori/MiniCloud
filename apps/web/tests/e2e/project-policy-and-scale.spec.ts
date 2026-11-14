import { expect, test } from '@playwright/test';

const projectId = 'project-1';
const deploymentId = 'deployment-1';
const email = 'owner@example.test';
const apiOrigin = 'http://localhost:8001';

test('owner quota edits and scale requests respect replica preconditions', async ({ page }) => {
  let quotaRequest: {
    body: unknown;
    headers: Record<string, string>;
  } | undefined;
  let scaleRequest: { body: unknown } | undefined;

  await page.route(`${apiOrigin}/**`, async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const corsHeaders = {
      'access-control-allow-origin': 'http://127.0.0.1:3100',
      'access-control-allow-methods': 'GET,POST,PATCH,DELETE,OPTIONS',
      'access-control-allow-headers': 'Authorization,Content-Type,If-Match,Idempotency-Key',
    };

    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }

    const respond = async (body: unknown, status = 200) => route.fulfill({
      status,
      headers: { ...corsHeaders, 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (pathname === '/api/v1/auth/register' && request.method() === 'POST') {
      await respond({
        user: { id: 'user-1', email },
        access_token: 'browser-test-token',
        expires_in: 3600,
      });
      return;
    }
    if (pathname === '/api/v1/projects' && request.method() === 'GET') {
      await respond({
        items: [{ id: projectId, name: 'Test project', slug: 'test-project', status: 'active', owner_id: 'user-1' }],
        next_cursor: null,
      });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/members` && request.method() === 'GET') {
      await respond([{ user_id: 'user-1', email, role: 'owner' }]);
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/quota` && request.method() === 'GET') {
      await respond({
        state: 'pending',
        version: 1,
        desired: {
          max_replicas: 2,
          cpu_request: '200m',
          cpu_limit: '1',
          memory_request: '256Mi',
          memory_limit: '1Gi',
        },
        applied_version: null,
        updated_at: '2026-11-14T00:00:00Z',
      });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/quota` && request.method() === 'PATCH') {
      quotaRequest = {
        body: request.postDataJSON(),
        headers: await request.allHeaders(),
      };
      await respond({
        state: 'pending',
        version: 2,
        desired: {
          max_replicas: 3,
          cpu_request: '300m',
          cpu_limit: '2',
          memory_request: '384Mi',
          memory_limit: '2Gi',
        },
        applied_version: null,
        updated_at: '2026-11-14T00:01:00Z',
      });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/deployments` && request.method() === 'GET') {
      await respond({
        items: [{
          id: deploymentId,
          project_id: projectId,
          owner_id: 'user-1',
          name: 'web',
          image: 'nginx:1.27.3-alpine',
          port: 8080,
          replicas: 2,
          kubernetes_resource_name: 'mc-deployment-1',
          status: 'ready',
          last_error: null,
        }],
        next_cursor: null,
      });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/deployments/${deploymentId}/events`) {
      await respond({ items: [], next_cursor: null });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/deployments/${deploymentId}/revisions`) {
      await respond({ items: [], next_cursor: null });
      return;
    }
    if (pathname === `/api/v1/projects/${projectId}/deployments/${deploymentId}/scale` && request.method() === 'PATCH') {
      scaleRequest = { body: request.postDataJSON() };
      await respond({
        deployment_id: deploymentId,
        kubernetes_resource_name: 'mc-deployment-1',
        revision_id: 'revision-2',
        job_state: 'queued',
        status: 'accepted',
        last_error: null,
      }, 202);
      return;
    }

    await respond({ detail: `Unexpected test request: ${request.method()} ${pathname}` }, 404);
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Register' }).click();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill('browser-test-password');
  await page.getByRole('button', { name: 'Create account' }).click();

  await expect(page.getByText('Policy pending · version 1')).toBeVisible();
  await page.getByLabel('Maximum replicas').fill('3');
  await page.getByLabel('CPU limit').fill('2');
  await page.getByLabel('Memory limit').fill('2Gi');
  await page.getByRole('button', { name: 'Save policy' }).click();
  await expect(page.getByText('Policy pending · version 2')).toBeVisible();
  expect(quotaRequest?.body).toEqual({
    max_replicas: 3,
    cpu_limit: '2',
    memory_limit: '2Gi',
  });
  expect(quotaRequest?.headers['if-match']).toBe('"1"');
  expect(quotaRequest?.headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/i);

  await page.getByRole('button', { name: 'Scale' }).click();
  const scaleInput = page.locator(`#scale-replicas-${deploymentId}`);
  await scaleInput.fill('5');
  await page.getByRole('button', { name: 'Apply scale' }).click();
  await expect(page.getByText('Replicas must be an integer between 1 and 4.')).toBeVisible();
  expect(scaleRequest).toBeUndefined();

  await scaleInput.fill('3');
  await page.getByRole('button', { name: 'Apply scale' }).click();
  await expect(page.getByText('Scale request accepted for 3 replicas; waiting for readiness.')).toBeVisible();
  expect(scaleRequest?.body).toEqual(expect.objectContaining({
    replicas: 3,
    expected_replicas: 2,
  }));
});
