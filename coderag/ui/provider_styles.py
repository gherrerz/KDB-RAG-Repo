"""Estilos compartidos para feedback visual de providers en la UI."""

PROVIDER_FEEDBACK_STYLES = """
            QLabel#providerWarning {
                color: #F4C46A;
                font-size: 12px;
                padding: 2px 0 2px 0;
            }
            QLabel#providerStatusChip {
                color: #DFF7EC;
                background-color: #1F7F58;
                border: 1px solid #2DAA78;
                border-radius: 10px;
                font-size: 12px;
                font-weight: 600;
                padding: 3px 9px;
            }
            QLabel#providerStatusChip[state="warning"] {
                color: #FFF3D6;
                background-color: #7A5A23;
                border: 1px solid #D9A645;
            }
            QLabel#providerStatusChip[state="blocked"] {
                color: #FFE2E6;
                background-color: #7C2B35;
                border: 1px solid #D35667;
            }
            QCheckBox#forceFallbackCheck {
                color: #C9D7F0;
                font-size: 12px;
                spacing: 6px;
            }
            QLabel#actionHint {
                color: #A8B7D6;
                font-size: 12px;
                padding: 2px 0 0 2px;
            }
"""