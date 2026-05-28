"""Tests for Swift semantic relation extraction phase 1."""

from coderag.core.models import ScannedFile
from coderag.ingestion.chunker import extract_symbol_chunks
from coderag.ingestion.semantic_swift import extract_swift_semantic_relations


def test_extract_swift_semantic_relations_core_types() -> None:
    """Extract IMPORTS, EXTENDS, IMPLEMENTS and CALLS for Swift basics."""
    content = (
        "import Foundation\n\n"
        "protocol Service {\n"
        "    func run()\n"
        "}\n\n"
        "class Base {\n"
        "    func helper() {}\n"
        "}\n\n"
        "class Impl: Base, Service {\n"
        "    func run() {\n"
        "        helper()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [ScannedFile(path="src/Impl.swift", language="swift", content=content)]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
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


def test_extract_swift_semantic_relations_resolves_cross_file_targets() -> None:
    """Resolve Swift supertypes and calls across repository files."""
    service_content = "protocol Service {\n    func run()\n}\n"
    base_content = "class Base {\n    func helper() {}\n}\n"
    impl_content = (
        "class Impl: Base, Service {\n"
        "    func run() {\n"
        "        helper()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/Service.swift", language="swift", content=service_content),
        ScannedFile(path="src/Base.swift", language="swift", content=base_content),
        ScannedFile(path="src/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    service_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Service.swift") and item.symbol_name == "Service"
    )
    base_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Base.swift") and item.symbol_name == "Base"
    )
    helper_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Base.swift") and item.symbol_name == "helper"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
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
    assert any(
        item.relation_type == "CALLS"
        and item.target_ref == "helper"
        and item.target_symbol_id == helper_symbol_id
        for item in relations
    )


def test_extract_swift_semantic_relations_supports_extension_contexts() -> None:
    """Resolve IMPLEMENTS and CALLS from methods declared inside extensions."""
    runnable_content = "protocol Runnable {\n    func execute()\n}\n"
    base_content = "class Base {\n    func helper() {}\n}\n"
    demo_content = "class Demo {}\n"
    extension_content = (
        "extension Demo: Runnable {\n"
        "    func execute() {\n"
        "        helper()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(path="src/Runnable.swift", language="swift", content=runnable_content),
        ScannedFile(path="src/Base.swift", language="swift", content=base_content),
        ScannedFile(path="src/Demo.swift", language="swift", content=demo_content),
        ScannedFile(
            path="src/Demo+Runnable.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    runnable_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Runnable.swift") and item.symbol_name == "Runnable"
    )
    helper_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Base.swift") and item.symbol_name == "helper"
    )
    extension_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Demo+Runnable.swift")
        and item.symbol_name == "Demo"
        and item.symbol_type == "extension"
    )
    execute_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Demo+Runnable.swift") and item.symbol_name == "execute"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPLEMENTS"
        and item.source_symbol_id == extension_symbol_id
        and item.target_ref == "Runnable"
        and item.target_symbol_id == runnable_symbol_id
        for item in relations
    )
    assert any(
        item.relation_type == "CALLS"
        and item.source_symbol_id == execute_symbol_id
        and item.target_ref == "helper"
        and item.target_symbol_id == helper_symbol_id
        for item in relations
    )


