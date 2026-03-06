"""Widgets de vista de consultas para hacer preguntas sobre el repositorio."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class QueryView(QWidget):
    """Panel de interfaz que gestiona consultas en lenguaje natural."""

    def __init__(self) -> None:
        """Inicialice el formulario de consulta y responda los widgets de salida."""
        super().__init__()

        self.title_label = QLabel("Consulta")
        self.subtitle_label = QLabel(
            "Haz preguntas sobre el repositorio indexado y revisa la respuesta sintetizada."
        )
        self.status_chip = QLabel("Lista")
        self.status_chip.setObjectName("queryStatusChip")
        self.status_chip.setProperty("state", "idle")
        self.copy_history_button = QPushButton("Copiar Historial")
        self.refresh_repo_ids_button = QPushButton("Actualizar IDs")

        self.repo_id = QComboBox()
        self.repo_id.setEditable(False)
        self.repo_id.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.repo_id.setMaxVisibleItems(20)
        self._repo_ids: list[str] = []

        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Consulta la base de conocimientos...")
        self.query_input.returnPressed.connect(self._trigger_submit)

        self.query_button = QPushButton("↑")
        self.query_button.setFixedWidth(44)

        self.history_output = QPlainTextEdit()
        self.history_output.setReadOnly(True)
        self.history_output.setPlaceholderText("El historial de preguntas y respuestas aparecerá aquí...")

        self.input_bar = QFrame()
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setProperty("state", "idle")

        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)
        input_layout.addWidget(self.query_input)
        input_layout.addWidget(self.query_button)
        self.input_bar.setLayout(input_layout)

        repo_bar = QHBoxLayout()
        repo_label = QLabel("ID de repositorio")
        repo_bar.addWidget(repo_label)
        repo_bar.addWidget(self.repo_id)
        repo_bar.addWidget(self.refresh_repo_ids_button)

        top_bar = QGridLayout()
        top_bar.addWidget(self.title_label, 0, 0)
        top_bar.addWidget(self.status_chip, 0, 1)
        top_bar.addWidget(self.copy_history_button, 0, 2)
        top_bar.addWidget(self.subtitle_label, 1, 0, 1, 3)

        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addLayout(repo_bar)
        layout.addWidget(self.history_output)
        layout.addWidget(self.input_bar)
        self.setLayout(layout)

        self.copy_history_button.clicked.connect(self.copy_all_history)

        self.append_assistant_message(
            "Listo para auditar. Haz una pregunta para comenzar."
        )

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
            }
            QLabel {
                color: #E5E7EB;
            }
            QueryView {
                background-color: #111827;
            }
            QLabel#queryStatusChip {
                padding: 4px 10px;
                border-radius: 10px;
                font-weight: 600;
                color: #F3F4F6;
                background-color: #4B5563;
                qproperty-alignment: AlignCenter;
            }
            QLabel#queryStatusChip[state="running"] {
                background-color: #1D4ED8;
            }
            QLabel#queryStatusChip[state="success"] {
                background-color: #15803D;
            }
            QLabel#queryStatusChip[state="error"] {
                background-color: #B91C1C;
            }
            QLineEdit, QComboBox {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px;
            }
            QPlainTextEdit {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #374151;
                border-radius: 10px;
                padding: 8px;
                selection-background-color: #1D4ED8;
            }
            QFrame#inputBar {
                background-color: #111827;
                border: 1px solid #374151;
                border-radius: 12px;
            }
            QFrame#inputBar[state="running"] {
                border: 1px solid #F59E0B;
                background-color: #1F2937;
            }
            QFrame#inputBar[state="error"] {
                border: 1px solid #B91C1C;
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
        """Actualice el estado y el texto del chip de estado de la consulta."""
        valid_states = {"idle", "running", "success", "error"}
        selected_state = state if state in valid_states else "idle"
        self.status_chip.setProperty("state", selected_state)
        self.status_chip.setText(text)
        self.status_chip.style().unpolish(self.status_chip)
        self.status_chip.style().polish(self.status_chip)
        if selected_state == "error":
            self._set_input_bar_state("error")
        elif selected_state == "running":
            self._set_input_bar_state("running")
        else:
            self._set_input_bar_state("idle")

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
