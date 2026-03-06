"""Vista de tabla de evidencia para citas devueltas por canal de consulta."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class EvidenceView(QWidget):
    """Muestra filas de pruebas recuperadas en una tabla de solo lectura."""

    def __init__(self) -> None:
        """Inicializa el componente de tabla de evidencia."""
        super().__init__()
        self.title_label = QLabel("Evidencia")

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Path", "Start", "End", "Score", "Reason"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(self.table)
        self.setLayout(layout)

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                background-color: #111827;
            }
            QLabel {
                color: #E5E7EB;
            }
            QTableWidget {
                background-color: #0F172A;
                color: #E5E7EB;
                gridline-color: #374151;
                border: 1px solid #374151;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #1F2937;
                color: #E5E7EB;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #374151;
            }
            QTableWidget::item:selected {
                background-color: #1D4ED8;
            }
            """
        )

    def set_citations(self, citations: list[object]) -> None:
        """Representar citas en filas de tabla."""
        self.table.setRowCount(len(citations))
        for index, citation in enumerate(citations):
            path = citation["path"] if isinstance(citation, dict) else citation.path
            start_line = (
                citation["start_line"]
                if isinstance(citation, dict)
                else citation.start_line
            )
            end_line = (
                citation["end_line"] if isinstance(citation, dict) else citation.end_line
            )
            score = citation["score"] if isinstance(citation, dict) else citation.score
            reason = citation["reason"] if isinstance(citation, dict) else citation.reason
            self.table.setItem(index, 0, QTableWidgetItem(str(path)))
            self.table.setItem(index, 1, QTableWidgetItem(str(start_line)))
            self.table.setItem(index, 2, QTableWidgetItem(str(end_line)))
            self.table.setItem(index, 3, QTableWidgetItem(f"{float(score):.4f}"))
            self.table.setItem(index, 4, QTableWidgetItem(str(reason)))

            for column in range(5):
                item = self.table.item(index, column)
                if item is not None:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
