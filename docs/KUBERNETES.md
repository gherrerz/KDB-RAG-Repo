# Kubernetes Deployment Guide

Guia tecnica consolidada para despliegue Kubernetes del proyecto.

## Objetivo de compatibilidad

- Manifiestos basados en APIs estables:
  - `apps/v1`
  - `v1`
  - `networking.k8s.io/v1`
- Objetivo: clusters con soporte moderno de Ingress v1 y Kustomize via
  `kubectl apply -k`.
- Namespaces y recursos estan pensados para operar bajo `coderag`.

## Artefactos incluidos

- Base:
  - `k8s/base/namespace.yaml`
  - `k8s/base/api-configmap.yaml`
  - `k8s/base/api-secret.yaml`
  - `k8s/base/neo4j-secret.yaml`
  - `k8s/base/api-pvc.yaml`
  - `k8s/base/api-deployment.yaml`
  - `k8s/base/api-service.yaml`
  - `k8s/base/neo4j-services.yaml`
  - `k8s/base/neo4j-statefulset.yaml`
- Addon Redis opcional:
  - `k8s/addons/redis/redis-services.yaml`
  - `k8s/addons/redis/redis-statefulset.yaml`
- Overlays:
  - `k8s/overlays/cloud/` (base + ingress + patch de imagen API + ExternalSecret Git SSH)
  - `k8s/overlays/cloud-with-redis/` (cloud + addon Redis + worker + escalado API)

## Prerequisitos

- Cluster Kubernetes accesible (AKS/EKS/GKE o equivalente).
- `kubectl` configurado con contexto correcto.
- Soporte de Kustomize en `kubectl` (`apply -k`).
- Registro de contenedores accesible desde el cluster.
- Secretos reales para API provider y Neo4j (no usar placeholders en produccion).
- External Secrets Operator instalado y `ClusterSecretStore` operativo.

## Build y push de imagen

Ejemplo con GHCR:

```bash
docker build -t ghcr.io/<org>/kdb-rag-api:<tag> .
docker push ghcr.io/<org>/kdb-rag-api:<tag>
```

Actualizar imagen en overlays:

- `k8s/overlays/cloud/patch-api-deployment.yaml`
- `k8s/overlays/cloud-with-redis/worker-deployment.yaml`

Recomendacion:

- Usar tags inmutables por release (`vX.Y.Z` o hash corto), evitar `latest` en prod.

## Configuracion de secretos

### Archivos a completar

- `k8s/base/api-secret.yaml`
- `k8s/base/neo4j-secret.yaml`
- `k8s/overlays/cloud/git-ssh-externalsecret.yaml`

Campos sensibles tipicos:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`, `service-account.json` (Vertex AI)
- `VERTEX_AI_PROJECT_ID`
- `GIT_SSH_KEY_CONTENT`, `GIT_SSH_KNOWN_HOSTS_CONTENT` en el Secret Kubernetes generado; el ExternalSecret puede mapear desde propiedades remotas existentes como `id_rsa` y `known_hosts`
- `NEO4J_PASSWORD`
- `NEO4J_AUTH` (en secreto Neo4j)

### Flujo recomendado

1. Completar secretos base (`api-secret`, `neo4j-secret`) antes de aplicar overlays.
2. Configurar `k8s/overlays/cloud/git-ssh-externalsecret.yaml` con tu `ClusterSecretStore` y keys remotos.
3. Confirmar que no queden placeholders (`""`, `password`) en secretos base.
4. Aplicar overlay correspondiente.

Ejemplo de secreto Kubernetes directo sin ExternalSecret:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: coderag-git-ssh-secret
type: Opaque
stringData:
  GIT_SSH_KEY_CONTENT_B64: <base64_private_key_openssh>
  GIT_SSH_KNOWN_HOSTS_CONTENT_B64: <base64_known_hosts>
```

Si ya usas ExternalSecret y tu secreto remoto expone `id_rsa` y `known_hosts`
en texto plano, puedes seguir mapeandolos a `GIT_SSH_KEY_CONTENT` y
`GIT_SSH_KNOWN_HOSTS_CONTENT` como hace el overlay actual.

