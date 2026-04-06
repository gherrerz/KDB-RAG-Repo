# Kubernetes Deployment Guide

This folder provides native Kubernetes manifests for deploying:

- API (FastAPI)
- Worker RQ dedicado para ingesta (overlay con Redis)
- Neo4j (StatefulSet)
- Redis (optional addon)

## Structure

- `base/`: API + Neo4j core resources
- `addons/redis/`: optional Redis resources
- `overlays/cloud/`: cloud-ready overlay (ingress + image patch)
- `overlays/cloud-with-redis/`: cloud overlay + Redis addon + worker

## Prerequisites

- Kubernetes cluster (AKS/EKS/GKE)
- `kubectl`
- Kustomize support (`kubectl apply -k`)

## Quick Start

Deploy cloud base (API + Neo4j):

```bash
kubectl apply -k k8s/overlays/cloud
```

Deploy cloud with Redis addon:

```bash
kubectl apply -k k8s/overlays/cloud-with-redis
```

## Required Adjustments Before Production

1. Update API image in `k8s/overlays/cloud/patch-api-deployment.yaml`.
2. If using `cloud-with-redis`, also update worker image in
  `k8s/overlays/cloud-with-redis/worker-deployment.yaml`.
3. Replace placeholder secret values in:
   - `k8s/base/api-secret.yaml`
   - `k8s/base/neo4j-secret.yaml`
4. Update ingress host/TLS in `k8s/overlays/cloud/ingress.yaml`.
5. Tune CPU/memory requests/limits and PVC sizes to your workload.

## Verification

```bash
kubectl get pods -n coderag
kubectl get svc -n coderag
kubectl get ingress -n coderag
```

Health endpoint (through ingress or service):

- `GET /health/storage`

## Notes

- API replicas are intentionally set to 1 in base because job execution and
  local state assumptions are currently in-process.
- In `cloud-with-redis`, la API se escala a 2 réplicas y la ingesta se
  ejecuta por cola en worker dedicado (modo `rq`).
- If you enable Redis and set `HEALTH_CHECK_REDIS=true`, Redis becomes part of
  storage preflight checks.
