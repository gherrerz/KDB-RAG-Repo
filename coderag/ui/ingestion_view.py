"""Widgets de vista de ingesta para la configuración y ejecución del repositorio."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class IngestionView(QWidget):
    """Panel de interfaz de usuario que captura parámetros y registros de ingesta."""

    STATUS_PULSE_MS = 180
    BUTTON_FLASH_MS = 140

    def __init__(self) -> None:
        """Inicialice los controles de formulario para la ingesta del repositorio."""
        super().__init__()
        self.title_label = QLabel("Ingesta de Repositorio")
        self.title_label.setObjectName("ingestionTitle")
        self.subtitle_label = QLabel(
            "Conecta un repositorio y monitorea el pipeline de indexación en tiempo real."
        )
        self.subtitle_label.setObjectName("ingestionSubtitle")
        self.status_chip = QLabel("Idle")
        self.status_chip.setObjectName("statusChip")
        self.status_chip.setProperty("state", "idle")

        title_font = QFont("Segoe UI", 17, QFont.Weight.Bold)
        subtitle_font = QFont("Segoe UI", 11, QFont.Weight.Medium)
        self.title_label.setFont(title_font)
        self.subtitle_label.setFont(subtitle_font)

        self.provider = QComboBox()
        self.provider.addItems(["github", "bitbucket"])

        self.repo_url = QLineEdit()
        self.repo_url.setPlaceholderText("https://github.com/org/repo.git")

        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)

        self.branch = QLineEdit("main")
        self.ingest_button = QPushButton("Ingestar")
        self.reset_button = QPushButton("Limpiar Todo")
        self.reset_button.setObjectName("dangerButton")
        self.reset_button.setProperty("variant", "danger")
        self.ingest_button.setObjectName("ingestPrimaryButton")

        self.job_id = QLineEdit()
        self.job_id.setReadOnly(True)
        self.job_id.setPlaceholderText("Se asigna al iniciar ingesta")

        self.repo_id = QLineEdit()
        self.repo_id.setReadOnly(True)
        self.repo_id.setPlaceholderText("Disponible al completar")

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ingestionProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.logs = QTextEdit()
        self.logs.setObjectName("ingestionLogs")
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs de ingesta...")

        self.top_card = QFrame()
        self.top_card.setObjectName("ingestionTopCard")

        self.form_card = QFrame()
        self.form_card.setObjectName("ingestCard")

        self.logs_card = QFrame()
        self.logs_card.setObjectName("ingestionLogsCard")

        form = QFormLayout()
        form.setContentsMargins(14, 12, 14, 12)
        form.setVerticalSpacing(10)
        form.addRow("Provider", self.provider)
        form.addRow("Repo URL", self.repo_url)
        form.addRow("Token", self.token)
        form.addRow("Branch", self.branch)
        form.addRow("Job ID", self.job_id)
        form.addRow("Repo ID", self.repo_id)
        self.form_card.setLayout(form)

        top_bar = QGridLayout()
        top_bar.setContentsMargins(14, 12, 14, 12)
        top_bar.setVerticalSpacing(6)
        top_bar.addWidget(self.title_label, 0, 0)
        top_bar.addWidget(self.status_chip, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        top_bar.addWidget(self.subtitle_label, 1, 0, 1, 2)
        top_bar.setColumnStretch(0, 1)
        self.top_card.setLayout(top_bar)

        logs_layout = QVBoxLayout()
        logs_layout.setContentsMargins(12, 12, 12, 12)
        logs_layout.addWidget(self.logs)
        self.logs_card.setLayout(logs_layout)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(self.top_card)
        layout.addWidget(self.form_card)
        layout.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.ingest_button)
        actions.addWidget(self.reset_button)

        layout.addLayout(actions)
        layout.addWidget(self.logs_card)
        self.setLayout(layout)

        self.ingest_button.clicked.connect(lambda: self._flash_button(self.ingest_button))
        self.reset_button.clicked.connect(lambda: self._flash_button(self.reset_button))

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                color: #EAF1FF;
            }
            IngestionView {
                background-color: #0A1324;
            }
            QFrame#ingestionTopCard,
            QFrame#ingestCard {
                background-color: #111C32;
                border: 1px solid #2A3A5A;
                border-radius: 12px;
            }
            QFrame#ingestionTopCard {
                background-color: #15243E;
            }
            QFrame#ingestionLogsCard {
                background-color: #111C32;
                border: 1px solid #2A3A5A;
                border-radius: 12px;
            }
            QLabel#ingestionTitle {
                color: #EAF1FF;
                letter-spacing: 0.4px;
            }
            QLabel#ingestionSubtitle {
                color: #A8B7D6;
            }
            QLabel#statusChip {
                padding: 4px 10px;
                border-radius: 10px;
                font-weight: 600;
                color: #F8FBFF;
                background-color: #41577D;
            }
            QLabel#statusChip[pulse="true"] {
                border: 1px solid #8FB9FF;
                padding: 3px 9px;
            }
            QLabel#statusChip[state="running"] {
                background-color: #D98F2B;
            }
            QLabel#statusChip[state="success"] {
                background-color: #1FA971;
            }
            QLabel#statusChip[state="error"] {
                background-color: #C93A4B;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #0E1A2F;
                color: #EAF1FF;
                border: 1px solid #2A3A5A;
                border-radius: 8px;
                padding: 7px;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid #5EA0FF;
                background-color: #10213B;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #A8B7D6;
                margin-right: 8px;
            }
            QProgressBar {
                border: 1px solid #2A3A5A;
                border-radius: 8px;
                text-align: center;
                color: #EAF1FF;
                background-color: #0E1A2F;
            }
            QProgressBar::chunk {
                background-color: #2F7BFF;
                border-radius: 6px;
            }
            QPushButton {
                background-color: #2F7BFF;
                color: #F8FBFF;
                border: none;
                border-radius: 8px;
                padding: 9px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #4A91FF;
            }
            QPushButton[flash="true"] {
                background-color: #6AA7FF;
            }
            QPushButton:disabled {
                background-color: #344561;
                color: #90A2C3;
            }
            QPushButton#dangerButton,
            QPushButton[variant="danger"] {
                background-color: #B53343;
            }
            QPushButton#dangerButton:hover,
            QPushButton[variant="danger"]:hover {
                background-color: #C93A4B;
            }
            QPushButton#dangerButton[flash="true"],
            QPushButton[variant="danger"][flash="true"] {
                background-color: #D45563;
            }
            """
        )

    def set_status(self, state: str, text: str) -> None:
        """Actualiza el estado del chip y el texto."""
        valid_states = {"idle", "running", "success", "error"}
        selected_state = state if state in valid_states else "idle"
        self.status_chip.setProperty("state", selected_state)
        self.status_chip.setText(text)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
        self._pulse_status_chip()

    def _pulse_status_chip(self) -> None:
        """Aplique un pulso visual breve al chip cuando cambia estado."""
        self.status_chip.setProperty("pulse", "true")
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
        QTimer.singleShot(self.STATUS_PULSE_MS, self._clear_status_chip_pulse)

    def _clear_status_chip_pulse(self) -> None:
        """Restablezca estilo base del chip tras el pulso."""
        self.status_chip.setProperty("pulse", "false")
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    def _flash_button(self, button: QPushButton) -> None:
        """Aplique feedback corto al botón para reforzar interacción."""
        button.setProperty("flash", "true")
        button.style().unpolish(button)
        button.style().polish(button)
        QTimer.singleShot(self.BUTTON_FLASH_MS, lambda b=button: self._clear_button_flash(b))

    @staticmethod
    def _clear_button_flash(button: QPushButton) -> None:
        """Limpie estado temporal de animación de botón."""
        button.setProperty("flash", "false")
        button.style().unpolish(button)
        button.style().polish(button)

    def set_progress(self, value: int) -> None:
        """Set ingestion progress percentage."""
        self.progress_bar.setValue(max(0, min(100, value)))

    def set_job_id(self, value: str) -> None:
        """Muestra el ID del trabajo de ingesta actual."""
        self.job_id.setText(value)

    def set_repo_id(self, value: str) -> None:
        """Muestra el ID del repositorio resultante."""
        self.repo_id.setText(value)

    def set_running(self, running: bool) -> None:
        """Habilite o deshabilite los controles de formulario según la ejecución de la ingesta."""
        self.provider.setDisabled(running)
        self.repo_url.setDisabled(running)
        self.token.setDisabled(running)
        self.branch.setDisabled(running)
        self.ingest_button.setDisabled(running)
        self.reset_button.setDisabled(running)
        self.ingest_button.setText("Ingestando..." if running else "Ingestar")

    def set_reset_running(self, running: bool) -> None:
        """Actualice la interfaz de usuario mientras se ejecuta la operación de reinicio completo."""
        self.provider.setDisabled(running)
        self.repo_url.setDisabled(running)
        self.token.setDisabled(running)
        self.branch.setDisabled(running)
        self.ingest_button.setDisabled(running)
        self.reset_button.setDisabled(running)
        self.reset_button.setText("Limpiando..." if running else "Limpiar Todo")

    def set_logs(self, lines: list[str]) -> None:
        """Renderiza todas las líneas de log de ingesta en el panel de consola."""
        self.logs.setPlainText("\n".join(lines))

    def append_log(self, text: str) -> None:
        """Agregue una única línea de registro a la consola de ingesta."""
        if not text:
            return
        current = self.logs.toPlainText()
        self.logs.setPlainText(f"{current}\n{text}".strip())
