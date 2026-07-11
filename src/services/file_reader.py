from pathlib import Path

from src.models.file_context import FileContext


class InvalidFilePathError(ValueError):
    pass


class SourceFileNotFoundError(FileNotFoundError):
    pass


def resolve_source_file(
    repository_root: Path,
    file_path: str,
) -> Path:
    repository_root = repository_root.resolve()

    requested_path = Path(file_path)

    if requested_path.is_absolute():
        raise InvalidFilePathError(
            "file_path must be relative to the repository root"
        )

    resolved_path = (repository_root / requested_path).resolve()

    try:
        resolved_path.relative_to(repository_root)
    except ValueError as exc:
        raise InvalidFilePathError(
            "file_path points outside the repository root"
        ) from exc

    if not resolved_path.exists():
        raise SourceFileNotFoundError(
            f"File does not exist: {file_path}"
        )

    if not resolved_path.is_file():
        raise InvalidFilePathError(
            f"Path is not a regular file: {file_path}"
        )

    return resolved_path


def read_file_context(
    *,
    repository: str,
    repository_root: Path,
    file_path: str,
    line_number: int,
    lines_before: int = 20,
    lines_after: int = 20,
) -> FileContext:
    if line_number < 1:
        raise ValueError("line_number must be at least 1")
    if lines_before < 0 or lines_after < 0:
        raise ValueError("context line counts must not be negative")

    source_file = resolve_source_file(
        repository_root=repository_root,
        file_path=file_path,
    )

    try:
        text = source_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = source_file.read_text(
            encoding="utf-8",
            errors="replace",
        )

    lines = text.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        raise ValueError(f"File is empty: {file_path}")

    if line_number > total_lines:
        raise ValueError(
            f"line_number {line_number} exceeds "
            f"file length {total_lines}"
        )

    target_index = line_number - 1
    start_index = max(0, target_index - lines_before)
    end_index = min(total_lines, target_index + lines_after + 1)

    context_lines: list[str] = []

    for idx in range(start_index, end_index):
        actual_line_number = idx + 1
        marker = ">" if actual_line_number == line_number else " "

        context_lines.append(
            f"{marker}{actual_line_number:5d} | {lines[idx]}"
        )

    return FileContext(
        repository=repository,
        file_path=file_path,
        target_line=line_number,
        start_line=start_index + 1,
        end_line=end_index,
        total_lines=total_lines,
        content="\n".join(context_lines),
        truncated=start_index > 0 or end_index < total_lines,
    )
