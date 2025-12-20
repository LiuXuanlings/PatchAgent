#### 📋 Installation & Configuration (For Developers)

To set up the development environment using VS Code DevContainers, follow these steps:

**1. Pull the Pre-built Docker Image**

We provide a pre-built Docker image with all dependencies installed for the agent itself:

```bash
docker pull liuxuanlings/my-patchagent:latest
```

**2. Pull OSS-Fuzz Base Images (Critical for First Use)**

Before running the agent (especially for C/C++ fuzzing/reproduction tasks), you **MUST** pull all OSS-Fuzz official base images on your local machine (these images are required for building/fuzzing OSS-Fuzz projects and will be reused by the agent).  

We provide a dedicated script for this (already placed in the project root directory):

```bash
# Run the one-time script to pull all OSS-Fuzz base images (local machine only)
python pull_oss_fuzz_base_images.py
```

> **Key Notes on OSS-Fuzz Base Images**:
> - These images are pulled to your **local Docker daemon** (not inside the devcontainer) and will be automatically detected by the agent when running OSS-Fuzz tasks.
> - For C/C++ projects, core images (base-runner/base-builder/base-clang) are mandatory; failures of non-C/C++ images (e.g., Go/Python) can be ignored.

**3. Configure Environment Variables (`.env`)**

You **MUST** create a `.env` file in the project root directory. This file handles both LLM API keys and your network proxy settings (critical for pulling OSS-Fuzz images and accessing LLM APIs).

Copy the template:
```bash
cp .env.template .env
```

**4. Edit `.env` for Proxy Settings**

Since the agent runs inside a Docker container, the proxy configuration depends on your host OS (ensure the proxy is running before pulling OSS-Fuzz images):

**For macOS / Windows Users:**
If you use a proxy tool (like Clash/v2ray) on port 7890, use `host.docker.internal`. **Ensure your proxy tool allows "LAN connections" (Allow LAN).**

```bash
# In .env file
http_proxy=http://host.docker.internal:7890
https_proxy=http://host.docker.internal:7890
all_proxy=socks5://host.docker.internal:7890
```

**For Linux Users:**
Use `127.0.0.1` and ensure you run with host networking (default in our devcontainer):

```bash
# In .env file
http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
all_proxy=socks5://127.0.0.1:7890
```

**5. Start VS Code**
Open the project folder in VS Code and click "Reopen in Container". The devcontainer will automatically reuse the locally pulled OSS-Fuzz base images for all OSS-Fuzz related tasks (build/fuzz/reproduce).