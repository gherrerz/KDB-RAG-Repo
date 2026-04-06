"""Brace-aware span resolution for C-style languages."""

from dataclasses import dataclass

from src.coderag.ingestion.extractors.base import SymbolSpan


@dataclass(frozen=True)
class CommentStyle:
    """Line and block comment markers for a language."""

    line_comment: str
    block_start: str
    block_end: str


class BraceAwareSpanResolver:
    """Finds balanced brace ranges while ignoring strings/comments."""

    def __init__(self, comment_style: CommentStyle) -> None:
        """Create a resolver with language-specific comment markers."""
        self._comment_style = comment_style

    def resolve_from_start(
        self,
        lines: list[str],
        start_line: int,
        search_window: int = 12,
    ) -> SymbolSpan:
        """Resolve the symbol span from a declaration start line."""
        if not lines:
            return SymbolSpan(start_line=1, end_line=1)

        clamped_start = max(1, min(start_line, len(lines)))
        open_line = self._find_open_brace_line(lines, clamped_start, search_window)
        if open_line is None:
            return SymbolSpan(start_line=clamped_start, end_line=clamped_start)

        end_line = self._find_balanced_end_line(lines, open_line)
        if end_line is None:
            end_line = min(len(lines), open_line + 100)
        return SymbolSpan(start_line=clamped_start, end_line=end_line)

    def _find_open_brace_line(
        self,
        lines: list[str],
        start_line: int,
        search_window: int,
    ) -> int | None:
        """Find the first code-level opening brace near the declaration."""
        end = min(len(lines), start_line + max(0, search_window))
        for line_number in range(start_line, end + 1):
            line = lines[line_number - 1]
            if self._line_has_code_brace(line):
                return line_number
        return None

    def _line_has_code_brace(self, line: str) -> bool:
        """Return True when line contains '{' outside strings/comments."""
        in_single_quote = False
        in_double_quote = False
        escaped = False
        index = 0

        while index < len(line):
            char = line[index]
            if not in_single_quote and not in_double_quote:
                if line.startswith(self._comment_style.line_comment, index):
                    return False
                if line.startswith(self._comment_style.block_start, index):
                    break

            if escaped:
                escaped = False
                index += 1
                continue

            if char == "\\":
                escaped = True
                index += 1
                continue

            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                index += 1
                continue

            if char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                index += 1
                continue

            if not in_single_quote and not in_double_quote and char == "{":
                return True

            index += 1

        return False

    def _find_balanced_end_line(self, lines: list[str], open_line: int) -> int | None:
        """Return line number where balanced braces close."""
        depth = 0
        in_single_quote = False
        in_double_quote = False
        in_block_comment = False
        escaped = False

        for line_number in range(open_line, len(lines) + 1):
            line = lines[line_number - 1]
            index = 0
            while index < len(line):
                if in_block_comment:
                    if line.startswith(self._comment_style.block_end, index):
                        in_block_comment = False
                        index += len(self._comment_style.block_end)
                    else:
                        index += 1
                    continue

                if not in_single_quote and not in_double_quote:
                    if line.startswith(self._comment_style.line_comment, index):
                        break
                    if line.startswith(self._comment_style.block_start, index):
                        in_block_comment = True
                        index += len(self._comment_style.block_start)
                        continue

                char = line[index]
                if escaped:
                    escaped = False
                    index += 1
                    continue

                if char == "\\":
                    escaped = True
                    index += 1
                    continue

                if char == "'" and not in_double_quote:
                    in_single_quote = not in_single_quote
                    index += 1
                    continue

                if char == '"' and not in_single_quote:
                    in_double_quote = not in_double_quote
                    index += 1
                    continue

                if not in_single_quote and not in_double_quote:
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            return line_number

                index += 1

        return None
