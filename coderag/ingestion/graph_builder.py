"""Neo4j integration for code knowledge graph construction."""

from typing import Any

from neo4j import GraphDatabase

from coderag.core.models import ScannedFile, SymbolChunk
from coderag.core.settings import get_settings


class GraphBuilder:
    """Graph builder to store files, symbols, and relationships."""

    def __init__(self) -> None:
        """Create Neo4j driver from settings."""
        settings = get_settings()
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def close(self) -> None:
        """Close Neo4j driver."""
        self.driver.close()

    def upsert_repo_graph(
        self,
        repo_id: str,
        scanned_files: list[ScannedFile],
        symbols: list[SymbolChunk],
    ) -> None:
        """Insert repository graph nodes, files, modules, and symbols."""
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

    def query_inventory(
        self,
        repo_id: str,
        target_term: str,
        module_name: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query graph for entities matching target term within optional module."""
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
        """Count graph inventory entities matching target term and module filter."""
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
        """List files within a module path for broad inventory requests."""
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
        """Expand graph neighborhood for symbols using variable-length path."""
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
