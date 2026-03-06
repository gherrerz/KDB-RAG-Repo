"""Main desktop window for RAG Hybrid Response Validator."""

import sys
from typing import Any

import requests
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from coderag.ui.evidence_view import EvidenceView
from coderag.ui.ingestion_view import IngestionView
from coderag.ui.query_view import QueryView

API_BASE = "http://127.0.0.1:8000"


class MainWindow(QMainWindow):
    """Main application window containing ingestion and query tabs."""

    def __init__(self) -> None:
        """Build widgets and connect UI events."""
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

        self._active_job_id: str | None = None
        self._job_poll_enabled = False
        self._last_logs: list[str] = []
        self._poll_timer_id = self.startTimer(1200)

        self.ingestion_view.set_status("idle", "Idle")
        self._apply_window_theme()
        self._refresh_repo_ids(log_on_error=False)

    def _apply_window_theme(self) -> None:
        """Set consistent dark style for shell widgets and tabs."""
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #0B1220;
            }
            QTabWidget::pane {
                border: 1px solid #374151;
                border-radius: 10px;
                background-color: #111827;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #1F2937;
                color: #CBD5E1;
                padding: 8px 14px;
                margin-right: 6px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QTabBar::tab:selected {
                background-color: #2563EB;
                color: #F8FAFC;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                background-color: #334155;
            }
            """
        )

    def _on_ingest(self) -> None:
        """Submit ingestion request and show initial job details."""
        repo_url = self.ingestion_view.repo_url.text().strip()
        if not repo_url:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.append_log("Repo URL es obligatorio")
            return

        payload = {
            "provider": self.ingestion_view.provider.currentText(),
            "repo_url": repo_url,
            "token": self.ingestion_view.token.text().strip() or None,
            "branch": self.ingestion_view.branch.text().strip() or "main",
        }
        self.ingestion_view.set_running(True)
        self.ingestion_view.set_status("running", "En progreso")
        self.ingestion_view.set_progress(5)
        self.ingestion_view.set_job_id("")
        self.ingestion_view.set_repo_id("")
        self._last_logs = []

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

    def _on_reset_all(self) -> None:
        """Request full reset of indexes, graph, metadata and workspace."""
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

    def timerEvent(self, event: Any) -> None:  # noqa: N802
        """Poll ingestion job endpoint and update status widgets."""
        if event.timerId() != self._poll_timer_id:
            return
        if not self._job_poll_enabled or not self._active_job_id:
            return

        endpoint = f"{API_BASE}/jobs/{self._active_job_id}"
        try:
            response = requests.get(endpoint, timeout=5)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self.ingestion_view.append_log(f"Polling falló: {exc}")
            return

        self._sync_job_ui(data)

    def _sync_job_ui(self, data: dict[str, Any]) -> None:
        """Apply polled job state and logs to ingestion controls."""
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
            self._job_poll_enabled = False
            self._active_job_id = None
            return

        if status in {"failed", "error"}:
            self.ingestion_view.set_status("error", "Error")
            self.ingestion_view.set_progress(100)
            self.ingestion_view.set_running(False)
            self.ingestion_view.append_log("Job falló")
            self._job_poll_enabled = False
            self._active_job_id = None
            return

        self.ingestion_view.set_status("running", "En progreso")
        self.ingestion_view.set_progress(30)

    def _on_query(self) -> None:
        """Send query request and render answer with citations."""
        repo_id = self.query_view.get_repo_id_text()
        question = self.query_view.get_question_text()

        if not repo_id:
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                "Debes seleccionar un ID de repositorio del listado.",
                error=True,
            )
            return

        if not self.query_view.has_repo_id(repo_id):
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                "El ID seleccionado no existe en la base de conocimiento. "
                "Actualiza la lista e intenta nuevamente.",
                error=True,
            )
            return

        if not question:
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                "Debes escribir una pregunta para consultar.",
                error=True,
            )
            return

        self.query_view.append_user_message(question)
        self.query_view.set_running(True)
        self.query_view.set_status("running", "Consultando")
        self.query_view.clear_question()

        payload = {
            "repo_id": repo_id,
            "query": question,
            "top_n": 80,
            "top_k": 20,
        }
        try:
            response = requests.post(f"{API_BASE}/query", json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            answer_text = str(data.get("answer") or "Sin respuesta.")
            self.query_view.append_assistant_message(answer_text)
            self.evidence_view.set_citations(data.get("citations") or [])
            self.query_view.set_status("success", "Completado")
        except requests.HTTPError:
            detail = "Error HTTP en consulta."
            try:
                error_data = response.json()
                detail = str(error_data.get("detail") or detail)
            except Exception:
                pass
            self.query_view.set_status("error", "Error")
            self.query_view.append_assistant_message(
                f"{detail}\n\nEndpoint: {API_BASE}/query",
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

    def _refresh_repo_ids(
        self,
        selected_repo_id: str | None = None,
        log_on_error: bool = False,
    ) -> None:
        """Refresh query repo-id dropdown from API catalog endpoint."""
        try:
            response = requests.get(f"{API_BASE}/repos", timeout=10)
            response.raise_for_status()
            data = response.json()
            repo_ids_raw = data.get("repo_ids") or []
            repo_ids = [str(value) for value in repo_ids_raw if str(value).strip()]
            self.query_view.set_repo_ids(repo_ids)
            if selected_repo_id and self.query_view.has_repo_id(selected_repo_id):
                self.query_view.repo_id.setCurrentText(selected_repo_id)
        except Exception as exc:
            if log_on_error:
                self.ingestion_view.append_log(
                    f"No se pudo actualizar lista de repos: {exc}"
                )


def main() -> None:
    """Run desktop application loop."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
