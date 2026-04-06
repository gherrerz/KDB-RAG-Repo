"""Plantillas rápidas para la generación y verificación de respuestas."""

SYSTEM_PROMPT = """
Eres un asistente de análisis de código con política anti-alucinación.
Reglas obligatorias:
1) No inventes relaciones ni hechos.
2) Toda afirmación debe estar soportada por evidencia del contexto.
3) Incluye citas de archivos y rangos de línea cuando sea posible.
4) Analizar a fondo la estructura, los patrones y las convenciones de la base de código proporcionada.
5) Investigar el tema en profundidad, relacionándolo con la base de código existente.
6) Identificar tecnologías, dependencias y decisiones arquitectónicas relevantes.
7) Identificar restricciones técnicas, riesgos y oportunidades.
8) Presentar hallazgos estructurados con secciones claras.
""".strip()


def build_answer_prompt(query: str, context: str) -> str:
    """Cree una respuesta final a partir de la consulta del usuario y el contexto recuperado."""
    return (
        f"Consulta del usuario:\n{query}\n\n"
        "Contexto recuperado:\n"
        f"{context}\n\n"
        "Genera una respuesta completa y basada solo en evidencia. "
        "Estructura tu respuesta con estas secciones cuando sea relevante:"
        "- **Resumen**: Resumen general de los hallazgos (2-3 oraciones)."
        "- **Análisis de la base de código**: Patrones clave, pila tecnológica, convenciones encontradas."
        "- **Hallazgos de la investigación**: Hallazgos detallados sobre el tema solicitado."
        "- **Restricciones y riesgos**: Limitaciones o riesgos técnicos a considerar."
        "- **Recomendaciones**: Próximos pasos prácticos basados ​​en los hallazgos."
        "Si existe un bloque INVENTORY[T], lista todos los elementos, "
        "del inventario para T, no solo una muestra."
    )


def build_verify_prompt(answer: str, context: str) -> str:
    """Cree un mensaje de verificación para verificar las reclamaciones no respaldadas."""
    return (
        "Valida si la respuesta contiene afirmaciones no soportadas por el "
        "contexto. Si detectas alucinación, responde con: INVALIDO. "
        "Si es válida, responde con: VALIDO.\n\n"
        f"Respuesta:\n{answer}\n\n"
        f"Contexto:\n{context}"
    )
