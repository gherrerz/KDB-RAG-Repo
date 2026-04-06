"""Estilos base compartidos entre vistas principales de UI."""

BASE_WIDGET_TEXT_STYLES = """
            QWidget {
                font-size: 13px;
                color: #EAF1FF;
            }
"""

BASE_INPUT_STYLES = """
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
"""

BASE_INPUT_STYLES_WITH_TEXTEDIT = """
            QLineEdit, QComboBox, QTextEdit {
                background-color: #0E1A2F;
                color: #EAF1FF;
                border: 1px solid #2A3A5A;
                border-radius: 8px;
                padding: 7px;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
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
"""

BASE_BUTTON_STYLES = """
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
            QPushButton:disabled {
                background-color: #344561;
                color: #90A2C3;
            }
"""