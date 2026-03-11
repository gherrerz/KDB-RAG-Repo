"""Widgets de vista de consultas para hacer preguntas sobre el repositorio."""

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
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
        self.copy_history_button.setProperty("variant", "secondary")
        self.refresh_repo_ids_button.setProperty("variant", "secondary")

        self.repo_id = QComboBox()
        self.repo_id.setEditable(False)
        self.repo_id.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.repo_id.setMaxVisibleItems(20)
        self._repo_ids: list[str] = []

        self.query_input = QLineEdit()
        self.query_input.setObjectName("queryInput")
        self.query_input.setPlaceholderText("Consulta la base de conocimientos...")
        self.query_input.returnPressed.connect(self._trigger_submit)

        self.query_button = QPushButton("↑")
        self.query_button.setObjectName("querySubmitButton")
        self.query_button.setFixedWidth(44)

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

        repo_bar = QHBoxLayout()
        repo_bar.setContentsMargins(14, 12, 14, 12)
        repo_bar.setSpacing(10)
        repo_label = QLabel("ID de repositorio")
        repo_label.setObjectName("queryRepoLabel")
        repo_bar.addWidget(repo_label)
        repo_bar.addWidget(self.repo_id)
        repo_bar.addWidget(self.refresh_repo_ids_button)
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
        self.setLayout(layout)

        self.copy_history_button.clicked.connect(self.copy_all_history)
        self.copy_history_button.clicked.connect(
            lambda: self._flash_button(self.copy_history_button)
        )
        self.refresh_repo_ids_button.clicked.connect(
            lambda: self._flash_button(self.refresh_repo_ids_button)
        )
        self.query_button.clicked.connect(lambda: self._flash_button(self.query_button))

        self.append_assistant_message(
            "Listo para auditar. Haz una pregunta para comenzar."
        )

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                color: #EAF1FF;
            }
            QueryView {
                background-color: #0A1324;
            }
            QFrame#queryTopCard,
            QFrame#queryRepoCard,
            QFrame#queryHistoryCard,
            QFrame#inputBar {
                background-color: #111C32;
                border: 1px solid #2A3A5A;
                border-radius: 12px;
            }
            QFrame#queryTopCard {
                background-color: #15243E;
            }
            QLabel#queryTitle {
                color: #EAF1FF;
                letter-spacing: 0.4px;
            }
            QLabel#querySubtitle {
                color: #A8B7D6;
            }
            QLabel#queryRepoLabel {
                color: #A8B7D6;
                font-weight: 600;
            }
            QLabel#queryStatusChip {
                padding: 4px 10px;
                border-radius: 10px;
                font-weight: 600;
                color: #F8FBFF;
                background-color: #41577D;
                qproperty-alignment: AlignCenter;
            }
            QLabel#queryStatusChip[pulse="true"] {
                border: 1px solid #8FB9FF;
                padding: 3px 9px;
            }
            QLabel#queryStatusChip[state="running"] {
                background-color: #D98F2B;
            }
            QLabel#queryStatusChip[state="success"] {
                background-color: #1FA971;
            }
            QLabel#queryStatusChip[state="error"] {
                background-color: #C93A4B;
            }
            QLineEdit, QComboBox {
                background-color: #0E1A2F;
                color: #EAF1FF;
                border: 1px solid #2A3A5A;
                border-radius: 8px;
                padding: 7px;
            }
            QLineEdit:focus, QComboBox:focus {
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
            QPushButton:disabled {
                background-color: #344561;
                color: #90A2C3;
            }
            """
        )

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
        self.refresh_repo_ids_button.setDisabled(running)
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
