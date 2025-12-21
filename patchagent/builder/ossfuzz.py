import os
import shutil
import subprocess
from functools import cached_property
from hashlib import md5
from pathlib import Path
from typing import List, Optional

import pexpect
import yaml
import subprocess
import re

from patchagent.builder import Builder, PoC
from patchagent.builder.utils import (
    BuilderProcessError,
    DockerUnavailableError,
    safe_subprocess_run,
)
from patchagent.lang import Lang
from patchagent.logger import logger
from patchagent.lsp.hybridc import HybridCServer
from patchagent.lsp.java import JavaLanguageServer
from patchagent.lsp.language import LanguageServer
from patchagent.parser import Sanitizer, SanitizerReport, parse_sanitizer_report
from patchagent.parser.unknown import UnknownSanitizerReport
from patchagent.utils import bear_path

from patchagent.parser.utils import remove_ansi_escape # 清理颜色


class OSSFuzzPoC(PoC):
    def __init__(self, path: Path, harness_name: str):
        super().__init__()
        self.path = path
        self.harness_name = harness_name


class OSSFuzzBuilder(Builder):
    SANITIZER_MAP = {
        Sanitizer.AddressSanitizer: "address",
        Sanitizer.UndefinedBehaviorSanitizer: "undefined",
        Sanitizer.LeakAddressSanitizer: "address",
        Sanitizer.MemorySanitizer: "memory",
        # OSS-Fuzz maps Jazzer to AddressSanitizer for JVM projects
        # Reference:
        #   - https://github.com/google/oss-fuzz/blob/master/projects/hamcrest/project.yaml
        #   - https://github.com/google/oss-fuzz/blob/master/projects/apache-commons-bcel/project.yaml
        #   - https://github.com/google/oss-fuzz/blob/master/projects/threetenbp/project.yaml
        Sanitizer.JazzerSanitizer: "address",
    }

    def __init__(
        self,
        project: str,
        source_path: Path,
        fuzz_tooling_path: Path,
        sanitizers: List[Sanitizer],
        workspace: Optional[Path] = None,
        clean_up: bool = True,
        replay_poc_timeout: int = 360,
        docker_registry: Optional[str] = None
    ):
        super().__init__(project, source_path, workspace, clean_up)
        self.project = project
        self.org_fuzz_tooling_path = fuzz_tooling_path

        self.sanitizers = sanitizers
        self.replay_poc_timeout = replay_poc_timeout
        self.docker_registry = docker_registry

    @cached_property
    def fuzz_tooling_path(self) -> Path:
        target_path = self.workspace / "immutable" / self.org_fuzz_tooling_path.name
        if not target_path.is_dir():
            # shutil.copytree(self.org_fuzz_tooling_path, target_path, symlinks=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-r", str(self.org_fuzz_tooling_path), str(target_path)], check=True)

        return target_path

    def hash_patch(self, sanitizer: Sanitizer, patch: str) -> str:
        return f"{md5(patch.encode()).hexdigest()}-{self.SANITIZER_MAP[sanitizer]}"

    def build_finish_indicator(self, sanitizer: Sanitizer, patch: str) -> Path:
        return self.workspace / self.hash_patch(sanitizer, patch) / ".build"

    def _image_exists(self, image_name: str) -> bool:
        try:
            subprocess.run(
                ["docker", "image", "inspect", image_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _build_image(self, fuzz_tooling_path: Path, tries: int = 3) -> None:
        target_oss_image = f"gcr.io/oss-fuzz/{self.project}"
        
        if self.docker_registry:
            remote_image = f"{self.docker_registry}/{self.project}:latest"
            if self._image_exists(remote_image):
                logger.info(f"[🐳] Found local image: {remote_image}. Re-tagging...")
            else:
                logger.info(f"[⬇️] Pulling image from hub: {remote_image}...")
                try:
                    subprocess.run(["docker", "pull", remote_image], check=True)
                except subprocess.CalledProcessError:
                    logger.warning(f"[⚠️] Pull failed. Falling back to local build.")
                    self._build_image_locally(fuzz_tooling_path, tries)
                    return

            try:
                subprocess.run(["docker", "tag", remote_image, target_oss_image], check=True)
                return 
            except subprocess.CalledProcessError as e:
                logger.error(f"[❌] Failed to tag image: {e}")
                raise DockerUnavailableError(str(e))

        self._build_image_locally(fuzz_tooling_path, tries)

    def _build_image_locally(self, fuzz_tooling_path: Path, tries: int = 3) -> None:
        for _ in range(tries):
            process = subprocess.Popen(
                ["infra/helper.py", "build_image", "--pull", self.project],
                cwd=fuzz_tooling_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            _, stderr = process.communicate()
            if process.returncode == 0:
                return

        raise DockerUnavailableError(stderr.decode(errors="ignore"))

    def _inject_debug_flags(self, build_sh_path: Path) -> None:
        """
        向 build.sh 注入调试友好的编译选项 (-O0 -g3)。
        策略：识别 Shebang 行，并在其后立即插入环境变量设置。
        """
        if not build_sh_path.exists():
            logger.warning(f"[⚠️] build.sh not found at {build_sh_path}, skipping injection.")
            return

        try:
            content = build_sh_path.read_text(errors="ignore")
            lines = content.splitlines()
            
            # 定义我们要注入的 flag
            # -O0: 关闭优化，防止变量被优化掉 (value optimized out)
            # -g3: 包含宏定义信息，允许 GDB 使用 print MACRO
            injection_lines = [
                'export CFLAGS="$CFLAGS -O0 -g3"',
                'export CXXFLAGS="$CXXFLAGS -O0 -g3"'
            ]

            new_lines = []
            
            # 判断第一行是否是 Shebang (#!/bin/bash ...)
            if lines and lines[0].startswith("#!"):
                new_lines.append(lines[0])       # 保留 Shebang
                new_lines.extend(injection_lines) # 插入 flag
                new_lines.extend(lines[1:])      # 追加剩余内容
            else:
                # 如果没有 Shebang，直接插在最前面
                new_lines.extend(injection_lines)
                new_lines.extend(lines)

            # 写回文件
            build_sh_path.write_text("\n".join(new_lines) + "\n")
            logger.info(f"[💉] Injected debug flags (-O0 -g3) into {build_sh_path.name}")
            
        except Exception as e:
            logger.error(f"[❌] Failed to inject debug flags: {e}")

    def _build(self, sanitizer: Sanitizer, patch: str = "") -> None:
        if self.build_finish_indicator(sanitizer, patch).is_file():
            return

        logger.info(f"[🧱] Building {self.project} with patch {self.hash_patch(sanitizer, patch)}")
        workspace = self.workspace / self.hash_patch(sanitizer, patch)
        source_path = workspace / self.org_source_path.name
        fuzz_tooling_path = workspace / self.org_fuzz_tooling_path.name

        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        # shutil.copytree(self.source_path, source_path, symlinks=True)
        subprocess.run(["cp", "-r", str(self.source_path), str(source_path)], check=True)
        # shutil.copytree(self.fuzz_tooling_path, fuzz_tooling_path, symlinks=True)
        subprocess.run(["cp", "-r", str(self.fuzz_tooling_path), str(fuzz_tooling_path)], check=True)

        # 注入 Flag
        build_sh_path = fuzz_tooling_path / "projects" / self.project / "build.sh"
        self._inject_debug_flags(build_sh_path)

        safe_subprocess_run(["patch", "-p1"], source_path, input=patch.encode())

        safe_subprocess_run(["patch", "-p1"], source_path, input=patch.encode())

        self._build_image(fuzz_tooling_path)

        safe_subprocess_run(
            [
                "infra/helper.py",
                "build_fuzzers",
                "--sanitizer",
                self.SANITIZER_MAP[sanitizer],
                "--clean",
                self.project,
                source_path,
            ],
            fuzz_tooling_path,
        )

        safe_subprocess_run(
            [
                "infra/helper.py",
                "check_build",
                "--sanitizer",
                self.SANITIZER_MAP[sanitizer],
                self.project,
            ],
            fuzz_tooling_path,
        )

        self.build_finish_indicator(sanitizer, patch).write_text(patch)

    def build(self, patch: str = "") -> None:
        for sanitizer in self.sanitizers:
            self._build(sanitizer, patch)

    def _extract_repro_command(self, content: str) -> str:
        """从 OSS-Fuzz 日志中精确提取复现命令的关键组件"""
        clean_content = remove_ansi_escape(content)

        # OSS-Fuzz 容器路径约定说明 (Hardcoded Paths):
        # 1. 目标二进制 (Binary): 总是位于 /out/ 目录下 (如 /out/target_binary)。
        # 2. 测试用例 (PoC): reproduce 模式下，helper.py 会将输入文件固定挂载为 /testcase。
        # 3. 这里的提取逻辑依赖于 helper.py 的标准输出格式："/out/binary [args...] /testcase [args...]"

        # 正则匹配逻辑:
        # 1. ^(/out/[^\s]+): 捕获二进制路径 (Group 1)，直到遇到第一个空格
        # 2. \s+(.*)$: 捕获后续所有参数 (Group 2)
        match = re.search(r"^(/out/[^\s]+)\s+(.*)$", clean_content, re.MULTILINE)

        if match:
            binary_path = match.group(1)
            full_args_str = match.group(2)
            args_tokens = full_args_str.split()

            # 查找 /testcase (PoC文件)
            poc_path = next((token for token in args_tokens if token == "/testcase"), None)

            # 过滤 flags/options，仅保留部分触发漏洞相关的 (如 -rss_limit_mb=2560)
            other_flags = [token for token in args_tokens if token != "/testcase"]
            flags_str = " ".join(other_flags)

            kept_args = []
            for token in args_tokens:
                # 保留 PoC (通常是 /testcase)
                if token == "/testcase":
                    continue # 后面单独拼装
                
                # 保留非 Flag 参数 (极其罕见，以防万一)
                if not token.startswith("-"):
                    kept_args.append(token)
                    continue

                # [保留] 内存限制：防止 OOM 类型的 Bug 无法复现
                if token.startswith("-rss_limit_mb="):
                    kept_args.append(token)
                    continue
                
                # [删除] 超时：调试时单步执行耗时很长，保留 timeout 会导致进程被 kill
                if token.startswith("-timeout="):
                    continue

                # [删除] 字典/配置：文件在 Agent 容器中不存在，会导致启动失败
                if token.startswith("-dict=") or token.startswith("-conf=") or token.startswith("-data_flow_trace="):
                    continue

                # [删除] 运行控制：调试只需要跑一次
                if token.startswith("-runs=") or token.startswith("-jobs=") or token.startswith("-workers="):
                    continue
                
                # [删除] 其他杂项
                if token.startswith("-artifact_prefix=") or token.startswith("-print_final_stats"):
                    continue

            # 3. 组装最终命令
            # 格式: binary [rss_limit] [other_safe_flags] /testcase
            cmd_parts = [binary_path] + kept_args
            if poc_path:
                cmd_parts.append(poc_path)
            
            clean_command = " ".join(cmd_parts)

            return (
                f"Reproduction Command Details:\n"
                f"Binary: {binary_path}\n"
                f"PoC File: {poc_path if poc_path else 'Unknown'}\n"
                f"Full Command: {clean_command}\n"
            )
        return ""

    def _replay(self, poc: PoC, sanitizer: Sanitizer, patch: str = "") -> Optional[SanitizerReport]:
        self._build(sanitizer, patch)

        assert isinstance(poc, OSSFuzzPoC), f"Invalid PoC type: {type(poc)}"
        assert poc.path.is_file(), "PoC file does not exist"
        assert self.build_finish_indicator(sanitizer, patch).is_file(), "Build failed"

        logger.info(f"[🔄] Replaying {self.project}/{poc.harness_name} with PoC {poc.path} and patch {self.hash_patch(sanitizer, patch)}")

        try:
            safe_subprocess_run(
                [
                    "infra/helper.py",
                    "reproduce",
                    self.project,
                    poc.harness_name,
                    poc.path,
                ],
                self.workspace / self.hash_patch(sanitizer, patch) / self.fuzz_tooling_path.name,
                timeout=self.replay_poc_timeout,
            )

            return None
        except BuilderProcessError as e:
            sanitizers: List[Sanitizer]
            match self.language:
                case Lang.CLIKE:
                    sanitizers = [sanitizer, Sanitizer.LibFuzzer]
                case Lang.JVM:
                    sanitizers = [sanitizer, Sanitizer.JavaNativeSanitizer, Sanitizer.LibFuzzer]
            
            repro_command = self._extract_repro_command(e.stdout)

            for report in [e.stdout, e.stderr]:
                for sanitizer in sanitizers:
                    if (
                        san_report := parse_sanitizer_report(
                            report,
                            sanitizer,
                            source_path=self.source_path,
                            run_command=repro_command,
                        )
                    ) is not None:
                        return san_report

            # HACK: Check for Docker-related errors in the output
            for output_stream in [e.stdout, e.stderr]:
                if "docker: Error response from daemon:" in output_stream:
                    raise DockerUnavailableError(output_stream)

            return UnknownSanitizerReport(e.stdout, e.stderr)

    def replay(self, poc: PoC, patch: str = "") -> Optional[SanitizerReport]:
        for sanitizer in self.sanitizers:
            report = self._replay(poc, sanitizer, patch)
            if report is not None:
                return report

        return None

    @cached_property
    def language(self) -> Lang:
        project_yaml = self.fuzz_tooling_path / "projects" / self.project / "project.yaml"
        assert project_yaml.is_file(), "project.yaml not found"
        yaml_data = yaml.safe_load(project_yaml.read_text())
        return Lang.from_str(yaml_data.get("language", "c"))

    @cached_property
    def language_server(self) -> LanguageServer:
        match self.language:
            case Lang.CLIKE:
                return self.construct_c_language_server()
            case Lang.JVM:
                return self.construct_java_language_server()

    def _build_clangd_compile_commands(self) -> Path:
        clangd_workdir = self.workspace / "clangd"
        clangd_source = clangd_workdir / self.source_path.name
        clangd_fuzz_tooling = clangd_workdir / self.fuzz_tooling_path.name
        compile_commands = clangd_fuzz_tooling / "build" / "out" / self.project / "compile_commands.json"

        if not compile_commands.is_file():
            shutil.rmtree(clangd_workdir, ignore_errors=True)

            os.makedirs(clangd_workdir, exist_ok=True)
            # shutil.copytree(self.source_path, clangd_source, symlinks=True)
            subprocess.run(["cp", "-r", str(self.source_path), str(clangd_source)], check=True)
            # shutil.copytree(self.fuzz_tooling_path, clangd_fuzz_tooling, symlinks=True)
            subprocess.run(["cp", "-r", str(self.fuzz_tooling_path), str(clangd_fuzz_tooling)], check=True)

            logger.info("[🔋] Generating compile_commands.json")
            self._build_image(clangd_fuzz_tooling)

            # 使用系统 cp 命令绕过 macOS Docker 挂载卷的符号链接解析问题
            # shutil.copytree(bear_path(), clangd_source / ".bear", symlinks=True) 
            subprocess.run(["cp", "-r", str(bear_path()), str(clangd_source / ".bear")], check=True)

            shell = pexpect.spawn(
                "python",
                [
                    "infra/helper.py",
                    "shell",
                    self.project,
                    clangd_source.as_posix(),
                ],
                cwd=clangd_fuzz_tooling,
                timeout=None,
                codec_errors="ignore",
            )
            shell.sendline("$(find /src -name .bear | head -n 1)/bear.sh")
            shell.sendline("exit")
            shell.expect(pexpect.EOF)

            dotpwd = clangd_fuzz_tooling / "build" / "out" / self.project / ".pwd"
            if dotpwd.is_file() and compile_commands.is_file():
                workdir = dotpwd.read_text().strip()
                compile_commands.write_text(
                    compile_commands.read_text().replace(
                        workdir,
                        clangd_source.as_posix(),
                    ),
                )
            else:
                compile_commands.write_text("[]")

        assert compile_commands.is_file(), "compile_commands.json not found"
        if compile_commands.read_text(errors="ignore").strip() == "[]":
            logger.error("[❌] compile_commands.json is empty")

        target_compile_commands = clangd_source / "compile_commands.json"
        shutil.copy(compile_commands, target_compile_commands)

        return clangd_source

    def construct_c_language_server(self) -> HybridCServer:
        ctags_source = self.workspace / "ctags"
        if not ctags_source.is_dir():
            # shutil.copytree(self.source_path, ctags_source, symlinks=True)
            subprocess.run(["cp", "-r", str(self.source_path), str(ctags_source)], check=True)

        clangd_source = self._build_clangd_compile_commands()
        return HybridCServer(ctags_source, clangd_source)

    def construct_java_language_server(self) -> JavaLanguageServer:
        return JavaLanguageServer(self.source_path)

    def get_develop_debug_paths(self) -> dict:
        """
        获取未打补丁状态下的调试路径映射信息。
        用于 Debugger 工具将 OSS-Fuzz 容器 (Target) 内的路径映射回 Agent 容器 (Develop) 内的真实路径。
        """
        sanitizer = self.sanitizers[0]
        # Debugger always runs on the unpatched (original) code initially
        empty_patch = ""
        hash_dir = self.hash_patch(sanitizer, empty_patch)

        # Source Path: Develop 端源码根目录
        # 对应关系: OSS-Fuzz:/src/[project] <==> Develop:[Workspace]/[Hash]/<原始目录名> (例如 .../source)
        develop_source_root = self.workspace / hash_dir / self.org_source_path.name

        # Out Path: Develop 端构建产物目录
        # 对应关系: OSS-Fuzz:/out <==> Develop:[Workspace]/[Hash]/oss-fuzz/build/out/[project]
        develop_out_root = self.workspace / hash_dir / self.org_fuzz_tooling_path.name / "build" / "out" / self.project

        return {
            "source_map": (f"/src/{self.project}", str(develop_source_root)),
            "out_root_map": ("/out", str(develop_out_root)),
            "develop_source_path_obj": develop_source_root
        }

    def resolve_poc_path(self, arg_token: str, pocs: List[OSSFuzzPoC]) -> str:
        if arg_token == "/testcase":
            if pocs:
                return str(pocs[0].path)
        return arg_token