def test_extract_swift_semantic_relations_prefers_imported_module_targets() -> None:
    """Resolve duplicated Swift type names using imported module path hints."""
    preferred_service_content = "protocol Service {\n    func run()\n}\n"
    other_service_content = "protocol Service {\n    func run()\n}\n"
    impl_content = (
        "import Payments\n\n"
        "class Impl: Service {\n"
        "    func run() {}\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=preferred_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=other_service_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    preferred_service_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "Service"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )

    assert any(
        item.relation_type == "IMPLEMENTS"
        and item.target_ref == "Service"
        and item.target_symbol_id == preferred_service_symbol_id
        and item.resolution_method == "import_module_path"
        for item in relations
    )


def test_extract_swift_semantic_relations_resolves_qualified_calls_by_owner_hint() -> None:
    """Resolve duplicated Swift methods using owner and module hints from calls."""
    payments_service_content = (
        "protocol Service {\n"
        "    static func execute()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    static func execute()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Impl {\n"
        "    func run() {\n"
        "        Service.execute()\n"
        "        Payments.Service.execute()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_execute_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "execute"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    execute_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "execute"
    ]

    assert any(
        item.target_symbol_id == payments_execute_symbol_id
        and item.resolution_method == "owner_import_module_path"
        for item in execute_relations
    )
    assert any(
        item.target_symbol_id == payments_execute_symbol_id
        and item.resolution_method == "owner_path"
        for item in execute_relations
    )


def test_extract_swift_semantic_relations_infers_receiver_types_for_calls() -> None:
    """Resolve Swift calls through typed parameters and simple local aliases."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "    func execute()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Impl {\n"
        "    func run(dependency: Service, another value: Payments.Service) {\n"
        "        let local: Service = dependency\n"
        "        var typed = dependency\n"
        "        let namespaced: Payments.Service = value\n"
        "        dependency.call()\n"
        "        local.call()\n"
        "        typed.call()\n"
        "        value.execute()\n"
        "        namespaced.execute()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )
    payments_execute_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "execute"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]
    execute_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "execute"
    ]

    assert len(call_relations) == 3
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method == "owner_import_module_path"
        for item in call_relations
    )
    assert len(execute_relations) == 2
    assert all(
        item.target_symbol_id == payments_execute_symbol_id
        for item in execute_relations
    )


def test_extract_swift_semantic_relations_infers_receiver_types_from_properties() -> None:
    """Resolve Swift calls through typed class properties and self access."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "    func execute()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Impl {\n"
        "    let dependency: Service\n"
        "    var payments: Payments.Service\n\n"
        "    func run() {\n"
        "        let local = dependency\n"
        "        let localSelf = self.dependency\n"
        "        dependency.call()\n"
        "        self.dependency.call()\n"
        "        local.call()\n"
        "        localSelf.call()\n"
        "        payments.execute()\n"
        "        self.payments.execute()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )
    payments_execute_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "execute"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]
    execute_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "execute"
    ]

    assert len(call_relations) == 4
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert len(execute_relations) == 2
    assert all(
        item.target_symbol_id == payments_execute_symbol_id
        for item in execute_relations
    )


