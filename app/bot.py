"""Telegram handlers. I/O only — business logic lives in services/.

`pending_selections` and `disclosure_cache` are process-memory UI state tied to
the telegram interaction; they intentionally stay here (see CLAUDE.md §6-4 —
moving them to shared storage is a later SaaS-transition task).
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN
from services import corp_service, disclosure_service, user_service, watchlist_service

pending_selections: dict[str, dict[str, str]] = {}
disclosure_cache: dict[str, dict] = {}


def build_add_keyboard(results, selected):
    keyboard = []
    for code, name in results:
        label = f"✅ {name}" if code in selected else name
        keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle:{code}:{name}")])
    keyboard.append([InlineKeyboardButton("📥 등록 완료", callback_data="confirm_add")])
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    first_name = update.effective_chat.first_name or ""
    await user_service.ensure_user(chat_id, first_name)
    msg = (
        f"안녕하세요 {first_name}님 👋\n"
        "foRG에 오신 것을 환영합니다.\n\n"
        "📌 명령어 안내\n"
        "/add 기업명 - 관심 기업 등록\n"
        "/remove 기업명 - 관심 기업 삭제\n"
        "/list - 등록된 기업 목록\n"
        "/today - 오늘 중요 공시 전체\n"
        "/mytoday - 내 기업 오늘 공시\n"
        "/keyword - /today 키워드 필터\n"
        "/mykeyword - /mytoday 키워드 필터\n"
        "/settings - 설정"
    )
    await update.message.reply_text(msg)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("기업명을 입력해주세요.\n예) /add 삼성전자")
        return

    corp_name_query = " ".join(context.args)
    await update.message.reply_text(f"🔍 '{corp_name_query}' 검색 중...")
    results = await corp_service.search_corps(corp_name_query)

    if not results:
        await update.message.reply_text(f"'{corp_name_query}'를 찾을 수 없습니다.")
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

    added, skipped = await watchlist_service.add_watchlist(chat_id, first_name, selected)

    msg = ""
    if added:
        msg += "✅ 등록 완료:\n" + "\n".join(f"• {n}" for n in added)
    if skipped:
        msg += "\n\n이미 등록된 기업:\n" + "\n".join(f"• {n}" for n in skipped)
    await query.edit_message_text(msg)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("기업명을 입력해주세요.\n예) /remove 삼성전자")
        return

    corp_name_query = " ".join(context.args)
    watchlist = await watchlist_service.find_by_name(chat_id, corp_name_query)

    if not watchlist:
        await update.message.reply_text(f"'{corp_name_query}'와 일치하는 등록 기업이 없습니다.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {w.corp_name}", callback_data=f"remove:{w.corp_code}:{w.corp_name}")]
        for w in watchlist
    ]
    await update.message.reply_text("삭제할 기업을 선택해주세요.", reply_markup=InlineKeyboardMarkup(keyboard))


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)
    _, corp_code, corp_name = query.data.split(":", 2)

    if not await watchlist_service.remove_by_code(chat_id, corp_code):
        await query.edit_message_text(f"'{corp_name}'을 찾을 수 없습니다.")
        return

    await query.edit_message_text(f"🗑️ '{corp_name}' 삭제 완료!")


async def list_corps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    watchlist = await watchlist_service.list_watchlist(chat_id)

    if not watchlist:
        await update.message.reply_text("등록된 기업이 없습니다.\n/add 기업명으로 등록해주세요.")
        return

    corp_list = "\n".join([f"• {w.corp_name}" for w in watchlist])
    await update.message.reply_text(f"📋 등록된 기업 목록\n\n{corp_list}")


def _disclosure_keyboard(disclosures: list[dict]):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{d['corp_name']} | {d['report_nm'][:20]}",
            callback_data=f"view:{d['rcept_no']}"
        )]
        for d in disclosures[:20]
    ])


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("📋 오늘 중요 공시 불러오는 중...")

    important = await disclosure_service.get_today_important()
    if not important:
        await update.message.reply_text("오늘 중요 공시가 없습니다.")
        return

    keywords = await user_service.get_today_keywords(chat_id)
    if keywords:
        important = disclosure_service.filter_by_keywords(important, keywords)
    if not important:
        await update.message.reply_text("키워드에 해당하는 공시가 없습니다.")
        return

    for d in important:
        disclosure_cache[d["rcept_no"]] = d

    kw_txt = f" (키워드: {', '.join(keywords)})" if keywords else ""
    await update.message.reply_text(
        f"📋 오늘 중요 공시 ({len(important)}건){kw_txt}\n공시를 선택하면 요약을 보여드립니다.",
        reply_markup=_disclosure_keyboard(important),
    )


async def mytoday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("📋 내 기업 오늘 공시 불러오는 중...")

    corp_codes = await watchlist_service.get_corp_codes(chat_id)
    if not corp_codes:
        await update.message.reply_text("등록된 기업이 없습니다.\n/add 기업명으로 등록해주세요.")
        return

    my_disclosures = await disclosure_service.get_mytoday(corp_codes)

    keywords = await user_service.get_mytoday_keywords(chat_id)
    if keywords:
        my_disclosures = disclosure_service.filter_by_keywords(my_disclosures, keywords)

    if not my_disclosures:
        await update.message.reply_text("내 기업의 오늘 공시가 없습니다.")
        return

    for d in my_disclosures:
        disclosure_cache[d["rcept_no"]] = d

    kw_txt = f" (키워드: {', '.join(keywords)})" if keywords else ""
    await update.message.reply_text(
        f"📋 내 기업 오늘 공시 ({len(my_disclosures)}건){kw_txt}\n공시를 선택하면 요약을 보여드립니다.",
        reply_markup=_disclosure_keyboard(my_disclosures),
    )


async def view_disclosure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    receipt_no = query.data.split(":", 1)[1]
    hint = disclosure_cache.get(receipt_no, {})
    await query.message.reply_text(f"⏳ '{hint.get('corp_name', '')}' 공시 요약 중...")

    result = await disclosure_service.summarize_by_receipt(receipt_no, hint)
    if result.get("resolved"):
        disclosure_cache[receipt_no] = result["resolved"]

    msg = (
        f"🏢 <b>{result['corp_name']}</b>\n"
        f"📋 {result['report_nm']}\n\n"
        f"📝 <b>요약</b>\n{result['summary']}\n\n"
        f'🔗 <a href="{result["dart_url"]}">원문 보기</a>'
    )
    await query.message.reply_text(msg, parse_mode="HTML")


async def keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = await user_service.get_user(chat_id)
    if not user:
        await update.message.reply_text("먼저 /start 를 입력해주세요.")
        return

    if not context.args:
        current = user.today_keywords or "없음"
        await update.message.reply_text(
            "📌 현재 /today 키워드: " + current + "\n\n설정: /keyword 전환사채 유상증자\n초기화: /keyword 삭제"
        )
        return

    if context.args[0] == "삭제":
        await user_service.clear_today_keywords(chat_id)
        await update.message.reply_text("✅ /today 키워드가 초기화됐습니다.")
        return

    kw = ",".join(context.args)
    synced = await user_service.set_today_keywords(chat_id, kw)
    sync_txt = " (/mytoday에도 동일 적용)" if synced else ""
    await update.message.reply_text("✅ /today 키워드 설정: " + kw + sync_txt)


async def mykeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = await user_service.get_user(chat_id)
    if not user:
        await update.message.reply_text("먼저 /start 를 입력해주세요.")
        return

    if not context.args:
        current = user.mytoday_keywords or "없음"
        await update.message.reply_text(
            "📌 현재 /mytoday 키워드: " + current + "\n\n설정: /mykeyword 감자 합병\n초기화: /mykeyword 삭제"
        )
        return

    if context.args[0] == "삭제":
        await user_service.clear_mytoday_keywords(chat_id)
        await update.message.reply_text("✅ /mytoday 키워드가 초기화됐습니다.")
        return

    kw = ",".join(context.args)
    await user_service.set_mytoday_keywords(chat_id, kw)
    await update.message.reply_text("✅ /mytoday 키워드 설정: " + kw)


def _settings_view(user):
    sync = bool(user.sync_keywords)
    today_kw = user.today_keywords or "없음"
    mytoday_kw = user.mytoday_keywords or "없음"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
        "키워드 동기화: " + ("ON ✅" if sync else "OFF ❌"),
        callback_data="toggle_sync",
    )]])
    text = (
        "⚙️ 설정\n\n📌 /today 키워드: " + today_kw
        + "\n📌 /mytoday 키워드: " + mytoday_kw
        + "\n\n키워드 동기화 ON 시 /keyword 설정이 /mykeyword에도 동일 적용됩니다."
    )
    return text, keyboard


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = await user_service.get_user(chat_id)
    if not user:
        await update.message.reply_text("먼저 /start 를 입력해주세요.")
        return

    text, keyboard = _settings_view(user)
    await update.message.reply_text(text, reply_markup=keyboard)


async def toggle_sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)

    user = await user_service.toggle_sync(chat_id)
    if not user:
        return

    text, keyboard = _settings_view(user)
    await query.edit_message_text(text, reply_markup=keyboard)


def create_bot_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_corps))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("mytoday", mytoday))
    app.add_handler(CommandHandler("keyword", keyword))
    app.add_handler(CommandHandler("mykeyword", mykeyword))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CallbackQueryHandler(toggle_callback, pattern="^toggle:"))
    app.add_handler(CallbackQueryHandler(confirm_add_callback, pattern="^confirm_add$"))
    app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(view_disclosure_callback, pattern="^view:"))
    app.add_handler(CallbackQueryHandler(toggle_sync_callback, pattern="^toggle_sync$"))
    return app
