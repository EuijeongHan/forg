import uuid
import zipfile
import io
import xml.etree.ElementTree as ET
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User, Watchlist
from config import TELEGRAM_BOT_TOKEN, DART_API_KEY

DART_BASE_URL = "https://opendart.fss.or.kr/api"
EXCLUDE_KEYWORDS = ["기업인수목적", "스팩", "SPAC"]

pending_selections: dict[str, dict[str, str]] = {}
disclosure_cache: dict[str, dict] = {}
_corp_cache: list[tuple[str, str, str]] = []  # (code, name, stock_code)


async def load_corp_cache():
    """DART 기업 코드 XML 캐싱 (앱 시작 후 첫 검색 시 1회만 로드)"""
    global _corp_cache
    if _corp_cache:
        return
    url = f"{DART_BASE_URL}/corpCode.xml"
    params = {"crtfc_key": DART_API_KEY}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=30)
            response.raise_for_status()
            zip_file = zipfile.ZipFile(io.BytesIO(response.content))
            xml_content = zip_file.read("CORPCODE.xml")
            root = ET.fromstring(xml_content)
            _corp_cache = [
                (
                    corp.findtext("corp_code", ""),
                    corp.findtext("corp_name", ""),
                    corp.findtext("stock_code", ""),
                )
                for corp in root.findall("list")
            ]
            print(f"기업 코드 캐시 로드 완료: {len(_corp_cache)}개")
        except Exception as e:
            print(f"기업 코드 캐시 로드 실패: {e}")


async def search_corps(corp_name: str) -> list[tuple[str, str]]:
    await load_corp_cache()

    exact = []
    starts_with = []
    partial = []
    seen_names = set()

    for code, name, stock_code in _corp_cache:
        if not (stock_code and stock_code.strip()):
            continue
        if any(kw in name for kw in EXCLUDE_KEYWORDS):
            continue
        if name in seen_names:
            continue

        seen_names.add(name)

        if name == corp_name:
            exact.append((code, name))
        elif name.startswith(corp_name):
            starts_with.append((code, name))
        elif corp_name in name:
            partial.append((code, name))

    starts_with.sort(key=lambda x: x[1])
    partial.sort(key=lambda x: x[1])
    return (exact + starts_with + partial)[:20]


