import shutil
import tempfile
from functools import cached_property
from pathlib import Path
from typing import Optional, Dict, List, Any

from git import Repo
import subprocess

from patchagent.builder.utils import BuilderProcessError, safe_subprocess_run
from patchagent.lang import Lang
from patchagent.logger import logger
from patchagent.lsp.language import LanguageServer
from patchagent.parser import SanitizerReport


class PoC:
    def __init__(self) -> None: ...


class Builder:
    def __init__(
        self,
        project: str,
        source_path: Path,
        workspace: Optional[Path] = None,
        clean_up: bool = True,
    ):
        self.project = project
        self.org_source_path = source_path
        self.workspace = workspace or Path(tempfile.mkdtemp())

        if clean_up:
            shutil.rmtree(self.workspace, ignore_errors=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    @cached_property
    def source_path(self) -> Path:
        target_path = self.workspace / "immutable" / self.org_source_path.name
        if not target_path.is_dir():
            # shutil.copytree(self.org_source_path, target_path, symlinks=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-r", str(self.org_source_path), str(target_path)], check=True)

        return target_path

    @cached_property
    def source_repo(self) -> Repo:
        target_path = self.workspace / "git" / self.org_source_path.name
        if not target_path.is_dir():
            # shutil.copytree(self.source_path, target_path, symlinks=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-r", str(self.source_path), str(target_path)], check=True)

        if (target_path / ".git").is_dir():
            shutil.rmtree(target_path / ".git")

        repo = Repo.init(target_path)

        # This is a workaround to prevent repo.index.add from altering file permissions
        # when files are added to the Git index
        repo.git.add(repo.untracked_files)
        repo.index.commit("Initial commit")
        return repo

    @cached_property
    def language(self) -> Lang:
        raise NotImplementedError("language not implemented")

    @cached_property
    def language_server(self) -> LanguageServer:
        raise NotImplementedError("language_server not implemented")

    def check_patch(self, patch: str) -> None:
        logger.info("[🔍] Checking patch")

        self.source_repo.git.reset("--hard")
        self.source_repo.git.clean("-fdx")

        safe_subprocess_run(
            ["git", "apply"],  # empty patch is not allowed
            Path(self.source_repo.working_dir),
            input=patch.encode(),
        )

    def format_patch(self, patch: str) -> Optional[str]:
        logger.info("[🩹] Formatting patch")

        self.source_repo.git.reset("--hard")
        self.source_repo.git.clean("-fdx")

        try:
            safe_subprocess_run(
                ["patch", "-F", "3", "--no-backup-if-mismatch", "-p1"],
                Path(self.source_repo.working_dir),
                input=patch.encode(),
            )

            return safe_subprocess_run(["git", "diff"], Path(self.source_repo.working_dir)).decode(errors="ignore")
        except BuilderProcessError:
            return None

    def build(self, patch: str = "") -> None:
        raise NotImplementedError("build not implemented")

    def replay(self, poc: PoC, patch: str = "") -> Optional[SanitizerReport]:
        raise NotImplementedError("replay not implemented")

    def function_test(self, patch: str = "") -> None: ...

     # === 新增调试接口 ===
    def get_develop_debug_paths(self) -> Dict[str, Any]:
        """
        获取调试环境的路径映射信息。
        必须返回包含以下键的字典:
        - "source_map": (remote_src_path, local_src_path)
        - "out_root_map": (remote_out_prefix, local_out_prefix)
        - "develop_source_path_obj": Path对象，指向开发环境源码根目录
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support debugging yet.")

    def resolve_poc_path(self, arg_token: str, pocs: List[PoC]) -> str:
        """
        将构建/运行环境中的特殊文件路径（如 /testcase）解析为 Agent 环境中的真实路径。
        默认实现：不进行转换，直接返回原参数。
        """
        return arg_token
