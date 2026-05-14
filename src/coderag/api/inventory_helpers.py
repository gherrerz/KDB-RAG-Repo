"""Inventory parsing and discovery helpers extracted from query service."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re
from threading import Lock
import unicodedata


INVENTORY_EQUIVALENT_GROUPS = [
    {"class", "clase"},
    {"service", "servicio"},
    {"controller", "controlador"},
    {"repository", "repositorio", "repo"},
    {"handler", "manejador"},
    {"model", "modelo"},
    {"entity", "entidad"},
    {"client", "cliente"},
    {"adapter", "adaptador"},
    {"gateway", "pasarela"},
    {"dao", "dataaccess", "data-access"},
    {"config", "configuration", "configuracion", "configuración"},
    {"implementation", "implementacion", "implementación", "impl"},
    {"manager", "gestor"},
    {"factory", "fabrica", "fábrica"},
    {"helper", "util", "utils", "utilidad"},
    {
        "dependency",
        "dependencies",
        "dependencia",
        "dependencias",
        "requirement",
        "requirements",
        "requisito",
        "requisitos",
    },
    {"component", "componente", "element", "elemento"},
    {"file", "archivo", "fichero"},
]

BROAD_FILE_INVENTORY_TERMS = {
    "component",
    "componente",
    "element",
    "elemento",
    "file",
    "archivo",
    "fichero",
}

MODULE_NAME_STOPWORDS = {
    "el",
    "la",
    "los",
    "las",
    "the",
    "a",
    "an",
    "de",
    "del",
    "tipo",
    "type",
    "clase",
    "class",
    "proyecto",
    "project",
}

DEPENDENCY_INVENTORY_TERMS = {
    "dependency",
    "dependencies",
    "dependencia",
    "dependencias",
    "requirement",
    "requirements",
    "requisito",
    "requisitos",
}

INVENTORY_TARGET_STOPWORDS = {
    "todo",
    "todos",
    "toda",
    "todas",
    "los",
    "las",
    "all",
    "the",
}


_MODULE_SCOPE_CACHE: dict[tuple[str, str], str] = {}
_MODULE_SCOPE_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class InventoryHelperHooks:
    """Injected collaborators required by inventory discovery helpers."""

    get_settings: Callable[[], object]
    graph_builder_factory: Callable[[], object]


def extract_module_name(query: str) -> str | None:
    """Extract a module or package token from a natural-language query."""
    normalized = query.lower()

    quoted = re.search(r"['\"]([a-z0-9_./-]+)['\"]", normalized)
    if quoted:
        return quoted.group(1)

    anchored_patterns = [
        (
            r"(?:carpeta|folder|directorio|directory|"
            r"modulo|módulo|module|package)\s+"
            r"(?:del?\s+|de\s+la\s+|de\s+los\s+|de\s+las\s+)?"
            r"([a-z0-9_./-]+)"
        ),
        (
            r"(?:componentes?|elements?|archivos?|files?)\s+"
            r"(?:de|en|in|from|of)\s+"
            r"(?:la|el|los|las|the)?\s*"
            r"(?:carpeta|folder|directorio|directory|"
            r"modulo|módulo|module|package)?\s*"
            r"([a-z0-9_./-]+)"
        ),
    ]
    for pattern in anchored_patterns:
        for match in re.finditer(pattern, normalized):
            token = match.group(1).strip(".,;:!?()[]{}")
            if token and token not in MODULE_NAME_STOPWORDS:
                return token

    patterns = [
        r"(?:modulo|módulo|module|package|servicio|service)\s+"
        r"([a-z0-9_./-]+)",
        r"(?:in|en|de|del|of|for)\s+([a-z0-9_./-]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            token = match.group(1).strip(".,;:!?()[]{}")
            if token and token not in MODULE_NAME_STOPWORDS:
                return token

    module_like = re.search(r"\b([a-z0-9]+(?:[-_/][a-z0-9]+)+)\b", normalized)
    if module_like:
        return module_like.group(1)
    return None


def normalize_inventory_token(token: str) -> str:
    """Normalize inventory tokens by removing accents and punctuation."""
    lowered = token.lower().strip(".,;:!?()[]{}")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )


def inventory_base_forms(token: str) -> set[str]:
    """Build candidate singular/plural base forms for an inventory token."""
    normalized = normalize_inventory_token(token)
    forms = {normalized}

    if normalized.endswith("ies") and len(normalized) > 3:
        forms.add(normalized[:-3] + "y")

    if normalized.endswith("es") and len(normalized) > 3:
        es_root = normalized[:-2]
        if normalized.endswith(
            (
                "ses",
                "xes",
                "zes",
                "ches",
                "shes",
                "ores",
                "dores",
                "tores",
                "ciones",
                "siones",
                "ades",
                "udes",
            )
        ):
            forms.add(es_root)

    if normalized.endswith("s") and len(normalized) > 2:
        forms.add(normalized[:-1])

    return {form for form in forms if form}


def canonical_inventory_term(token: str) -> str:
    """Return the canonical inventory term for a user-provided token."""
    forms = inventory_base_forms(token)
    known_terms = {
        term for group in INVENTORY_EQUIVALENT_GROUPS for term in group
    }
    for form in sorted(forms, key=lambda item: (len(item), item)):
        if form in known_terms:
            return form
    return normalize_inventory_token(token)


def plural_variants(token: str) -> set[str]:
    """Generate plural and superficial variants for a normalized token."""
    variants = {token}
    if not token:
        return variants

    if token.endswith(("s", "x", "z", "ch", "sh", "or", "ion", "dad", "dor")):
        variants.add(f"{token}es")
    else:
        variants.add(f"{token}s")
    if token.endswith("y") and len(token) > 1:
        variants.add(f"{token[:-1]}ies")
    return variants


def extract_inventory_target(query: str) -> str | None:
    """Extract the normalized target entity from an inventory query."""
    normalized = query.lower()

    match_type_spec = re.search(
        r"(?:de\s+)?tipo\s+(?:de\s+)?([a-z0-9_-]+)",
        normalized,
    )
    if match_type_spec:
        token = match_type_spec.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return canonical_inventory_term(token)

    match_component_type = re.search(
        r"(?:componentes?|elements?|elementi?s)\s+([a-z0-9_-]+)",
        normalized,
    )
    if match_component_type:
        token = match_component_type.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS and token not in {
            "de",
            "del",
            "de la",
            "de los",
        }:
            return canonical_inventory_term(token)

    patterns = [
        r"tod(?:os|as)?\s+(?:los|las)?\s*([a-z0-9_-]+)",
        r"cuales?\s+son\s+(?:tod(?:os|as)?\s+)?(?:los|las)?\s*([a-z0-9_-]+)",
        r"(?:lista|listar)\s+(?:los|las)?\s*([a-z0-9_-]+)",
        r"all\s+([a-z0-9_-]+)",
        r"which\s+([a-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        token = match.group(1)
        if token not in INVENTORY_TARGET_STOPWORDS:
            return canonical_inventory_term(token)

    return None


def is_inventory_explain_query(query: str) -> bool:
    """Return whether the query asks for per-component explanation."""
    normalized = normalize_inventory_token(query)
    explanation_signals = [
        "que funcion",
        "que hace",
        "para que sirve",
        "funcion cumplen",
        "funcion de cada",
        "explain",
        "what each",
        "each one does",
        "what each one does",
        "function of each",
        "role of each",
        "what does",
        "what do",
        "purpose",
    ]
    return any(signal in normalized for signal in explanation_signals)


def inventory_term_aliases(target_term: str) -> list[str]:
    """Expand an inventory target with plural and multilingual aliases."""
    base_forms = inventory_base_forms(target_term)
    aliases: set[str] = set()
    for form in base_forms:
        aliases.update(plural_variants(form))

    for group in INVENTORY_EQUIVALENT_GROUPS:
        if base_forms.intersection(group):
            for token in group:
                normalized = normalize_inventory_token(token)
                aliases.update(plural_variants(normalized))

    return sorted(aliases)


def query_inventory_entities(
    repo_id: str,
    target_term: str,
    module_name: str | None,
    *,
    hooks: InventoryHelperHooks,
) -> list[dict]:
    """Query inventory entities from the graph using a generic target term."""
    settings = hooks.get_settings()
    graph = hooks.graph_builder_factory()
    try:
        canonical_target = canonical_inventory_term(target_term)
        if module_name and canonical_target in BROAD_FILE_INVENTORY_TERMS:
            module_files = graph.query_module_files(
                repo_id=repo_id,
                module_name=module_name,
                limit=settings.inventory_entity_limit,
            )
            return sorted(module_files, key=lambda item: item.get("path", ""))

        entities_by_key: dict[tuple, dict] = {}
        aliases = inventory_term_aliases(target_term)[: settings.inventory_alias_limit]
        if not aliases:
            return []

        alias_results: dict[str, list[dict]] = {}
        if len(aliases) == 1:
            alias = aliases[0]
            alias_results[alias] = graph.query_inventory(
                repo_id=repo_id,
                target_term=alias,
                module_name=module_name,
                limit=settings.inventory_entity_limit,
            )
        else:
            max_workers = min(4, len(aliases))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    alias: executor.submit(
                        graph.query_inventory,
                        repo_id,
                        alias,
                        module_name,
                        settings.inventory_entity_limit,
                    )
                    for alias in aliases
                }
                alias_results = {
                    alias: future.result()
                    for alias, future in futures.items()
                }

        for alias in aliases:
            entities = alias_results.get(alias, [])
            for item in entities:
                path = str(item.get("path", ""))
                label = str(item.get("label", ""))
                kind = str(item.get("kind", "file"))
                start_line = int(item.get("start_line", 1))
                end_line = int(item.get("end_line", 1))
                if canonical_target in DEPENDENCY_INVENTORY_TERMS:
                    key = (path, start_line, end_line, label, kind)
                else:
                    key = (path, start_line, end_line)
                if key not in entities_by_key:
                    entities_by_key[key] = item
        return sorted(
            entities_by_key.values(),
            key=lambda item: (
                str(item.get("path", "")),
                str(item.get("label", "")),
                str(item.get("kind", "")),
            ),
        )
    except Exception:
        return []
    finally:
        graph.close()


def resolve_module_scope(
    repo_id: str,
    module_name: str | None,
    *,
    hooks: InventoryHelperHooks,
) -> str | None:
    """Resolve a user module token into a canonical graph module path."""
    if not module_name:
        return None

    cleaned = module_name.strip().strip("/\\").replace("\\", "/")
    if not cleaned:
        return None

    cache_key = (repo_id, cleaned.lower())
    with _MODULE_SCOPE_CACHE_LOCK:
        cached = _MODULE_SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    graph = hooks.graph_builder_factory()
    try:
        known_modules = graph.query_repo_modules(repo_id)
    except Exception:
        known_modules = []
    finally:
        graph.close()

    if cleaned in known_modules:
        with _MODULE_SCOPE_CACHE_LOCK:
            _MODULE_SCOPE_CACHE[cache_key] = cleaned
        return cleaned

    lowered = cleaned.lower()
    matches: list[str] = []
    for module_path in known_modules:
        normalized = module_path.strip().strip("/")
        if not normalized:
            continue
        rel_lower = normalized.lower()
        tail = normalized.rsplit("/", 1)[-1].lower()
        if tail == lowered or rel_lower.endswith(f"/{lowered}"):
            matches.append(normalized)

    if not matches:
        with _MODULE_SCOPE_CACHE_LOCK:
            _MODULE_SCOPE_CACHE[cache_key] = cleaned
        return cleaned

    matches.sort(key=lambda item: (item.count("/"), len(item), item))
    resolved = matches[0]
    with _MODULE_SCOPE_CACHE_LOCK:
        _MODULE_SCOPE_CACHE[cache_key] = resolved
    return resolved