async def get_or_create_user(session, chat_id: str, first_name: str) -> None:
    result = await session.execute(select(User).where(User.chat_id == chat_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(chat_id=chat_id, first_name=first_name)
        session.add(user)
        await session.flush()


def build_add_keyboard(results: list[tuple[str, str]], selected: dict[str, str]) -> InlineKeyboardMarkup:
    keyboard = []
    for code, name in results:
        label = f"✅ {name}" if code in selected else name
        keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle:{code}:{name}")])
    keyboard.append([InlineKeyboardButton("📥 등록 완료", callback_data="confirm_add")])
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    first_name = update.effective_chat.first_name or ""

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(chat_id=chat_id, first_name=first_name)
            session.add(user)
            await session.commit()
            await update.message.reply_text(
                f"안녕하세요 {first_name}님 👋\n"
                f"foRG에 오신 것을 환영합니다.\n\n"
                f"📌 명령어 안내\n"
                f"/add 기업명 - 관심 기업 등록\n"
                f"/remove 기업명 - 관심 기업 삭제\n"
                f"/list - 등록된 기업 목록 확인\n"
                f"/today - 오늘 중요 공시 전체\n"
                f"/mytoday - 내 기업 오늘 공시"
            )
        else:
            await update.message.reply_text(
                f"반갑습니다 {first_name}님 👋\n"
                f"이미 등록된 계정입니다.\n\n"
                f"📌 명령어 안내\n"
                f"/add 기업명 - 관심 기업 등록\n"
                f"/remove 기업명 - 관심 기업 삭제\n"
                f"/list - 등록된 기업 목록 확인\n"
                f"/today - 오늘 중요 공시 전체\n"
                f"/mytoday - 내 기업 오늘 공시"
            )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text("기업명을 입력해주세요.\n예) /add 삼성전자")
        return

    corp_name_query = " ".join(context.args)
    await update.message.reply_text(f"🔍 '{corp_name_query}' 검색 중...")

    results = await search_corps(corp_name_query)

    if not results:
        await update.message.reply_text(
            f"'{corp_name_query}'를 찾을 수 없습니다.\n정확한 기업명으로 다시 시도해주세요."
        )
        return

    context.user_data["search_results"] = {code: name for code, name in results}
    pending_selections[chat_id] = {}

    reply_markup = build_add_keyboard(results, {})
    await update.message.reply_text(
        f"🔍 '{corp_name_query}' 검색 결과입니다.\n등록할 기업을 선택하고 완료 버튼을 눌러주세요.",
        reply_markup=reply_markup,
    )


async def toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.from_user.id)
    _, corp_code, corp_name = query.data.split(":", 2)

    if chat_id not in pending_selections:
        pending_selections[chat_id] = {}

    if corp_code in pending_selections[chat_id]:
        del pending_selections[chat_id][corp_code]
    else:
        pending_selections[chat_id][corp_code] = corp_name

    search_results = context.user_data.get("search_results", {})
    results = list(search_results.items())
    reply_markup = build_add_keyboard([(c, n) for c, n in results], pending_selections[chat_id])
    await query.edit_message_reply_markup(reply_markup=reply_markup)


async def confirm_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.from_user.id)
    first_name = query.from_user.first_name or ""
    selected = pending_selections.pop(chat_id, {})

    if not selected:
        await query.edit_message_text("선택된 기업이 없습니다.")
        return

    added = []
    skipped = []

    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, chat_id, first_name)

        for corp_code, corp_name in selected.items():
            existing = await session.execute(
                select(Watchlist).where(
                    Watchlist.chat_id == chat_id,
                    Watchlist.corp_code == corp_code,
                )
            )
            if existing.scalar_one_or_none():
                skipped.append(corp_name)
                continue

            session.add(Watchlist(
                id=str(uuid.uuid4()),
                chat_id=chat_id,
                corp_code=corp_code,
                corp_name=corp_name,
            ))
            added.append(corp_name)

        await session.commit()

    msg = ""
    if added:
        msg += "✅ 등록 완료:\n" + "\n".join(f"• {n}" for n in added)
    if skipped:
        msg += "\n\n⚠️ 이미 등록된 기업:\n" + "\n".join(f"• {n}" for n in skipped)

    await query.edit_message_text(msg)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text("기업명을 입력해주세요.\n예) /remove 삼성전자")
        return

    corp_name_query = " ".join(context.args)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(
                Watchlist.chat_id == chat_id,
                Watchlist.corp_name.ilike(f"%{corp_name_query}%"),
            )
        )
        watchlist = result.scalars().all()

    if not watchlist:
        await update.message.reply_text(f"'{corp_name_query}'와 일치하는 등록 기업이 없습니다.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {w.corp_name}", callback_data=f"remove:{w.corp_code}:{w.corp_name}")]
        for w in watchlist
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("삭제할 기업을 선택해주세요.", reply_markup=reply_markup)


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.from_user.id)
    _, corp_code, corp_name = query.data.split(":", 2)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(
                Watchlist.chat_id == chat_id,
                Watchlist.corp_code == corp_code,
            )
        )
        watchlist = result.scalar_one_or_none()

        if not watchlist:
            await query.edit_message_text(f"'{corp_name}'을 찾을 수 없습니다.")
            return

        await session.delete(watchlist)
        await session.commit()

    await query.edit_message_text(f"🗑️ '{corp_name}' 삭제 완료!")


