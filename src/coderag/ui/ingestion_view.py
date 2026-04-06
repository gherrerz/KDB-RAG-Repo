"""Widgets de vista de ingesta para la configuración y ejecución del repositorio."""

from PySide6.QtCore import QSettings, Qt, QTimer
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
    QScrollArea,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.coderag.core.settings import get_settings
from src.coderag.ui.base_styles import (
    BASE_BUTTON_STYLES,
    BASE_INPUT_STYLES_WITH_TEXTEDIT,
    BASE_WIDGET_TEXT_STYLES,
)
from src.coderag.ui.card_styles import (
    frame_card_styles,
    status_chip_styles,
    title_subtitle_styles,
    top_card_styles,
)
from src.coderag.ui.provider_feedback import (
    apply_status_chip,
)
from src.coderag.ui.model_catalog_client import fetch_models_for_provider
from src.coderag.ui.model_catalog_client import should_show_remote_catalog_fallback_hint
from src.coderag.ui.provider_styles import PROVIDER_FEEDBACK_STYLES
from src.coderag.ui.provider_ui_state import resolve_embedding_ui_state


class IngestionView(QWidget):
    """Panel de interfaz de usuario que captura parámetros y registros de ingesta."""

    STATUS_PULSE_MS = 180
    BUTTON_FLASH_MS = 140
    _SETTINGS_KEY_EXPANDED = "ingestion/layout/config_expanded"
    _SETTINGS_KEY_SPLITTER = "ingestion/layout/splitter_sizes"

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

        # Mantiene la misma altura visual que Consulta para evitar clipping y
        # asegurar consistencia entre pestañas.
        for control in (
            self.provider,
            self.embedding_provider,
            self.embedding_model,
            self.repo_url,
            self.token,
            self.branch,
            self.job_id,
            self.repo_id,
        ):
            control.setMinimumHeight(30)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ingestionProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.logs = QTextEdit()
        self.logs.setObjectName("ingestionLogs")
        self.logs.setReadOnly(True)
        self.logs.setMinimumHeight(280)
        self.logs.setPlaceholderText("Logs de ingesta...")

        self.top_card = QFrame()
        self.top_card.setObjectName("ingestionTopCard")

        self.form_card = QFrame()
        self.form_card.setObjectName("ingestCard")

        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("ingestionFormScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.logs_card = QFrame()
        self.logs_card.setObjectName("ingestionLogsCard")

        self.form_toggle_button = QToolButton()
        self.form_toggle_button.setObjectName("ingestionFormToggle")
        self.form_toggle_button.setText("Configuracion de ingesta")
        self.form_toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.form_toggle_button.setArrowType(Qt.ArrowType.DownArrow)
        self.form_toggle_button.setCheckable(True)
        self.form_toggle_button.setChecked(True)

        self.form_section = QFrame()
        self.form_section.setObjectName("ingestionFormSection")

        self.ingestion_splitter = QSplitter(Qt.Orientation.Vertical)
        self.ingestion_splitter.setObjectName("ingestionMainSplitter")
        self.ingestion_splitter.setChildrenCollapsible(False)
        self.ingestion_splitter.setHandleWidth(8)
        self._ingestion_splitter_initialized = False
        self._saved_ingestion_splitter_sizes: list[int] | None = None
        self._layout_settings = QSettings("CodeRAG", "DesktopUI")

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
        self.form_card.setMinimumHeight(self.form_card.sizeHint().height())
        self.form_scroll.setWidget(self.form_card)

        form_section_layout = QVBoxLayout()
        form_section_layout.setContentsMargins(0, 0, 0, 0)
        form_section_layout.setSpacing(8)
        form_section_layout.addWidget(self.form_toggle_button)
        form_section_layout.addWidget(self.form_scroll)
        self.form_section.setLayout(form_section_layout)
        self.form_section.setMinimumHeight(170)

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
        self.logs_card.setMinimumHeight(300)

        self.ingestion_splitter.addWidget(self.form_section)
        self.ingestion_splitter.addWidget(self.logs_card)
        self.ingestion_splitter.setStretchFactor(0, 0)
        self.ingestion_splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(self.top_card)
        layout.addWidget(self.progress_bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.ingest_button)
        actions.addWidget(self.reset_button)

        layout.addLayout(actions)
        layout.addWidget(self.ingest_action_hint)
        layout.addWidget(self.ingestion_splitter, 1)
        self.setLayout(layout)

        self.form_toggle_button.toggled.connect(self._set_form_section_expanded)
        self.ingestion_splitter.splitterMoved.connect(self._on_splitter_moved)

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
            QToolButton#ingestionFormToggle {
                background-color: #162A47;
                border: 1px solid #2A3A5A;
                border-radius: 10px;
                padding: 7px 10px;
                color: #D7E6FF;
                font-weight: 600;
                text-align: left;
            }
            QToolButton#ingestionFormToggle:hover {
                background-color: #1C3252;
            }
            QSplitter#ingestionMainSplitter::handle {
                background-color: #14233C;
                border-radius: 4px;
                margin: 2px 16px;
            }
            QSplitter#ingestionMainSplitter::handle:hover {
                background-color: #2A4E7D;
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
        self._load_layout_preferences()
        self._on_embedding_provider_changed(self.embedding_provider.currentText())

    def showEvent(self, event) -> None:
        """Aplica proporción inicial del splitter tras primer render del panel."""
        super().showEvent(event)
        if self._ingestion_splitter_initialized:
            return
        self._ingestion_splitter_initialized = True
        QTimer.singleShot(0, self._restore_or_apply_ingestion_splitter_sizes)

    def _set_form_section_expanded(self, expanded: bool) -> None:
        """Colapsa o expande formulario para priorizar logs de ejecución."""
        self.form_scroll.setVisible(expanded)
        self.form_toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        if expanded:
            self.form_section.setMinimumHeight(170)
            self.form_section.setMaximumHeight(16777215)
        else:
            collapsed_height = self.form_toggle_button.sizeHint().height() + 8
            self.form_section.setMinimumHeight(collapsed_height)
            self.form_section.setMaximumHeight(collapsed_height)
        self._persist_layout_preferences()
        QTimer.singleShot(0, self._apply_ingestion_splitter_sizes)

    def _apply_ingestion_splitter_sizes(self) -> None:
        """Define proporción 30/70 entre configuración y logs."""
        total = max(360, self.ingestion_splitter.height())
        if not self.form_toggle_button.isChecked():
            collapsed = self.form_toggle_button.sizeHint().height() + 10
            self.ingestion_splitter.setSizes([collapsed, max(240, total - collapsed)])
            return
        config_size = max(170, int(total * 0.30))
        log_size = max(240, total - config_size)
        self.ingestion_splitter.setSizes([config_size, log_size])

    def _restore_or_apply_ingestion_splitter_sizes(self) -> None:
        """Restaura tamaños previos del splitter o aplica proporción por defecto."""
        if self._saved_ingestion_splitter_sizes and len(self._saved_ingestion_splitter_sizes) == 2:
            self.ingestion_splitter.setSizes(self._saved_ingestion_splitter_sizes)
            return
        self._apply_ingestion_splitter_sizes()

    def _load_layout_preferences(self) -> None:
        """Carga preferencias persistidas de layout para la vista de ingesta."""
        expanded = self._layout_settings.value(
            self._SETTINGS_KEY_EXPANDED,
            True,
            type=bool,
        )
        saved_sizes_raw = self._layout_settings.value(
            self._SETTINGS_KEY_SPLITTER,
            "",
            type=str,
        )
        self._saved_ingestion_splitter_sizes = self._parse_splitter_sizes(saved_sizes_raw)

        self.form_toggle_button.blockSignals(True)
        self.form_toggle_button.setChecked(bool(expanded))
        self.form_toggle_button.blockSignals(False)
        self._set_form_section_expanded(bool(expanded))

    def _persist_layout_preferences(self) -> None:
        """Guarda estado colapsado y tamaños actuales del splitter."""
        self._layout_settings.setValue(
            self._SETTINGS_KEY_EXPANDED,
            self.form_toggle_button.isChecked(),
        )
        sizes = self.ingestion_splitter.sizes()
        if len(sizes) == 2 and all(size > 0 for size in sizes):
            self._layout_settings.setValue(
                self._SETTINGS_KEY_SPLITTER,
                self._serialize_splitter_sizes(sizes),
            )

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        """Persiste cambios de tamaño cuando el usuario mueve el splitter."""
        self._persist_layout_preferences()

    @staticmethod
    def _serialize_splitter_sizes(sizes: list[int]) -> str:
        """Serializa tamaños del splitter en formato compacto persistible."""
        return ",".join(str(int(size)) for size in sizes)

    @staticmethod
    def _parse_splitter_sizes(value: str) -> list[int] | None:
        """Parsea tamaños de splitter desde preferencias persistidas."""
        if not value.strip():
            return None
        parts = [item.strip() for item in value.split(",")]
        if len(parts) != 2:
            return None
        try:
            sizes = [int(parts[0]), int(parts[1])]
        except ValueError:
            return None
        if any(size <= 0 for size in sizes):
            return None
        return sizes

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
