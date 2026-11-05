# 智链 · 声形工坊（VoxForma）

基于 [VoiceStudio](https://github.com/debpalash/VoiceStudio) 的本地 AI 语音与数字人口播工作台。

将文字转语音、音色克隆、形象库和视频对口型接到同一套界面与任务队列中。

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

先安装 Git、[uv](https://docs.astral.sh/uv/) 和 [Bun](https://bun.sh/)，并准备好 Linux/WSL 与 NVIDIA 驱动。

```bash
git clone --branch feat/voxforma-digital-human https://github.com/wentaoqqq/zhilian-VoxForma.git
cd zhilian-VoxForma
uv sync --locked
bun install --frozen-lockfile
bun run --cwd electron build:web
uv run --no-sync python -m backend.integrations.avatar.launcher --host 0.0.0.0 --port 3900
```

打开 `http://服务器地址:3900/`，在 **设置 → 模型 → 数字人** 中点击安装 Student。模型只在用户点击安装时下载；现有安装可继续复用。首次准备 VoiceStudio 本身的依赖仍需一定磁盘空间和时间。

Student 需要约 **15.27 GB** 模型下载及独立运行环境；安装前至少预留 **30 GiB** 可用空间，生成视频还需要额外空间。GPU、WSL 和 Windows 端口转发属于主机配置，监听 Linux 的 `0.0.0.0` 不会自动配置 Windows 防火墙。

完整说明：[数字人部署与安装](docs/engines/tbdub-student.md)。

**请使用集成启动器**。直接运行原版 `backend.main:app` 不会启用本 Fork 的完整 GPU 协调层。

## 更新与上游同步

本仓库保留 VoiceStudio 的 Git 历史。上游更新通过升级分支合并、检查和验证后发布；模型、运行环境、音色、形象与任务结果独立于源码保存。

- [Fork 维护说明](docs/fork-maintenance.md)
- [原版 VoiceStudio README](README.upstream.md)
- [TBDub 官方项目](https://github.com/TaoLiveAIGC/TBDub)

不要使用原版桌面自动更新覆盖本 Fork。未来如发布桌面包，需要采用本仓库自己的发布与更新地址。

## 验证范围

已有部署上通过了 29 项针对性后端检查、TypeScript、22 个语言目录检查和浏览器构建；独立环境中的 Student 短视频生成、范围下载和 GPU 释放也通过了检查。

这些记录不代表所有显卡、所有视频姿态和全新主机都已验证。本分支合并更新后只进行源码与针对性检查，不自动下载模型或启动耗时生成测试。

## 许可与致谢

VoxForma 基于 **VoiceStudio**，应用代码继续遵守 [GNU AGPL-3.0](LICENSE)，并保留[原许可声明](LICENSE-NOTICE.md)。TBDub 源码与权重、RIFE 及其他第三方组件保留各自的 LICENSE / NOTICE 和模型条款；应用许可证不替代模型许可证。

感谢 VoiceStudio、TBDub、DiffSynth-Studio、X-Dub、RIFE 及各语音模型的作者和贡献者。
