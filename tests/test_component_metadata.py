"""Pruebas para heurísticas de purpose summaries por archivo."""

from coderag.ingestion.component_metadata import infer_component_purpose


def test_infer_component_purpose_recognizes_next_page_file() -> None:
    """Describe correctamente páginas App Router por convención de archivo."""
    purpose, source = infer_component_purpose(
        "app/dashboard/page.tsx",
        "export default function Page(): JSX.Element {\n  return <main />;\n}\n",
    )

    assert purpose is not None
    assert "página principal" in purpose.lower()
    assert source == "next_filename_heuristic"


def test_infer_component_purpose_recognizes_next_layout_file() -> None:
    """Describe correctamente layouts App Router por convención de archivo."""
    purpose, source = infer_component_purpose(
        "app/dashboard/layout.tsx",
        "export default function Layout({ children }: { children: React.ReactNode }) {\n  return <section>{children}</section>;\n}\n",
    )

    assert purpose is not None
    assert "layout compartido" in purpose.lower()
    assert source == "next_filename_heuristic"


def test_infer_component_purpose_recognizes_frontend_provider() -> None:
    """Describe providers frontend a partir del símbolo principal."""
    purpose, source = infer_component_purpose(
        "src/providers/AuthProvider.tsx",
        "export function AuthProvider({ children }: { children: React.ReactNode }) {\n  return <AuthContext.Provider value={{}}>{children}</AuthContext.Provider>;\n}\n",
    )

    assert purpose is not None
    assert "contexto compartido" in purpose.lower()
    assert source == "frontend_filename_heuristic"


def test_infer_component_purpose_recognizes_frontend_hook() -> None:
    """Describe hooks frontend reutilizables."""
    purpose, source = infer_component_purpose(
        "src/hooks/useSession.ts",
        "export function useSession(): string | null {\n  return null;\n}\n",
    )

    assert purpose is not None
    assert "hook reutilizable" in purpose.lower()
    assert source == "frontend_filename_heuristic"


def test_infer_component_purpose_recognizes_route_handler_file() -> None:
    """Describe rutas API de Next.js por convención de archivo."""
    purpose, source = infer_component_purpose(
        "app/api/users/route.ts",
        "export async function GET(): Promise<Response> {\n  return Response.json([]);\n}\n",
    )

    assert purpose is not None
    assert "handlers http" in purpose.lower()
    assert source == "next_filename_heuristic"