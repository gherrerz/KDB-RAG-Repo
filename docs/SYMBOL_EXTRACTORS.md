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

Cobertura frontend actual del extractor ECMAScript:

- `.js` y `.jsx` se clasifican como `javascript`.
- `.ts` y `.tsx` se clasifican como `typescript`.
- React y Next.js reutilizan `JavaScriptBraceExtractor` para detección de
    símbolos estructurales.

## Seleccion de estrategia

La resolucion ocurre por `language` normalizado en minusculas:

- `python` -> `PythonAstExtractor`
- `java` -> `JavaBraceExtractor`
- `javascript`, `js`, `typescript`, `ts` -> `JavaScriptBraceExtractor`
- cualquier otro -> `GenericFallbackExtractor`

Notas prácticas para React y Next.js:

- Los componentes React y hooks se indexan como `function`.
- Las clases React se indexan como `class`.
- Los `export default` anónimos se nombran sintéticamente a partir del archivo,
    por ejemplo `page.tsx -> Page`, `layout.tsx -> Layout`, `middleware.ts -> Middleware`.
- En `route.ts` y rutas API equivalentes, los handlers HTTP se indexan por
    verbo exportado (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`).
- Hay soporte básico para patrones simples de `styled-components` y HOCs como
    `memo(Component)` o `withRouter(Component)`.

## Rollout y rollback

El comportamiento se controla por `SYMBOL_EXTRACTOR_V2_ENABLED`:

- `true` (default): usa extractores modulares por lenguaje.
- `false`: usa modo legacy de ventana fija para codigo.

La variable se lee desde `Settings.symbol_extractor_v2_enabled`.

La extracción semántica de JavaScript usa un flag separado `SEMANTIC_GRAPH_JAVASCRIPT_ENABLED`.
La de TypeScript mantiene `SEMANTIC_GRAPH_TYPESCRIPT_ENABLED`.

En TypeScript, la fase semántica también infiere relaciones `CALLS` desde uso de
componentes JSX/TSX como `<Button />`, además de llamadas tradicionales con
paréntesis.

## Como registrar un nuevo lenguaje

`LanguageExtractorRegistry` permite registrar estrategias nuevas:

```python
from coderag.ingestion.extractors.base import SymbolDetection, SymbolSpan
from coderag.ingestion.extractors.registry import DEFAULT_LANGUAGE_EXTRACTOR_REGISTRY


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
