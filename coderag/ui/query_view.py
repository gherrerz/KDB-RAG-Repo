"""Query view widgets for asking repository questions."""

from PySide6.QtWidgets import (
    QFormLayout,
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
        self.repo_id = QLineEdit()
        self.query_input = QTextEdit()
        self.query_button = QPushButton("Consultar")
        self.answer_output = QTextEdit()
        self.answer_output.setReadOnly(True)

        form = QFormLayout()
        form.addRow("Repo ID", self.repo_id)
        form.addRow("Pregunta", self.query_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.query_button)
        layout.addWidget(self.answer_output)
        self.setLayout(layout)
