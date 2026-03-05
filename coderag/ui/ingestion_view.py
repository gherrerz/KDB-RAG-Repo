"""Ingestion view widgets for repository setup and execution."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class IngestionView(QWidget):
    """UI panel that captures ingestion parameters and logs."""

    def __init__(self) -> None:
        """Initialize form controls for repository ingestion."""
        super().__init__()
        self.title_label = QLabel("Ingesta de Repositorio")
        self.subtitle_label = QLabel(
            "Conecta un repositorio y monitorea el pipeline de indexación en tiempo real."
        )
        self.status_chip = QLabel("Idle")
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setProperty("state", "idle")

        self.provider = QComboBox()
        self.provider.addItems(["github", "bitbucket"])

        self.repo_url = QLineEdit()
        self.repo_url.setPlaceholderText("https://github.com/org/repo.git")

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)

        self.branch = QLineEdit("main")
        self.ingest_button = QPushButton("Ingestar")

        self.job_id = QLineEdit()
        self.job_id.setReadOnly(True)
        self.job_id.setPlaceholderText("Se asigna al iniciar ingesta")

        self.repo_id = QLineEdit()
        self.repo_id.setReadOnly(True)
        self.repo_id.setPlaceholderText("Disponible al completar")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs de ingesta...")

        form = QFormLayout()
        form.addRow("Provider", self.provider)
        form.addRow("Repo URL", self.repo_url)
        form.addRow("Token", self.token)
        form.addRow("Branch", self.branch)
        form.addRow("Job ID", self.job_id)
        form.addRow("Repo ID", self.repo_id)

        card = QFrame()
        card.setObjectName("ingestCard")
        card.setLayout(form)

        top_bar = QGridLayout()
        top_bar.addWidget(self.title_label, 0, 0)
        top_bar.addWidget(self.status_chip, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        top_bar.addWidget(self.subtitle_label, 1, 0, 1, 2)

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(card)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.ingest_button)
        layout.addWidget(self.logs)
        self.setLayout(layout)

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
            }
            QLabel {
                color: #E5E7EB;
            }
            IngestionView {
                background-color: #111827;
            }
            QFrame#ingestCard {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 10px;
                padding: 10px;
            }
            QLabel#statusChip {
                padding: 4px 10px;
                border-radius: 10px;
                font-weight: 600;
                color: #F3F4F6;
                background-color: #4B5563;
            }
            QLabel#statusChip[state="running"] {
                background-color: #1D4ED8;
            }
            QLabel#statusChip[state="success"] {
                background-color: #15803D;
            }
            QLabel#statusChip[state="error"] {
                background-color: #B91C1C;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px;
            }
            QProgressBar {
                border: 1px solid #374151;
                border-radius: 8px;
                text-align: center;
                color: #E5E7EB;
                background-color: #0F172A;
            }
            QProgressBar::chunk {
                background-color: #2563EB;
                border-radius: 6px;
            }
            QPushButton {
                background-color: #2563EB;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 9px;
                font-weight: 700;
            }
            QPushButton:disabled {
                background-color: #334155;
                color: #CBD5E1;
            }
            """
        )

    def set_status(self, state: str, text: str) -> None:
        """Update status chip state and text."""
        valid_states = {"idle", "running", "success", "error"}
        selected_state = state if state in valid_states else "idle"
        self.status_chip.setProperty("state", selected_state)
        self.status_chip.setText(text)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    def set_progress(self, value: int) -> None:
        """Set ingestion progress percentage."""
        self.progress_bar.setValue(max(0, min(100, value)))

    def set_job_id(self, value: str) -> None:
        """Display current ingestion job ID."""
        self.job_id.setText(value)

    def set_repo_id(self, value: str) -> None:
        """Display resulting repository ID."""
        self.repo_id.setText(value)

    def set_running(self, running: bool) -> None:
        """Enable or disable form controls based on ingestion execution."""
        self.provider.setDisabled(running)
        self.repo_url.setDisabled(running)
        self.token.setDisabled(running)
        self.branch.setDisabled(running)
        self.ingest_button.setDisabled(running)
        self.ingest_button.setText("Ingestando..." if running else "Ingestar")

    def set_logs(self, lines: list[str]) -> None:
        """Render all ingestion log lines in console panel."""
        self.logs.setPlainText("\n".join(lines))

    def append_log(self, text: str) -> None:
        """Append a single log line to the ingestion console."""
        if not text:
            return
        current = self.logs.toPlainText()
        self.logs.setPlainText(f"{current}\n{text}".strip())
