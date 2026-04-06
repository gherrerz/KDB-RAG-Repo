# Extractores de Simbolos por Lenguaje

Este documento describe el framework modular de extraccion de simbolos usado por la ingesta.

## Objetivo

- Extraer spans completos de simbolos cuando el lenguaje lo permite.
- Mantener rollback seguro al comportamiento legacy.
- Permitir registrar extractores nuevos sin modificar el orquestador principal.

## Componentes

- Contrato base: `src/coderag/ingestion/extractors/base.py`
- Registry: `src/coderag/ingestion/extractors/registry.py`
- Extractores incluidos:
  - `PythonAstExtractor`
  - `JavaBraceExtractor`
  - `JavaScriptBraceExtractor` (reutilizado para TypeScript)
  - `GenericFallbackExtractor`

## Seleccion de estrategia

La resolucion ocurre por `language` normalizado en minusculas:

- `python` -> `PythonAstExtractor`
- `java` -> `JavaBraceExtractor`
- `javascript`, `js`, `typescript`, `ts` -> `JavaScriptBraceExtractor`
- cualquier otro -> `GenericFallbackExtractor`

## Rollout y rollback

El comportamiento se controla por `SYMBOL_EXTRACTOR_V2_ENABLED`:

- `true` (default): usa extractores modulares por lenguaje.
- `false`: usa modo legacy de ventana fija para codigo.

La variable se lee desde `Settings.symbol_extractor_v2_enabled`.

## Como registrar un nuevo lenguaje

`LanguageExtractorRegistry` permite registrar estrategias nuevas:

```python
from src.coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from src.coderag.ingestion.extractors.registry import DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY


class RubyExtractor:
    def detect_symbols(self, content: str) -> list[SymbolDetection]:
        detections: list[SymbolDetection] = []
        for index, line in enumerate(content.splitlines(), start=1):
            if line.strip().startswith("def "):
                name = line.strip().split()[1].split("(")[0]
                detections.append(
                    SymbolDetection(
                        symbol_name=name,
                        symbol_type="function",
                        start_line=index,
                    )
                )
        return detections

    def resolve_span(
        self,
        content: str,
        detection: SymbolDetection,
    ) -> SymbolSpan:
        # Ejemplo simple: primer `end` despues del inicio
        lines = content.splitlines()
        for line_number in range(detection.start_line, len(lines) + 1):
            if lines[line_number - 1].strip() == "end":
                return SymbolSpan(
                    start_line=detection.start_line,
                    end_line=line_number,
                )
        return SymbolSpan(
            start_line=detection.start_line,
            end_line=detection.start_line,
        )


DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY.register("ruby", RubyExtractor())
```

## Observabilidad de extraccion

Durante la ingesta se emite un log de observabilidad con:

- modo (`v2` o `legacy`)
- archivos por lenguaje
- chunks por lenguaje
- span promedio
- p95 de span
- chunks con span > 30 lineas

Esto permite detectar si un extractor nuevo esta produciendo spans anormalmente cortos o largos.

## Pruebas recomendadas para un extractor nuevo

1. Deteccion de simbolos basicos.
2. Resolucion de span en casos multilinea.
3. Manejo de comentarios y strings cuando aplique.
4. Caso de fallback cuando no se puede resolver cierre.
5. Prueba de integracion con `extract_symbol_chunks` y el flag `SYMBOL_EXTRACTOR_V2_ENABLED`.
