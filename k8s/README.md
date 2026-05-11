# Kubernetes Deployment Guide

Documento tecnico consolidado:

- [docs/KUBERNETES.md](../docs/KUBERNETES.md)

Este README mantiene un resumen rapido; para procedimientos completos de build,
secretos, deploy dev/prod, rollback y validacion funcional, usa
[docs/KUBERNETES.md](../docs/KUBERNETES.md).

This folder provides native Kubernetes manifests for deploying:

- API (FastAPI)
- Worker RQ dedicado para ingesta (overlay con Redis)
- Neo4j (StatefulSet)
- Redis (optional addon)

Chroma remoto y Postgres no forman parte de estos manifests base; deben estar
disponibles como servicios externos o provisionarse por separado segun el
entorno.

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

Deploy cloud base (API + Neo4j; requires Chroma remoto y Postgres ya
resueltos por el entorno):

```bash
kubectl apply -k k8s/overlays/cloud
```

Deploy cloud with Redis addon:

```bash
kubectl apply -k k8s/overlays/cloud-with-redis
```

## Required Adjustments Before Production

1. Update API image in `k8s/overlays/cloud/patch-api-deployment.yaml`.
1. If using `cloud-with-redis`, also update worker image in
  `k8s/overlays/cloud-with-redis/worker-deployment.yaml`.
1. Replace placeholder secret values in:

   - `k8s/base/api-secret.yaml`
   - `k8s/base/neo4j-secret.yaml`
   - Define `VERTEX_SERVICE_ACCOUNT_JSON_B64` in `k8s/base/api-secret.yaml` for Vertex AI.

1. Update ingress host/TLS in `k8s/overlays/cloud/ingress.yaml`.
1. Tune CPU/memory requests/limits and PVC sizes to your workload.

## Verification

```bash
kubectl get pods -n coderag
kubectl get svc -n coderag
kubectl get ingress -n coderag
```

Health endpoint (through ingress or service):

- `GET /health`

## Notes

- API replicas are intentionally set to 1 in base because job execution and
  local state assumptions are currently in-process.
- In `cloud-with-redis`, la API se escala a 2 réplicas y la ingesta se
  ejecuta por cola en worker dedicado (modo `rq`).
- If you enable Redis and set `HEALTH_CHECK_REDIS=true`, Redis becomes part of
  storage preflight checks.
- The recommended runtime architecture still assumes Chroma remoto and
  Postgres as external operational dependencies.
