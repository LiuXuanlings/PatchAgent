import base64
import tempfile
from pathlib import Path

import git

from patchagent.agent.generator import agent_generator
from patchagent.builder import OSSFuzzBuilder, OSSFuzzPoC
from patchagent.parser.sanitizer import Sanitizer
from patchagent.task import PatchTask
DOCKER_REGISTRY = "liuxuanlings"  # DockerHub 用户名

oss_fuzz_url = "https://github.com/google/oss-fuzz.git"
oss_fuzz_commit = "26f36ff7ce9cd61856621ba197f8e8db24b15ad9"

mruby_url = "https://github.com/mruby/mruby.git"
mruby_commit = "0ed3fcf"

poc_text = """
send"send","send","send","send","send","send","send","send","send","send","send","send","send","send","send","send"
"""

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        oss_fuzz_path = tmppath / "oss-fuzz"
        source_path = tmppath / "source"
        poc_path = tmppath / "poc.bin"

        print(f"[🔍] POC Path: {poc_path}")
        poc_path.write_bytes(poc_text.strip().encode('latin-1'))

        print(f"[🔍] OSSFuzz Path: {oss_fuzz_path}")
        oss_fuzz_repo = git.Repo.clone_from(oss_fuzz_url, oss_fuzz_path)
        oss_fuzz_repo.git.checkout(oss_fuzz_commit)

        print(f"[🔍] Source Path: {source_path}")
        source_repo = git.Repo.clone_from(mruby_url, source_path)
        source_repo.git.checkout(mruby_commit)

        patchtask = PatchTask(
            [OSSFuzzPoC(poc_path, "mruby_fuzzer")],
            OSSFuzzBuilder(
                "mruby",
                source_path,
                oss_fuzz_path,
                [Sanitizer.AddressSanitizer],
                docker_registry=DOCKER_REGISTRY,
            ),
        )

        patchtask.initialize()
        print(f"Patch: {patchtask.repair(agent_generator())}")


# 首次运行时拉取所有基础镜像
# python pull_all_oss_fuzz_base_images.py
# 执行后会拉取所有官方基础镜像，和运行 `python infra/helper.py pull_images` 效果一致

# python -m patchagent.example.mruby
