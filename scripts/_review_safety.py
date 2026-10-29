"""Read-only path preflight shared by review clients (not an OS sandbox)."""
import os
from pathlib import Path


def protected_roots(root):
    """Locate this checkout, its main checkout and Git administration directories.

    Only Git path metadata is read; never run Git or inspect configuration.
    """
    root = Path(root).resolve()
    protected = {root}
    dotgit = root / ".git"
    if dotgit.is_file():
        text = dotgit.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir: ") or "\n" in text:
            raise ValueError("无法确定 Git 管理目录，拒绝写出。")
        gitdir = (root / text[len("gitdir: "):]).resolve()
    elif dotgit.is_dir():
        gitdir = dotgit.resolve()
    elif dotgit.is_symlink():
        raise ValueError("Git 路径元数据为悬空链接，拒绝写出。")
    else:
        return protected
    protected.add(gitdir)
    common_file = gitdir / "commondir"
    if common_file.exists():
        text = common_file.read_text(encoding="utf-8").strip()
        if not text or "\n" in text:
            raise ValueError("无法确定 Git 共享目录，拒绝写出。")
        common = (gitdir / text).resolve()
    else:
        common = gitdir
    protected.add(common)
    if common.name == ".git":
        protected.add(common.parent)
    return protected


def outside_repository(path, root):
    resolved = Path(path).resolve()
    if any(resolved == p or p in resolved.parents for p in protected_roots(root)):
        raise ValueError("审查产物不得写入当前仓库、原主仓库或 Git 管理目录。")
    return resolved


def task_directory(path, root):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError("任务目录应为绝对路径。")
    path = outside_repository(path, root)
    if not path.is_dir():
        raise ValueError("任务目录不存在或不是目录。")
    if not os.access(path, os.W_OK | os.X_OK):
        raise ValueError("任务目录不可写入或访问。")
    return path


def output_path(path, root, *, base=None, directory=False):
    """Reject symlink/dangling/non-directory artifact components before writing.

    A resolved task base may have arrived via a system temporary-directory alias.
    Without a task base (shared ledger), only system temporary aliases are allowed.
    Callers must still handle write failures and concurrent filesystem changes.
    """
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    anchor = Path(base) if base is not None else Path(path.anchor)
    try:
        parts = path.relative_to(anchor).parts
    except ValueError:
        raise ValueError("输出路径不在指定任务目录内。")
    if any(part in (".", "..") for part in parts):
        raise ValueError("输出路径不能含上级目录组件。")
    current = anchor
    for index, part in enumerate(parts):
        current = current / part
        last = index == len(parts) - 1
        if current.is_symlink():
            temp_alias = (base is None and not last and
                          current in [Path("/tmp"), Path("/var"), Path("/var/tmp")])
            if not temp_alias:
                raise ValueError("输出路径组件不能是软链接或悬空链接。")
        if current.exists() and ((not last or directory) and not current.is_dir()):
            raise ValueError("输出目录组件不是目录。")
        if last and not directory and current.exists() and not current.is_file():
            raise ValueError("输出账本不是普通文件。")
    resolved = outside_repository(path, root)
    parent = resolved if directory and resolved.exists() else resolved.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError("输出父目录不可写入或访问。")
    if not directory and resolved.exists() and not os.access(resolved, os.W_OK):
        raise ValueError("输出文件不可写入。")
    return resolved
