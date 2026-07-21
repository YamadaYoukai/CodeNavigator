import os
from pathlib import Path


DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[2] / "public-repos"


def get_base_repository() -> Path:
    return Path(
        os.getenv("REPOSITORY_ROOT", DEFAULT_REPOSITORY_ROOT)
    ).expanduser().resolve()


def get_repository_root(repository: str) -> Path:
    base_repository = get_base_repository()
    repository_path = (base_repository / repository).resolve()

    if not repository_path.is_relative_to(base_repository):
        raise ValueError(f"非法的 repository 路径: {repository}")

    if not repository_path.is_dir():
        raise FileNotFoundError(f"Repository 不存在: {repository_path}")

    return repository_path
