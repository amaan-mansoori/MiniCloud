# 09 — UI/UX Specification

**Status:** Design direction only. No UI or components are implemented.

## Design principles

Permanent dark developer console: near-black canvas, charcoal panels, restrained
1px borders, muted secondary text with readable contrast, clear typography,
compact data-dense tables, one restrained accent, minimal transitions, no
decorative dashboard statistics or glowing gradients. Show text and icon for
status; color is never the sole signal. Respect reduced-motion preference and
keyboard navigation.

Use original layout/components. Reference galleries are inspiration, not
permission to copy. At implementation, review each reused component/asset
license and attribution; the references below were not license-audited in this
phase. No assets or gallery components are selected.

## Design tokens (proposed)

| Token | Value / rule |
| --- | --- |
| `surface.canvas` | `#0B0D10` |
| `surface.panel` | `#12161B` |
| `surface.elevated` | `#191F26` |
| `border.default` | `#2A313A` |
| `text.primary` | `#F2F5F7` |
| `text.secondary` | `#A6B0BA` |
| `text.muted` | `#7C8792`, only nonessential copy after contrast testing |
| `accent.primary` | `#61A9FF`, restrained links/focus/action |
| `status.ready` | `#56C58A` + “Ready” text |
| `status.progressing` | `#E7B85C` + “Progressing” text |
| `status.failed` | `#F07878` + “Failed” text |
| `status.unavailable` | neutral icon + “Unavailable” text |
| Typography | system UI or licensed bundled family; 12/14/16/20/24 px scale; monospace for IDs/logs |
| Spacing | 4px base; compact 8/12/16/24/32px |
| Radius | 4px inputs, 6px panels, avoid pill-heavy layout |
| Shadow | minimal elevation only; borders define most surfaces |
| Motion | 120–180ms opacity/position for state feedback; honor reduced motion |

Colors are candidates; verify WCAG contrast ratios for actual text/background
combinations before code release.

## Shared shell and behaviors

Persistent left project navigation on wide screens, collapsible accessible
navigation on narrow screens, top bar with project selector/account/session
state, page title and contextual action. Every request shows correlation ID on
error. Polling uses bounded backoff and stops on terminal state; show last
updated timestamp and stale badge. Confirm destructive delete/rollback with
target and consequence. Forms preserve non-secret values after recoverable
failure; never persist secret values in local storage.

## Required screen inventory

| Screen | Purpose, audience, hierarchy/layout/components/actions | API / validation | Loading, error, accessibility, responsive |
| --- | --- | --- | --- |
| Login/registration | Auth for all users; centered compact form, email/password, submit, switch mode, error summary | auth routes; client hints + server authoritative | disabled while request; generic credential errors; labels/keyboard; full-width mobile |
| Overview | Operator/member landing; project selector, recent deployments table, only sourced counts | project/deployment lists; no fabricated aggregates | skeleton rows; empty next-step CTA; dependency unavailable banner; table becomes cards on small viewport |
| Project list | Discover/create authorized projects; searchable table/name/role/status/create | GET/POST projects; slug validation | empty state; create error; row actions keyboard menu; scrollable or cards on mobile |
| Project details | Membership, quota, deployments, namespace provisioning | project/membership/quota APIs | pending namespace is explicit; forbidden/no project; tabs become stacked |
| Deployment list | Project deployments with status, image, replicas, updated time | deployment list/cursor | pagination/loading/error/empty; status text; responsive columns |
| Deployment creation | Submit supported image/config/resources/probes/env refs | POST deployment; required fields, image syntax, quota bounds | field errors; warn private image not configured; secret reference selector never reveals value |
| Deployment detail | Desired vs observed config/state, operation ID, readiness, events/logs, actions | detail/operation/events/logs | stale vs unavailable explicit; tabs accessible; log panel horizontally scrollable/preformatted |
| Rollout timeline | Chronological operation/revision events and observed condition changes | revisions/events cursor | no invented event; empty “no events recorded”; semantic ordered list |
| Revision history/rollback | Compare revisions and choose rollback target | history + rollback POST | confirm dialog; in-flight conflict visible; table/card responsive |
| Scaling controls | Manual replica range and observed count | scale POST + ETag/quota | pending operation; numeric accessible input; announce new status; no HPA claim |
| Logs/events | Bounded real source, timestamp, container, truncation | logs/events endpoints | absent source has unavailable panel; no silent empty; copy action excludes secrets; keyboard selectable |
| Monitoring/metrics | Timestamped real metrics, units/source/freshness | metrics endpoint | metrics provider unavailable state with setup explanation; table fallback for charts |
| Alerts | Rule list/create/edit/test state (SHOULD) | alert API only when capability says available | otherwise noninteractive “not configured/available”; never fake active alert |
| Resource quotas | Desired/applied CPU/memory/replicas and project policy | quota get/patch | drift/failed application explicit; units shown; owner-only edit |
| Membership/access | Members/roles/invite/revoke | member endpoints | owner/member capabilities; last-owner confirmation/invariant errors |
| Account settings | Profile, sessions/logout, credential lifecycle | auth/account APIs (future paths subject to contract) | expired session reauth; accessible forms |
| Error/empty/loading/forbidden/unavailable | Consistent states across screens | error contract + request ID | retry only safe/idempotent action; no permission leak |

Every button, menu item, and icon button must have accessible name, enabled
condition, confirmation if destructive, and test. Do not render inactive
decorative controls.

## Real data, fixtures, charts

Production screens consume API responses with explicit `source`, `observed_at`,
and availability. Development fixtures must be behind a development-only
explicit configuration, visibly labeled “fixture data,” and impossible to
confuse with live state. No static activity feed, hardcoded metrics, invented
success-rate card, or default-to-zero when provider is absent. Prefer a table
when it communicates better than chart; chart has accessible textual summary
and tabular alternative.

## Accessibility and responsive criteria

Target WCAG 2.2 AA as a proposed review goal, not a compliance claim until
audited. Use landmarks, heading order, skip link, visible focus, form labels,
error association, keyboard-operable dialogs, focus return, screen-reader
announcements for operation state, non-color status, sufficient contrast,
zoom/reflow, reduced motion. Test at narrow phone, tablet, and desktop widths;
no fixed-width page overflow except bounded code/log panes.

## Asset and component references

- [MotionSites](https://motionsites.ai/)
- [21st.dev](https://21st.dev/)
- [Trickle](https://trickle.so/)
- [Collect UI](https://collectui.com/)
- [shadcn/ui](https://ui.shadcn.com/)
- [Lucide](https://lucide.dev/)

These are discovery/inspiration sources. License and redistribution terms must
be verified per asset/component and recorded in attribution before reuse.
Prefer original implementations if source/license is unclear.