async def list_corps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(Watchlist.chat_id == chat_id)
        )
        watchlist = result.scalars().all()

    if not watchlist:
        await update.message.reply_text(
            "등록된 기업이 없습니다.\n/add 기업명으로 등록해주세요."
        )
        return

    corp_list = "\n".join([f"• {w.corp_name}" for w in watchlist])
    await update.message.reply_text(f"📋 등록된 기업 목록\n\n{corp_list}")


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 오늘 중요 공시 불러오는 중...")

    from dart import fetch_recent_disclosures, is_important
    disclosures = await fetch_recent_disclosures()
    important = [d for d in disclosures if is_important(d.get("report_nm", ""))]

    if not important:
        await update.message.reply_text("오늘 중요 공시가 없습니다.")
        return

    for d in important:
        disclosure_cache[d["rcept_no"]] = d

    keyboard = [
        [InlineKeyboardButton(
            f"{d['corp_name']} | {d['report_nm'][:20]}",
            callback_data=f"view:{d['rcept_no']}"
        )]
        for d in important[:20]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📋 오늘 중요 공시 ({len(important)}건)\n공시를 선택하면 요약을 보여드립니다.",
        reply_markup=reply_markup,
    )


async def mytoday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("📋 내 기업 오늘 공시 불러오는 중...")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(Watchlist.chat_id == chat_id)
        )
        watchlist = result.scalars().all()

    if not watchlist:
        await update.message.reply_text("등록된 기업이 없습니다.\n/add 기업명으로 등록해주세요.")
        return

    from dart import fetch_recent_disclosures
    disclosures = await fetch_recent_disclosures()
    my_corp_codes = {w.corp_code for w in watchlist}
    my_disclosures = [d for d in disclosures if d.get("corp_code") in my_corp_codes]

    if not my_disclosures:
        await update.message.reply_text("내 기업의 오늘 공시가 없습니다.")
        return

    for d in my_disclosures:
        disclosure_cache[d["rcept_no"]] = d

    keyboard = [
        [InlineKeyboardButton(
            f"{d['corp_name']} | {d['report_nm'][:20]}",
            callback_data=f"view:{d['rcept_no']}"
        )]
        for d in my_disclosures[:20]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📋 내 기업 오늘 공시 ({len(my_disclosures)}건)\n공시를 선택하면 요약을 보여드립니다.",
        reply_markup=reply_markup,
    )


async def view_disclosure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    receipt_no = query.data.split(":", 1)[1]
    disclosure = disclosure_cache.get(receipt_no, {})
    corp_name = disclosure.get("corp_name", "")
    report_nm = disclosure.get("report_nm", "")
    corp_code = disclosure.get("corp_code", "")
    rcept_dt = disclosure.get("rcept_dt", "")

    # 캐시 미스 시 DART API로 재조회
    if not corp_code or not rcept_dt:
        from dart import fetch_recent_disclosures
        disclosures = await fetch_recent_disclosures()
        for d in disclosures:
            if d["rcept_no"] == receipt_no:
                corp_name = d.get("corp_name", corp_name)
                report_nm = d.get("report_nm", report_nm)
                corp_code = d.get("corp_code", "")
                rcept_dt = d.get("rcept_dt", "")
                disclosure_cache[receipt_no] = d
                break

    await query.message.reply_text(f"⏳ '{corp_name}' 공시 요약 중...")

    from dart import fetch_disclosure_detail, fetch_typed_disclosure
    from summarizer import summarize_disclosure, summarize_typed_disclosure
    
    typed_data = {}
    if corp_code and rcept_dt:
        typed_data = await fetch_typed_disclosure(corp_code, receipt_no, report_nm, rcept_dt)
    
    if typed_data:
        summary = await summarize_typed_disclosure(corp_name, report_nm, typed_data)
    else:
        # 2순위: 원문 크롤링
        content = await fetch_disclosure_detail(receipt_no)
        summary = await summarize_disclosure(corp_name, report_nm, content)

    dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
    msg = (
        f"🏢 <b>{corp_name}</b>\n"
        f"📋 {report_nm}\n\n"
        f"📝 <b>요약</b>\n{summary}\n\n"
        f'🔗 <a href="{dart_url}">원문 보기</a>'
    )
    await query.message.reply_text(msg, parse_mode="HTML")


def create_bot_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_corps))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("mytoday", mytoday))
    app.add_handler(CallbackQueryHandler(toggle_callback, pattern="^toggle:"))
    app.add_handler(CallbackQueryHandler(confirm_add_callback, pattern="^confirm_add$"))
    app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(view_disclosure_callback, pattern="^view:"))
    return app
