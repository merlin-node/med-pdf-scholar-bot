“””
PDF Scholar Bot — Gemini File API edition
• 整份 PDF 通过 google.generativeai upload_file() 上传给 Google 原生解析
• Gemini 直接视觉解析 PDF，完整支持医学表格、图片 OCR、双栏排版
• 笔记写入临时 .md 文件，以文件附件形式发回 Telegram
• 所有临时文件处理完立即删除，VPS 不留存任何内容
“””

import os
import sys
import time
import logging
import tempfile
from pathlib import Path

import google.generativeai as genai
from google.generativeai import types as genai_types

from telegram import Update
from telegram.ext import (
ApplicationBuilder, CommandHandler, MessageHandler,
filters, ContextTypes,
)

# ─────────────────────────── Logging ────────────────────────────

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s [%(levelname)s] %(name)s: %(message)s”,
)
logger = logging.getLogger(__name__)

# ──────────────────────────── Config ────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ[“TELEGRAM_BOT_TOKEN”]          # required
GEMINI_API_KEY     = os.environ[“GEMINI_API_KEY”]              # required
GEMINI_MODEL       = os.getenv(“GEMINI_MODEL”, “gemini-2.5-flash”)

# ── Whitelist ────────────────────────────────────────────────────

# Comma-separated Telegram user IDs allowed to use this bot.

# Example in .env:  ALLOWED_USER_IDS=123456789,987654321

# Leave empty → bot rejects everyone and logs a warning.

_raw_ids = os.getenv(“ALLOWED_USER_IDS”, “”)
ALLOWED_USER_IDS: set[int] = {
int(uid.strip()) for uid in _raw_ids.split(”,”) if uid.strip().isdigit()
}
if not ALLOWED_USER_IDS:
logger.warning(“ALLOWED_USER_IDS is empty — bot will reject ALL users!”)
else:
logger.info(“Whitelist: %s”, ALLOWED_USER_IDS)

genai.configure(api_key=GEMINI_API_KEY)

# ─────────────────────────── Prompt ─────────────────────────────

ANALYSIS_PROMPT = “””  
你是一位专业的医学文献分析专家，精通内外科、肿瘤学、病理学、影像学等各科室的中英双语学术论文阅读与解析。

请对这份医学 PDF 文献进行完整深度分析，严格按照以下 Markdown 结构输出笔记。

**全局要求：**

- 全程使用中文，专业术语首次出现时保留英文原名并附中文，格式为：中文译名（English Term）
- 所有数据（p值、OR/HR/RR、置信区间、样本量、生存率等）务必精确抄录，不得捏造或模糊化
- 不要在输出中插入任何图片链接或 Markdown 图片语法，图表内容一律用文字+表格描述
- 内容完整优先，每个章节都必须输出，不得省略

-----

# 📌 全文总结

600～1000 字，结构化撰写，依次涵盖：

1. **研究背景**：该疾病/问题的流行病学背景、现有诊疗痛点、本研究动机
1. **研究设计与方法**：研究类型（RCT/队列/病例报告等）、样本来源、干预措施或暴露因素、主要结局指标
1. **核心结论**：主要阳性/阴性发现，附关键数值
1. **临床意义**：对临床实践、指南制定或未来研究的影响

-----

# 📝 逐章节笔记

按原文章节顺序（Abstract → Introduction → Methods → Results → Discussion → Conclusion）逐节整理。
每节固定格式：

- **核心内容要点**（2～5 条，用简洁中文概括，保留原文关键数字）
- **关键数据或发现**（精确列出统计数据：样本量 n=、p=、OR/HR=、95%CI、生存率等）
- **本节亮点或值得关注的疑问**（临床启示、方法论亮点、或需批判性思考的地方）

-----

# 🔬 数据与表格摘录

识别并整理论文中所有重要表格和图表（包括扫描图片中的表格，OCR 后重建）：

对每张表格/图：

