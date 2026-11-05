# VoxForma：客户首次安装指南（Windows + WSL / Linux）

目标是从源码启动浏览器工作台，再在页面中安装数字人模型。**这不是 Windows 安装包，也不是自动安装整台电脑的脚本。**

本指南对应本仓库默认 `main` 分支的 VoxForma 源码，包含数字人集成。原版 VoiceStudio 安装包不包含这些新增功能；请使用本仓库，并按下面的文件检查确认源码完整。

已验证的目标配置是 RTX 5080 16GB + 64GB 内存。其他配置需要单独验收，不能仅凭“安装成功”保证任意视频都能生成。

## 1. 先确定命令在哪执行

- **Windows PowerShell（管理员）**：仅用于安装/检查 WSL、可选的 Windows 端口转发。
- **Ubuntu / Linux 终端**：Git、uv、Node.js、Bun、Python 环境、构建、启动，全部在这里执行。
- **浏览器**：可以在 Windows 或另一台获准访问的电脑上打开。

不要在 Windows PowerShell 中执行本指南的 Bash 命令，也不要复用 Windows 的 Python 虚拟环境或 `node_modules`。

没有 WSL 时，在 Windows 管理员 PowerShell 中执行：

```powershell
wsl --install -d Ubuntu-22.04
```

按系统提示重启，首次打开 Ubuntu，完成 Linux 用户名和密码设置。已有 Ubuntu 时，先执行 `wsl -l -v` 查看真实发行版名称，不必重复安装第二套。进入已安装的发行版，例如：

```powershell
wsl -l -v
wsl -d Ubuntu-22.04
```

版本列应为 `2`。后续命令都在 Ubuntu 中执行。原生 Linux 用户直接进入下一节。

