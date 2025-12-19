#### 📋 Installation & Configuration (For Developers)

To set up the development environment using VS Code DevContainers, follow these steps:

**1. Pull the Docker Image**

We provide a pre-built Docker image with all dependencies installed.

```bash
docker pull liuxuanlings/my-patchagent:latest
```

**2. Configure Environment Variables (`.env`)**

You **MUST** create a `.env` file in the project root directory. This file handles both LLM API keys and your network proxy settings.

Copy the template:
```bash
cp .env.template .env
```

**3. Edit `.env` for Proxy Settings**

Since the agent runs inside a Docker container, the proxy configuration depends on your host OS.

**For macOS / Windows Users:**
If you use a proxy tool (like Clash/v2ray) on port 7890, use `host.docker.internal`. **Ensure your proxy tool allows "LAN connections" (Allow LAN).**

```bash
# In .env file
http_proxy=http://host.docker.internal:7890
https_proxy=http://host.docker.internal:7890
all_proxy=socks5://host.docker.internal:7890
```

**For Linux Users:**
Use `127.0.0.1` and ensure you run with host networking (default in our devcontainer).

```bash
# In .env file
http_proxy=http://127.0.0.1:7890
https_proxy=http://127.0.0.1:7890
all_proxy=socks5://127.0.0.1:7890
```

**4. Start VS Code**
Open the project folder in VS Code and click "Reopen in Container".
