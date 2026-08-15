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
    greeting = f"안녕하세요 {first_name}님 👋" if first_name else "안녕하세요 👋"
    msg = (
        f"{greeting}\n"
        "DART 공시 실시간 알림 서비스 forG입니다.\n\n"
        "하루 수천 건의 공시 속에서 중요한 것을 놓치지 않도록,\n"
        "관심 기업의 핵심 공시만 골라 AI 요약과 함께 즉시 보내드립니다.\n\n"
        "🚀 이렇게 시작하세요\n"
        "1) /add 삼성전자 — 관심 기업 등록\n"
        "2) 이후 새 중요 공시가 올라오면 자동으로 알림이 도착합니다\n"
        "   (상장폐지·회생절차 같은 중대 사건은 관심기업이 아니어도 🚨 즉시 알림)\n\n"
        "자주 쓰는 명령\n"
        "/today - 관심기업 오늘 공시 (예: /today 유상증자)\n"
        "/market - 전체 시장 오늘 공시\n"
        "/list - 등록된 기업 목록\n"
        "/help - 전체 사용법\n\n"
        "ℹ️ forG는 DART 공시를 AI로 요약해 전달하는 참고용 도구입니다.\n"
        "투자 자문·종목 추천 서비스가 아니며, AI 요약에는 오류·지연이 있을 수 있습니다.\n"
        "투자 판단 전 반드시 DART 원문을 확인하세요."
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


PAGE_SIZE = 20


def _disclosure_keyboard(disclosures: list[dict]):
    # 텔레그램 인라인 키보드 한도와 가독성 때문에 한 번에 PAGE_SIZE건만 보여준다.
    # 잘린 사실은 호출자가 문구로 알려준다 — 조용히 자르면 사용자는 나머지 공시가
    # 존재하는지조차 모른다.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{d['corp_name']} | {d['report_nm'][:20]}",
            callback_data=f"view:{d['rcept_no']}"
        )]
        for d in disclosures[:PAGE_SIZE]
    ])


async def _send_query_result(update, result, empty_hint: str = ""):
    """조회 결과를 표시한다. 빈 결과는 원인을 구분해 안내한다."""
    if not result.items:
        if result.filtered_to_empty:
            await update.message.reply_text(
                f"'{result.query}'에 해당하는 공시가 없습니다.\n"
                f"(검색어를 빼면 {result.total_before_query}건)"
            )
        else:
            msg = "오늘 공시가 없습니다."
            if empty_hint:
                msg += f"\n{empty_hint}"
            await update.message.reply_text(msg)
        return

    for d in result.items:
        disclosure_cache[d["rcept_no"]] = d

    shown = min(len(result.items), PAGE_SIZE)
    text = result.header() + "\n공시를 선택하면 요약을 보여드립니다."
    if len(result.items) > PAGE_SIZE:
        text += f"\n(최근 {shown}건 표시 — 검색어를 붙이면 좁힐 수 있습니다. 예: /today 유상증자)"
    await update.message.reply_text(text, reply_markup=_disclosure_keyboard(result.items))


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """관심기업의 오늘 중요 공시. 인자를 주면 이번 조회에만 적용되는 검색어."""
    chat_id = str(update.effective_chat.id)
    query = " ".join(context.args) if context.args else ""

    corp_codes = await watchlist_service.get_corp_codes(chat_id)
    if not corp_codes:
        await update.message.reply_text(
            "등록된 관심기업이 없습니다.\n"
            "/add 삼성전자 처럼 기업을 등록하면 오늘 공시를 모아 보여드립니다.\n"
            "전체 시장을 보려면 /market 을 입력하세요."
        )
        return

    await update.message.reply_text("📋 관심기업 오늘 공시 불러오는 중...")
    result = await disclosure_service.query_disclosures(
        scope="watchlist", corp_codes=corp_codes, important_only=True, query=query
    )
    await _send_query_result(
        update, result, empty_hint="전체 시장을 보려면 /market 을 입력하세요."
    )


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """전체 시장의 오늘 중요 공시. 인자를 주면 이번 조회에만 적용되는 검색어."""
    query = " ".join(context.args) if context.args else ""
    await update.message.reply_text("📋 전체 시장 오늘 공시 불러오는 중...")
    result = await disclosure_service.query_disclosures(
        scope="market", important_only=True, query=query
    )
    await _send_query_result(update, result)


async def mytoday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """구 명령 — /today가 관심기업 기준이 되어 역할이 같아졌다. 당분간 별칭으로 둔다."""
    await today(update, context)


async def view_disclosure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    receipt_no = query.data.split(":", 1)[1]
    hint = disclosure_cache.get(receipt_no, {})
    await query.message.reply_text(f"⏳ '{hint.get('corp_name', '')}' 공시 요약 중...")

    result = await disclosure_service.summarize_by_receipt(receipt_no, hint)
    if result.get("resolved"):
        disclosure_cache[receipt_no] = result["resolved"]

    # 알림 경로와 동일한 빌더 사용 — HTML 이스케이프 일원화
    from notifier import build_disclosure_message
    msg = build_disclosure_message(
        result["corp_name"], result["report_nm"], receipt_no, result["summary"]
    )
    await query.message.reply_text(msg, parse_mode="HTML")