- 标注原始编号与完整标题（中英对照）
- 用标准 Markdown 表格完整重现数据，列头保留英文并加中文注释
- 表格下方用 2～4 句话说明该表的核心临床结论

对每张图（流程图、KM曲线、森林图、病理图等）：

- 详细文字描述图的内容、趋势、分组差异
- 提炼图示的核心结论

-----

# 🔑 核心术语对照表

列出本文 15～25 个最重要的医学专业术语，覆盖疾病名称、诊断指标、治疗手段、病理/影像术语、统计学概念：

|英文术语|中文译名|简短解释（结合本文语境）|
|----|----|------------|

-----

# ⭐ 亮点与局限性

**方法学亮点：**
（研究设计的优势、创新点、统计方法的合理性等，逐条列出）

**重要临床发现：**
（对临床实践有直接指导意义的结论，逐条列出并附数据支撑）

**研究局限性：**
（样本量、随访时间、混杂因素、外部效度、偏倚风险等，逐条列出）

-----

# ❓ 延伸思考问题

列出 4～6 个有深度的临床或研究问题，格式为：
**问题标题：** 具体问题描述（结合本文结论，提出值得进一步研究或临床验证的方向）”””

# ──────────────────────── Gemini helpers ────────────────────────

def _upload_and_wait(pdf_path: str) -> genai_types.File:
“”“Upload PDF to Gemini File API, block until state == ACTIVE.”””
logger.info(“Uploading to Gemini File API: %s”, pdf_path)
f = genai.upload_file(path=pdf_path, mime_type=“application/pdf”)
logger.info(“Uploaded → %s  state=%s”, f.name, f.state.name)

```
deadline = time.time() + 180          # 3-minute max wait
while f.state.name == "PROCESSING":
    if time.time() > deadline:
        raise TimeoutError("Gemini File API processing timeout (180 s)")
    time.sleep(4)
    f = genai.get_file(f.name)
    logger.info("  state=%s", f.state.name)

if f.state.name != "ACTIVE":
    raise RuntimeError(f"Gemini File API entered state {f.state.name!r}")
return f
```

def _delete_remote(f: genai_types.File) -> None:
try:
genai.delete_file(f.name)
logger.info(“Deleted remote file: %s”, f.name)
except Exception as exc:
logger.warning(“Could not delete remote file %s: %s”, f.name, exc)

def _sync_analyze(pdf_path: str) -> str:
“””
Synchronous end-to-end: upload → wait → generate → delete remote.
Safe to call from run_in_executor (Gemini SDK is synchronous).
“””
remote = None
try:
remote = _upload_and_wait(pdf_path)
model = genai.GenerativeModel(model_name=GEMINI_MODEL)
response = model.generate_content(
contents=[remote, ANALYSIS_PROMPT],
generation_config=genai_types.GenerationConfig(
temperature=0.3,
max_output_tokens=32768,
),
request_options={“timeout”: 600},
)
return response.text
finally:
if remote is not None:
_delete_remote(remote)

# ───────────────────────── File helper ──────────────────────────

def _write_md(notes: str, original_name: str) -> str:
“”“Write notes to a named temp .md file; returns path. Caller must unlink.”””
stem = Path(original_name).stem[:60]
fd, path = tempfile.mkstemp(prefix=f”{stem}_notes_”, suffix=”.md”)
with os.fdopen(fd, “w”, encoding=“utf-8”) as fh:
fh.write(notes)
return path

# ──────────────────────── Whitelist guard ───────────────────────

def _is_allowed(update: Update) -> bool:
“”“Return True if the sender is in the whitelist.”””
return update.effective_user.id in ALLOWED_USER_IDS

async def _reject(update: Update) -> None:
“”“Silently log and send a generic rejection — no info leaked.”””
uid = update.effective_user.id
name = update.effective_user.username or update.effective_user.first_name
logger.warning(“Rejected unauthorized user: %s (%d)”, name, uid)
await update.message.reply_text(“⛔ 无访问权限。”)