## Deploy en entorno dev

Escenario dev de cluster (API + Neo4j, sin Redis):

```bash
kubectl apply -k k8s/overlays/cloud
```

Validacion inicial:

```bash
kubectl get pods -n coderag
kubectl get svc -n coderag
kubectl get ingress -n coderag
```

Notas:

- Base define `replicas: 1` para API.
- Modo de ingesta por defecto en configmap base: `thread`.

## Deploy en entorno prod

Escenario recomendado para carga (API + Neo4j + Redis + worker RQ):

```bash
kubectl apply -k k8s/overlays/cloud-with-redis
```

Este overlay hace:

- Escala API a 2 replicas (`patch-api-deployment-replicas.yaml`).
- Activa `INGESTION_EXECUTION_MODE=rq` (`patch-api-configmap-redis.yaml`).
- Despliega `coderag-worker` dedicado.
- Añade Redis con StatefulSet y servicios.

## Probes y endpoints

### API (`coderag-api`)

- `startupProbe`: `GET /health`.
- `readinessProbe`: `GET /health`.
- `livenessProbe`: `GET /health`.
- Puerto de servicio: `8000`.

### Neo4j (`neo4j`)

- `readinessProbe`: `tcpSocket` en puerto `7687`.
- `livenessProbe`: `tcpSocket` en puerto `7687`.

### Redis (`redis`, opcional)

- `readinessProbe`: `tcpSocket` en `6379`.
- `livenessProbe`: `tcpSocket` en `6379`.

## Persistencia

- API:
  - PVC `coderag-api-storage`
  - `ReadWriteOnce`
  - solicitud de `20Gi`
  - montaje en `/app/storage`
- Neo4j:
  - `volumeClaimTemplates` del StatefulSet
  - `ReadWriteOnce`
  - solicitud de `20Gi`
  - montaje en `/data`
- Redis (addon):
  - `volumeClaimTemplates`
  - `ReadWriteOnce`
  - solicitud de `5Gi`
  - montaje en `/data`

## Notas operativas

- Si Redis no esta desplegado, mantener `INGESTION_EXECUTION_MODE=thread`.
- Si se activa Redis, usar modo `rq` y worker dedicado.
- `HEALTH_CHECK_OPENAI` suele ir en `false` para entornos sin salida a Internet.
- `HEALTH_CHECK_REDIS=true` agrega Redis al preflight de storage.
- Ajustar requests/limits y almacenamiento segun carga real.

## Rollback rapido

Rollback de deployment API:

```bash
kubectl rollout undo deployment/coderag-api -n coderag
kubectl rollout status deployment/coderag-api -n coderag
```

Rollback de worker (si existe):

```bash
kubectl rollout undo deployment/coderag-worker -n coderag
kubectl rollout status deployment/coderag-worker -n coderag
```

Rollback por release Kustomize:

1. Volver a tag de imagen anterior en overlays.
2. Reaplicar overlay:

```bash
kubectl apply -k k8s/overlays/cloud
# o
kubectl apply -k k8s/overlays/cloud-with-redis
```

## Validacion funcional minima

1. Estado de workloads:

```bash
kubectl get pods -n coderag
kubectl get deploy,statefulset -n coderag
```

2. Revisar probes/eventos si hay fallas:

```bash
kubectl describe pod <pod-name> -n coderag
kubectl logs deploy/coderag-api -n coderag --tail=200
```

3. Smoke HTTP:

```bash
kubectl port-forward svc/coderag-api 8000:8000 -n coderag
```

En otra terminal:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/docs
```

4. Smoke de flujo de negocio (minimo):

- Ejecutar un `POST /repos/ingest` con un repo pequeno.
- Monitorear `GET /jobs/{job_id}` hasta estado final.

## Relacion con otras guias

- Resumen rapido Kubernetes previo: `../k8s/README.md`
- Arquitectura: `ARCHITECTURE.md`
- Configuracion de variables: `CONFIGURATION.md`
- API y contratos: `API_REFERENCE.md`