async def keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """폐기 예정 — 영구 저장 필터는 없앴다. 새 사용법만 안내하고 상태는 바꾸지 않는다.

    저장된 키워드가 조회 결과를 조용히 바꾸는 것이 기존 설계의 핵심 문제였다.
    이제 검색어는 조회할 때마다 인자로 준다. DB 컬럼은 롤백 여지를 위해 남겨두되
    더 이상 읽거나 쓰지 않는다.
    """
    given = " ".join(context.args) if context.args else "유상증자"
    await update.message.reply_text(
        "/keyword 는 더 이상 사용하지 않습니다.\n\n"
        "이제 검색어를 조회할 때 함께 입력합니다. 저장되지 않으므로 다음 조회에\n"
        "영향을 주지 않습니다.\n\n"
        f"전체 시장에서 찾기:  /market {given}\n"
        f"관심기업에서 찾기:  /today {given}\n\n"
        "자세한 사용법은 /help"
    )


async def mykeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """폐기 예정 — keyword와 동일. /today에 인자를 붙이는 방식으로 대체한다."""
    given = " ".join(context.args) if context.args else "유상증자"
    await update.message.reply_text(
        "/mykeyword 는 더 이상 사용하지 않습니다.\n\n"
        f"관심기업 공시를 검색어로 좁히려면:  /today {given}\n"
        "검색어는 이번 조회에만 적용되고 저장되지 않습니다.\n\n"
        "자세한 사용법은 /help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 forG 사용법\n\n"
        "■ 관심기업\n"
        "/add 기업명 - 관심기업 등록 (예: /add 삼성전자)\n"
        "/remove 기업명 - 관심기업 삭제\n"
        "/list - 등록된 기업 목록\n\n"
        "■ 공시 조회\n"
        "/today - 관심기업의 오늘 중요 공시\n"
        "/today 유상증자 - 위 결과를 검색어로 좁히기\n"
        "/market - 전체 시장의 오늘 중요 공시\n"
        "/market 감사보고서 - 위 결과를 검색어로 좁히기\n\n"
        "검색어는 이번 조회에만 적용되고 저장되지 않습니다.\n\n"
        "■ 알림 (자동, 설정 불필요)\n"
        "🚨 긴급 - 상장폐지·회생절차·부도·횡령배임은 관심기업이 아니어도 즉시 알림\n"
        "⚠️ 중요 - 관심기업의 증자·CB·실적·5%지분·내부자매매·배당 등 즉시 알림\n"
        "📄 참고 - 관심기업의 그 외 공시(정기보고서·IR 등)는 매일 18:30 묶음 전달\n\n"
        "■ 기타\n"
        "/settings - 설정\n"
        "/deletedata - 내 데이터 전체 삭제\n\n"
        "ℹ️ forG는 DART 공시를 AI로 요약해 전달하는 참고용 도구입니다.\n"
        "투자 자문·종목 추천 서비스가 아니며, AI 요약에는 오류·지연이 있을 수 있습니다.\n"
        "투자 판단 전 반드시 DART 원문을 확인하세요."
    )


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


async def deletedata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개인정보처리방침이 안내한 삭제 요청 경로. 되돌릴 수 없으므로 확인을 받는다."""
    chat_id = str(update.effective_chat.id)
    user = await user_service.get_user(chat_id)
    if not user:
        await update.message.reply_text("삭제할 데이터가 없습니다.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 모두 삭제", callback_data="confirm_delete")],
        [InlineKeyboardButton("취소", callback_data="cancel_delete")],
    ])
    await update.message.reply_text(
        "내 데이터를 모두 삭제합니다.\n\n"
        "삭제 대상: 관심기업 목록, 알림 발송 기록, 계정 정보\n"
        "삭제하면 알림이 중단되고 되돌릴 수 없습니다. 다시 쓰려면 /start 로 처음부터 등록해야 합니다.",
        reply_markup=keyboard,
    )


async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)

    counts = await user_service.delete_user_data(chat_id)
    # disclosure_cache는 rcept_no 기준 공용 캐시라 개인정보가 아니다(공시 원문).
    # 사용자별 UI 상태만 정리한다.
    pending_selections.pop(chat_id, None)
    await query.edit_message_text(
        "삭제를 완료했습니다.\n"
        f"관심기업 {counts['watchlist']}건, 발송 기록 {counts['seen']}건, 계정 {counts['user']}건\n\n"
        "다시 이용하시려면 /start 를 입력해주세요."
    )


async def cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("삭제를 취소했습니다. 데이터는 그대로 유지됩니다.")


def create_bot_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_corps))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mytoday", mytoday))
    app.add_handler(CommandHandler("keyword", keyword))
    app.add_handler(CommandHandler("mykeyword", mykeyword))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("deletedata", deletedata))
    app.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern="^confirm_delete$"))
    app.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern="^cancel_delete$"))
    app.add_handler(CallbackQueryHandler(toggle_callback, pattern="^toggle:"))
    app.add_handler(CallbackQueryHandler(confirm_add_callback, pattern="^confirm_add$"))
    app.add_handler(CallbackQueryHandler(remove_callback, pattern="^remove:"))
    app.add_handler(CallbackQueryHandler(view_disclosure_callback, pattern="^view:"))
    app.add_handler(CallbackQueryHandler(toggle_sync_callback, pattern="^toggle_sync$"))
    return app