**空间位置**：WSL 中的 `~`、项目依赖和默认模型目录占用的是发行版虚拟磁盘所在的 Windows 分区；把下载的 ZIP 放在 D 盘，不代表 WSL 就在 D 盘。若系统盘空间不足，先按 [Microsoft WSL 文档](https://learn.microsoft.com/windows/wsl/)安排发行版位置，再安装依赖。

## 2. GPU、系统依赖和开发工具

在 Ubuntu 中检查：

```bash
uname -m
nvidia-smi
```

应为 `x86_64`，且能识别 NVIDIA 显卡。Student 使用 CUDA 12.8 的 PyTorch 构建，主机驱动需要与其兼容。WSL 使用 Windows 的 NVIDIA 驱动透传；不要为了此项目在 WSL 中安装另一套 Linux 显卡驱动。也不要求手动安装完整 CUDA Toolkit。

以下系统依赖只在客户的新环境准备时安装，本仓库不会因打开页面自动执行它们：

```bash
sudo apt update
sudo apt install -y git curl unzip ca-certificates build-essential pkg-config \
  ffmpeg libgl1 libglib2.0-0 libsndfile1 libportaudio2
```

准备 **Linux 版**工具：

| 工具 | 要求 | 用途 |
| --- | --- | --- |
| Git | 可正常克隆 GitHub | 获取源码和更新 |
| uv | 可正常执行 `uv --version` | 管理 Python 及隔离环境 |
| Node.js | 使用 22.x，至少 22.12 | 构建命令中的 `node` 脚本；Bun 不能替代这个前置条件 |
| Bun | `1.4.2`，与根目录 `package.json` 一致 | 安装前端依赖并构建 |

uv 的官方安装方式：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Node.js 按 [Node.js 官方下载页](https://nodejs.org/en/download)安装 **Linux x64 的 22.x**，不是 Windows 版本。

Bun 的官方指定版本安装方式：

```bash
curl -fsSL https://bun.com/install | bash -s "bun-v1.4.2"
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
```

然后确认工具确实来自 Linux：

```bash
git --version
uv --version
node --version
node -p 'process.platform'
bun --version
bun -p 'process.platform'
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
```

Node/Bun 的平台都应为 `linux`，Bun 应为 `1.4.2`。若命令来自 `/mnt/c/...`，先修正 PATH，不能继续复用 Windows 安装。

参考：[uv 安装](https://docs.astral.sh/uv/getting-started/installation/)；[Bun 安装与指定版本](https://bun.sh/docs/installation)。

## 3. 获取正确的代码分支

推荐在 Linux 文件系统内工作，例如 `~/projects`：

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/wentaoqqq/zhilian-VoxForma.git
cd zhilian-VoxForma
git branch --show-current
test -f backend/integrations/avatar/launcher.py
test -f backend/services/video_install.py
```

分支应为 `main`，后两条检查应成功。已经克隆过本仓库时，只在工作区没有自己的未保存修改时更新：

```bash
git status --short
git fetch origin
git switch main
git pull --ff-only origin main
```

不要用 `git reset --hard` 或覆盖用户修改来完成更新。正在使用旧集成分支的用户，也应先保留本地修改，再切换到 `main`。

## 4. 准备 VoiceStudio 环境并构建页面

在项目根目录执行：

```bash
uv sync --locked
bun install --frozen-lockfile
bun run --cwd electron build:web
```

`uv sync` 会安装 VoiceStudio 本身的 Python/CUDA 等依赖；它不是 Student 权重下载。父环境按仓库锁文件安装，Student 之后由安装器创建独立 Python 3.10 环境。不要手动把两套依赖装到一起。

继续之前确认：

```bash
test -x .venv/bin/python
test -s frontend/dist/index.html
```

没有 `frontend/dist/index.html` 时不要继续启动，也不要把“后端 `/health` 返回 200”当成页面已准备完成。

## 5. 先在本机打开页面

先使用本机地址完成初始设置，避免把权限与网络问题混进安装排错：

```bash
uv run --no-sync python -m backend.integrations.avatar.launcher --host 127.0.0.1 --port 3900
```

保留这个终端。在运行后端的电脑浏览器打开：

```text
http://127.0.0.1:3900/
```

Windows 通常可通过 WSL 的 localhost 转发访问；若不通，先在 **Ubuntu 的另一终端**检查：

```bash
curl --noproxy '*' -fsS http://127.0.0.1:3900/health
curl --noproxy '*' -fsS http://127.0.0.1:3900/engines/video
```

第一条是服务状态；第二条应包含 `tbdub-student`。Ubuntu 内也不通时检查启动终端错误；Ubuntu 通、Windows 不通时检查 WSL localhost 转发，而不是重下模型。

本启动方式是前台运行。关闭终端、退出 WSL 或电脑重启后，需要再次运行启动命令；本指南没有配置开机自启。

## 6. 首次设置与安装 Student

首次打开是初始化引导，不一定直接显示工作台：

1. 完成系统检查。
2. 到模型步骤，打开“高级”，选择“数字人”。
3. 点击“下载并安装 Student”，观察安装阶段、下载进度与日志。
4. 安装完成后继续引导，完成隐私/账户选项，再进入工作台。

**只使用“上传音频 + 数字人”时，不要求额外安装 TTS 模型。** 已装好的 Student 可以满足引导中的模型条件，但不会跳过隐私/账户选择。

进入工作台后，安装位置为 **设置 → 模型 → 数字人**。Student 的必需权重约 **15.27 GB（十进制）**，另有独立 Python/CUDA 依赖。至少预留 **30 GiB 用于 Student 安装**，还要另计 VoiceStudio 自身依赖、下载缓存、形象视频与生成临时文件。30 GiB 不是整台机器的总空间承诺。

安装器按顺序准备源码、独立环境、模型文件，并检查固定版本、大小和 SHA-256；通过运行库/命令行检查后才启用。暂停保留已完成的下载分块，重试继续；失败不要直接删除整个数据目录。

60 帧是可选组件：先装好 Student，再点击“下载 60 帧组件”。缺少 RIFE 不影响 25 帧生成。

## 7. 第一次生成的检查顺序

先用一条短视频和已存在的音频检查链路：

1. 在“形象库”上传人物视频，命名并确认预览正确。
2. 在“数字人口播”选择这个形象，使用“上传音频”，先选 25 帧。
3. 提交任务，确认进入队列、生成完成，且结果可以预览与下载。

之后再配置 TTS：在原生语音模型设置安装需要的引擎，创建/选择音色，输入文案。**安装 Student 不会同时安装 IndexTTS 或其他 TTS。** 配音链路通过当前启动器配置的本地端口访问 VoiceStudio，不能依赖固定的 `3901` 端口。

部分原生 TTS sidecar 安装接口仍遵循上游“仅本机管理”的规则，API key 不会绕过它。若远程安装 IndexTTS 被拒绝，请在服务器本机/真实 loopback 的管理连接上完成，不要关闭全局权限校验。已安装引擎的正常使用另按上游权限规则执行。

详细数据目录和引擎配置见 [Student 技术说明](../engines/tbdub-student.md)。

## 8. 需要局域网访问时，再启用远程管理

监听 `0.0.0.0` 只改变网络监听，不等于获得模型安装的管理员权限。分享 PIN 和可信网段也不等于管理员 API key。

停止前台服务。在 Ubuntu 项目目录，用现有 Python 生成一份自己的管理员 key：

```bash
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

将它保存在项目 `.env` 中；若文件已存在，编辑并保留原配置，不要覆盖整个文件：

```dotenv
OMNIVOICE_SERVER_MODE=1
OMNIVOICE_API_KEY=替换为刚生成的真实字符串
```

```bash
chmod 600 .env
uv run --no-sync python -m backend.integrations.avatar.launcher --host 0.0.0.0 --port 3901
```

这里让后端使用 **3901**，将 Windows 的 **3900** 留给可选入口转发，避免两个监听抢同一个端口。浏览器提示管理员登录时输入自己的 API key。不要把 key 放进网址、Git 仓库或对外分享的安装文档。

原生 Linux 可按实际防火墙规则访问 `http://Linux地址:3901/`；管理员会话和权限要求仍然适用。

### Windows + WSL 的局域网入口

先在 Windows PowerShell 确认 WSL 后端可通过 localhost 访问：

```powershell
Invoke-WebRequest http://127.0.0.1:3901/health
Get-NetTCPConnection -State Listen -LocalPort 3900 -ErrorAction SilentlyContinue
netsh interface portproxy show all
```

若 3900 已被其他服务占用或有其他目标的转发，先查清，不要覆盖。确认空闲后，在 **Windows 管理员 PowerShell**中按需配置：

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=3900 connectaddress=127.0.0.1 connectport=3901
New-NetFirewallRule -Name VoxForma-LAN-3900 -DisplayName 'VoxForma LAN 3900' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3900 -RemoteAddress LocalSubnet -Profile Any
```

此时其他获准的局域网电脑访问 `http://Windows局域网地址:3900/`。若客户端不在同一网段，或使用 ZeroTier 等网络，需要按实际网段调整路由与限定防火墙范围；不能因为一个地址能开，就断言另一个地址也能开。

转发保存不等于 WSL 后端自动启动。重启后先启动后端，再分别检查 Windows localhost、Windows 局域网地址和真实客户端页面。

权限机制参考 [API 认证说明](../api-auth.md)；WSL 网络参考 [Microsoft 文档](https://learn.microsoft.com/windows/wsl/networking)。

## 9. 常见失败应查哪里

| 现象 | 先检查 |
| --- | --- |
| 没有数字人菜单或 `launcher.py` | 是否用了原版仓库/安装包，或尚未更新本仓库旧代码 |
| `node: command not found` | 是否在 Ubuntu 装了 Node.js，而不只是 Windows/Bun |
| `fcntl` 导入失败 | 是否错误地在原生 Windows Python 中启动 |
| 页面 404 / 空白，但 health 为 200 | 前端构建是否成功，`frontend/dist/index.html` 是否存在 |
| 安装按钮 401/403 | 是否是真实本机请求；远程是否设置 server mode 并使用管理员 key |
| Student 已装好，引导仍不能继续 | 是否拿到了最新 `main` 并重新构建，是否仍使用旧前端缓存 |
| 配音无法回调 / 连接 3901 被拒绝 | 是否运行了最新端口修复，`AVATAR_VOICE_URL` 是否被旧配置覆盖 |
| 模型下载慢 | Hugging Face 端点、代理、实际分块进度；不能只看预分配文件大小 |
| Python/CUDA 包下载慢 | PyPI/PyTorch 下载源；Hugging Face 镜像不控制这部分下载 |
| 服务退出后打不开 | 前台终端是否关闭，WSL 是否退出；此流程没有配置自启 |

## 10. 更新和验收边界

保留音色、形象、任务和模型目录，暂停提交任务并等待当前任务完成后再更新。不要在客户已经有本地修改的目录里强行覆盖。具体同步与回滚见 [Fork 维护说明](../fork-maintenance.md)。

本指南已经按源码核对入口、工具版本、端口、首次引导与权限条件，并使用不下载模型的回归检查验证相关修复。它不等于已经在每一台全新电脑完成下载安装验收；真正交付新服务器时，仍要按第 7 节验证实际生成、下载及所需的 60 帧/TTS 路径。
