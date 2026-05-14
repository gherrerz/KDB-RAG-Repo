"""Pure query-intent and file-reference signal helpers."""

import re
import unicodedata


def is_module_query(query: str) -> bool:
    """Return whether the user asks explicitly about repository modules."""
    normalized = query.lower()
    return any(
        token in normalized
        for token in ["modulo", "módulo", "module", "modulos", "módulos"]
    )


def is_inventory_query(query: str) -> bool:
    """Return whether the query explicitly asks for inventory."""
    normalized = query.lower()
    inventory_tokens = ("inventario", "inventory")
    return any(token in normalized for token in inventory_tokens)


def is_external_import_query(query: str) -> bool:
    """Detect queries about external imports or dependencies."""
    normalized = query.lower()
    signals = (
        "import",
        "imported",
        "imports",
        "dependency",
        "dependencies",
        "dependencia",
        "dependencias",
    )
    return any(token in normalized for token in signals)


def normalize_query_signal_text(query: str) -> str:
    """Normalize query text to detect signals with or without accents."""
    normalized = unicodedata.normalize("NFKD", query.lower())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def extract_file_reference_candidates(query: str) -> tuple[str, ...]:
    """Extract file-like references from the user query."""
    matches = re.findall(
        r"[A-Za-z0-9_./\\-]+\."
        r"(?:py|js|jsx|ts|tsx|java|json|ya?ml|md|txt|c|h|cpp|hpp|cs|go|rb|php|rs)",
        query,
        flags=re.IGNORECASE,
    )
    candidates: list[str] = []
    for raw_match in matches:
        candidate = raw_match.strip().strip("'\"`()[]{}<>,;:")
        candidate = candidate.replace("\\", "/").lstrip("./")
        if not candidate:
            continue
        candidates.append(candidate.lower())
    return tuple(dict.fromkeys(candidates))


def is_reverse_file_import_query(query: str) -> bool:
    """Detect questions about which files import or use a given file."""
    candidates = extract_file_reference_candidates(query)
    if not candidates:
        return False

    normalized = normalize_query_signal_text(query)
    reverse_patterns = (
        r"\bwho\s+(?:imports?|uses?)\b",
        r"\b(?:which|what)\s+files?\s+(?:import|imports|use|uses)\b",
        r"\bwhere\s+is\s+.+\s+(?:imported|used)\b",
        r"\b(?:imported|used)\s+by\b",
        r"\bquien\s+(?:importa|usa)\b",
        r"\b(?:que|cuales?)\s+archivos?\s+(?:importa|importan|usa|usan)\b",
        r"\b(?:en\s+)?que\s+archivos?\s+se\s+(?:importa|importan|usa|usan)\b",
        r"\bdonde\s+se\s+(?:importa|usa)\b",
    )
    if any(re.search(pattern, normalized) for pattern in reverse_patterns):
        return True

    has_file_scope = any(
        token in normalized for token in ("archivo", "archivos", "file", "files")
    )
    has_import_signal = any(
        token in normalized
        for token in (
            "importa",
            "importan",
            "imports",
            "imported",
            "usa",
            "usan",
            "use",
            "uses",
            "used",
        )
    )
    return has_file_scope and has_import_signal