#!/usr/bin/env python3
"""
OSS-Fuzz 基础镜像全量拉取脚本
============================================
【核心用途】
1. 完全复刻 OSS-Fuzz 官方 `python infra/helper.py pull_images` 命令逻辑
2. 拉取 OSS-Fuzz 官方定义的所有基础镜像（包含 C/C++/Go/Python/Java 等全语言）

【适配场景】
- 仅需执行一次（首次部署/初始化环境时）
- 优先保障 C/C++ 项目核心镜像（base-runner/base-builder 等）拉取，非核心镜像失败不阻断
- 无需依赖 OSS-Fuzz 源码，可独立运行

【使用方式】
1. 独立执行（推荐）：
   $ python pull_all_oss_fuzz_base_images.py
2. 集成到业务代码（可选）：
   from pull_all_oss_fuzz_base_images import pull_all_oss_fuzz_base_images
   pull_all_oss_fuzz_base_images()  # 首次运行时执行一次即可

【镜像范围】
- generic: base-image/base-clang/base-builder/base-runner/base-runner-debug（C/C++ 核心）
- go: base-builder-go
- javascript: base-builder-javascript
- jvm: base-builder-jvm
- python: base-builder-python
- rust: base-builder-rust
- ruby: base-builder-ruby
- swift: base-builder-swift

【注意事项】
1. 需确保 Docker 已安装并启动，且当前用户有 Docker 执行权限（必要时加 sudo）
2. 核心镜像（base-runner/base-builder）拉取失败会影响 C/C++ 项目的 build/reproduce 操作
3. 非核心镜像（如 Go/Python 专属）拉取失败不影响 C/C++ 项目正常使用
============================================
"""

import subprocess
import sys
from typing import Dict, List

# 完全复刻 OSS-Fuzz 官方 BASE_IMAGES 定义（helper.py）
BASE_IMAGES: Dict[str, List[str]] = {
    'generic': [
        'gcr.io/oss-fuzz-base/base-image',
        'gcr.io/oss-fuzz-base/base-clang',
        'gcr.io/oss-fuzz-base/base-builder',
        'gcr.io/oss-fuzz-base/base-runner',
        'gcr.io/oss-fuzz-base/base-runner-debug',
    ],
    'go': ['gcr.io/oss-fuzz-base/base-builder-go'],
    'javascript': ['gcr.io/oss-fuzz-base/base-builder-javascript'],
    'jvm': ['gcr.io/oss-fuzz-base/base-builder-jvm'],
    'python': ['gcr.io/oss-fuzz-base/base-builder-python'],
    'rust': ['gcr.io/oss-fuzz-base/base-builder-rust'],
    'ruby': ['gcr.io/oss-fuzz-base/base-builder-ruby'],
    'swift': ['gcr.io/oss-fuzz-base/base-builder-swift'],
}

def docker_pull(image: str) -> bool:
    """封装 docker pull，兼容官方逻辑（自动拉取 latest 标签）"""
    full_image = f"{image}:latest"  # 官方默认拉取 latest 标签
    
    # 检查镜像是否已存在，避免重复拉取
    try:
        subprocess.run(
            ["docker", "image", "inspect", full_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        print(f"✅ 镜像 {full_image} 已存在，跳过拉取")
        return True
    except subprocess.CalledProcessError:
        pass

    # 执行拉取（和官方 helper.py 的 docker_pull 逻辑一致）
    try:
        print(f"📥 拉取镜像: {full_image}")
        subprocess.run(
            ["docker", "pull", full_image],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"✅ 镜像 {full_image} 拉取完成")
        return True
    except subprocess.CalledProcessError as e:
        error = e.stderr.strip() if e.stderr else "未知错误"
        print(f"⚠️  镜像 {full_image} 拉取失败: {error}", file=sys.stderr)
        return False

def pull_all_oss_fuzz_base_images() -> bool:
    """
    完全复刻官方 `python infra/helper.py pull_images` 逻辑
    拉取所有 OSS-Fuzz 基础镜像（和官方命令效果一致）
    """
    print("=" * 60)
    print("开始拉取 OSS-Fuzz 所有基础镜像（和官方 pull_images 命令一致）")
    print("=" * 60)

    all_success = True
    # 遍历所有语言类型的基础镜像（和官方逻辑一致）
    for lang, images in BASE_IMAGES.items():
        print(f"\n🔹 拉取 {lang.upper()} 类型基础镜像...")
        for img in images:
            if not docker_pull(img):
                all_success = False

    print("\n" + "=" * 60)
    if all_success:
        print("✅ 所有 OSS-Fuzz 基础镜像拉取完成！")
    else:
        print("❌ 部分镜像拉取失败（非核心镜像不影响 C/C++ 项目使用）", file=sys.stderr)
    print("=" * 60)
    return all_success

if __name__ == "__main__":
    # 执行一次拉取，返回值：0=全部成功，1=部分失败
    sys.exit(0 if pull_all_oss_fuzz_base_images() else 1)