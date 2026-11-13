# 原生 DeepSeek Harness 剪辑页面

视频生产页面直接内嵌 DeepSeek Harness 原生 Web UI。项目选择、模型设置、会话、文件读取和命令执行均由 Harness 负责；不经过自定义产品表单、分类数据库或 production agent 批次执行器。

原项目仍为服务器 `D:\5080-ftp\剪辑系统\电动圆桌自动剪辑库`，WSL 访问同一目录 `/mnt/d/5080-ftp/剪辑系统/电动圆桌自动剪辑库`。素材放进原来的 inbox，在原生页面中选择该项目并对话即可。集成不复制项目、不修改原项目规则、不启动剪辑任务。

部署运行 `@deepseek-ai/dsh@0.2.2-rc.1` 与独立 Node 24。原生服务监听 loopback 3080；应用同源前缀 `/native-harness/` 转发 HTTP 和 WebSocket，保留 Host、原生 token/cookie 认证及流式消息。原生启动 URL 保存为服务器权限 0600 的 JSON `{ "url": "..." }`，不写入 Git 或前端配置。

应用环境配置：

- `VOXFORMA_HARNESS_UPSTREAM=http://127.0.0.1:3080`
- `VOXFORMA_HARNESS_ENTRY_FILE=/path/to/entry.json`

Harness 启动配置：`web --no-open --port 3080 --public-url http://HOST:3900/native-harness/ --trusted-host HOST`。使用自己的实际访问主机名；代理剥离前缀并将 cookie Path 改为 `/native-harness/`。启动包装器须在 Harness 每次重启后更新 entry JSON。

页面通过 `/production/harness` 获取原生入口，沿用现有应用消费者鉴权；其余 Harness 请求由 Harness 原生认证。该前缀从外层转发，不进入语音/数字人 GPU 租约或应用业务 CSRF 检查。

`legacy-production-page.tsx` 与旧后端数据保留作为回退记录，不作为新页面的入口。部署前保留旧 release、服务配置和 Git 标签；回退切换 release 并撤销本次服务配置即可，原目录及既有素材不会被迁移或删除。
