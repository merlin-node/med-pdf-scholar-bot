# Medhub PDF Scholar Bot 📚

发一份医学 PDF 文献给 Telegram Bot，收一个完整的中文 `.md` 笔记附件。

- **Gemini File API** 原生解析整份 PDF，支持视觉表格 OCR、医学图表识别、双栏排版
- 笔记以 `.md` **文件附件**形式发回，不在聊天框里刷屏
- **白名单保护**，绑定你的 Telegram ID，防止 API Token 被刷
- **零存储**：临时文件处理完立即删除，VPS 不留任何文档
- **一键部署**：直接拉取 ghcr.io 预构建镜像，无需在服务器编译

-----

## 笔记输出结构

|章节       |内容                                        |
|---------|------------------------------------------|
|📌 全文总结   |400～600 字，研究背景、方法、核心结论、临床意义               |
|📝 逐章节笔记  |按 Abstract→Methods→Results→Discussion 顺序整理|
|🔬 数据与表格摘录|视觉识别论文表格，重建为 Markdown 表格                  |
|🔑 核心术语对照表|15～20 条中英对照 + 语境解释                        |
|⭐ 亮点与局限  |方法学亮点 / 重要临床发现 / 研究局限                     |
|❓ 延伸思考   |4～5 个深度研究问题                               |

-----

## 快速部署

> ⚠️ **VPS 节点选择：** 请使用美国、新加坡、日本等节点。
> **不要用香港 / 大陆节点**，直连 Google API 会被 GFW 阻断，报 SSL EOF 错误。

### 第一步：安装 Docker

```bash
curl -fsSL https://get.docker.com | bash -s docker
```

### 第二步：创建目录并下载配置文件

```bash
mkdir -p ~/med_bot && cd ~/med_bot

# 下载 docker-compose.yml
wget https://raw.githubusercontent.com/merlin-node/med-pdf-scholar-bot/main/docker-compose.yml

# 下载 .env 模板
wget -O .env https://raw.githubusercontent.com/merlin-node/med-pdf-scholar-bot/main/.env.example
```

### 第三步：编辑配置

```bash
nano .env
```

填入：

```env
TELEGRAM_BOT_TOKEN=你的BotFather_Token
GEMINI_API_KEY=你的Gemini_API_Key

# 白名单：填入你的 Telegram 数字 ID（多个用逗号分隔）
ALLOWED_USER_IDS=123456789

# 模型（推荐 gemini-2.5-flash，免费额度宽松、速度快）
GEMINI_MODEL=gemini-2.5-flash
```

**获取方式：**

- **Telegram Bot Token** → 搜索 `@BotFather`，发送 `/newbot` 按提示创建
- **Gemini API Key** → https://aistudio.google.com/apikey （Key 格式为 `AIza` 开头）
- **你的 Telegram ID** → 发消息给 `@userinfobot` 查询

### 第四步：启动

```bash
docker compose up -d
```

查看日志确认启动成功：

```bash
docker compose logs -f
```

看到 `PDF Scholar Bot started` 即成功。按 `Ctrl+C` 退出日志查看（容器会继续运行）。

-----

## 更新 Bot

```bash
cd ~/med_bot
docker compose down
docker compose pull
docker compose up -d
```

每次仓库更新后执行这三条命令即可获取最新版本。

-----

## Bot 命令

|命令       |说明                         |
|---------|---------------------------|
|`/start` |欢迎信息                       |
|`/help`  |使用说明                       |
|`/status`|查看当前模型和配置                  |
|发送 PDF   |开始分析，约 30～120 秒后收到 `.md` 附件|

-----

## 常见问题

|问题                                 |原因                                    |解决                                   |
|-----------------------------------|--------------------------------------|-------------------------------------|
|`SSL: UNEXPECTED_EOF_WHILE_READING`|VPS 被 GFW 阻断 Google API               |换美国/新加坡/日本 VPS                       |
|`API_KEY_INVALID`                  |Gemini Key 填错                         |去 aistudio.google.com 重新生成（`AIza` 开头）|
|`Broken pipe`                      |偶发连接超时                                |等 2～3 分钟重新发送 PDF                     |
|`⛔ 无访问权限`                          |Telegram ID 不在白名单                     |检查 `.env` 的 `ALLOWED_USER_IDS`       |
|笔记章节不完整 / 有空白灌水                    |Prompt 强约束导致模型截断                      |更新镜像：`docker compose pull`           |
|免费额度用完                             |Gemini Flash 免费每分钟 10 次、每天 500K tokens|等几分钟再发，或换付费 key                      |

-----

## 技术架构

```
用户发 PDF
    ↓
Telegram Bot 接收
    ↓
白名单校验（ALLOWED_USER_IDS）
    ↓
下载到容器内临时文件（不持久化）
    ↓
google.generativeai.upload_file()
    → Gemini File API 原生视觉解析 PDF
    ↓
generate_content([file, prompt])，超时 600 秒
    → 生成 Markdown 中文笔记
    ↓
delete_file() 主动删除 Google 远端文件
    ↓
写入临时 .md → reply_document() 以附件发回
    ↓
finally 块删除本地临时 PDF + .md
```

**依赖极简**：只需 `google-generativeai` 和 `python-telegram-bot`。

-----

## 性能参考（基于 gemini-2.5-flash）

- **处理时间**：30～120 秒（视 PDF 大小）
- **笔记大小**：10～30 KB（精简完整的 Markdown 文本）
- **免费额度**：每天约可处理 **15～20 篇** 普通医学论文，个人使用绰绰有余
- **最大 PDF**：20 MB（Telegram Bot API 上传限制）

-----

## 使用小贴士

- **不要连续发多个 PDF**：免费额度下连续请求容易触发限速或 Broken pipe
- **每次发送间隔 2～3 分钟** 最稳定
- **偶发失败很正常**：大模型输出有随机性，失败重发即可
- **笔记文件越小越好**：10～30 KB 代表 Gemini 精炼输出，几百 KB 可能是灌水空白
