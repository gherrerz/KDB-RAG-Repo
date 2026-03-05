"""Main desktop window for CodeRAG Studio."""

import sys

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
        self.setWindowTitle("CodeRAG Studio")
        self.resize(1100, 700)

        self.ingestion_view = IngestionView()
        self.query_view = QueryView()
        self.evidence_view = EvidenceView()

        query_container = QWidget()
        query_layout = QVBoxLayout()
        query_layout.addWidget(self.query_view)
        query_layout.addWidget(self.evidence_view)
        query_container.setLayout(query_layout)

        tabs = QTabWidget()
        tabs.addTab(self.ingestion_view, "Ingesta")
        tabs.addTab(query_container, "Consulta")
        self.setCentralWidget(tabs)

        self.ingestion_view.ingest_button.clicked.connect(self._on_ingest)
        self.query_view.query_button.clicked.connect(self._on_query)

    def _on_ingest(self) -> None:
        """Submit ingestion request and show initial job details."""
        payload = {
            "provider": self.ingestion_view.provider.currentText(),
            "repo_url": self.ingestion_view.repo_url.text().strip(),
            "token": self.ingestion_view.token.text().strip() or None,
            "branch": self.ingestion_view.branch.text().strip() or "main",
        }
        try:
            response = requests.post(f"{API_BASE}/repos/ingest", json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            message = (
                f"Job creado: {data['id']}\n"
                f"Estado: {data['status']}\n"
                f"Usa GET /jobs/{{id}} para ver progreso."
            )
            self.ingestion_view.logs.setPlainText(message)
        except Exception as exc:
            self.ingestion_view.logs.setPlainText(f"Error de ingesta: {exc}")

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
