"""Integración de Neo4j para la construcción de gráficos de conocimiento de código."""

import hashlib
from collections import defaultdict
from threading import Lock
from typing import Any

from neo4j import GraphDatabase

from coderag.core.models import ScannedFile, SemanticRelation, SymbolChunk
from coderag.core.settings import get_settings
from coderag.ingestion.component_metadata import infer_component_purpose


class GraphBuilder:
    """Generador de gráficos para almacenar archivos, símbolos y relaciones."""

    _shared_driver: Any | None = None
    _shared_config: tuple[str, str, str] | None = None
    _driver_lock: Lock = Lock()

    def __init__(self) -> None:
        """Cree el controlador Neo4j desde la configuración."""
        settings = get_settings()
        config = (settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        with self._driver_lock:
            if self._shared_driver is None or self._shared_config != config:
                self.__class__._shared_driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
                self.__class__._shared_config = config
            self.driver = self._shared_driver

    def close(self) -> None:
        """Mantiene compatibilidad de API; el driver se comparte por proceso."""
        return None

    @classmethod
    def close_shared_driver(cls) -> None:
        """Cierra explícitamente el driver compartido cuando sea necesario."""
        with cls._driver_lock:
            if cls._shared_driver is not None:
                cls._shared_driver.close()
                cls._shared_driver = None
                cls._shared_config = None

    def upsert_repo_graph(
        self,
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
        semantic_relations: list[SemanticRelation] | None = None,
    ) -> None:
        """Inserte nodos, archivos, módulos y símbolos del gráfico del repositorio."""
        file_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (m:Module {repo_id: $repo_id, path: $module_path})
        SET m.name = $module_name,
            m.path_depth = $module_depth
        MERGE (r)-[:HAS_MODULE]->(m)
        MERGE (f:File {repo_id: $repo_id, path: $path})
        SET f.language = $language,
            f.file_name = $file_name,
            f.module_path = $module_path,
            f.path_depth = $path_depth,
            f.purpose_summary = $purpose_summary,
            f.purpose_source = $purpose_source,
            f.top_level_symbol_names = $top_level_symbol_names,
            f.top_level_symbol_types = $top_level_symbol_types
        MERGE (m)-[:CONTAINS]->(f)
        """
        symbol_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (f:File {repo_id: $repo_id, path: $path})
        MERGE (s:Symbol {id: $symbol_id})
        SET s.repo_id = $repo_id,
            s.name = $symbol_name,
            s.name_lc = toLower($symbol_name),
            s.type = $symbol_type,
            s.start_line = $start_line,
            s.end_line = $end_line
        MERGE (f)-[:DECLARES]->(s)
        """
        with self.driver.session() as session:
            symbols_by_path: dict[str, list[SymbolChunk]] = defaultdict(list)
            for symbol in symbols:
                symbols_by_path[symbol.path].append(symbol)

            for file_obj in scanned_files:
                module_path = file_obj.path.split("/", 1)[0] if "/" in file_obj.path else "."
                module_name = module_path.rsplit("/", 1)[-1] if module_path != "." else "."
                module_depth = 0 if module_path == "." else module_path.count("/") + 1
                purpose_summary, purpose_source = infer_component_purpose(
                    file_obj.path,
                    file_obj.content,
                )
                file_symbols = symbols_by_path.get(file_obj.path, [])
                top_level_symbol_names = [item.symbol_name for item in file_symbols[:20]]
                top_level_symbol_types = sorted(
                    {item.symbol_type for item in file_symbols if item.symbol_type}
                )[:20]
                session.run(
                    file_query,
                    repo_id=repo_id,
                    module_path=module_path,
                    module_name=module_name,
                    module_depth=module_depth,
                    path=file_obj.path,
                    language=file_obj.language,
                    file_name=file_obj.path.rsplit("/", 1)[-1],
                    path_depth=file_obj.path.count("/") + 1,
                    purpose_summary=purpose_summary,
                    purpose_source=purpose_source,
                    top_level_symbol_names=top_level_symbol_names,
                    top_level_symbol_types=top_level_symbol_types,
                )
            for symbol in symbols:
                session.run(
                    symbol_query,
                    repo_id=repo_id,
                    path=symbol.path,
                    symbol_id=symbol.id,
                    symbol_name=symbol.symbol_name,
                    symbol_type=symbol.symbol_type,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                )
            self._upsert_semantic_relations(
                session=session,
                repo_id=repo_id,
                semantic_relations=semantic_relations or [],
            )

    def _upsert_semantic_relations(
        self,
        session: Any,
        repo_id: str,
        semantic_relations: list[SemanticRelation],
    ) -> None:
        """Inserta relaciones semánticas en lotes con idempotencia por relación."""
        if not semantic_relations:
            return

        resolved_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        unresolved_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        supported_types = {"CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"}

        for relation in semantic_relations:
            relation_type = relation.relation_type.strip().upper()
            if relation_type not in supported_types:
                continue
            row = {
                "source_symbol_id": relation.source_symbol_id,
                "target_symbol_id": relation.target_symbol_id,
                "target_ref": relation.target_ref,
                "target_kind": relation.target_kind,
                "path": relation.path,
                "line": relation.line,
                "confidence": relation.confidence,
                "language": relation.language,
                "relation_id": self._relation_id(relation),
            }
            if relation.target_symbol_id:
                resolved_by_type[relation_type].append(row)
            else:
                unresolved_by_type[relation_type].append(row)

        for relation_type, rows in resolved_by_type.items():
            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MATCH (t:Symbol {{id: row.target_symbol_id}})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language
                """,
                repo_id=repo_id,
                rows=rows,
            )

        for relation_type, rows in unresolved_by_type.items():
            session.run(
                f"""
                UNWIND $rows AS row
                MATCH (s:Symbol {{id: row.source_symbol_id}})
                MERGE (t:ExternalSymbol {{
                    repo_id: $repo_id,
                    ref: row.target_ref,
                    language: row.language
                }})
                MERGE (s)-[r:{relation_type} {{repo_id: $repo_id, relation_id: row.relation_id}}]->(t)
                SET r.path = row.path,
                    r.line = row.line,
                    r.confidence = row.confidence,
                    r.target_ref = row.target_ref,
                    r.target_kind = row.target_kind,
                    r.language = row.language
                """,
                repo_id=repo_id,
                rows=rows,
            )

    @staticmethod
    def _relation_id(relation: SemanticRelation) -> str:
        """Construye un ID determinista para evitar duplicados de relaciones."""
        payload = (
            f"{relation.repo_id}|{relation.source_symbol_id}|"
            f"{relation.relation_type}|{relation.target_symbol_id or ''}|"
            f"{relation.target_ref}|{relation.path}|{relation.line}|"
            f"{relation.language}"
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def has_repo_data(self, repo_id: str) -> bool:
        """Indica si existen nodos asociados al repositorio en Neo4j."""
        query = "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS total"
        with self.driver.session() as session:
            record = session.run(query, repo_id=repo_id).single()
            if record is None:
                return False
            return int(record.get("total", 0) or 0) > 0

    def delete_repo_subgraph(self, repo_id: str) -> int:
        """Elimina el subgrafo asociado al repo_id y devuelve nodos borrados."""
        query = "MATCH (n {repo_id: $repo_id}) DETACH DELETE n"
        with self.driver.session() as session:
            result = session.run(query, repo_id=repo_id)
            summary = result.consume()
            return int(summary.counters.nodes_deleted)

    def query_inventory(
        self,
        repo_id: str,
        target_term: str,
        module_name: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Gráfico de consulta para entidades que coincidan con el término objetivo dentro del módulo opcional."""
        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            $module_name IS NULL OR
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        OPTIONAL MATCH (f)-[:DECLARES]->(s:Symbol)
        WITH f,
             collect({
                 name: toLower(coalesce(s.name, '')),
                 type: toLower(coalesce(s.type, ''))
             }) AS symbols,
             split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name,
             toLower($target_term) AS target
        WHERE (
            toLower(f.path) CONTAINS target OR
            toLower(file_name) CONTAINS target OR
            any(symbol IN symbols WHERE
                (
                    symbol.type IN [
                        'class', 'interface', 'struct', 'enum',
                        'trait', 'record', 'type', 'component'
                    ]
                    AND symbol.name CONTAINS target
                )
                OR symbol.type = target
            )
        )
        RETURN DISTINCT
            file_name AS label,
            f.path AS path,
            'file' AS kind,
            1 AS start_line,
            1 AS end_line
        ORDER BY path
        SKIP $offset
        LIMIT $limit
        """
        with self.driver.session() as session:
            records = session.run(
                query,
                repo_id=repo_id,
                target_term=target_term,
                module_name=module_name,
                limit=limit,
                offset=offset,
            )
            return [record.data() for record in records]

    def query_inventory_total(
        self,
        repo_id: str,
        target_term: str,
        module_name: str | None = None,
    ) -> int:
        """Cuente las entidades del inventario del gráfico que coincidan con el término objetivo y el filtro del módulo."""
        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            $module_name IS NULL OR
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        OPTIONAL MATCH (f)-[:DECLARES]->(s:Symbol)
        WITH f,
             collect({
                 name: toLower(coalesce(s.name, '')),
                 type: toLower(coalesce(s.type, ''))
             }) AS symbols,
             split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name,
             toLower($target_term) AS target
        WHERE (
            toLower(f.path) CONTAINS target OR
            toLower(file_name) CONTAINS target OR
            any(symbol IN symbols WHERE
                (
                    symbol.type IN [
                        'class', 'interface', 'struct', 'enum',
                        'trait', 'record', 'type', 'component'
                    ]
                    AND symbol.name CONTAINS target
                )
                OR symbol.type = target
            )
        )
        RETURN count(DISTINCT f.path) AS total
        """
        with self.driver.session() as session:
            record = session.run(
                query,
                repo_id=repo_id,
                target_term=target_term,
                module_name=module_name,
            ).single()
            if record is None:
                return 0
            return int(record.get("total", 0) or 0)

    def query_module_files(
        self,
        repo_id: str,
        module_name: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Enumere archivos dentro de una ruta de módulo para solicitudes de inventario amplias."""
        query = """
        MATCH (f:File {repo_id: $repo_id})
        WHERE (
            f.path STARTS WITH $module_name + '/' OR
            f.path CONTAINS '/' + $module_name + '/' OR
            split(f.path, '/')[0] = $module_name
        )
        WITH f, split(f.path, '/')[size(split(f.path, '/')) - 1] AS file_name
        RETURN DISTINCT
            file_name AS label,
            f.path AS path,
            'file' AS kind,
            1 AS start_line,
            1 AS end_line
        ORDER BY path
        SKIP $offset
        LIMIT $limit
        """
        with self.driver.session() as session:
            records = session.run(
                query,
                repo_id=repo_id,
                module_name=module_name,
                limit=limit,
                offset=offset,
            )
            return [record.data() for record in records]

    def query_repo_modules(self, repo_id: str) -> list[str]:
        """Lista módulos persistidos del repositorio sin depender del workspace."""
        query = """
        MATCH (:Repo {id: $repo_id})-[:HAS_MODULE]->(m:Module {repo_id: $repo_id})
        WHERE m.path <> '.'
        RETURN DISTINCT m.path AS module_path
        ORDER BY module_path ASC
        """
        with self.driver.session() as session:
            records = session.run(query, repo_id=repo_id)
            modules: list[str] = []
            for record in records:
                module_path = str(record.get("module_path", "") or "").strip()
                if module_path:
                    modules.append(module_path)
            return modules

    def query_file_purpose_summaries(
        self,
        repo_id: str,
        paths: list[str],
    ) -> dict[str, dict[str, str]]:
        """Devuelve propósitos persistidos por archivo para inventory explain."""
        normalized_paths = [path.strip() for path in paths if path and path.strip()]
        if not normalized_paths:
            return {}

        query = """
        UNWIND $paths AS requested_path
        MATCH (f:File {repo_id: $repo_id, path: requested_path})
        RETURN f.path AS path,
               f.purpose_summary AS purpose_summary,
               f.purpose_source AS purpose_source
        """
        with self.driver.session() as session:
            records = session.run(
                query,
                repo_id=repo_id,
                paths=normalized_paths,
            )
            summaries: dict[str, dict[str, str]] = {}
            for record in records:
                path = str(record.get("path", "") or "").strip()
                summary = str(record.get("purpose_summary", "") or "").strip()
                source = str(record.get("purpose_source", "") or "").strip()
                if path and summary:
                    summaries[path] = {
                        "purpose_summary": summary,
                        "purpose_source": source,
                    }
            return summaries

    def expand_symbols(
        self,
        symbol_ids: list[str],
        hops: int = 2,
        relation_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Expanda vecindad de símbolos con filtros opcionales de relación."""
        safe_hops = max(1, int(hops))
        query = f"""
        MATCH (s:Symbol)
        WHERE s.id IN $symbol_ids
        MATCH p=(s)-[*1..{safe_hops}]-(n)
        WHERE (
            $relation_types IS NULL OR
            size($relation_types) = 0 OR
            all(rel IN relationships(p) WHERE type(rel) IN $relation_types)
        )
        RETURN DISTINCT s.id as seed,
               labels(n) as labels,
               properties(n) as props,
             size(relationships(p)) as edge_count,
             [rel IN relationships(p) | type(rel)] as relation_types,
             CASE size(relationships(p))
                  WHEN 0 THEN 1.0
                  ELSE reduce(
                   confidence = 0.0,
                   rel IN relationships(p) |
                   confidence + toFloat(coalesce(rel.confidence, 1.0))
                  ) / toFloat(size(relationships(p)))
             END as relation_confidence_avg
        LIMIT $limit
        """
        with self.driver.session() as session:
            records = session.run(
                query,
                symbol_ids=symbol_ids,
                relation_types=relation_types,
                limit=max(1, int(limit)),
            )
            return [record.data() for record in records]
