"""Helpers de estilos compartidos para cards y chips de estado."""


def frame_card_styles(*object_names: str) -> str:
    """Construye estilos base de card para object names dados."""
    selectors = ",\n            ".join(f"QFrame#{name}" for name in object_names)
    return (
        f"""
            {selectors} {{
                background-color: #111C32;
                border: 1px solid #2A3A5A;
                border-radius: 12px;
            }}
        """
    )


def top_card_styles(object_name: str) -> str:
    """Construye estilo para card superior destacado."""
    return (
        f"""
            QFrame#{object_name} {{
                background-color: #15243E;
            }}
        """
    )


def title_subtitle_styles(title_name: str, subtitle_name: str) -> str:
    """Construye estilos para labels de titulo y subtitulo."""
    return (
        f"""
            QLabel#{title_name} {{
                color: #EAF1FF;
                letter-spacing: 0.4px;
            }}
            QLabel#{subtitle_name} {{
                color: #A8B7D6;
            }}
        """
    )


def status_chip_styles(object_name: str, center: bool = False) -> str:
    """Construye estilos para chip de estado con estados comunes."""
    alignment = "\n                qproperty-alignment: AlignCenter;" if center else ""
    return (
        f"""
            QLabel#{object_name} {{
                padding: 4px 10px;
                border-radius: 10px;
                font-weight: 600;
                color: #F8FBFF;
                background-color: #41577D;{alignment}
            }}
            QLabel#{object_name}[pulse="true"] {{
                border: 1px solid #8FB9FF;
                padding: 3px 9px;
            }}
            QLabel#{object_name}[state="running"] {{
                background-color: #D98F2B;
            }}
            QLabel#{object_name}[state="success"] {{
                background-color: #1FA971;
            }}
            QLabel#{object_name}[state="error"] {{
                background-color: #C93A4B;
            }}
        """
    )