# ──────────────────────── Bot handlers ──────────────────────────

async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
if not _is_allowed(update):
await _reject(update)
return
await update.message.reply_text(
“👋 你好！我是 PDF Scholar Bot。\n\n”
“📄 直接发给我一份 PDF（学术论文 / 医学文献 / 双语版均可）\n\n”
“我会用 Gemini 对整份 PDF 进行视觉解析（含表格 / 图片 OCR），”
“然后把中文笔记以 .md 附件发回给你。\n\n”
“/help — 使用说明\n”
“/status — 当前配置”
)

async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
if not _is_allowed(update):
await _reject(update)
return
await update.message.reply_text(
“📚 使用说明\n\n”
“1. 发送 PDF 文件（Telegram 限制 ≤ 20 MB）\n”
“2. Bot 把整份 PDF 上传至 Gemini File API\n”
“   └ Gemini 原生解析，支持视觉表格识别 / OCR\n”
“3. 生成中文笔记后，以 .md 文件附件发回\n”
“4. 处理完所有临时文件立即删除\n\n”
f”当前模型：{GEMINI_MODEL}”
)

async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
if not _is_allowed(update):
await _reject(update)
return
await update.message.reply_text(
“⚙️ 当前配置\n\n”
f”模型：{GEMINI_MODEL}\n”
f”Gemini API Key：{‘✅ 已设置’ if GEMINI_API_KEY else ‘❌ 未设置’}”
)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not _is_allowed(update):
await _reject(update)
return

```
doc = update.message.document

if not doc.file_name.lower().endswith(".pdf"):
    await update.message.reply_text("❌ 请发送 PDF 文件（.pdf）")
    return

await update.message.reply_text(
    f"⏳ 收到《{doc.file_name}》，正在上传至 Gemini 解析…\n"
    "（通常 30～120 秒，视 PDF 大小而定）"
)

pdf_path: str | None = None
md_path:  str | None = None

try:
    # 1. Download PDF → temp file
    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(pdf_path)
    size_kb = os.path.getsize(pdf_path) // 1024
    logger.info("Downloaded %s → %s (%d KB)", doc.file_name, pdf_path, size_kb)

    # 2. Analyze with Gemini (blocking SDK call → thread pool)
    import asyncio
    loop = asyncio.get_running_loop()
    notes = await loop.run_in_executor(None, _sync_analyze, pdf_path)

    # 3. Write .md attachment
    md_path = _write_md(notes, doc.file_name)

    # 4. Send as Telegram document (file attachment, not inline text)
    with open(md_path, "rb") as fh:
        await update.message.reply_document(
            document=fh,
            filename=Path(md_path).name,
            caption=(
                f"✅ 《{doc.file_name}》中文学术笔记\n"
                f"模型：{GEMINI_MODEL}"
            ),
        )
    logger.info("Sent notes: %s", Path(md_path).name)

except Exception as exc:
    logger.exception("Error processing %s", doc.file_name)
    await update.message.reply_text(f"❌ 处理出错：{type(exc).__name__}: {exc}")

finally:
    # 5. Delete both temp files — nothing persists on VPS
    for path in (pdf_path, md_path):
        if path:
            try:
                os.unlink(path)
                logger.info("Deleted temp: %s", path)
            except OSError as exc:
                logger.warning("Could not delete %s: %s", path, exc)
```

async def handle_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
if not _is_allowed(update):
await _reject(update)
return
await update.message.reply_text(“请直接发送 PDF 文件。输入 /help 查看说明。”)

# ─────────────────────────── Main ───────────────────────────────

def main() -> None:
app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
app.add_handler(CommandHandler(“start”,  cmd_start))
app.add_handler(CommandHandler(“help”,   cmd_help))
app.add_handler(CommandHandler(“status”, cmd_status))
app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
logger.info(“PDF Scholar Bot started (model=%s)”, GEMINI_MODEL)
app.run_polling(drop_pending_updates=True)

if **name** == “**main**”:
main()
