"""Ventana principal del escritorio para el Validador Híbrido de Respuestas RAG."""

import sys
import os
from typing import Any
from urllib.parse import quote, quote_plus

import requests
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from coderag.core.provider_model_catalog import normalize_provider_name
from coderag.core.settings import get_settings
from coderag.ui.provider_action_state import (
    ActionState,
    evaluate_ingest_action,
    evaluate_query_action,
)
from coderag.ui.evidence_view import EvidenceView
from coderag.ui.ingestion_view import IngestionView
from coderag.ui.provider_messages import (
    ingest_requires_repo_url_message,
)
from coderag.ui.query_preconditions import evaluate_local_query_preconditions
from coderag.ui.query_response_formatter import (
    build_query_answer_text,
    build_repo_not_ready_message,
)
from coderag.ui.query_view import QueryView

API_BASE = os.getenv("CODERAG_API_BASE", "http://127.0.0.1:8000")
UI_REQUEST_TIMEOUT_SECONDS = get_settings().ui_request_timeout_seconds
JOB_POLL_LOGS_TAIL = 180
QUERY_PROFILE_SETTINGS: dict[str, dict[str, float | int | bool]] = {
    "rapido": {
        "top_n": 40,
        "top_k": 10,
        "timeout_seconds": max(90.0, float(UI_REQUEST_TIMEOUT_SECONDS)),
        "allow_retry": True,
        "retry_top_n": 25,
        "retry_top_k": 8,
        "retry_timeout_seconds": max(60.0, float(UI_REQUEST_TIMEOUT_SECONDS)),
    },
    "balanceado": {
        "top_n": 80,
        "top_k": 20,
        "timeout_seconds": float(UI_REQUEST_TIMEOUT_SECONDS),
        "allow_retry": True,
        "retry_top_n": 40,
        "retry_top_k": 10,
        "retry_timeout_seconds": 45.0,
    },
    "profundo": {
        "top_n": 120,
        "top_k": 30,
        "timeout_seconds": max(120.0, float(UI_REQUEST_TIMEOUT_SECONDS)),
        "allow_retry": True,
        "retry_top_n": 60,
        "retry_top_k": 15,
        "retry_timeout_seconds": 60.0,
    },
}