def test_extract_swift_semantic_relations_reuses_type_properties_in_extensions() -> None:
    """Resolve Swift extension calls using properties declared on the base type."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    demo_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let dependency: Service\n"
        "}\n"
    )
    extension_content = (
        "import Payments\n\n"
        "extension Demo {\n"
        "    func run() {\n"
        "        dependency.call()\n"
        "        self.dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=demo_content),
        ScannedFile(
            path="src/App/Demo+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_infers_receivers_from_base_classes() -> None:
    """Resolve Swift calls through properties inherited from a base class."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    base_content = (
        "import Payments\n\n"
        "class Base {\n"
        "    let dependency: Service\n"
        "}\n"
    )
    impl_content = (
        "class Impl: Base {\n"
        "    func run() {\n"
        "        dependency.call()\n"
        "        self.dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Base.swift", language="swift", content=base_content),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_infers_receivers_from_protocol_requirements() -> None:
    """Resolve Swift calls through protocol property requirements in extensions."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "import Payments\n\n"
        "protocol HasDependency {\n"
        "    var dependency: Service { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency {\n"
        "    func run() {\n"
        "        dependency.call()\n"
        "        self.dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_disambiguates_inherited_base_by_imported_module() -> None:
    """Resolve inherited Swift receivers when the base type is duplicated by module."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    payments_base_content = (
        "import Payments\n\n"
        "class Base {\n"
        "    let dependency: Service\n"
        "}\n"
    )
    analytics_base_content = (
        "import Analytics\n\n"
        "class Base {\n"
        "    let dependency: Service\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Impl: Base {\n"
        "    func run() {\n"
        "        dependency.call()\n"
        "        self.dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/Payments/Base.swift",
            language="swift",
            content=payments_base_content,
        ),
        ScannedFile(
            path="src/Analytics/Base.swift",
            language="swift",
            content=analytics_base_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_infers_receivers_from_associatedtype_constraints() -> None:
    """Resolve Swift calls through associated types constrained in a protocol."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "import Payments\n\n"
        "protocol HasDependency {\n"
        "    associatedtype Dependency: Service\n"
        "    var dependency: Dependency { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency {\n"
        "    func run() {\n"
        "        dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 1
    assert call_relations[0].target_symbol_id == payments_call_symbol_id
    assert call_relations[0].resolution_method in {
        "owner_path",
        "owner_import_module_path",
    }


def test_extract_swift_semantic_relations_propagates_associatedtype_constraints_to_local_aliases() -> None:
    """Resolve Swift calls through local aliases seeded by associated type constraints."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "import Payments\n\n"
        "protocol HasDependency {\n"
        "    associatedtype Dependency: Service\n"
        "    var dependency: Dependency { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency {\n"
        "    func run() {\n"
        "        let current = dependency\n"
        "        let next = current\n"
        "        next.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 1
    assert call_relations[0].target_symbol_id == payments_call_symbol_id
    assert call_relations[0].resolution_method in {
        "owner_path",
        "owner_import_module_path",
    }


def test_extract_swift_semantic_relations_propagates_self_property_aliases() -> None:
    """Resolve Swift calls through aliases seeded from self.property receivers."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Impl {\n"
        "    let currentDependency: Service\n\n"
        "    func run() {\n"
        "        let current = self.currentDependency\n"
        "        let next = current\n"
        "        next.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Impl.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 1
    assert call_relations[0].target_symbol_id == payments_call_symbol_id
    assert call_relations[0].resolution_method in {
        "owner_path",
        "owner_import_module_path",
    }


def test_extract_swift_semantic_relations_infers_receivers_from_extension_where_constraints() -> None:
    """Resolve Swift calls through associated types constrained in extension where clauses."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "protocol HasDependency {\n"
        "    associatedtype Dependency\n"
        "    var dependency: Dependency { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency where Dependency == Payments.Service {\n"
        "    func runPayments() {\n"
        "        dependency.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 1
    assert call_relations[0].target_symbol_id == payments_call_symbol_id
    assert call_relations[0].resolution_method == "owner_path"


def test_extract_swift_semantic_relations_propagates_where_constraints_to_local_aliases() -> None:
    """Resolve Swift calls through alias chains seeded by extension where constraints."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "protocol HasDependency {\n"
        "    associatedtype Dependency\n"
        "    var dependency: Dependency { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency where Dependency == Payments.Service {\n"
        "    func runPayments() {\n"
        "        let current = dependency\n"
        "        let next = current\n"
        "        next.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 1
    assert call_relations[0].target_symbol_id == payments_call_symbol_id
    assert call_relations[0].resolution_method == "owner_path"


def test_extract_swift_semantic_relations_propagates_conditional_binding_aliases() -> None:
    """Resolve Swift calls through if/guard binding aliases seeded by prior hints."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    protocol_content = (
        "import Payments\n\n"
        "protocol HasDependency {\n"
        "    associatedtype Dependency: Service\n"
        "    var dependency: Dependency { get }\n"
        "}\n"
    )
    extension_content = (
        "extension HasDependency {\n"
        "    func run() {\n"
        "        if let current = dependency {\n"
        "            current.call()\n"
        "        }\n"
        "        guard let fallback = self.dependency else {\n"
        "            return\n"
        "        }\n"
        "        fallback.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(
            path="src/App/HasDependency.swift",
            language="swift",
            content=protocol_content,
        ),
        ScannedFile(
            path="src/App/HasDependency+Run.swift",
            language="swift",
            content=extension_content,
        ),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_resolves_optional_wrappers_and_aliases() -> None:
    """Resolve Swift calls through optional receivers and simple optional aliases."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let optionalDependency: Payments.Service?\n\n"
        "    func run() {\n"
        "        optionalDependency?.call()\n"
        "        let current = optionalDependency\n"
        "        current?.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 2
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_resolves_typed_collection_wrappers() -> None:
    """Resolve Swift calls through collection element receivers and aliases."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let dependencies: [Payments.Service]\n"
        "    let arrayStyle: Array<Payments.Service>\n\n"
        "    func run() {\n"
        "        dependencies[0].call()\n"
        "        arrayStyle[0].call()\n"
        "        let current = dependencies[0]\n"
        "        current.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 3
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {"owner_path", "owner_import_module_path"}
        for item in call_relations
    )


