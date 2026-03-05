"""Query view widgets for asking repository questions."""

from PySide6.QtWidgets import (
    QFrame,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class QueryView(QWidget):
    """UI panel that handles natural language queries."""

    def __init__(self) -> None:
        """Initialize query form and answer output widgets."""
        super().__init__()

        self.title_label = QLabel("Consulta")
        self.subtitle_label = QLabel(
            "Haz preguntas sobre el repositorio indexado y revisa la respuesta sintetizada."
        )

        self.repo_id = QLineEdit()
        self.repo_id.setPlaceholderText("UUID del repositorio")

        self.query_input = QTextEdit()
        self.query_input.setPlaceholderText("Ejemplo: ¿Qué módulos manejan autenticación?")

        self.query_button = QPushButton("Consultar")

        self.answer_output = QTextEdit()
        self.answer_output.setReadOnly(True)
        self.answer_output.setPlaceholderText("La respuesta aparecerá aquí...")

        form = QFormLayout()
        form.addRow("Repo ID", self.repo_id)
        form.addRow("Pregunta", self.query_input)

        card = QFrame()
        card.setObjectName("queryCard")
        card.setLayout(form)

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(card)
        layout.addWidget(self.query_button)
        layout.addWidget(self.answer_output)
        self.setLayout(layout)

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
            QFrame#queryCard {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 10px;
                padding: 10px;
            }
            QLineEdit, QTextEdit {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #374151;
                border-radius: 8px;
                padding: 6px;
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
