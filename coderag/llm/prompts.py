"""Prompt templates for answer generation and verification."""

SYSTEM_PROMPT = """
Eres un asistente de análisis de código con política anti-alucinación.
Reglas obligatorias:
1) No inventes relaciones ni hechos.
2) Toda afirmación debe estar soportada por evidencia del contexto.
3) Si no hay evidencia suficiente, responde exactamente:
   \"No se encontró información en el repositorio.\"
4) Incluye citas de archivos y rangos de línea cuando sea posible.
""".strip()


def build_answer_prompt(query: str, context: str) -> str:
    """Create final answer prompt from user query and retrieved context."""
    return (
        f"Consulta del usuario:\n{query}\n\n"
        "Contexto recuperado:\n"
        f"{context}\n\n"
        "Genera una respuesta completa y basada solo en evidencia. "
        "Incluye: 1) respuesta principal, 2) componentes/archivos clave, "
        "3) flujo o relaciones relevantes si aparecen en el contexto, "
        "4) citas de archivos con líneas. "
        "Si existe un bloque INVENTORY[T], lista todos los elementos "
        "del inventario para T, no solo una muestra."
    )


def build_verify_prompt(answer: str, context: str) -> str:
    """Create verifier prompt to check unsupported claims."""
    return (
        "Valida si la respuesta contiene afirmaciones no soportadas por el "
        "contexto. Si detectas alucinación, responde con: INVALIDO. "
        "Si es válida, responde con: VALIDO.\n\n"
        f"Respuesta:\n{answer}\n\n"
        f"Contexto:\n{context}"
    )
