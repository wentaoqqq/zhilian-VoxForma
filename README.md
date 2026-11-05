# 智链 · 声形工坊（VoxForma）

基于 [VoiceStudio](https://github.com/debpalash/VoiceStudio) 的本地 AI 语音与数字人口播工作台。

将文字转语音、音色克隆、形象库和视频对口型接到同一套界面与任务队列中。

请使用本仓库默认 `main` 分支的源码，按下方指南部署。原版 VoiceStudio 安装包不包含本 Fork 的数字人功能。

## 这个 Fork 增加了什么

- **数字人口播**：接入 TBDub Student，支持选择已保存的形象，使用上传音频或 VoiceStudio 原生 TTS 配音生成视频。
- **形象库**：上传视频、命名、预览、编辑、替换、删除和恢复；已提交任务保留形象版本。
- **一键安装 Student**：在设置 → 模型 → 数字人准备独立环境、下载并校验权重；支持暂停、断点续传和重试。
- **可选 60 帧输出**：单独安装 RIFE 插帧组件，原生生成仍为 25 帧。
- **共享 GPU 调度**：配音保存后释放语音模型，再执行视频推理，降低多个模型抢占显存的风险。
- **长视频与复杂姿态处理**：保留已修改的流式 VAE/视频输出、时间基准转换及人脸检测恢复；无法可靠处理的片段按策略保留原画面。
- **中文功能命名与浏览器兼容**：独立维护中文用词，并兼容局域网 HTTP 下的 UUID 生成。

原有 TTS、ASR、视频译配等功能来自 VoiceStudio，仍遵循各自的模型安装与许可要求。

## 当前支持范围

数字人后端目前面向 **x86-64 Linux / Ubuntu WSL2 + NVIDIA GPU**；已使用 RTX 5080 16GB、64GB 内存验证。其他显卡与新主机仍需实际验收。

本仓库当前提供**源码与浏览器后端**，没有发布 VoxForma 桌面安装包。原版 VoiceStudio 的安装包不会自动包含本 Fork 的数字人功能。这里也不提供自动安装 WSL 或 GPU 驱动的承诺。

## 源码启动

完整步骤见 **[客户首次安装指南](docs/install/voxforma-wsl.md)**，包括 WSL、工具安装、首次引导、管理员权限与局域网入口。

先准备 Linux/WSL 与 NVIDIA 驱动，再在 **Ubuntu/Linux 终端**安装 Git、[uv](https://docs.astral.sh/uv/)、Linux 版 **Node.js 22.x（至少 22.12）** 和 **Bun 1.4.2**。下面的命令全部在 Ubuntu/Linux 中执行，不要复用 Windows Python 或 `node_modules`。

```bash
git clone https://github.com/wentaoqqq/zhilian-VoxForma.git
cd zhilian-VoxForma
uv sync --locked
bun install --frozen-lockfile
bun run --cwd electron build:web
uv run --no-sync python -m backend.integrations.avatar.launcher --host 127.0.0.1 --port 3900
```

先在运行后端的电脑打开 `http://127.0.0.1:3900/`。首次引导的模型步骤中打开“高级 → 数字人”并安装 Student，之后完成隐私/账户选项；使用上传音频时不必先安装 TTS。进入工作台后入口为 **设置 → 模型 → 数字人**。模型只在用户点击安装时下载；现有安装可继续复用。

Student 需要约 **15.27 GB** 模型下载及独立运行环境；至少预留 **30 GiB 用于 Student 安装**，另外计算 VoiceStudio 依赖、缓存和视频空间。局域网访问还需要管理员登录及正确的 Windows 转发/防火墙；监听 `0.0.0.0` 本身不授予模型安装权限，具体按首次安装指南配置。

完整说明：[数字人部署与安装](docs/engines/tbdub-student.md)。

**请使用集成启动器**。直接运行原版 `backend.main:app` 不会启用本 Fork 的完整 GPU 协调层。

## 更新与上游同步

本仓库保留 VoiceStudio 的 Git 历史。上游更新通过升级分支合并、检查和验证后发布；模型、运行环境、音色、形象与任务结果独立于源码保存。

- [Fork 维护说明](docs/fork-maintenance.md)
- [原版 VoiceStudio README](README.upstream.md)
- [原版功能目录](docs/feature-catalog.md)
- [TBDub 官方项目](https://github.com/TaoLiveAIGC/TBDub)

不要使用原版桌面自动更新覆盖本 Fork。未来如发布桌面包，需要采用本仓库自己的发布与更新地址。

## 验证范围

当前安装流程修复通过了 31 项针对性后端测试、612 项语言/源码/版本检查、首次引导的桌面与网页配置测试、TypeScript、22 个语言目录检查和浏览器构建。此前独立环境中的 Student 短视频生成、范围下载和 GPU 释放也通过了检查。

这些记录不代表所有显卡、所有视频姿态和全新主机都已验证。本分支合并更新后只进行源码与针对性检查，不自动下载模型或启动耗时生成测试。

## 许可与致谢

VoxForma 基于 **VoiceStudio**，应用代码继续遵守 [GNU AGPL-3.0](LICENSE)，并保留[原许可声明](LICENSE-NOTICE.md)。TBDub 源码与权重、RIFE 及其他第三方组件保留各自的 LICENSE / NOTICE 和模型条款；应用许可证不替代模型许可证。

感谢 VoiceStudio、TBDub、DiffSynth-Studio、X-Dub、RIFE 及各语音模型的作者和贡献者。

原版的翻译文档、团队和赞助者名单保留在 [上游说明](README.upstream.md)。如向上游提交贡献，请阅读其 [贡献许可说明](README.upstream.md#contribution-licensing)。