def test_extract_swift_semantic_relations_resolves_convenience_collection_accessors() -> None:
    """Resolve Swift calls through first/last and chained optional accessors."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let dependencies: [Payments.Service]\n"
        "    let optionalDependencies: [Payments.Service]?\n\n"
        "    func run() {\n"
        "        dependencies.first?.call()\n"
        "        dependencies.last?.call()\n"
        "        optionalDependencies?.first?.call()\n"
        "        optionalDependencies?.last?.call()\n"
        "        let current = dependencies.first\n"
        "        current?.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 5
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {
            "owner_path",
            "owner_import_module_path",
            "import_module_path",
        }
        for item in call_relations
    )


def test_extract_swift_semantic_relations_resolves_collection_wrapper_accessors() -> None:
    """Resolve Swift calls through collection wrappers that preserve element type."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let dependencies: [Payments.Service]\n\n"
        "    func run() {\n"
        "        dependencies.lazy.first?.call()\n"
        "        dependencies.dropFirst().first?.call()\n"
        "        dependencies.prefix(1).first?.call()\n"
        "        let current = dependencies.lazy.first\n"
        "        current?.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 4
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {
            "owner_path",
            "owner_import_module_path",
            "import_module_path",
        }
        for item in call_relations
    )


def test_extract_swift_semantic_relations_resolves_deep_sequence_wrappers() -> None:
    """Resolve Swift calls through deeper sequence wrappers preserving elements."""
    payments_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    analytics_service_content = (
        "protocol Service {\n"
        "    func call()\n"
        "}\n"
    )
    impl_content = (
        "import Payments\n\n"
        "class Demo {\n"
        "    let dependencies: [Payments.Service]\n\n"
        "    func run() {\n"
        "        dependencies.reversed().first?.call()\n"
        "        dependencies.sorted().first?.call()\n"
        "        dependencies.filter { _ in true }.first?.call()\n"
        "        dependencies.dropFirst().reversed().first?.call()\n"
        "        let current = dependencies.reversed().first\n"
        "        current?.call()\n"
        "    }\n"
        "}\n"
    )
    scanned_files = [
        ScannedFile(
            path="src/Payments/Service.swift",
            language="swift",
            content=payments_service_content,
        ),
        ScannedFile(
            path="src/Analytics/Service.swift",
            language="swift",
            content=analytics_service_content,
        ),
        ScannedFile(path="src/App/Demo.swift", language="swift", content=impl_content),
    ]
    symbols = extract_symbol_chunks(repo_id="repo-swift", scanned_files=scanned_files)

    payments_call_symbol_id = next(
        item.id
        for item in symbols
        if item.path.endswith("Payments/Service.swift")
        and item.symbol_name == "call"
    )

    relations = extract_swift_semantic_relations(
        repo_id="repo-swift",
        scanned_files=scanned_files,
        symbols=symbols,
    )
    call_relations = [
        item
        for item in relations
        if item.relation_type == "CALLS" and item.target_ref == "call"
    ]

    assert len(call_relations) == 5
    assert all(
        item.target_symbol_id == payments_call_symbol_id for item in call_relations
    )
    assert all(
        item.resolution_method in {
            "owner_path",
            "owner_import_module_path",
            "import_module_path",
        }
        for item in call_relations
    )