"""Ingestion view widgets for repository setup and execution."""

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
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
        self.provider = QComboBox()
        self.provider.addItems(["github", "bitbucket"])

        self.repo_url = QLineEdit()
        self.repo_url.setPlaceholderText("https://github.com/org/repo.git")

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)

        self.branch = QLineEdit("main")
        self.ingest_button = QPushButton("Ingestar")
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)

        form = QFormLayout()
        form.addRow("Provider", self.provider)
        form.addRow("Repo URL", self.repo_url)
        form.addRow("Token", self.token)
        form.addRow("Branch", self.branch)

        group = QGroupBox("Ingesta")
        group.setLayout(form)

        layout = QVBoxLayout()
        layout.addWidget(group)
        layout.addWidget(self.ingest_button)
        layout.addWidget(self.logs)
        self.setLayout(layout)
