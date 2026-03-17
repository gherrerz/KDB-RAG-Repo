"""Widgets de vista de ingesta para la configuración y ejecución del repositorio."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
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

from coderag.core.settings import get_settings
from coderag.ui.base_styles import (
    BASE_BUTTON_STYLES,
    BASE_INPUT_STYLES_WITH_TEXTEDIT,
    BASE_WIDGET_TEXT_STYLES,
)
from coderag.ui.card_styles import (
    frame_card_styles,
    status_chip_styles,
    title_subtitle_styles,
    top_card_styles,
)
from coderag.ui.provider_feedback import (
    apply_status_chip,
)
from coderag.ui.model_catalog_client import fetch_models_for_provider
from coderag.ui.model_catalog_client import should_show_remote_catalog_fallback_hint
from coderag.ui.provider_styles import PROVIDER_FEEDBACK_STYLES
from coderag.ui.provider_ui_state import resolve_embedding_ui_state


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

        self.embedding_provider = QComboBox()
        self.embedding_provider.addItems(
            ["openai", "anthropic", "gemini", "vertex_ai"]
        )

        self.embedding_model = QComboBox()
        self.embedding_model.setEditable(True)
        self.embedding_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.embedding_model.setMaxVisibleItems(15)
        if self.embedding_model.lineEdit() is not None:
            self.embedding_model.lineEdit().setPlaceholderText("Modelo embeddings")
        self.refresh_embedding_models_button = QPushButton("Refrescar modelos")
        self.refresh_embedding_models_button.setProperty("variant", "secondary")
        self.embedding_warning = QLabel("")
        self.embedding_warning.setObjectName("providerWarning")
        self.embedding_warning.setWordWrap(True)
        self.embedding_status_chip = QLabel("Embeddings: Listo")
        self.embedding_status_chip.setObjectName("providerStatusChip")
        self.embedding_status_chip.setProperty("state", "ready")
        self.force_fallback = QCheckBox("Forzar fallback si provider no esta listo")
        self.force_fallback.setObjectName("forceFallbackCheck")
        self.ingest_action_hint = QLabel("")
        self.ingest_action_hint.setObjectName("actionHint")
        self.ingest_action_hint.setWordWrap(True)

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
        form.addRow("Embedding Provider", self.embedding_provider)
        form.addRow("Embedding Model", self.embedding_model)
        form.addRow("", self.refresh_embedding_models_button)
        form.addRow("", self.embedding_warning)
        form.addRow("", self.embedding_status_chip)
        form.addRow("", self.force_fallback)
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
        layout.addWidget(self.ingest_action_hint)
        layout.addWidget(self.logs_card)
        self.setLayout(layout)

        self.ingest_button.clicked.connect(lambda: self._flash_button(self.ingest_button))
        self.reset_button.clicked.connect(lambda: self._flash_button(self.reset_button))
        self.refresh_embedding_models_button.clicked.connect(
            lambda: self._refresh_embedding_models(force_refresh=True)
        )
        self.refresh_embedding_models_button.clicked.connect(
            lambda: self._flash_button(self.refresh_embedding_models_button)
        )

        self.setStyleSheet(
            """
            """
            + BASE_WIDGET_TEXT_STYLES
            + """
            IngestionView {
                background-color: #0A1324;
            }
            """
            + frame_card_styles("ingestionTopCard", "ingestCard", "ingestionLogsCard")
            + top_card_styles("ingestionTopCard")
            + title_subtitle_styles("ingestionTitle", "ingestionSubtitle")
            + status_chip_styles("statusChip")
            + PROVIDER_FEEDBACK_STYLES
            + """
            QLabel#providerWarning {
                padding: 2px 0 4px 0;
            }
            """
            + BASE_INPUT_STYLES_WITH_TEXTEDIT
            + """
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
            """
            + BASE_BUTTON_STYLES
            + """
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

        self.embedding_provider.currentTextChanged.connect(
            self._on_embedding_provider_changed
        )
        self._on_embedding_provider_changed(self.embedding_provider.currentText())

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
        self.embedding_provider.setDisabled(running)
        self.embedding_model.setDisabled(running)
        self.refresh_embedding_models_button.setDisabled(running)
        self.repo_url.setDisabled(running)
        self.token.setDisabled(running)
        self.branch.setDisabled(running)
        self.ingest_button.setDisabled(running)
        self.reset_button.setDisabled(running)
        self.ingest_button.setText("Ingestando..." if running else "Ingestar")

    def set_reset_running(self, running: bool) -> None:
        """Actualice la interfaz de usuario mientras se ejecuta la operación de reinicio completo."""
        self.provider.setDisabled(running)
        self.embedding_provider.setDisabled(running)
        self.embedding_model.setDisabled(running)
        self.refresh_embedding_models_button.setDisabled(running)
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

    def _on_embedding_provider_changed(self, provider: str) -> None:
        """Autocompleta modelo de embeddings y muestra estado de capabilities."""
        self._refresh_embedding_models(force_refresh=False, provider=provider)

    def _refresh_embedding_models(
        self,
        *,
        force_refresh: bool,
        provider: str | None = None,
    ) -> None:
        """Recarga catálogo de embeddings desde API y aplica fallback local."""
        selected_provider = (provider or self.embedding_provider.currentText()).strip()
        settings = get_settings()
        state = resolve_embedding_ui_state(
            settings,
            selected_provider,
            context="ingestion",
        )
        current_model = self.embedding_model.currentText().strip()
        if provider is not None:
            preferred_model = state.default_model
        else:
            preferred_model = current_model or state.default_model
        catalog = fetch_models_for_provider(
            selected_provider,
            "embedding",
            force_refresh=force_refresh,
        )
        self._set_combo_items(
            self.embedding_model,
            catalog.models,
            preferred_model,
        )

        warning_parts: list[str] = []
        if state.warning:
            warning_parts.append(state.warning)
        if (
            catalog.source == "fallback"
            and should_show_remote_catalog_fallback_hint(catalog.warning)
        ):
            warning_parts.append(
                "No se pudo actualizar el catálogo remoto; usando lista local."
            )
        self.embedding_warning.setText(" ".join(warning_parts).strip())
        apply_status_chip(self.embedding_status_chip, state.chip_state, state.chip_text)

    @staticmethod
    def _set_combo_items(combo: QComboBox, options: list[str], selected: str) -> None:
        """Recarga opciones del combo preservando una selección válida."""
        chosen = selected.strip() if selected else ""
        values = [item.strip() for item in options if item.strip()]
        if chosen and chosen not in values:
            values.append(chosen)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        if chosen:
            combo.setCurrentText(chosen)
        elif values:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def is_force_fallback_enabled(self) -> bool:
        """Indica si el usuario decidió forzar fallback para ingesta."""
        return self.force_fallback.isChecked()

    def is_embedding_provider_ready(self) -> tuple[bool, str]:
        """Evalúa si el provider de embeddings está listo para ingesta."""
        settings = get_settings()
        state = resolve_embedding_ui_state(
            settings,
            self.embedding_provider.currentText(),
            context="ingestion",
        )
        return state.ready, state.reason

    def set_ingest_action_hint(self, text: str) -> None:
        """Actualiza el mensaje inline asociado al botón de ingesta."""
        self.ingest_action_hint.setText(text.strip())

    def get_embedding_model(self) -> str:
        """Devuelve el modelo de embeddings actual para payload de ingesta."""
        return self.embedding_model.currentText().strip()
