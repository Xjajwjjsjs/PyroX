# SHIO.NEXUS Userbot

基于 Pyrofork 的 Telegram 自用机器人，集成 AI 交互、DevOps 工具、媒体处理、隐私保护等 49+ 模块。

## 功能概览

- **AI 交互** — 支持多模型对话（Claude、Gemini 等）
- **DevOps 命令** — VPS 监控、速度测试、IP 检测
- **媒体工具** — 聊天导出、语音/视频处理、贴纸制作
- **隐私功能** — 自动回复、消息管理
- **实用工具** — 天气查询、用户信息、Web 文件托管

## 部署

1. 安装依赖：
   ```bash
   pip install pyrofork speedtest-cli openai
   ```

2. 配置 `api_id` 和 `api_hash`（从 [my.telegram.org](https://my.telegram.org) 获取）

3. 运行：
   ```bash
   python userbot.py
   ```

## 注意

- 配置区的敏感信息已脱敏，部署前需填入真实值
- 需要 Python 3.10+

## License

MIT
