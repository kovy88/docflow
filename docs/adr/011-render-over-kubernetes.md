# ADR-011: Render + Docker Compose over Kubernetes

## Context

Production needs exactly two long-running processes (API, worker), one
managed Redis, and one managed Postgres — see
[ARCHITECTURE.md](../ARCHITECTURE.md) for the full shape. Local development
and the one-command demo need the same containers runnable identically on a
laptop. The target customer (SMB, initially Czech/Central-European market —
see [README.md](../../README.md)) implies a small-scale, cost-conscious
deployment, not a fleet.

## Decision

Render for production (`docflow-api` web service, `docflow-worker`
background worker, managed Redis — see [render.yaml](../../render.yaml) and
[DEPLOYMENT.md](../DEPLOYMENT.md)), Docker Compose for local development and
the demo path. No Kubernetes manifests, no cluster.

## Alternatives considered

- **Kubernetes (EKS/GKE/self-managed).** The industry-default answer for
  "deploy containers," and the right one at a different scale — many
  services, independent scaling policies per service, a platform team to
  own the control plane. None of that describes this system: two service
  *kinds* (API, worker), each currently running as a single scalable
  process type, no service mesh requirement, no multi-cluster story. A
  Kubernetes manifest set here would mean YAML, an ingress controller, a
  secrets provider, and an on-call surface — all real ongoing cost — bought
  against zero capability this deployment actually needs today. This is the
  most consequential "no" in this project's infrastructure choices, made
  explicitly rather than by default: Kubernetes wasn't skipped because it's
  hard, it was skipped because the workload doesn't ask for what it's for.
- **A bare VM with Docker Compose in production**, not Render. Cheaper, and
  viable — but pushes health checks, zero-downtime deploys, TLS, and secret
  storage back onto whoever operates it, all of which Render provides as
  the platform's job. For a project where infrastructure operations aren't
  the differentiator, buying that off a platform is the better trade.
- **Fully serverless** (Lambda-style functions for the API, a scheduled
  function for the worker). Rejected specifically for the worker: `arq`'s
  long-running polling worker process doesn't map cleanly onto a
  request-triggered function model, and a job queue with a persistent
  worker is a better fit for continuous document-processing throughput than
  cold-start-per-job execution.

## Consequences

- Scaling is coarse — `numInstances` in [render.yaml](../../render.yaml),
  changed manually or via Render's dashboard, not an autoscaling policy
  reacting to load. Acceptable at current scale; revisit if worker queue
  depth (`docflow_queue_depth` in Prometheus) shows sustained backlog rather
  than transient bursts — see [DEPLOYMENT.md](../DEPLOYMENT.md#scaling-notes).
- Local dev and production run the *same* containers (same Dockerfile, same
  images) with different orchestration around them (Compose locally,
  Render's blueprint in production) — not a Kubernetes-only production
  config that diverges from what a contributor actually runs on their
  laptop.
- If this system's shape changes significantly — many independent services,
  need for canary/blue-green deploys beyond what Render's rolling deploy
  gives, a platform team taking ownership of infrastructure — this is the
  decision to revisit, not something to route around by adding Kubernetes
  manifests alongside the Render config for "flexibility." Until then, a
  second deployment target that's never actually used is complexity with no
  payoff.