class MainWindow(QMainWindow):
    """Ventana principal de la aplicación que contiene pestañas de ingesta y consulta."""

    def __init__(self) -> None:
        """Cree widgets y conecte eventos de UI."""
        super().__init__()
        self.setWindowTitle("RAG Hybrid Response Validator · Desktop")
        self.resize(1100, 700)

        self.ingestion_view = IngestionView()
        self.query_view = QueryView()
        self.evidence_view = EvidenceView()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.ingestion_view, "Ingesta")
        self.tabs.addTab(self.query_view, "Consulta")

        container = QWidget()
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(16, 14, 16, 14)
        container_layout.setSpacing(10)
        container_layout.addWidget(self.tabs)
        container.setLayout(container_layout)
        self.setCentralWidget(container)

        self.ingestion_view.ingest_button.clicked.connect(self._on_ingest)
        self.ingestion_view.reset_button.clicked.connect(self._on_reset_all)
        self.query_view.query_button.clicked.connect(self._on_query)
        self.query_view.refresh_repo_ids_button.clicked.connect(
            lambda: self._refresh_repo_ids(log_on_error=True)
        )
        self.query_view.delete_repo_button.clicked.connect(
            self._on_delete_selected_repo
        )
        self._connect_action_state_signals()

        self._active_job_id: str | None = None
        self._job_poll_enabled = False
        self._last_logs: list[str] = []
        self._poll_timeout_seconds = max(8.0, min(float(UI_REQUEST_TIMEOUT_SECONDS), 60.0))
        self._poll_failure_count = 0
        self._last_poll_failure_message = ""
        self._poll_timer_id = self.startTimer(1200)

        self.ingestion_view.set_status("idle", "Idle")
        self._apply_window_theme()
        self._refresh_repo_ids(log_on_error=False)
        self._selected_query_repo_id = self.query_view.get_repo_id_text()
        self.query_view.repo_id.currentTextChanged.connect(self._on_query_repo_changed)
        self._sync_query_limits_from_profile()
        self._update_ingest_action_state()
        self._update_query_action_state()

    def _set_query_controls_enabled(self, enabled: bool) -> None:
        """Habilita o deshabilita acciones de consulta durante operaciones críticas."""
        self.query_view.repo_id.setDisabled(not enabled)
        self.query_view.embedding_provider.setDisabled(not enabled)
        self.query_view.embedding_model.setDisabled(not enabled)
        self.query_view.llm_provider.setDisabled(not enabled)
        self.query_view.answer_model.setDisabled(not enabled)
        self.query_view.verifier_model.setDisabled(not enabled)
        self.query_view.query_profile.setDisabled(not enabled)
        self.query_view.top_n_input.setDisabled(not enabled)
        self.query_view.top_k_input.setDisabled(not enabled)
        self.query_view.retrieval_only_mode.setDisabled(not enabled)
        self.query_view.include_context.setDisabled(
            (not enabled) or (not self.query_view.is_retrieval_only_enabled())
        )
        self.query_view.refresh_repo_ids_button.setDisabled(not enabled)
        self.query_view.refresh_models_button.setDisabled(not enabled)
        self.query_view.delete_repo_button.setDisabled(not enabled)
        self.query_view.query_input.setDisabled(not enabled)
        if not enabled:
            state = evaluate_query_action(
                controls_enabled=False,
                has_repo=False,
                has_question=False,
                embedding_ready=True,
                embedding_reason="ok",
                llm_ready=True,
                llm_reason="ok",
                force_fallback=False,
                retrieval_only_mode=False,
            )
            self._apply_query_action_state(state)
            return
        self._update_query_action_state()

    def _update_ingest_action_state(self) -> None:
        """Actualiza habilitacion y tooltip de Ingestar segun readiness actual."""
        if self.ingestion_view.ingest_button.text() == "Ingestando...":
            return

        ready, reason = self.ingestion_view.is_embedding_provider_ready()
        state = evaluate_ingest_action(
            embedding_ready=ready,
            embedding_reason=reason,
            force_fallback=self.ingestion_view.is_force_fallback_enabled(),
        )
        self._apply_ingest_action_state(state)

    def _update_query_action_state(self) -> None:
        """Actualiza habilitacion y tooltip de Consultar segun estado operativo."""
        if self.query_view.query_input.isEnabled() is False:
            return

        emb_ready, emb_reason = self.query_view.is_embedding_provider_ready()
        llm_ready, llm_reason = self.query_view.is_llm_provider_ready()
        has_repo = bool(self.query_view.get_repo_id_text())
        has_question = bool(self.query_view.get_question_text())
        state = evaluate_query_action(
            controls_enabled=True,
            has_repo=has_repo,
            has_question=has_question,
            embedding_ready=emb_ready,
            embedding_reason=emb_reason,
            llm_ready=llm_ready,
            llm_reason=llm_reason,
            force_fallback=self.query_view.is_force_fallback_enabled(),
            retrieval_only_mode=self.query_view.is_retrieval_only_enabled(),
        )
        self._apply_query_action_state(state)

    def _connect_action_state_signals(self) -> None:
        """Conecta señales de UI que afectan disponibilidad de acciones."""
        self.ingestion_view.embedding_provider.currentTextChanged.connect(
            lambda _: self._update_ingest_action_state()
        )
        self.ingestion_view.force_fallback.toggled.connect(
            lambda _: self._update_ingest_action_state()
        )
        self.query_view.embedding_provider.currentTextChanged.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.llm_provider.currentTextChanged.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.force_fallback.toggled.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.retrieval_only_mode.toggled.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.include_context.toggled.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.repo_id.currentTextChanged.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.query_input.textChanged.connect(
            lambda _: self._update_query_action_state()
        )
        self.query_view.query_profile.currentTextChanged.connect(
            lambda _: self._sync_query_limits_from_profile()
        )

    def _sync_query_limits_from_profile(self) -> None:
        """Restaura top-n/top-k por defecto cada vez que cambia el perfil."""
        profile_settings = self._resolve_query_profile_settings(
            self.query_view.get_query_profile()
        )
        self.query_view.set_query_limits(
            top_n=int(profile_settings["top_n"]),
            top_k=int(profile_settings["top_k"]),
        )

    def _apply_ingest_action_state(self, state: ActionState) -> None:
        """Aplica estado evaluado al botón/hint de ingesta."""
        self.ingestion_view.ingest_button.setDisabled(not state.enabled)
        self.ingestion_view.ingest_button.setToolTip(state.message)
        self.ingestion_view.set_ingest_action_hint(state.message)

    def _apply_query_action_state(self, state: ActionState) -> None:
        """Aplica estado evaluado al botón/hint de consulta."""
        self.query_view.query_button.setDisabled(not state.enabled)
        self.query_view.query_button.setToolTip(state.message)
        self.query_view.set_query_action_hint(state.message)

    def _finalize_job_poll(self) -> None:
        """Finaliza polling de job y libera estado asociado."""
        self._job_poll_enabled = False
        self._active_job_id = None

    def _apply_window_theme(self) -> None:
        """Establezca un estilo oscuro consistente para pestañas y widgets de shell."""
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #081326;
            }
            QTabWidget::pane {
                border: 1px solid #2A3A5A;
                border-radius: 12px;
                background-color: #0F1D34;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #162A47;
                color: #B5C6E4;
                padding: 9px 16px;
                margin-right: 6px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QTabBar::tab:selected {
                background-color: #2F7BFF;
                color: #F8FAFC;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                background-color: #203965;
            }
            """
        )

    def _on_ingest(self) -> None:
        """Envíe la solicitud de ingesta y muestre los detalles iniciales del trabajo."""
        repo_url = self.ingestion_view.repo_url.text().strip()
        if not repo_url:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.append_log(ingest_requires_repo_url_message())
            return

        embedding_ready, reason = self.ingestion_view.is_embedding_provider_ready()
        if not embedding_ready and not self.ingestion_view.is_force_fallback_enabled():
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.append_log(
                "Provider de embeddings no esta listo "
                f"({reason}). Activa 'Forzar fallback' para continuar."
            )
            return

        payload = {
            "provider": self.ingestion_view.provider.currentText(),
            "repo_url": repo_url,
            "branch": self.ingestion_view.branch.text().strip() or "main",
            "embedding_provider": self.ingestion_view.embedding_provider.currentText(),
            "embedding_model": self.ingestion_view.get_embedding_model() or None,
        }
        auth_payload = self.ingestion_view.get_auth_payload()
        if auth_payload is not None:
            payload["auth"] = auth_payload
        self.ingestion_view.set_running(True)
        self.ingestion_view.set_status("running", "En progreso")
        self.ingestion_view.set_progress(5)
        self.ingestion_view.set_job_id("")
        self.ingestion_view.set_repo_id("")
        self._last_logs = []
        self._poll_failure_count = 0
        self._last_poll_failure_message = ""

        try:
            response = requests.post(f"{API_BASE}/repos/ingest", json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()

            job_id = str(data.get("job_id") or data.get("id") or "")
            status = str(data.get("status") or "pending")
            self.ingestion_view.set_running(False)
            self.ingestion_view.set_job_id(job_id)
            self.ingestion_view.append_log(f"Job creado: {job_id}")
            self.ingestion_view.append_log(f"Estado inicial: {status}")

            if job_id:
                self._active_job_id = job_id
                self._job_poll_enabled = True
                self._set_query_controls_enabled(False)
                self.ingestion_view.set_status("running", "En progreso")
                self.ingestion_view.set_progress(15)
                self.ingestion_view.append_log("Monitoreando estado del job...")
            else:
                self.ingestion_view.set_status("error", "Error")
                self.ingestion_view.append_log("No se recibió job_id")
        except Exception as exc:
            self.ingestion_view.set_running(False)
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.set_progress(0)
            self.ingestion_view.append_log(f"Error de ingesta: {exc}")
            self._update_ingest_action_state()

    def _on_reset_all(self) -> None:
        """Solicite un restablecimiento completo de índices, gráficos, metadatos y espacio de trabajo."""
        if self._job_poll_enabled:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.append_log(
                "No se puede limpiar mientras hay una ingesta en progreso."
            )
            return

        self.ingestion_view.set_reset_running(True)
        self.ingestion_view.set_status("running", "Limpiando")
        self.ingestion_view.set_progress(0)
        self.ingestion_view.append_log("Iniciando limpieza total del sistema...")

        try:
            response = requests.post(f"{API_BASE}/admin/reset", timeout=120)
            response.raise_for_status()
            data = response.json()
            message = str(data.get("message") or "Limpieza total completada")
            self.ingestion_view.append_log(message)

            for item in data.get("cleared") or []:
                self.ingestion_view.append_log(f"- {item}")
            for warning in data.get("warnings") or []:
                self.ingestion_view.append_log(f"Advertencia: {warning}")

            self.ingestion_view.set_job_id("")
            self.ingestion_view.set_repo_id("")
            self.query_view.clear_repo_id()
            self.evidence_view.set_citations([])
            self._refresh_repo_ids(log_on_error=True)

            self.ingestion_view.set_progress(100)
            self.ingestion_view.set_status("success", "Limpio")
        except requests.HTTPError:
            detail = "Error HTTP al limpiar."  # pragma: no cover - network detail
            try:
                error_data = response.json()
                detail = str(error_data.get("detail") or detail)
            except Exception:
                pass
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.set_progress(0)
            self.ingestion_view.append_log(f"Error de limpieza: {detail}")
        except Exception as exc:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.set_progress(0)
            self.ingestion_view.append_log(f"Error de limpieza: {exc}")
        finally:
            self.ingestion_view.set_reset_running(False)
            self._update_ingest_action_state()

    def _on_delete_selected_repo(self) -> None:
        """Solicita y ejecuta el borrado completo del repo seleccionado en consulta."""
        repo_id = self.query_view.get_repo_id_text()
        if not repo_id:
            self._show_query_error("Selecciona un ID de repositorio para eliminar.")
            return

        confirmation = QMessageBox.question(
            self,
            "Confirmar eliminación",
            (
                "Se eliminará el repositorio seleccionado de Chroma, BM25, "
                "Neo4j, workspace y metadata SQLite.\n\n"
                f"Repositorio: {repo_id}\n"
                "Esta acción no se puede deshacer."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        encoded_repo_id = quote(repo_id, safe="")
        self.query_view.set_running(True)
        self.query_view.set_status("running", "Eliminando")

        try:
            response = requests.delete(
                f"{API_BASE}/repos/{encoded_repo_id}",
                timeout=UI_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()

            cleared = payload.get("cleared") or []
            warnings = payload.get("warnings") or []

            self.query_view.clear_history()
            self.query_view.clear_question()
            self.query_view.clear_repo_id()
            self.evidence_view.set_citations([])
            self._refresh_repo_ids(log_on_error=True)

            self.query_view.set_status("success", "Eliminado")
            self.query_view.append_assistant_message(
                str(payload.get("message") or f"Repositorio '{repo_id}' eliminado.")
            )
            if cleared:
                self.ingestion_view.append_log(
                    "Borrado repo completado en capas: " + ", ".join(cleared)
                )
            for warning in warnings:
                self.ingestion_view.append_log(
                    f"Advertencia borrado repo '{repo_id}': {warning}"
                )
        except requests.HTTPError:
            detail = "Error HTTP al eliminar repositorio."
            try:
                error_data = response.json()
                detail = str(error_data.get("detail") or detail)
            except Exception:
                pass
            self._show_query_error(detail)
        except Exception as exc:
            self._show_query_error(f"Error eliminando repositorio: {exc}")
        finally:
            self.query_view.set_running(False)
            self._update_query_action_state()

    def timerEvent(self, event: Any) -> None:  # noqa: N802
        """Sondear el punto final del trabajo de ingesta y actualizar los widgets de estado."""
        if event.timerId() != self._poll_timer_id:
            return
        if not self._job_poll_enabled or not self._active_job_id:
            return

        endpoint = f"{API_BASE}/jobs/{self._active_job_id}?logs_tail={JOB_POLL_LOGS_TAIL}"
        try:
            response = requests.get(endpoint, timeout=self._poll_timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._poll_failure_count += 1
            error_message = str(exc)
            should_log = (
                self._poll_failure_count == 1
                or self._poll_failure_count % 5 == 0
                or error_message != self._last_poll_failure_message
            )
            if should_log:
                self.ingestion_view.append_log(
                    "Polling falló "
                    f"(intentos={self._poll_failure_count}): {error_message}"
                )
            self._last_poll_failure_message = error_message
            return

        self._poll_failure_count = 0
        self._last_poll_failure_message = ""

        self._sync_job_ui(data)

    def _sync_job_ui(self, data: dict[str, Any]) -> None:
        """Aplique el estado del trabajo sondeado y los registros a los controles de ingesta."""
        status = str(data.get("status") or "pending").lower()
        logs = data.get("logs")
        if isinstance(logs, list):
            text_logs = [str(line) for line in logs]
            if text_logs != self._last_logs:
                self._last_logs = text_logs
                self.ingestion_view.set_logs(text_logs)

        repo_id = str(data.get("repo_id") or "")
        if repo_id:
            self.ingestion_view.set_repo_id(repo_id)
            self._refresh_repo_ids(selected_repo_id=repo_id, log_on_error=True)

        if status in {"pending", "queued"}:
            self.ingestion_view.set_status("running", "En progreso")
            self.ingestion_view.set_progress(15)
            return

        if status in {"running", "in_progress"}:
            self.ingestion_view.set_status("running", "En progreso")
            self.ingestion_view.set_progress(55)
            return

        if status in {"completed", "done", "success"}:
            self.ingestion_view.set_status("success", "Completado")
            self.ingestion_view.set_progress(100)
            self.ingestion_view.set_running(False)
            self.ingestion_view.append_log("Job completado")
            self._set_query_controls_enabled(True)
            self._update_ingest_action_state()
            self._finalize_job_poll()
            return

        if status in {"partial"}:
            self.ingestion_view.set_status("error", "Parcial")
            self.ingestion_view.set_progress(100)
            self.ingestion_view.set_running(False)
            self.ingestion_view.append_log(
                "Job completado parcialmente: revisar readiness antes de consultar."
            )
            self._set_query_controls_enabled(True)
            self._update_ingest_action_state()
            self._finalize_job_poll()
            return

        if status in {"failed", "error"}:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.set_progress(100)
            self.ingestion_view.set_running(False)
            self.ingestion_view.append_log("Job falló")
            self._set_query_controls_enabled(True)
            self._update_ingest_action_state()
            self._finalize_job_poll()
            return

        self.ingestion_view.set_status("running", "En progreso")
        self.ingestion_view.set_progress(30)

    def _on_query(self) -> None:
        """Enviar solicitud de consulta y dar respuesta con citas."""
        repo_id = self.query_view.get_repo_id_text()
        question = self.query_view.get_question_text()
        profile = self.query_view.get_query_profile()
        profile_settings = self._resolve_query_profile_settings(profile)

        emb_ready, emb_reason = self.query_view.is_embedding_provider_ready()
        llm_ready, llm_reason = self.query_view.is_llm_provider_ready()
        local_check = evaluate_local_query_preconditions(
            repo_id=repo_id,
            question=question,
            has_repo_in_catalog=self.query_view.has_repo_id(repo_id),
            job_poll_enabled=self._job_poll_enabled,
            embedding_ready=emb_ready,
            embedding_reason=emb_reason,
            llm_ready=llm_ready,
            llm_reason=llm_reason,
            force_fallback=self.query_view.is_force_fallback_enabled(),
            retrieval_only_mode=self.query_view.is_retrieval_only_enabled(),
        )
        if not local_check.allowed:
            self._show_query_error(local_check.message)
            return

        try:
            status_url = self._repo_status_url(
                repo_id=repo_id,
                requested_embedding_provider=self.query_view.get_embedding_provider(),
                requested_embedding_model=self.query_view.get_embedding_model() or None,
            )
            status_response = requests.get(
                status_url,
                timeout=UI_REQUEST_TIMEOUT_SECONDS,
            )
            status_response.raise_for_status()
            status_payload = status_response.json()
            if not bool(status_payload.get("query_ready")):
                self._show_query_error(
                    build_repo_not_ready_message(status_payload.get("warnings") or []),
                )
                return
        except requests.HTTPError:
            self._show_query_error(
                "No se pudo validar el estado del repositorio antes de consultar.",
            )
            return
        except Exception as exc:
            self._show_query_error(
                f"Error validando estado del repositorio: {exc}",
            )
            return

        self.query_view.append_user_message(question)
        self.query_view.set_running(True)
        self.query_view.set_status("running", "Consultando")
        self.query_view.clear_question()

        retrieval_only_mode = self.query_view.is_retrieval_only_enabled()
        payload = {
            "repo_id": repo_id,
            "query": question,
            "top_n": self.query_view.get_top_n(),
            "top_k": self.query_view.get_top_k(),
            "embedding_provider": self.query_view.get_embedding_provider(),
            "embedding_model": self.query_view.get_embedding_model() or None,
        }
        if retrieval_only_mode:
            payload["include_context"] = self.query_view.is_include_context_enabled()
            query_endpoint = f"{API_BASE}/query/retrieval"
        else:
            payload.update(
                {
                    "llm_provider": self.query_view.get_llm_provider(),
                    "answer_model": self.query_view.get_answer_model() or None,
                    "verifier_model": self.query_view.get_verifier_model() or None,
                }
            )
            query_endpoint = f"{API_BASE}/query"

        query_timeout = float(profile_settings["timeout_seconds"])
        query_timeout = self._adjust_timeout_for_model(
            query_timeout,
            payload.get("answer_model"),
        )
        try:
            response = requests.post(
                query_endpoint,
                json=payload,
                timeout=query_timeout,
            )
            response.raise_for_status()
            data = response.json()
            answer_text = self._format_query_success_text(
                response_payload=data,
                retrieval_only_mode=retrieval_only_mode,
            )
            self.query_view.append_assistant_message(answer_text)
            self.evidence_view.set_citations(data.get("citations") or [])
            self.query_view.set_status("success", "Completado")
        except requests.Timeout:
            if not bool(profile_settings["allow_retry"]):
                self.query_view.set_status("error", "Error")
                self.query_view.append_assistant_message(
                    (
                        "Timeout en consulta con perfil rapido. "
                        "Prueba perfil balanceado o profundo para permitir "
                        "reintento automatico."
                    ),
                    error=True,
                )
                return

            retry_payload = dict(payload)
            retry_payload["top_n"] = int(profile_settings["retry_top_n"])
            retry_payload["top_k"] = int(profile_settings["retry_top_k"])
            try:
                retry_timeout = float(profile_settings["retry_timeout_seconds"])
                retry_timeout = self._adjust_timeout_for_model(
                    retry_timeout,
                    retry_payload.get("answer_model"),
                )
                response = requests.post(
                    query_endpoint,
                    json=retry_payload,
                    timeout=retry_timeout,
                )
                response.raise_for_status()
                data = response.json()
                answer_text = self._format_query_success_text(
                    response_payload=data,
                    retrieval_only_mode=retrieval_only_mode,
                )
                self.query_view.append_assistant_message(
                    "Respuesta obtenida tras reintento automatico (modo rapido).\n\n"
                    f"{answer_text}"
                )
                self.evidence_view.set_citations(data.get("citations") or [])
                self.query_view.set_status("success", "Completado")
            except requests.HTTPError:
                detail = "Error HTTP en consulta (reintento rapido)."
                try:
                    error_data = response.json()
                    detail = self._format_query_http_detail(error_data.get("detail"))
                except Exception:
                    pass
                self.query_view.set_status("error", "Error")
                self.query_view.append_assistant_message(
                    f"{detail}\n\nEndpoint: {query_endpoint}",
                    error=True,
                )
            except Exception as exc:
                self.query_view.set_status("error", "Error")
                self.query_view.append_assistant_message(
                    (
                        "Error en consulta tras timeout inicial: "
                        f"{exc}. Sugerencia: reduce complejidad de pregunta "
                        "o cambia temporalmente de modelo."
                    ),
                    error=True,
                )
        except requests.HTTPError:
            detail = "Error HTTP en consulta."
            try:
                error_data = response.json()
                detail = self._format_query_http_detail(error_data.get("detail"))
            except Exception:
                pass
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                f"{detail}\n\nEndpoint: {query_endpoint}",
                error=True,
            )
        except Exception as exc:
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                f"Error en consulta: {exc}",
                error=True,
            )
        finally:
            self.query_view.set_running(False)
            self._update_query_action_state()

    @staticmethod
    def _resolve_query_profile_settings(
        profile: str,
    ) -> dict[str, float | int | bool]:
        """Devuelve estrategia de consulta segun el perfil de UX seleccionado."""
        normalized = profile.strip().lower()
        return QUERY_PROFILE_SETTINGS.get(
            normalized,
            QUERY_PROFILE_SETTINGS["balanceado"],
        )

    @staticmethod
    def _adjust_timeout_for_model(
        base_timeout_seconds: float,
        model: object,
    ) -> float:
        """Ajusta timeout por modelo para evitar expiraciones prematuras en UI."""
        selected = str(model or "").strip().lower()
        if selected.startswith("gpt-5"):
            return max(base_timeout_seconds, 120.0)
        return base_timeout_seconds

    def _show_query_error(self, message: str) -> None:
        """Muestra error de consulta con formato consistente en la UI."""
        self.query_view.set_status("error", "Error")
        self.query_view.append_assistant_message(message, error=True)

    @staticmethod
    def _format_query_success_text(
        *,
        response_payload: dict[str, Any],
        retrieval_only_mode: bool,
    ) -> str:
        """Construye salida visible diferenciada según modo de consulta ejecutado."""
        base_answer = str(response_payload.get("answer") or "Sin respuesta.")
        if not retrieval_only_mode:
            return build_query_answer_text(
                base_answer,
                response_payload.get("diagnostics") or {},
            )

        diagnostics = response_payload.get("diagnostics") or {}
        inventory_route = str(diagnostics.get("inventory_route") or "").strip()
        if inventory_route:
            inventory_target = str(diagnostics.get("inventory_target") or "").strip()
            inventory_total = int(diagnostics.get("inventory_total") or 0)
            inventory_page = int(diagnostics.get("inventory_page") or 1)
            inventory_page_size = int(diagnostics.get("inventory_page_size") or 0)
            header = "Modo: Retrieval-only inventario (sin LLM)"
            summary_parts = [
                f"Total: {inventory_total}",
                f"Página: {inventory_page}",
            ]
            if inventory_page_size > 0:
                summary_parts.append(f"Page size: {inventory_page_size}")
            if inventory_target:
                summary_parts.append(f"Objetivo: {inventory_target}")
            summary = " | ".join(summary_parts)

            context_block = str(response_payload.get("context") or "").strip()
            if not context_block:
                return f"{header}\n{summary}\n\n{base_answer}"
            return (
                f"{header}\n{summary}\n\n{base_answer}"
                f"\n\nContexto ensamblado:\n{context_block}"
            )

        context_block = str(response_payload.get("context") or "").strip()
        if not context_block:
            return base_answer
        return f"{base_answer}\n\nContexto ensamblado:\n{context_block}"

    @staticmethod
    def _format_query_http_detail(detail_payload: object) -> str:
        """Normaliza payload de error HTTP para mensajes de consulta legibles en UI."""
        if isinstance(detail_payload, dict):
            code = str(detail_payload.get("code") or "").strip().lower()
            if code in {"repo_not_ready", "embedding_incompatible"}:
                repo_status = detail_payload.get("repo_status")
                if isinstance(repo_status, dict):
                    warnings = repo_status.get("warnings") or []
                    if isinstance(warnings, list):
                        return build_repo_not_ready_message(warnings)
            message = detail_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return str(detail_payload or "Error HTTP en consulta.")

    def _on_query_repo_changed(self, repo_id: str) -> None:
        """Limpie la conversación y evidencias cuando cambia el repositorio activo."""
        selected_repo = repo_id.strip()
        previous_repo = getattr(self, "_selected_query_repo_id", "")
        if selected_repo == previous_repo:
            return

        self._selected_query_repo_id = selected_repo
        self.query_view.clear_history()
        self.query_view.clear_question()
        self.query_view.set_status("idle", "Lista")
        self.evidence_view.set_citations([])
        self._sync_query_embedding_with_repo_runtime(selected_repo)

    @staticmethod
    def _repo_status_url(
        *,
        repo_id: str,
        requested_embedding_provider: str | None = None,
        requested_embedding_model: str | None = None,
    ) -> str:
        """Construye endpoint de estado por repo con hints opcionales de embeddings."""
        base = f"{API_BASE}/repos/{repo_id}/status"
        params: list[str] = []
        if requested_embedding_provider:
            params.append(
                "requested_embedding_provider="
                f"{quote_plus(requested_embedding_provider)}"
            )
        if requested_embedding_model:
            params.append(
                "requested_embedding_model="
                f"{quote_plus(requested_embedding_model)}"
            )
        if not params:
            return base
        return f"{base}?{'&'.join(params)}"

    def _sync_query_embedding_with_repo_runtime(self, repo_id: str) -> None:
        """Sincroniza provider/model de consulta con la última ingesta conocida del repo."""
        if not repo_id:
            return
        try:
            status_response = requests.get(
                self._repo_status_url(repo_id=repo_id),
                timeout=UI_REQUEST_TIMEOUT_SECONDS,
            )
            status_response.raise_for_status()
            payload = status_response.json()
        except Exception as exc:
            self.ingestion_view.append_log(
                "No se pudo sincronizar embedding de consulta con runtime "
                f"del repo: {exc}"
            )
            return

        runtime_provider = str(payload.get("last_embedding_provider") or "").strip()
        runtime_model = str(payload.get("last_embedding_model") or "").strip()
        if runtime_provider:
            self.query_view.embedding_provider.setCurrentText(
                normalize_provider_name(runtime_provider)
            )

        if runtime_model:
            combo = self.query_view.embedding_model
            combo.blockSignals(True)
            if combo.findText(runtime_model) < 0:
                combo.addItem(runtime_model)
            combo.setCurrentText(runtime_model)
            combo.blockSignals(False)

        self._update_query_action_state()

    def _refresh_repo_ids(
        self,
        selected_repo_id: str | None = None,
        log_on_error: bool = False,
    ) -> None:
        """Actualice el menú desplegable de ID de repositorio de consulta desde el punto final del catálogo de API."""
        try:
            previous_repo = self.query_view.get_repo_id_text()
            response = requests.get(f"{API_BASE}/repos", timeout=10)
            response.raise_for_status()
            data = response.json()
            repo_ids_raw = data.get("repo_ids") or []
            repo_ids = [str(value) for value in repo_ids_raw if str(value).strip()]
            self.query_view.set_repo_ids(repo_ids)
            if selected_repo_id and self.query_view.has_repo_id(selected_repo_id):
                self.query_view.repo_id.setCurrentText(selected_repo_id)

            current_repo = self.query_view.get_repo_id_text()
            if current_repo != previous_repo:
                self._on_query_repo_changed(current_repo)
            self._update_query_action_state()
        except Exception as exc:
            if log_on_error:
                self.ingestion_view.append_log(
                    f"No se pudo actualizar lista de repos: {exc}"
                )


def main() -> None:
    """Ejecute el bucle de la aplicación de escritorio."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
