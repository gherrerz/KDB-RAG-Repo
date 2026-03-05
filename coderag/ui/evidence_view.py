"""Evidence table view for citations returned by query pipeline."""

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

class EvidenceView(QWidget):
    """Displays retrieved evidence rows in a read-only table."""

    def __init__(self) -> None:
        """Initialize evidence table widget."""
        super().__init__()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Path", "Start", "End", "Score", "Reason"]
        )
        layout = QVBoxLayout()
        layout.addWidget(self.table)
        self.setLayout(layout)

    def set_citations(self, citations: list[object]) -> None:
        """Render citations in table rows."""
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
