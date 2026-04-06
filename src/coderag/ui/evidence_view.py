"""Vista de tabla de evidencia para citas devueltas por canal de consulta."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class EvidenceView(QWidget):
    """Muestra filas de pruebas recuperadas en una tabla de solo lectura."""

    PANEL_PULSE_MS = 180
    NEW_ROW_HIGHLIGHT_MS = 320
    NEW_ROW_HIGHLIGHT_COLOR = QColor("#1D3A54")

    def __init__(self) -> None:
        """Inicializa el componente de tabla de evidencia."""
        super().__init__()
        self._last_citation_keys: set[tuple[str, int, int]] = set()
        self.title_label = QLabel("Evidencia")
        self.title_label.setObjectName("evidenceTitle")
        title_font = QFont("Segoe UI", 15, QFont.Weight.Bold)
        self.title_label.setFont(title_font)

        self.card = QFrame()
        self.card.setObjectName("evidenceCard")

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("evidenceTable")
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

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(10, 10, 10, 10)
        card_layout.addWidget(self.table)
        self.card.setLayout(card_layout)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.card)
        self.setLayout(layout)

        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                background-color: #0A1324;
                color: #EAF1FF;
            }
            QLabel#evidenceTitle {
                color: #EAF1FF;
                letter-spacing: 0.3px;
            }
            QLabel#evidenceTitle[pulse="true"] {
                color: #F4F8FF;
            }
            QFrame#evidenceCard {
                background-color: #111C32;
                border: 1px solid #2A3A5A;
                border-radius: 12px;
            }
            QFrame#evidenceCard[pulse="true"] {
                border: 1px solid #5EA0FF;
            }
            QTableWidget#evidenceTable {
                background-color: #0E1A2F;
                color: #EAF1FF;
                alternate-background-color: #11203A;
                gridline-color: #2A3A5A;
                border: 1px solid #2A3A5A;
                border-radius: 10px;
            }
            QHeaderView::section {
                background-color: #1A2D4D;
                color: #DCE8FF;
                padding: 7px;
                border: none;
                border-bottom: 1px solid #2A3A5A;
            }
            QTableWidget::item:selected {
                background-color: #2F7BFF;
            }
            """
        )
        self.table.setAlternatingRowColors(True)

    def set_citations(self, citations: list[object]) -> None:
        """Representar citas en filas de tabla."""
        parsed: list[tuple[str, int, int, float, str]] = []
        for citation in citations:
            path = str(citation["path"] if isinstance(citation, dict) else citation.path)
            start_line = int(
                citation["start_line"] if isinstance(citation, dict) else citation.start_line
            )
            end_line = int(
                citation["end_line"] if isinstance(citation, dict) else citation.end_line
            )
            score = float(citation["score"] if isinstance(citation, dict) else citation.score)
            reason = str(citation["reason"] if isinstance(citation, dict) else citation.reason)
            parsed.append((path, start_line, end_line, score, reason))

        current_keys = {(path, start_line, end_line) for path, start_line, end_line, _, _ in parsed}
        new_keys = current_keys - self._last_citation_keys

        self.table.setRowCount(len(citations))
        for index, (path, start_line, end_line, score, reason) in enumerate(parsed):
            self.table.setItem(index, 0, QTableWidgetItem(str(path)))
            self.table.setItem(index, 1, QTableWidgetItem(str(start_line)))
            self.table.setItem(index, 2, QTableWidgetItem(str(end_line)))
            self.table.setItem(index, 3, QTableWidgetItem(f"{float(score):.4f}"))
            self.table.setItem(index, 4, QTableWidgetItem(str(reason)))

            is_new = (path, start_line, end_line) in new_keys
            for column in range(5):
                item = self.table.item(index, column)
                if item is not None:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if is_new:
                        item.setBackground(self.NEW_ROW_HIGHLIGHT_COLOR)

        self._last_citation_keys = current_keys
        self._pulse_panel()
        if new_keys:
            QTimer.singleShot(self.NEW_ROW_HIGHLIGHT_MS, self._clear_new_rows_highlight)

    def _pulse_panel(self) -> None:
        """Aplique pulso breve al título y contenedor al actualizar evidencia."""
        self.title_label.setProperty("pulse", "true")
        self.card.setProperty("pulse", "true")
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        QTimer.singleShot(self.PANEL_PULSE_MS, self._clear_panel_pulse)

    def _clear_panel_pulse(self) -> None:
        """Quite el estado de pulso tras el feedback de actualización."""
        self.title_label.setProperty("pulse", "false")
        self.card.setProperty("pulse", "false")
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)

    def _clear_new_rows_highlight(self) -> None:
        """Limpie resaltado temporal de filas nuevas manteniendo selección activa."""
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is not None and not item.isSelected():
                    item.setBackground(Qt.GlobalColor.transparent)
