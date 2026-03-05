"""Main desktop window for CodeRAG Studio."""

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
        self.setWindowTitle("CodeRAG Studio · Desktop")
        self.resize(1100, 700)

        self.ingestion_view = IngestionView()
        self.query_view = QueryView()
        self.evidence_view = EvidenceView()

        query_container = QWidget()
        query_layout = QVBoxLayout()
        query_layout.setContentsMargins(0, 0, 0, 0)
        query_layout.setSpacing(12)
        query_layout.addWidget(self.query_view)
        query_layout.addWidget(self.evidence_view)
        query_container.setLayout(query_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.ingestion_view, "Ingesta")
        self.tabs.addTab(query_container, "Consulta")

        container = QWidget()
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(16, 14, 16, 14)
        container_layout.setSpacing(10)
        container_layout.addWidget(self.tabs)
        container.setLayout(container_layout)
        self.setCentralWidget(container)

        self.ingestion_view.ingest_button.clicked.connect(self._on_ingest)
        self.query_view.query_button.clicked.connect(self._on_query)

        self._active_job_id: str | None = None
        self._job_poll_enabled = False
        self._last_logs: list[str] = []
        self._poll_timer_id = self.startTimer(1200)

        self.ingestion_view.set_status("idle", "Idle")
        self._apply_window_theme()

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
        payload = {
            "repo_id": self.query_view.repo_id.text().strip(),
            "query": self.query_view.query_input.toPlainText().strip(),
            "top_n": 80,
            "top_k": 20,
        }
        try:
            response = requests.post(f"{API_BASE}/query", json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            self.query_view.answer_output.setPlainText(data["answer"])
            self.evidence_view.set_citations(data["citations"])
        except Exception as exc:
            self.query_view.answer_output.setPlainText(f"Error en consulta: {exc}")


def main() -> None:
    """Run desktop application loop."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
