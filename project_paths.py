import os
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_project_path(path_value: str = "", default: str = "") -> Path:
    raw_value = str(path_value or default or "").strip()
    if not raw_value:
        return get_project_root()
    path = Path(raw_value)
    if not path.is_absolute():
        path = get_project_root() / path
    return path


def get_instance_name() -> str:
    return str(os.getenv("XIANYU_INSTANCE", "")).strip()


def get_browser_name() -> str:
    return str(os.getenv("XIANYU_BROWSER_NAME", "")).strip()


def get_env_file_path() -> Path:
    return resolve_project_path(os.getenv("XIANYU_ENV_FILE", ""), ".env")


def get_env_example_paths() -> list[Path]:
    env_path = get_env_file_path()
    candidates: list[Path] = []

    env_name = env_path.name
    if env_name and not env_name.endswith(".example"):
        candidates.append(env_path.with_name(f"{env_name}.example"))
    candidates.append(get_project_root() / ".env.example")

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = str(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(item)
    return unique


def get_data_dir_path() -> Path:
    return resolve_project_path(os.getenv("XIANYU_DATA_DIR", ""), "data")


def get_data_file_path(*parts: str) -> Path:
    return get_data_dir_path().joinpath(*parts)
