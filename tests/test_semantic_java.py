"""Tests for Java semantic relation extraction phase 1."""

from coderag.core.models import ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_java import extract_java_semantic_relations


def test_extract_java_semantic_relations_core_types() -> None:
    """Extracts IMPORTS, EXTENDS, IMPLEMENTS and CALLS for Java basics."""
    content = (
        "package demo;\n"
        "import java.util.List;\n\n"
        "public interface Service {\n"
        "    void run();\n"
        "}\n\n"
        "public class Base {\n"
        "    public void helper() {}\n"
        "}\n\n"
        "public class Impl extends Base implements Service {\n"
        "    public void run() {\n"
        "        helper();\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/Impl.java", language="java", content=content)
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)

    relations = extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(item.relation_type == "IMPORTS" for item in relations)
    assert any(
        item.relation_type == "EXTENDS" and item.target_ref == "Base"
        for item in relations
    )
    assert any(
        item.relation_type == "IMPLEMENTS" and item.target_ref == "Service"
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS" and item.target_ref == "helper"
        for item in relations
    )


def test_extract_java_semantic_relations_resolves_cross_file_targets() -> None:
    """Resuelve target_symbol_id de imports y tipos en archivos Java separados."""
    api_content = (
        "package com.acme.api;\n\n"
        "public interface Service {\n"
        "    void run();\n"
        "}\n"
    )
    impl_content = (
        "package com.acme.impl;\n"
        "import com.acme.api.Service;\n"
        "import com.acme.impl.Base;\n\n"
        "public class Base {\n"
        "    public void helper() {}\n"
        "}\n\n"
        "public class Impl extends Base implements Service {\n"
        "    public void run() {\n"
        "        helper();\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/com/acme/api/Service.java", language="java", content=api_content),
        ScannedFile(path="src/com/acme/impl/Impl.java", language="java", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)

    service_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Service.java") and item.symbol_name == "Service"
    )
    base_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Impl.java") and item.symbol_name == "Base"
    )

    relations = extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPORTS"
        and item.target_ref == "com.acme.api.Service"
        and item.target_symbol_id == service_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "EXTENDS"
        and item.target_ref == "Base"
        and item.target_symbol_id == base_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "IMPLEMENTS"
        and item.target_ref == "Service"
        and item.target_symbol_id == service_symbol_id
        for item in relations
    )


def test_extract_java_semantic_relations_resolves_wildcard_import_targets() -> None:
    """Resuelve targets usando imports wildcard dentro del mismo repositorio."""
    api_content = (
        "package com.acme.api;\n\n"
        "public interface Service {\n"
        "    void run();\n"
        "}\n"
    )
    impl_content = (
        "package com.acme.impl;\n"
        "import com.acme.api.*;\n\n"
        "public class Impl implements Service {\n"
        "    public void run() {}\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/com/acme/api/Service.java", language="java", content=api_content),
        ScannedFile(path="src/com/acme/impl/Impl.java", language="java", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)
    service_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Service.java") and item.symbol_name == "Service"
    )

    relations = extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPLEMENTS"
        and item.target_ref == "Service"
        and item.target_symbol_id == service_symbol_id
        for item in relations
    )


def test_extract_java_semantic_relations_resolves_static_import_member() -> None:
    """Resuelve llamadas con import static explícito al tipo dueño."""
    utils_content = (
        "package com.acme.util;\n\n"
        "public class MathUtil {\n"
        "    public static int max(int a, int b) { return a > b ? a : b; }\n"
        "}\n"
    )
    use_content = (
        "package com.acme.app;\n"
        "import static com.acme.util.MathUtil.max;\n\n"
        "public class Use {\n"
        "    public int run() {\n"
        "        return max(1, 2);\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/com/acme/util/MathUtil.java", language="java", content=utils_content),
        ScannedFile(path="src/com/acme/app/Use.java", language="java", content=use_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)
    util_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("MathUtil.java") and item.symbol_name == "MathUtil"
    )

    relations = extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "max"
        and item.target_symbol_id == util_symbol_id
        for item in relations
    )


def test_extract_java_semantic_relations_resolves_static_wildcard_import() -> None:
    """Resuelve llamadas con import static wildcard al tipo dueño."""
    utils_content = (
        "package com.acme.util;\n\n"
        "public class MathUtil {\n"
        "    public static int min(int a, int b) { return a < b ? a : b; }\n"
        "}\n"
    )
    use_content = (
        "package com.acme.app;\n"
        "import static com.acme.util.MathUtil.*;\n\n"
        "public class Use {\n"
        "    public int run() {\n"
        "        return min(1, 2);\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/com/acme/util/MathUtil.java", language="java", content=utils_content),
        ScannedFile(path="src/com/acme/app/Use.java", language="java", content=use_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)
    util_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("MathUtil.java") and item.symbol_name == "MathUtil"
    )

    relations = extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "min"
        and item.target_symbol_id == util_symbol_id
        for item in relations
    )


def test_extract_java_semantic_relations_exposes_resolution_sources() -> None:
    """Reporta conteos por origen de resolución para observabilidad Java."""
    util_content = (
        "package com.acme.util;\n\n"
        "public class MathUtil {\n"
        "    public static int max(int a, int b) { return a > b ? a : b; }\n"
        "}\n"
    )
    impl_content = (
        "package com.acme.impl;\n"
        "import static com.acme.util.MathUtil.max;\n"
        "import com.acme.util.MathUtil;\n\n"
        "public class Impl {\n"
        "    public int run() {\n"
        "        return max(1, 2);\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/com/acme/util/MathUtil.java", language="java", content=util_content),
        ScannedFile(path="src/com/acme/impl/Impl.java", language="java", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-java", scanned_files=scanned_files)
    stats: dict[str, int] = {}

    extract_java_semantic_relations(
        repo_id="repo-java",
        scanned_files=scanned_files,
        symbols=symbols,
        resolution_stats_sink=stats,
    )

    assert stats.get("static_import_member", 0) >= 1
    assert stats.get("fqcn", 0) >= 1
