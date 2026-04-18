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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY     = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL       = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip().isdigit()
}
if not ALLOWED_USER_IDS:
    logger.warning("ALLOWED_USER_IDS is empty — bot will reject ALL users!")
else:
    logger.info("Whitelist: %s", ALLOWED_USER_IDS)

genai.configure(api_key=GEMINI_API_KEY)

ANALYSIS_PROMPT = """
你是医学文献分析专家。请对这份 PDF 生成中文 Markdown 笔记，严格按以下章节顺序输出，每个章节都必须完整。

**要求：** 中文为主；专业术语附英文（如：腺癌 (Adenocarcinoma)）；精确抄录数据（p值/OR/HR/CI/n等）；不插入图片链接。

# 📌 全文总结
400~600字，依次写：研究背景、研究方法、核心结论（附关键数值）、临床意义。

# 📝 逐章节笔记
按 Abstract→Methods→Results→Discussion 顺序，每节用3条列表：
- 核心要点
- 关键数据
- 亮点或疑问

# 🔬 数据与表格摘录
只摘录最关键的1~2个表格，用简洁 Markdown 表格重现，每表下1句话结论。图片仅列编号+1句说明。

# 🔑 核心术语对照表
15~20个核心术语：

| 英文 | 中文 | 语境解释 |
|------|------|----------|

# ⭐ 亮点与局限
**方法学亮点：** 
**重要临床发现：** 
**研究局限：** 

# ❓ 延伸思考
4~5个研究问题，每个：**标题：** 描述

**按顺序依次输出，如内容过多请精简前面章节。**

"""


def _upload_and_wait(pdf_path: str) -> genai_types.File:
    logger.info("Uploading to Gemini File API: %s", pdf_path)
    f = genai.upload_file(path=pdf_path, mime_type="application/pdf")
    logger.info("Uploaded -> %s  state=%s", f.name, f.state.name)
    deadline = time.time() + 180
    while f.state.name == "PROCESSING":
        if time.time() > deadline:
            raise TimeoutError("Gemini File API processing timeout (180 s)")
        time.sleep(4)
        f = genai.get_file(f.name)
        logger.info("  state=%s", f.state.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini File API entered state {f.state.name!r}")
    return f


def _delete_remote(f: genai_types.File) -> None:
    try:
        genai.delete_file(f.name)
        logger.info("Deleted remote file: %s", f.name)
    except Exception as exc:
        logger.warning("Could not delete remote file %s: %s", f.name, exc)


def _sync_analyze(pdf_path: str) -> str:
    last_error = None
    for attempt in range(1, 4):
        remote = None
        try:
            logger.info("Analyze attempt %d/3", attempt)
            remote = _upload_and_wait(pdf_path)
            model = genai.GenerativeModel(model_name=GEMINI_MODEL)
            response = model.generate_content(
                contents=[remote, ANALYSIS_PROMPT],
                generation_config=genai_types.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=32768,
                ),
                request_options={"timeout": 600},
            )
            return response.text
        except Exception as exc:
            last_error = exc
            logger.warning("Attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(5 * attempt)  # 5s, 10s 递增等待
        finally:
            if remote is not None:
                _delete_remote(remote)
    raise last_error



def _write_md(notes: str, original_name: str) -> str:
    stem = Path(original_name).stem[:60]
    fd, path = tempfile.mkstemp(prefix=f"{stem}_notes_", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(notes)
    return path


def _is_allowed(update: Update) -> bool:
    return update.effective_user.id in ALLOWED_USER_IDS


async def _reject(update: Update) -> None:
    uid = update.effective_user.id
    name = update.effective_user.username or update.effective_user.first_name
    logger.warning("Rejected unauthorized user: %s (%d)", name, uid)
    await update.message.reply_text("⛔ 无访问权限。")


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text(
        "👋 你好！我是 PDF Scholar Bot。\n\n"
        "📄 直接发给我一份 PDF（学术论文 / 医学文献 / 双语版均可）\n\n"
        "我会用 Gemini 对整份 PDF 进行视觉解析，然后把中文笔记以 .md 附件发回。\n\n"
        "/help — 使用说明\n/status — 当前配置"
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text(
        f"📚 发送 PDF 文件（≤20MB），收到 .md 笔记附件。\n\n当前模型：{GEMINI_MODEL}"
    )


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text(
        f"⚙️ 模型：{GEMINI_MODEL}\nGemini Key：{'✅' if GEMINI_API_KEY else '❌'}"
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ 请发送 PDF 文件。")
        return
    await update.message.reply_text(
        f"⏳ 正在解析《{doc.file_name}》，请稍候...（通常30~120秒）"
    )
    pdf_path: str | None = None
    md_path: str | None = None
    try:
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(pdf_path)
        import asyncio
        loop = asyncio.get_running_loop()
        notes = await loop.run_in_executor(None, _sync_analyze, pdf_path)
        md_path = _write_md(notes, doc.file_name)
        with open(md_path, "rb") as fh:
            await update.message.reply_document(
                document=fh,
                filename=Path(md_path).name,
                caption=f"✅ 《{doc.file_name}》中文学术笔记\n模型：{GEMINI_MODEL}",
            )
    except Exception as exc:
        logger.exception("Error processing %s", doc.file_name)
        await update.message.reply_text(f"❌ 处理出错：{type(exc).__name__}: {exc}")
    finally:
        for path in (pdf_path, md_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def handle_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text("请直接发送 PDF 文件。输入 /help 查看说明。")


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("PDF Scholar Bot started (model=%s)", GEMINI_MODEL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
