"""Widgets de vista de consultas para hacer preguntas sobre el repositorio."""

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coderag.core.settings import get_settings
from coderag.ui.base_styles import (
    BASE_BUTTON_STYLES,
    BASE_INPUT_STYLES,
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
from coderag.ui.provider_styles import PROVIDER_FEEDBACK_STYLES
from coderag.ui.provider_ui_state import (
    resolve_embedding_ui_state,
    resolve_llm_ui_state,
)


class QueryView(QWidget):
    """Panel de interfaz que gestiona consultas en lenguaje natural."""

    STATUS_PULSE_MS = 180
    BUTTON_FLASH_MS = 140

    def __init__(self) -> None:
        """Inicialice el formulario de consulta y responda los widgets de salida."""
        super().__init__()

        self.title_label = QLabel("Consulta")
        self.subtitle_label = QLabel(
            "Haz preguntas sobre el repositorio indexado y revisa la respuesta sintetizada."
        )
        self.title_label.setObjectName("queryTitle")
        self.subtitle_label.setObjectName("querySubtitle")

        self.status_chip = QLabel("Lista")
        self.status_chip.setObjectName("queryStatusChip")
        self.status_chip.setProperty("state", "idle")

        self.copy_history_button = QPushButton("Copiar Historial")
        self.refresh_repo_ids_button = QPushButton("Actualizar IDs")
        self.refresh_models_button = QPushButton("Refrescar Modelos")
        self.copy_history_button.setProperty("variant", "secondary")
        self.refresh_repo_ids_button.setProperty("variant", "secondary")
        self.refresh_models_button.setProperty("variant", "secondary")

        self.repo_id = QComboBox()
        self.repo_id.setEditable(False)
        self.repo_id.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.repo_id.setMaxVisibleItems(20)
        self._repo_ids: list[str] = []

        self.embedding_provider = QComboBox()
        self.embedding_provider.addItems(
            ["openai", "anthropic", "gemini", "vertex_ai"]
        )
        self.embedding_model = QComboBox()
        self.embedding_model.setEditable(True)
        self.embedding_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.embedding_model.setMaxVisibleItems(15)
        self.embedding_warning = QLabel("")
        self.embedding_warning.setObjectName("providerWarning")
        self.embedding_warning.setWordWrap(True)
        self.embedding_status_chip = QLabel("Embeddings: Listo")
        self.embedding_status_chip.setObjectName("providerStatusChip")
        self.embedding_status_chip.setProperty("state", "ready")

        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["openai", "anthropic", "gemini", "vertex_ai"])
        self.answer_model = QComboBox()
        self.answer_model.setEditable(True)
        self.answer_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.answer_model.setMaxVisibleItems(15)
        self.verifier_model = QComboBox()
        self.verifier_model.setEditable(True)
        self.verifier_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.verifier_model.setMaxVisibleItems(15)
        self.query_profile = QComboBox()
        self.query_profile.addItems(["rapido", "balanceado", "profundo"])
        self.query_profile.setCurrentText("balanceado")
        self.llm_warning = QLabel("")
        self.llm_warning.setObjectName("providerWarning")
        self.llm_warning.setWordWrap(True)
        self.llm_status_chip = QLabel("LLM: Listo")
        self.llm_status_chip.setObjectName("providerStatusChip")
        self.llm_status_chip.setProperty("state", "ready")
        self.force_fallback = QCheckBox("Forzar fallback si provider no esta listo")
        self.force_fallback.setObjectName("forceFallbackCheck")

        self.query_input = QLineEdit()
        self.query_input.setObjectName("queryInput")
        self.query_input.setPlaceholderText("Consulta la base de conocimientos...")
        self.query_input.returnPressed.connect(self._trigger_submit)

        self.query_button = QPushButton("↑")
        self.query_button.setObjectName("querySubmitButton")
        self.query_button.setFixedWidth(44)
        self.query_action_hint = QLabel("")
        self.query_action_hint.setObjectName("actionHint")
        self.query_action_hint.setWordWrap(True)

        self.history_output = QPlainTextEdit()
        self.history_output.setObjectName("queryHistory")
        self.history_output.setReadOnly(True)
        self.history_output.setPlaceholderText(
            "El historial de preguntas y respuestas aparecerá aquí..."
        )

        self.input_bar = QFrame()
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setProperty("state", "idle")

        self.top_card = QFrame()
        self.top_card.setObjectName("queryTopCard")

        self.repo_card = QFrame()
        self.repo_card.setObjectName("queryRepoCard")

        self.history_card = QFrame()
        self.history_card.setObjectName("queryHistoryCard")

        title_font = QFont("Segoe UI", 17, QFont.Weight.Bold)
        subtitle_font = QFont("Segoe UI", 11, QFont.Weight.Medium)
        self.title_label.setFont(title_font)
        self.subtitle_label.setFont(subtitle_font)

        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)
        input_layout.addWidget(self.query_input)
        input_layout.addWidget(self.query_button)
        self.input_bar.setLayout(input_layout)

        repo_bar = QGridLayout()
        repo_bar.setContentsMargins(14, 12, 14, 12)
        repo_bar.setHorizontalSpacing(10)
        repo_bar.setVerticalSpacing(8)
        repo_label = QLabel("ID de repositorio")
        repo_label.setObjectName("queryRepoLabel")
        repo_bar.addWidget(repo_label, 0, 0)
        repo_bar.addWidget(self.repo_id, 0, 1)
        repo_bar.addWidget(self.refresh_repo_ids_button, 0, 2)
        repo_bar.addWidget(self.refresh_models_button, 0, 3)
        repo_bar.addWidget(QLabel("Embedding Provider"), 1, 0)
        repo_bar.addWidget(self.embedding_provider, 1, 1)
        repo_bar.addWidget(QLabel("Embedding Model"), 1, 2)
        repo_bar.addWidget(self.embedding_model, 1, 3)
        repo_bar.addWidget(QLabel("LLM Provider"), 2, 0)
        repo_bar.addWidget(self.llm_provider, 2, 1)
        repo_bar.addWidget(QLabel("Answer Model"), 2, 2)
        repo_bar.addWidget(self.answer_model, 2, 3)
        repo_bar.addWidget(QLabel("Verifier Model"), 3, 0)
        repo_bar.addWidget(self.verifier_model, 3, 1)
        repo_bar.addWidget(QLabel("Perfil"), 3, 2)
        repo_bar.addWidget(self.query_profile, 3, 3)
        repo_bar.addWidget(self.embedding_warning, 4, 0, 1, 4)
        repo_bar.addWidget(self.llm_warning, 5, 0, 1, 4)
        repo_bar.addWidget(self.embedding_status_chip, 6, 0, 1, 2)
        repo_bar.addWidget(self.llm_status_chip, 6, 2, 1, 2)
        repo_bar.addWidget(self.force_fallback, 7, 0, 1, 4)
        self.repo_card.setLayout(repo_bar)

        top_bar = QGridLayout()
        top_bar.setContentsMargins(14, 12, 14, 12)
        top_bar.setHorizontalSpacing(10)
        top_bar.setVerticalSpacing(6)
        top_bar.setColumnStretch(0, 1)
        top_bar.addWidget(self.title_label, 0, 0)
        top_bar.addWidget(self.status_chip, 0, 1)
        top_bar.addWidget(self.copy_history_button, 0, 2)
        top_bar.addWidget(self.subtitle_label, 1, 0, 1, 3)
        self.top_card.setLayout(top_bar)

        history_layout = QVBoxLayout()
        history_layout.setContentsMargins(12, 12, 12, 12)
        history_layout.addWidget(self.history_output)
        self.history_card.setLayout(history_layout)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(self.top_card)
        layout.addWidget(self.repo_card)
        layout.addWidget(self.history_card)
        layout.addWidget(self.input_bar)
        layout.addWidget(self.query_action_hint)
        self.setLayout(layout)

        self.copy_history_button.clicked.connect(self.copy_all_history)
        self.copy_history_button.clicked.connect(
            lambda: self._flash_button(self.copy_history_button)
        )
        self.refresh_repo_ids_button.clicked.connect(
            lambda: self._flash_button(self.refresh_repo_ids_button)
        )
        self.refresh_models_button.clicked.connect(
            lambda: self.refresh_model_catalogs(force_refresh=True)
        )
        self.refresh_models_button.clicked.connect(
            lambda: self._flash_button(self.refresh_models_button)
        )
        self.query_button.clicked.connect(lambda: self._flash_button(self.query_button))

        self.append_assistant_message(
            "Listo para auditar. Haz una pregunta para comenzar."
        )

        self.setStyleSheet(
            """
            """
            + BASE_WIDGET_TEXT_STYLES
            + """
            QueryView {
                background-color: #0A1324;
            }
            """
            + frame_card_styles("queryTopCard", "queryRepoCard", "queryHistoryCard", "inputBar")
            + top_card_styles("queryTopCard")
            + title_subtitle_styles("queryTitle", "querySubtitle")
            + """
            QLabel#queryRepoLabel {
                color: #A8B7D6;
                font-weight: 600;
            }
            """
            + PROVIDER_FEEDBACK_STYLES
            + """
            """
            + status_chip_styles("queryStatusChip", center=True)
            + """
            """
            + BASE_INPUT_STYLES
            + """
            QPlainTextEdit {
                background-color: #0E1A2F;
                color: #EAF1FF;
                border: 1px solid #2A3A5A;
                border-radius: 10px;
                padding: 10px;
                selection-background-color: #2F7BFF;
            }
            QFrame#inputBar {
                background-color: #15243E;
                border-radius: 12px;
            }
            QFrame#inputBar[state="running"] {
                border: 1px solid #D98F2B;
                background-color: #212B3D;
            }
            QFrame#inputBar[state="error"] {
                border: 1px solid #C93A4B;
            }
            """
            + BASE_BUTTON_STYLES
            + """
            QPushButton[variant="secondary"] {
                background-color: #1D2A45;
                border: 1px solid #2C436A;
                color: #D7E6FF;
                font-weight: 600;
            }
            QPushButton[variant="secondary"]:hover {
                background-color: #223556;
            }
            QPushButton[variant="secondary"][flash="true"] {
                background-color: #2C436A;
            }
            """
        )

        self.embedding_provider.currentTextChanged.connect(
            self._on_embedding_provider_changed
        )
        self.llm_provider.currentTextChanged.connect(self._on_llm_provider_changed)
        self.refresh_model_catalogs(force_refresh=False)

    def set_status(self, state: str, text: str) -> None:
        """Actualice el estado y el texto del chip de estado de la consulta."""
        valid_states = {"idle", "running", "success", "error"}
        selected_state = state if state in valid_states else "idle"
        self.status_chip.setProperty("state", selected_state)
        self.status_chip.setText(text)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
        self._pulse_status_chip()
        if selected_state == "error":
            self._set_input_bar_state("error")
        elif selected_state == "running":
            self._set_input_bar_state("running")
        else:
            self._set_input_bar_state("idle")

    def _pulse_status_chip(self) -> None:
        """Aplique un pulso visual breve al chip para enfatizar cambio de estado."""
        self.status_chip.setProperty("pulse", "true")
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
        QTimer.singleShot(self.STATUS_PULSE_MS, self._clear_status_chip_pulse)

    def _clear_status_chip_pulse(self) -> None:
        """Restablezca el estilo normal del chip tras el pulso."""
        self.status_chip.setProperty("pulse", "false")
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)

    def _flash_button(self, button: QPushButton) -> None:
        """Aplique un feedback táctil corto en botones de acción."""
        button.setProperty("flash", "true")
        button.style().unpolish(button)
        button.style().polish(button)
        QTimer.singleShot(self.BUTTON_FLASH_MS, lambda b=button: self._clear_button_flash(b))

    @staticmethod
    def _clear_button_flash(button: QPushButton) -> None:
        """Limpie el estado visual temporal de feedback del botón."""
        button.setProperty("flash", "false")
        button.style().unpolish(button)
        button.style().polish(button)

    def set_running(self, running: bool) -> None:
        """Habilite y deshabilite los controles mientras la solicitud de consulta está en curso."""
        self.repo_id.setDisabled(running)
        self.embedding_provider.setDisabled(running)
        self.embedding_model.setDisabled(running)
        self.llm_provider.setDisabled(running)
        self.answer_model.setDisabled(running)
        self.verifier_model.setDisabled(running)
        self.query_profile.setDisabled(running)
        self.refresh_repo_ids_button.setDisabled(running)
        self.refresh_models_button.setDisabled(running)
        self.query_input.setDisabled(running)
        self.query_button.setDisabled(running)
        self.query_button.setText("…" if running else "↑")
        self._set_input_bar_state("running" if running else "idle")

    def get_repo_id_text(self) -> str:
        """Devuelve la identificación del repositorio actual ingresada o seleccionada por el usuario."""
        return self.repo_id.currentText().strip()

    def clear_repo_id(self) -> None:
        """Borrar texto editable combinado de identificación del repositorio."""
        self.repo_id.setCurrentIndex(-1)

    def set_repo_ids(self, repo_ids: list[str]) -> None:
        """Cargue los identificadores de repositorio disponibles en el menú desplegable, preservando el valor actual."""
        current = self.repo_id.currentText().strip()
        self._repo_ids = [item for item in repo_ids if item.strip()]
        self.repo_id.blockSignals(True)
        self.repo_id.clear()
        if self._repo_ids:
            self.repo_id.addItems(self._repo_ids)
            if current in self._repo_ids:
                self.repo_id.setCurrentText(current)
            else:
                self.repo_id.setCurrentIndex(0)
        else:
            self.repo_id.setCurrentIndex(-1)
        self.repo_id.blockSignals(False)

    def has_repo_id(self, repo_id: str) -> bool:
        """Devuelve si existe una identificación de repositorio en el catálogo cargado."""
        return repo_id in self._repo_ids

    def _set_input_bar_state(self, state: str) -> None:
        """Aplicar estado visual a la barra de entrada de consulta."""
        self.input_bar.setProperty("state", state)
        self.input_bar.style().unpolish(self.input_bar)
        self.input_bar.style().polish(self.input_bar)

    def get_question_text(self) -> str:
        """Devuelve el texto de entrada de pregunta recortado."""
        return self.query_input.text().strip()

    def get_embedding_provider(self) -> str:
        """Devuelve el provider de embeddings seleccionado en consulta."""
        return self.embedding_provider.currentText().strip()

    def get_embedding_model(self) -> str:
        """Devuelve el modelo de embeddings seleccionado en consulta."""
        return self.embedding_model.currentText().strip()

    def get_llm_provider(self) -> str:
        """Devuelve el provider LLM seleccionado en consulta."""
        return self.llm_provider.currentText().strip()

    def get_answer_model(self) -> str:
        """Devuelve el modelo answer seleccionado en consulta."""
        return self.answer_model.currentText().strip()

    def get_verifier_model(self) -> str:
        """Devuelve el modelo verifier seleccionado en consulta."""
        return self.verifier_model.currentText().strip()

    def get_query_profile(self) -> str:
        """Devuelve el perfil de consulta seleccionado por el usuario."""
        return self.query_profile.currentText().strip().lower()

    def clear_question(self) -> None:
        """Borre la entrada de la consulta después de un envío exitoso."""
        self.query_input.clear()

    def clear_history(self) -> None:
        """Borre el historial de conversación para iniciar una nueva sesión."""
        self.history_output.clear()

    def append_user_message(self, text: str) -> None:
        """Agregue la pregunta del usuario al historial de chat."""
        self._append_message(text=text, role="user", error=False)

    def append_assistant_message(self, text: str, error: bool = False) -> None:
        """Agregue la respuesta o el error del asistente al historial de chat."""
        self._append_message(text=text, role="assistant", error=error)

    def _append_message(self, text: str, role: str, error: bool) -> None:
        """Agregue una entrada de transcripción de chat seleccionable y de ancho completo."""
        icon = "👤" if role == "user" else "🤖"
        title = "Pregunta" if role == "user" else "Respuesta"
        if error:
            title = "Error"

        entry = f"{icon} {title}\n{text}\n"
        if self.history_output.toPlainText().strip():
            self.history_output.appendPlainText("")
        self.history_output.appendPlainText(entry)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        """Desplácese por la vista de chat hasta el último mensaje."""
        scrollbar = self.history_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def copy_all_history(self) -> None:
        """Copie todo el historial de conversaciones al portapapeles."""
        QApplication.clipboard().setText(self.history_output.toPlainText())

    def _trigger_submit(self) -> None:
        """Haga clic en el botón de consulta desde el teclado. Tecla Intro."""
        if self.query_button.isEnabled():
            self.query_button.click()

    def _on_embedding_provider_changed(self, provider: str) -> None:
        """Autocompleta modelo de embeddings y emite warning de capabilities."""
        self._refresh_embedding_model_catalog(force_refresh=False, provider=provider)

    def _refresh_embedding_model_catalog(
        self,
        *,
        force_refresh: bool,
        provider: str | None = None,
    ) -> None:
        """Recarga catálogo de modelos de embeddings para consulta."""
        selected_provider = (provider or self.embedding_provider.currentText()).strip()
        settings = get_settings()
        state = resolve_embedding_ui_state(
            settings,
            selected_provider,
            context="query",
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
        self._set_model_options(
            self.embedding_model,
            catalog.models,
            preferred_model,
        )

        warning_parts: list[str] = []
        if state.warning:
            warning_parts.append(state.warning)
        if (
            catalog.source == "fallback"
            and catalog.warning
            and catalog.warning != "catalog_service_unavailable"
        ):
            warning_parts.append(
                "No se pudo actualizar el catálogo remoto; usando lista local."
            )
        self.embedding_warning.setText(" ".join(warning_parts).strip())
        apply_status_chip(self.embedding_status_chip, state.chip_state, state.chip_text)

    def _on_llm_provider_changed(self, provider: str) -> None:
        """Autocompleta modelos LLM y emite warning de capabilities."""
        self._refresh_llm_model_catalog(force_refresh=False, provider=provider)

    def _refresh_llm_model_catalog(
        self,
        *,
        force_refresh: bool,
        provider: str | None = None,
    ) -> None:
        """Recarga catálogo de modelos LLM para answer/verifier."""
        selected_provider = (provider or self.llm_provider.currentText()).strip()
        settings = get_settings()
        state = resolve_llm_ui_state(settings, selected_provider)
        current_answer_model = self.answer_model.currentText().strip()
        current_verifier_model = self.verifier_model.currentText().strip()
        catalog = fetch_models_for_provider(
            selected_provider,
            "llm",
            force_refresh=force_refresh,
        )
        if provider is not None:
            answer_preferred = state.default_model
            verifier_preferred = state.default_model
        else:
            answer_preferred = current_answer_model or state.default_model
            verifier_preferred = current_verifier_model or state.default_model
        self._set_model_options(self.answer_model, catalog.models, answer_preferred)
        self._set_model_options(self.verifier_model, catalog.models, verifier_preferred)

        warning_parts: list[str] = []
        if state.warning:
            warning_parts.append(state.warning)
        if (
            catalog.source == "fallback"
            and catalog.warning
            and catalog.warning != "catalog_service_unavailable"
        ):
            warning_parts.append(
                "No se pudo actualizar el catálogo remoto; usando lista local."
            )
        self.llm_warning.setText(" ".join(warning_parts).strip())
        apply_status_chip(self.llm_status_chip, state.chip_state, state.chip_text)

    def refresh_model_catalogs(self, *, force_refresh: bool) -> None:
        """Refresca catálogos de modelos de embeddings y LLM en consulta."""
        self._refresh_embedding_model_catalog(force_refresh=force_refresh)
        self._refresh_llm_model_catalog(force_refresh=force_refresh)

    @staticmethod
    def _set_model_options(combo: QComboBox, options: list[str], selected: str) -> None:
        """Recarga las opciones del combo y deja seleccionado el modelo preferido."""
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
        """Indica si el usuario decidió forzar fallback para consultas."""
        return self.force_fallback.isChecked()

    def is_embedding_provider_ready(self) -> tuple[bool, str]:
        """Evalúa si el provider de embeddings está listo para query."""
        settings = get_settings()
        state = resolve_embedding_ui_state(
            settings,
            self.embedding_provider.currentText(),
            context="query",
        )
        return state.ready, state.reason

    def is_llm_provider_ready(self) -> tuple[bool, str]:
        """Evalúa si el provider LLM está listo para query."""
        settings = get_settings()
        state = resolve_llm_ui_state(settings, self.llm_provider.currentText())
        return state.ready, state.reason

    def set_query_action_hint(self, text: str) -> None:
        """Actualiza el mensaje inline asociado al botón de consulta."""
        self.query_action_hint.setText(text.strip())
