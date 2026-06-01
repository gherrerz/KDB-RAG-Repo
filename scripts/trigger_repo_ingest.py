"""Dispara una ingesta remota y espera la finalizacion del job."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, parse, request


def env_value(name: str, default: str = "") -> str:
    """Retorna una variable de entorno saneada."""
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else default


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: float = 60.0,
) -> tuple[int, object]:
    """Ejecuta una peticion HTTP y retorna status code y cuerpo JSON."""
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            parsed = json.loads(raw_body) if raw_body else {}
            return response.status, parsed
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw_body}
        return exc.code, parsed


def resolve_repo_context() -> tuple[str, str, str, str]:
    """Resuelve la URL del API y los datos efectivos del repo desde env vars."""
    api_base_url = env_value("INGEST_API_BASE_URL").rstrip("/")
    repo_url = env_value("EFFECTIVE_REPO_URL")
    branch = env_value("EFFECTIVE_BRANCH")
    commit = env_value("EFFECTIVE_COMMIT")

    if not api_base_url:
        raise SystemExit("Define INGEST_API_BASE_URL")
    if not repo_url:
        raise SystemExit("No se pudo resolver repo_url desde el webhook")
    if not branch:
        raise SystemExit("No se pudo resolver branch desde el webhook")

    return api_base_url, repo_url, branch, commit


def wait_for_health(api_base_url: str) -> None:
    """Espera a que el endpoint /health responda ok=true."""
    timeout_seconds = float(
        env_value("INGEST_STARTUP_TIMEOUT_SECONDS", "300") or "300"
    )
    deadline = time.monotonic() + timeout_seconds
    last_payload: object = {}

    while time.monotonic() < deadline:
        try:
            status, payload = request_json(
                "GET",
                f"{api_base_url}/health",
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            last_payload = {"error": str(exc)}
            time.sleep(5)
            continue

        last_payload = payload
        if status == 200 and isinstance(payload, dict) and payload.get("ok"):
            print(
                "API lista:",
                json.dumps(
                    {
                        "failed_components": payload.get("failed_components", []),
                        "context": payload.get("context"),
                    },
                    ensure_ascii=False,
                ),
            )
            return

        print(
            "Esperando /health ok=true:",
            json.dumps(last_payload, ensure_ascii=False),
        )
        time.sleep(5)

    raise SystemExit(
        "La API no quedo lista dentro del timeout. "
        f"Ultimo payload: {json.dumps(last_payload, ensure_ascii=False)}"
    )


def build_ingest_payload(
    *,
    repo_url: str,
    branch: str,
    commit: str,
) -> dict[str, object]:
    """Construye el payload de /repos/ingest desde variables de entorno."""
    auth_username = env_value("INGEST_AUTH_USERNAME")
    auth_secret = env_value("INGEST_AUTH_SECRET")
    legacy_token = env_value("INGEST_TOKEN")

    if legacy_token and auth_secret:
        raise SystemExit("Usa INGEST_TOKEN o INGEST_AUTH_SECRET, pero no ambos.")
    if auth_username and not auth_secret:
        raise SystemExit(
            "Define INGEST_AUTH_SECRET cuando uses INGEST_AUTH_USERNAME."
        )
    if auth_secret and not auth_username:
        raise SystemExit(
            "Define INGEST_AUTH_USERNAME cuando uses INGEST_AUTH_SECRET."
        )

    payload: dict[str, object] = {
        "provider": env_value("INGEST_PROVIDER", "bitbucket") or "bitbucket",
        "repo_url": repo_url,
        "branch": branch,
    }

    if commit:
        payload["commit"] = commit

    embedding_provider = env_value("INGEST_EMBEDDING_PROVIDER")
    if embedding_provider:
        payload["embedding_provider"] = embedding_provider

    embedding_model = env_value("INGEST_EMBEDDING_MODEL")
    if embedding_model:
        payload["embedding_model"] = embedding_model

    if legacy_token:
        payload["token"] = legacy_token

    if auth_secret:
        payload["auth"] = {
            "deployment": env_value("INGEST_AUTH_DEPLOYMENT", "server")
            or "server",
            "transport": env_value("INGEST_AUTH_TRANSPORT", "https")
            or "https",
            "method": env_value("INGEST_AUTH_METHOD", "http_basic")
            or "http_basic",
            "username": auth_username,
            "secret": auth_secret,
        }

    return payload


def mask_payload(payload: dict[str, object]) -> dict[str, Any]:
    """Oculta secretos antes de imprimir el payload."""
    masked = json.loads(json.dumps(payload))
    if "token" in masked:
        masked["token"] = "***"
    if "auth" in masked and isinstance(masked["auth"], dict):
        masked["auth"]["secret"] = "***"
    return masked


def poll_job(api_base_url: str, job_id: str) -> None:
    """Consulta el job remoto hasta completion o fail."""
    timeout_seconds = float(
        env_value("INGEST_JOB_TIMEOUT_SECONDS", "1800") or "1800"
    )
    poll_interval = float(
        env_value("INGEST_POLL_INTERVAL_SECONDS", "10") or "10"
    )
    logs_tail = int(env_value("INGEST_LOGS_TAIL", "50") or "50")
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    last_progress = None

    while time.monotonic() < deadline:
        status_code, payload = request_json(
            "GET",
            (
                f"{api_base_url}/jobs/"
                f"{parse.quote(job_id, safe='')}"
                f"?logs_tail={logs_tail}"
            ),
            timeout=30.0,
        )

        if status_code != 200:
            raise SystemExit(
                "Fallo consultando /jobs/{job_id}: "
                f"status={status_code}, "
                f"body={json.dumps(payload, ensure_ascii=False)}"
            )

        if not isinstance(payload, dict):
            raise SystemExit(
                "Respuesta inesperada de /jobs/{job_id}: "
                f"{json.dumps(payload, ensure_ascii=False)}"
            )

        job_status = str(payload.get("status", "")).lower()
        progress = payload.get("progress")
        if job_status != last_status or progress != last_progress:
            print(
                "Estado job:",
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": job_status,
                        "progress": progress,
                        "repo_id": payload.get("repo_id"),
                    },
                    ensure_ascii=False,
                ),
            )
            last_status = job_status
            last_progress = progress

        if job_status == "completed":
            print(
                "Job completado:",
                json.dumps(
                    {
                        "job_id": job_id,
                        "repo_id": payload.get("repo_id"),
                        "diagnostics": payload.get("diagnostics", {}),
                    },
                    ensure_ascii=False,
                ),
            )
            return

        if job_status == "failed":
            raise SystemExit(
                "Job de ingesta fallido: "
                f"{json.dumps(payload, ensure_ascii=False)}"
            )

        time.sleep(poll_interval)

    raise SystemExit("Timeout esperando la finalizacion del job de ingesta.")


def run_ingest() -> None:
    """Ejecuta el flujo end-to-end contra la API remota."""
    api_base_url, repo_url, branch, commit = resolve_repo_context()
    wait_for_health(api_base_url)

    ingest_payload = build_ingest_payload(
        repo_url=repo_url,
        branch=branch,
        commit=commit,
    )
    print(
        "Payload /repos/ingest:",
        json.dumps(mask_payload(ingest_payload), ensure_ascii=False),
    )

    status_code, ingest_response = request_json(
        "POST",
        f"{api_base_url}/repos/ingest",
        payload=ingest_payload,
        timeout=60.0,
    )
    if status_code != 200:
        raise SystemExit(
            "Fallo llamando /repos/ingest: "
            f"status={status_code}, "
            f"body={json.dumps(ingest_response, ensure_ascii=False)}"
        )

    if not isinstance(ingest_response, dict):
        raise SystemExit(
            "Respuesta inesperada de /repos/ingest: "
            f"{json.dumps(ingest_response, ensure_ascii=False)}"
        )

    job_id = str(ingest_response.get("id", "")).strip()
    if not job_id:
        raise SystemExit("La respuesta de /repos/ingest no incluyo job id.")

    print(
        "Job creado:",
        json.dumps(
            {
                "job_id": job_id,
                "status": ingest_response.get("status"),
                "repo_id": ingest_response.get("repo_id"),
            },
            ensure_ascii=False,
        ),
    )
    poll_job(api_base_url, job_id)


def main() -> None:
    """Punto de entrada CLI para Jenkins y otros orquestadores."""
    run_ingest()


if __name__ == "__main__":
    main()