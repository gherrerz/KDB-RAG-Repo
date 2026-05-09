"""Migra snapshots BM25 existentes al LexicalStore en PostgreSQL.

Uso:
    python scripts/migrate_bm25_to_lexical.py

Variables de entorno requeridas:
    POSTGRES_URL  — DSN de PostgreSQL destino
    BM25_DIR      — directorio raíz de snapshots BM25 (default: ./storage/bm25)

El script es idempotente: re-ejecutarlo actualiza los documentos existentes
gracias al ON CONFLICT DO UPDATE en LexicalStore.index_documents().
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    postgres_url = os.environ.get("POSTGRES_URL", "").strip()
    if not postgres_url:
        print("ERROR: La variable POSTGRES_URL es obligatoria.", file=sys.stderr)
        sys.exit(1)

    # Añadir src/ al path para importar módulos del proyecto
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from coderag.storage.lexical_store import LexicalStore

    bm25_dir_env = os.environ.get("BM25_DIR", "").strip()
    if bm25_dir_env:
        bm25_dir = Path(bm25_dir_env)
    else:
        bm25_dir = repo_root / "storage" / "bm25"

    if not bm25_dir.is_dir():
        print(f"Directorio BM25 no encontrado: {bm25_dir}", file=sys.stderr)
        sys.exit(1)

    fts_language = os.environ.get("LEXICAL_FTS_LANGUAGE", "english").strip()
    store = LexicalStore(postgres_url, fts_language)

    snapshots = sorted(bm25_dir.glob("*.json"))
    if not snapshots:
        print(f"No se encontraron snapshots BM25 en {bm25_dir}.")
        sys.exit(0)

    print(f"Migrando {len(snapshots)} snapshot(s) desde {bm25_dir} ...")
    total_docs = 0
    errors = 0

    for snapshot_path in snapshots:
        repo_id = snapshot_path.stem
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            docs: list[str] = data.get("docs") or []
            metadatas: list[dict] = data.get("metadatas") or []

            if not docs or len(docs) != len(metadatas):
                print(f"  [SKIP] {repo_id} — snapshot vacío o inconsistente.")
                continue

            store.index_documents(repo_id=repo_id, docs=docs, metadatas=metadatas)
            print(f"  [OK]   {repo_id} — {len(docs)} documentos indexados.")
            total_docs += len(docs)
        except Exception as exc:
            print(f"  [ERR]  {repo_id} — {exc}", file=sys.stderr)
            errors += 1

    print(
        f"\nMigración completada: {len(snapshots) - errors} repos, "
        f"{total_docs} docs totales, {errors} errores."
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
