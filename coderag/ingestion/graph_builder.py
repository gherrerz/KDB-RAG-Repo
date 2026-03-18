"""Integración de Neo4j para la construcción de gráficos de conocimiento de código."""

from threading import Lock
from typing import Any

from neo4j import GraphDatabase

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.core.settings import get_settings


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
    ) -> None:
        """Inserte nodos, archivos, módulos y símbolos del gráfico del repositorio."""
        file_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (m:Module {repo_id: $repo_id, path: $module_path})
        MERGE (r)-[:HAS_MODULE]->(m)
        MERGE (f:File {repo_id: $repo_id, path: $path})
        SET f.language = $language
        MERGE (m)-[:CONTAINS]->(f)
        """
        symbol_query = """
        MERGE (r:Repo {id: $repo_id})
        MERGE (f:File {repo_id: $repo_id, path: $path})
        MERGE (s:Symbol {id: $symbol_id})
        SET s.name = $symbol_name,
            s.name_lc = toLower($symbol_name),
            s.type = $symbol_type,
            s.start_line = $start_line,
            s.end_line = $end_line
        MERGE (f)-[:DECLARES]->(s)
        """
        with self.driver.session() as session:
            for file_obj in scanned_files:
                module_path = file_obj.path.split("/", 1)[0] if "/" in file_obj.path else "."
                session.run(
                    file_query,
                    repo_id=repo_id,
                    module_path=module_path,
                    path=file_obj.path,
                    language=file_obj.language,
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

    def expand_symbols(self, symbol_ids: list[str], hops: int = 2) -> list[dict[str, Any]]:
        """Expanda la vecindad del gráfico para símbolos usando una ruta de longitud variable."""
        query = """
        MATCH (s:Symbol)
        WHERE s.id IN $symbol_ids
        MATCH p=(s)-[*1..$hops]-(n)
        RETURN DISTINCT s.id as seed,
               labels(n) as labels,
               properties(n) as props
        LIMIT 200
        """
        with self.driver.session() as session:
            records = session.run(query, symbol_ids=symbol_ids, hops=hops)
            return [record.data() for record in records]
