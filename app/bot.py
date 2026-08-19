"""Telegram handlers. I/O only — business logic lives in services/.

`pending_selections` and `disclosure_cache` are process-memory UI state tied to
the telegram interaction; they intentionally stay here (see CLAUDE.md §6-4 —
moving them to shared storage is a later SaaS-transition task).
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN
from services import corp_service, disclosure_service, feedback_service, query_service, user_service, watchlist_service

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
        "   (상장폐지·거래정지·회생절차 등 시장 중대 공시는 관심기업이 아니어도 즉시 알림)\n\n"
        "자주 쓰는 명령\n"
        "/my - 내 관심기업 공시 (예: /my 유상증자)\n"
        "/market - 전체 시장 오늘 공시\n"
        "/ask 질문 - 자연어 공시 검색 (베타)\n"
        "/list - 등록된 기업 목록\n"
        "/feedback 내용 - 오류·개선 신고\n"
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
            msg = f"{result.date_label()} 공시가 없습니다."
            if empty_hint:
                msg += f"\n{empty_hint}"
            await update.message.reply_text(msg)
        return

    for d in result.items:
        disclosure_cache[d["rcept_no"]] = d

    shown = min(len(result.items), PAGE_SIZE)
    text = result.header() + "\n공시를 선택하면 요약을 보여드립니다."
    if len(result.items) > PAGE_SIZE:
        text += f"\n(최근 {shown}건 표시 — 검색어를 붙이면 좁힐 수 있습니다. 예: /my 유상증자)"
    await update.message.reply_text(text, reply_markup=_disclosure_keyboard(result.items))


async def my_disclosures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """관심기업의 중요 공시 — 기본 오늘. 인자: 검색어·날짜(YYYYMMDD/어제) 조합 가능.

    명령명은 /my — /market(전체 시장)과 '범위'로 대칭을 이룬다. 구 명령
    /today는 시간을 이름으로 써서 날짜 인자와 자기모순이었다(/today 어제).
    """
    chat_id = str(update.effective_chat.id)
    raw = " ".join(context.args) if context.args else ""
    date, query = disclosure_service.split_date_and_query(raw)

    corp_codes = await watchlist_service.get_corp_codes(chat_id)
    if not corp_codes:
        await update.message.reply_text(
            "등록된 관심기업이 없습니다.\n"
            "/add 삼성전자 처럼 기업을 등록하면 오늘 공시를 모아 보여드립니다.\n"
            "전체 시장을 보려면 /market 을 입력하세요."
        )
        return

    await update.message.reply_text("📋 관심기업 공시 불러오는 중...")
    try:
        result = await disclosure_service.query_disclosures(
            scope="watchlist", corp_codes=corp_codes, important_only=True,
            query=query, date=date,
        )
    except Exception as e:
        # DART 장애를 "공시 없음"으로 위장하지 않는다 (리뷰 P1과 같은 원칙)
        print(f"/my 조회 실패 (chat={chat_id}): {type(e).__name__}: {e}")
        await update.message.reply_text(
            "지금 DART 조회에 문제가 있습니다. 잠시 후 다시 시도해주세요."
        )
        return
    await _send_query_result(
        update, result, empty_hint="전체 시장을 보려면 /market 을 입력하세요."
    )


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """전체 시장의 중요 공시 — 기본 오늘. 인자: 검색어·날짜(YYYYMMDD/어제) 조합 가능."""
    raw = " ".join(context.args) if context.args else ""
    date, query = disclosure_service.split_date_and_query(raw)
    await update.message.reply_text("📋 전체 시장 공시 불러오는 중...")
    try:
        result = await disclosure_service.query_disclosures(
            scope="market", important_only=True, query=query, date=date,
        )
    except Exception as e:
        print(f"/market 조회 실패: {type(e).__name__}: {e}")
        await update.message.reply_text(
            "지금 DART 조회에 문제가 있습니다. 잠시 후 다시 시도해주세요."
        )
        return
    await _send_query_result(update, result)


async def legacy_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """구 명령(/today·/mytoday) — /my의 조용한 별칭. 깨뜨리지 않고 안내만 한다."""
    await update.message.reply_text("이 명령은 /my 로 이름이 바뀌었습니다. 이번엔 그대로 조회해드릴게요.")
    await my_disclosures(update, context)


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
        f"관심기업에서 찾기:  /my {given}\n\n"
        "자세한 사용법은 /help"
    )


async def mykeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """폐기 예정 — keyword와 동일. /my에 인자를 붙이는 방식으로 대체한다."""
    given = " ".join(context.args) if context.args else "유상증자"
    await update.message.reply_text(
        "/mykeyword 는 더 이상 사용하지 않습니다.\n\n"
        f"관심기업 공시를 검색어로 좁히려면:  /my {given}\n"
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
        "/my - 내 관심기업의 오늘 중요 공시\n"
        "/my 유상증자 - 위 결과를 검색어로 좁히기\n"
        "/market - 전체 시장의 오늘 중요 공시\n"
        "/market 감사보고서 - 위 결과를 검색어로 좁히기\n\n"
        "지난 날짜 보기: 날짜를 함께 입력 (검색어와 조합 가능)\n"
        "/my 어제  ·  /market 20260810  ·  /my 20260810 유상증자\n\n"
        "검색어는 이번 조회에만 적용되고 저장되지 않습니다.\n\n"
        "■ 질문하기 (베타)\n"
        "/ask 씨젠 최근 1년 CB 공시 찾아줘 - 자연어로 공시 검색\n"
        "후속 질문은 30분간 문맥이 이어집니다. 초기화: /ask 초기화\n"
        "답변에는 항상 DART 원문 링크가 붙습니다.\n\n"
        "■ 알림 (자동, 설정 불필요)\n"
        "🚨 긴급 - 상장폐지 확정·정리매매·회생절차·부도·횡령배임 등은 관심기업이 아니어도 즉시 알림\n"
        "📌 시장 공지 - 상장폐지 우려·심사, 거래정지 발생 등 예고 단계도 시장 전체에 즉시 알림\n"
        "⚠️ 중요 - 관심기업의 증자·CB·실적·5%지분·내부자매매·배당 등 즉시 알림\n"
        "📄 참고 - 관심기업의 그 외 공시(정기보고서·IR 등)는 매일 18:30 묶음 전달\n\n"
        "■ 기타\n"
        "/feedback 내용 - 오류·불편 신고 (특히 '와야 할 알림이 안 온 경우' 제보가 가장 큰 도움이 됩니다)\n"
        "/settings - 설정\n"
        "/deletedata - 내 데이터 전체 삭제\n\n"
        "ℹ️ forG는 DART 공시를 AI로 요약해 전달하는 참고용 도구입니다.\n"
        "투자 자문·종목 추천 서비스가 아니며, AI 요약에는 오류·지연이 있을 수 있습니다.\n"
        "투자 판단 전 반드시 DART 원문을 확인하세요."
    )


async def _settings_text(chat_id: str) -> str:
    """현재 상태 요약. 키워드 동기화 UI는 /keyword 폐기와 함께 제거했다
    (죽은 설정을 보여주면 사용자가 켜고 끄며 의미를 찾게 된다)."""
    watchlist = await watchlist_service.list_watchlist(chat_id)
    return (
        "⚙️ 설정\n\n"
        f"📋 관심기업: {len(watchlist)}곳 (/list 로 확인, /add 로 추가)\n\n"
        "🔔 알림은 자동입니다 — 별도 설정이 없습니다.\n"
        "  🚨 긴급 — 시장 전체 중대 공시(확정), 항상 켜짐\n"
        "  📌 시장 공지 — 시장 전체 예고 단계(상폐 우려·거래정지 등)\n"
        "  ⚠️ 중요 — 관심기업 핵심 공시, 즉시\n"
        "  📄 참고 — 관심기업 그 외 공시, 매일 18:30 묶음\n\n"
        "🗑 데이터 전체 삭제: /deletedata"
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = await user_service.get_user(chat_id)
    if not user:
        await update.message.reply_text("먼저 /start 를 입력해주세요.")
        return

    await update.message.reply_text(await _settings_text(chat_id))


async def toggle_sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """과거 메시지에 남아 있는 '키워드 동기화' 버튼용 — 기능은 폐기됐고,
    누르면 현재 설정 화면으로 바꿔준다. 상태는 건드리지 않는다."""
    query = update.callback_query
    await query.answer("키워드 동기화 설정은 폐기되었습니다.")
    chat_id = str(query.from_user.id)
    await query.edit_message_text(await _settings_text(chat_id))


ASK_MAX_REPLY = 4000  # 텔레그램 한도 내 (notifier.MAX_MESSAGE_LENGTH와 동일 기준)


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """자연어 공시 질의 (Stage 8). 후속 질문은 30분간 문맥이 이어진다."""
    chat_id = str(update.effective_chat.id)
    question = " ".join(context.args) if context.args else ""

    if not question:
        await update.message.reply_text(
            "질문을 함께 입력해주세요.\n"
            "예) /ask 씨젠 최근 1년 자기주식 공시 찾아줘\n"
            "예) /ask 그중 처분 금액이 얼마야?  (후속 질문은 문맥이 이어집니다)\n"
            "대화 초기화: /ask 초기화"
        )
        return

    if question.strip() in ("초기화", "리셋", "reset"):
        query_service.reset_session(chat_id)
        await update.message.reply_text("대화를 초기화했습니다. 새 질문을 해주세요.")
        return

    await update.message.reply_text("🔎 공시를 검색하는 중...")
    try:
        answer = await query_service.answer_query(chat_id, question)
    except Exception as e:
        print(f"/ask 처리 실패 (chat={chat_id}): {type(e).__name__}: {e}")
        await update.message.reply_text(
            "검색 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        )
        return

    if len(answer) > ASK_MAX_REPLY:
        answer = answer[:ASK_MAX_REPLY] + "\n\n... (내용이 잘렸습니다)"
    await update.message.reply_text(answer, disable_web_page_preview=True)


async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """피드백 접수 — DB 저장 후 운영자에게 즉시 전달한다.

    '와야 할 알림이 안 왔다'는 신고가 서비스 목적함수(놓침 0)의 유일한
    실측 데이터라, 봇 안에서 한 줄로 신고할 수 있게 한다. 저장이 먼저고
    전달은 그다음 — 운영자 전달이 실패해도 접수는 유실되지 않는다.
    """
    chat_id = str(update.effective_chat.id)
    first_name = update.effective_chat.first_name or ""
    text = " ".join(context.args) if context.args else ""

    if not text:
        await update.message.reply_text(
            "내용을 함께 입력해주세요.\n"
            "예) /feedback 삼성전자 CB 공시 알림이 안 왔어요\n"
            "예) /feedback 요약 숫자가 원문과 달라요\n"
            "예) /feedback 이런 기능이 있으면 좋겠어요\n\n"
            "특히 '와야 할 알림이 안 온 경우'를 알려주시면 가장 큰 도움이 됩니다."
        )
        return

    await feedback_service.save_feedback(chat_id, first_name, text)

    # 운영자 전달 실패는 접수 실패가 아니다 — DB에 이미 남아 있다.
    try:
        from config import TELEGRAM_CHAT_ID
        from notifier import send_system_message
        if TELEGRAM_CHAT_ID:
            sender = f"{first_name}({chat_id})" if first_name else chat_id
            await send_system_message(TELEGRAM_CHAT_ID, f"📨 피드백 — {sender}\n{text}")
    except Exception as e:
        print(f"피드백 운영자 전달 실패 (chat={chat_id}): {type(e).__name__}: {e}")

    await update.message.reply_text("접수했습니다. 확인 후 반영하겠습니다. 감사합니다 🙏")


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
        "삭제 대상: 관심기업 목록, 알림 발송 기록, 피드백, 계정 정보\n"
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
        f"관심기업 {counts['watchlist']}건, 발송 기록 {counts['seen']}건, "
        f"피드백 {counts.get('feedback', 0)}건, 계정 {counts['user']}건\n\n"
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
    app.add_handler(CommandHandler("my", my_disclosures))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("feedback", feedback))
    # 구 명령 별칭 — 기존 사용자의 손버릇을 깨지 않는다
    app.add_handler(CommandHandler("today", legacy_today))
    app.add_handler(CommandHandler("mytoday", legacy_today))
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
