from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass
class LoginSource:
    group: str
    host: str
    kind: Literal["wtmp", "btmp"]


def _has_kind_files(host_dir: Path, kind: str) -> bool:
    if (host_dir / kind).is_file():
        return True
    return any(host_dir.glob(f"{kind}.[0-9]*"))


def build_index(base_path: Path) -> list[LoginSource]:
    """Discover (group, host, kind) triples with at least one wtmp/btmp file.

    Returns an empty list (never raises) if `base_path` doesn't exist, so
    wtmp/btmp support degrades to a no-op when unconfigured — matching how
    sar_index.build_index behaves when SAR_BASE_PATH is missing.
    """
    if not base_path.is_dir():
        return []
    entries: list[LoginSource] = []
    for group_dir in sorted(p for p in base_path.iterdir() if p.is_dir()):
        for host_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            for kind in ("wtmp", "btmp"):
                if _has_kind_files(host_dir, kind):
                    entries.append(
                        LoginSource(group=group_dir.name, host=host_dir.name, kind=kind)
                    )
    return entries
