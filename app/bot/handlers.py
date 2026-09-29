import logging
from app.api.max_client import max_client
from app.services.meeting_service import (
    get_or_create_user, get_active_meetings, join_meeting, 
    leave_meeting, get_my_meetings, get_meeting_by_id, count_active_meetings,
    get_draft, await_input, save_comment, stop_awaiting_input, delete_draft,
)
from app.database.session import AsyncSessionLocal
from app.services.demo_data import is_demo_user
from app.services.catalog import ACTIVITIES, OTHER_ACTIVITY, activity_emoji, find_location
from app.services.meeting_service import create_meeting
from app.bot import create_flow

logger = logging.getLogger(__name__)

MAIN_MENU = [
    [{"text": "🔍 Найти активности", "callback_data": "find_activities"}],
    [{"text": "📅 Мои встречи", "callback_data": "my_meetings"}],
    [{"text": "➕ Создать встречу", "callback_data": "create_meeting"}]
]

BACK_TO_LIST = [{"text": "« К списку активностей", "callback_data": "find_activities"}]
BACK_TO_MENU = [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]

# Upper bound on buttons in "my meetings" (MAX allows up to 30 keyboard rows)
MAX_LIST_ITEMS = 20

# With more upcoming meetings than this, the list is grouped by activity type
GROUPING_THRESHOLD = 10
# Meetings per page inside a group / the full list
PAGE_SIZE = 10

LIST_PREFIX = "list:"
ALL_KEY = "all"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _emoji(activity_type: str) -> str:
    return activity_emoji(activity_type)


def _short_dt(dt) -> str:
    return f"{WEEKDAYS[dt.weekday()]} {dt.strftime('%d.%m %H:%M')}"


def _joined_count(meeting) -> int:
    return sum(1 for p in meeting.participants if p.status == "joined")


def _user_status(meeting, user_id: int):
    return next((p.status for p in meeting.participants
                 if p.user_id == user_id and p.status in ("joined", "waitlist")), None)


def _activity_label(meeting) -> str:
    """Activity name for lists; "Другое" says nothing, so its comment is shown instead."""
    if meeting.activity_type == OTHER_ACTIVITY and meeting.comment:
        return meeting.comment
    return meeting.activity_type


def _list_button(meeting, user_id: int) -> dict:
    joined = _joined_count(meeting)
    if _user_status(meeting, user_id):
        mark = "⭐"
    elif meeting.is_full(joined):
        mark = "🔴"
    elif meeting.is_confirmed:
        mark = "✅"
    else:
        mark = "⏳"
    text = (f"{mark} {_emoji(meeting.activity_type)} {_activity_label(meeting)} · "
            f"{_short_dt(meeting.date_time)} · {meeting.capacity_label(joined)}")
    return {"text": text, "callback_data": f"meeting_{meeting.id}"}


def _activity_key(activity_type: str) -> str:
    """Stable key of an activity for callback payloads: its catalog index.
    Types missing from the catalog are grouped under "Другое"."""
    names = [name for name, _ in ACTIVITIES]
    if activity_type not in names:
        activity_type = OTHER_ACTIVITY
    return str(names.index(activity_type))


def _group_title(key: str) -> str:
    if key == ALL_KEY:
        return "📋 Все встречи"
    name, emoji = ACTIVITIES[int(key)]
    return f"{emoji} {name}"


def _list_payload(key: str, page: int) -> str:
    return f"{LIST_PREFIX}{key}:{page}"


def _parse_list_payload(data: str):
    """'list:<key>:<page>' -> (key, page) or None."""
    try:
        key, page = data[len(LIST_PREFIX):].split(":")
        page = int(page)
    except ValueError:
        return None
    valid = key == ALL_KEY or (key.isdigit() and int(key) < len(ACTIVITIES))
    return (key, page) if valid and page >= 0 else None


def _back_to_list(meeting, grouped: bool) -> list:
    if grouped:
        return [{"text": "« К списку активностей", "callback_data": _list_payload(_activity_key(meeting.activity_type), 0)}]
    return BACK_TO_LIST


def _render_groups(meetings: list):
    counts = {}
    for m in meetings:
        key = _activity_key(m.activity_type)
        counts[key] = counts.get(key, 0) + 1

    order = [str(i) for i in range(len(ACTIVITIES))]
    kb = [[{"text": f"{_group_title(k)} · {counts[k]}", "callback_data": _list_payload(k, 0)}]
          for k in order if k in counts]
    kb.append([{"text": f"{_group_title(ALL_KEY)} · {len(meetings)}", "callback_data": _list_payload(ALL_KEY, 0)}])
    kb.append(BACK_TO_MENU)
    text = (f"🔍 **Ближайшие активности** ({len(meetings)})\n\n"
            "Выберите вид активности:")
    return text, kb


def _render_list(meetings: list, user_id: int, key: str, page: int):
    """Paged list of meetings of one group (or all meetings when key is 'all')."""
    if key != ALL_KEY:
        meetings = [m for m in meetings if _activity_key(m.activity_type) == key]

    pages = max(1, -(-len(meetings) // PAGE_SIZE))
    page = min(page, pages - 1)
    chunk = meetings[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    title = f"{_group_title(key)} — {len(meetings)}"
    if pages > 1:
        title += f" · стр. {page + 1} из {pages}"
    text = (f"**{title}**\n\n"
            "Выберите встречу, чтобы посмотреть детали и записаться.\n\n"
            "⏳ идёт сбор · ✅ подтверждена · 🔴 мест нет · ⭐ вы записаны")
    if not chunk:
        text = f"**{_group_title(key)}**\n\nВ этой группе больше нет предстоящих встреч 😔"

    kb = [[_list_button(m, user_id)] for m in chunk]
    nav = []
    if page > 0:
        nav.append({"text": "« Назад", "callback_data": _list_payload(key, page - 1)})
    if page < pages - 1:
        nav.append({"text": "Вперёд »", "callback_data": _list_payload(key, page + 1)})
    if nav:
        kb.append(nav)
    kb.append([{"text": "« К видам активности", "callback_data": "find_activities"}])
    kb.append(BACK_TO_MENU)
    return text, kb


def _meeting_card(meeting, user_id: int, grouped: bool = False):
    joined = _joined_count(meeting)
    waitlist = sum(1 for p in meeting.participants if p.status == "waitlist")
    text = (f"{_emoji(meeting.activity_type)} **{meeting.title}**\n\n"
            f"🏷 Активность: {meeting.activity_type} ({meeting.skill_level})\n"
            f"📍 Место: {meeting.location}\n"
            f"🕒 Время: {_short_dt(meeting.date_time)}\n"
            f"👥 Участники: {meeting.capacity_label(joined)}")
    if meeting.max_participants is None:
        text += " (без ограничения)"
    if waitlist:
        text += f", в листе ожидания: {waitlist}"
    if meeting.comment and meeting.comment != meeting.title:
        text += f"\n💬 {meeting.comment}"

    if meeting.is_confirmed:
        text += "\n\n✅ **Встреча подтверждена!**"
    else:
        text += f"\n\n⏳ **Идёт сбор состава**: нужно ещё {meeting.min_quorum - joined} для подтверждения"

    status = _user_status(meeting, user_id)
    if status == "joined":
        text += "\n\n⭐ Вы в основном составе"
        action = {"text": "❌ Отменить участие", "callback_data": f"leave_{meeting.id}"}
    elif status == "waitlist":
        text += "\n\n⭐ Вы в листе ожидания"
        action = {"text": "❌ Покинуть лист ожидания", "callback_data": f"leave_{meeting.id}"}
    elif meeting.is_full(joined):
        action = {"text": "⏳ Встать в лист ожидания", "callback_data": f"join_{meeting.id}"}
    else:
        action = {"text": "✅ Записаться", "callback_data": f"join_{meeting.id}"}

    rows = [[action]]
    if find_location(meeting.location):
        rows.append([{"text": "📍 Метка на карте", "callback_data": f"map_{meeting.id}"}])
    return text, rows + [_back_to_list(meeting, grouped), BACK_TO_MENU]


WELCOME_TEXT = ("👋 **Привет! Я бот «В сборе».**\n\nЯ помогаю находить компанию для спорта и досуга на кампусе. "
                "Больше не нужно искать людей по чатам — я соберу кворум и пришлю подтверждение!\n\nЧто будем делать?")


async def handle_update(update: dict):
    update_type = update.get("update_type")

    try:
        async with AsyncSessionLocal() as db:
            if update_type == "message_created":
                await handle_message(db, update.get("message") or {})
            elif update_type == "message_callback":
                await handle_callback(db, update.get("callback") or {})
            elif update_type == "bot_started":
                await handle_bot_started(db, update)
    except Exception:
        logger.exception(f"Failed to handle update of type {update_type}")


def _user_fields(user: dict):
    user_id = user.get("user_id")
    if user_id is None:
        return None, None
    return str(user_id), user.get("username") or user.get("name") or "Unknown"


async def send_main_menu(user_id: str):
    await max_client.send_message(user_id, WELCOME_TEXT, keyboard=MAIN_MENU)


async def handle_bot_started(db, update: dict):
    sender_id, username = _user_fields(update.get("user") or {})
    if not sender_id:
        return

    await get_or_create_user(db, max_id=sender_id, username=username)
    await send_main_menu(sender_id)


async def handle_message(db, message: dict):
    text = ((message.get("body") or {}).get("text") or "").strip()
    sender_id, username = _user_fields(message.get("sender") or {})

    if not sender_id:
        return

    user = await get_or_create_user(db, max_id=sender_id, username=username)

    if text.startswith("/"):
        await stop_awaiting_input(db, user.id)

    draft = await get_draft(db, user.id)
    if draft and draft.awaiting:
        await handle_text_input(db, user, sender_id, draft, text)
    elif text in ("/start", "/menu"):
        await send_main_menu(sender_id)
    else:
        await max_client.send_message(
            sender_id,
            "Извини, я пока понимаю только нажатия на кнопки и команду /start ⚙️",
            keyboard=MAIN_MENU
        )


def _parse_meeting_id(data: str):
    try:
        return int(data.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


async def _is_grouped(db) -> bool:
    return await count_active_meetings(db) > GROUPING_THRESHOLD


async def handle_callback(db, callback: dict):
    data = callback.get("payload") or ""
    callback_id = callback.get("callback_id")
    sender_id, username = _user_fields(callback.get("user") or {})

    if not sender_id or data == create_flow.NOOP:
        # decorative buttons (calendar headers, empty cells)
        return

    async def reply(text: str, keyboard: list = None):
        """Replaces the message with the pressed button; falls back to a new message."""
        if callback_id and await max_client.edit_by_callback(callback_id, text, keyboard):
            return
        await max_client.send_message(sender_id, text, keyboard=keyboard)

    user = await get_or_create_user(db, max_id=sender_id, username=username)
    # Any button press ends a pending text input (steps that accept text re-enable it)
    await stop_awaiting_input(db, user.id)

    if data.startswith(("join_", "leave_", "meeting_", "map_")) and _parse_meeting_id(data) is None:
        await reply("Не удалось распознать встречу 🤔 Попробуйте ещё раз из меню.", MAIN_MENU)
        return

    if data == "main_menu":
        await reply(WELCOME_TEXT, MAIN_MENU)

    elif data == "find_activities":
        meetings = await get_active_meetings(db)
        if not meetings:
            await reply("Пока нет запланированных встреч 😔\nХочешь создать свою?", [
                [{"text": "➕ Создать встречу", "callback_data": "create_meeting"}],
                BACK_TO_MENU
            ])
            return

        if len(meetings) > GROUPING_THRESHOLD:
            text, kb = _render_groups(meetings)
        else:
            text = ("🔍 **Ближайшие активности**\n\n"
                    "Выберите встречу, чтобы посмотреть детали и записаться.\n\n"
                    "⏳ идёт сбор · ✅ подтверждена · 🔴 мест нет · ⭐ вы записаны")
            kb = [[_list_button(m, user.id)] for m in meetings]
            kb.append(BACK_TO_MENU)
        await reply(text, kb)

    elif data.startswith(LIST_PREFIX):
        parsed = _parse_list_payload(data)
        if not parsed:
            await reply("Не удалось открыть список 🤔", [BACK_TO_LIST, BACK_TO_MENU])
            return
        meetings = await get_active_meetings(db)
        text, kb = _render_list(meetings, user.id, *parsed)
        await reply(text, kb)

    elif data == "my_meetings":
        meetings = await get_my_meetings(db, user.id)
        if not meetings:
            await reply("Вы еще не записаны ни на одну встречу 🕸️", [
                [{"text": "🔍 Найти активности", "callback_data": "find_activities"}],
                BACK_TO_MENU
            ])
            return

        text = ("📅 **Мои встречи**\n\n"
                "Выберите встречу, чтобы посмотреть детали или отменить участие.\n\n"
                "⭐ основной состав · ⏳ лист ожидания")
        kb = []
        for m in meetings[:MAX_LIST_ITEMS]:
            mark = "⭐" if _user_status(m, user.id) == "joined" else "⏳"
            kb.append([{"text": f"{mark} {_emoji(m.activity_type)} {_activity_label(m)} · {_short_dt(m.date_time)}",
                        "callback_data": f"meeting_{m.id}"}])
        kb.append(BACK_TO_MENU)
        await reply(text, kb)

    elif data.startswith("meeting_"):
        meeting = await get_meeting_by_id(db, _parse_meeting_id(data))
        if not meeting:
            await reply("Эта встреча больше не существует 😔", [BACK_TO_LIST, BACK_TO_MENU])
            return
        text, kb = _meeting_card(meeting, user.id, await _is_grouped(db))
        await reply(text, kb)

    elif data.startswith("map_"):
        meeting = await get_meeting_by_id(db, _parse_meeting_id(data))
        loc = find_location(meeting.location) if meeting else None
        if not loc:
            await reply("Для этой встречи нет точки на карте 🤔", [BACK_TO_LIST, BACK_TO_MENU])
            return
        # A map can't replace the card without hiding its text, so it is a separate message
        await max_client.send_location(sender_id, loc.latitude, loc.longitude)

    elif data.startswith("join_"):
        status, meeting, confirmed_users, active_p = await join_meeting(db, _parse_meeting_id(data), user.id)

        if status == "not_found":
            await reply("Эта встреча больше не существует 😔", [BACK_TO_LIST, BACK_TO_MENU])
            return

        if status == "already_joined":
            notice = "👌 Вы уже записаны на эту встречу."
        elif status == "waitlist":
            notice = ("😔 Мест нет, но вы добавлены в ⏳ **лист ожидания**. "
                      "Если кто-то отменит запись, мы переведем вас в основной состав и сообщим.")
        elif confirmed_users:
            notice = f"🔥 **Ура! Вы записаны, и минимальный состав собран** ({meeting.capacity_label(active_p)}). Встреча состоится!"
        else:
            notice = f"🎉 **Вы записаны!** Участников: {meeting.capacity_label(active_p)}."

        meeting = await get_meeting_by_id(db, meeting.id)
        text, kb = _meeting_card(meeting, user.id, await _is_grouped(db))
        await reply(f"{notice}\n\n{text}", kb)

        # Notify the other participants that the quorum was just reached
        for pu in confirmed_users or []:
            if pu.id == user.id or is_demo_user(pu):
                continue
            await max_client.send_message(
                pu.max_id,
                f"🔥 **Ура! Минимальный состав собран** ({meeting.capacity_label(active_p)}).\n\n"
                f"Встреча '{meeting.title}' гарантированно состоится! До встречи на площадке 🏆",
                keyboard=[[{"text": "Открыть встречу", "callback_data": f"meeting_{meeting.id}"}]]
            )

    elif data.startswith("leave_"):
        status, meeting, promoted_user = await leave_meeting(db, _parse_meeting_id(data), user.id)

        if status == "not_found":
            await reply("Эта встреча больше не существует 😔", [BACK_TO_LIST, BACK_TO_MENU])
            return

        if status == "not_in_meeting":
            notice = "🤔 Вы не были записаны на эту встречу."
        else:
            notice = "Вы отменили участие. Будем рады видеть вас на других активностях! 🌟"

        meeting = await get_meeting_by_id(db, meeting.id)
        text, kb = _meeting_card(meeting, user.id, await _is_grouped(db))
        await reply(f"{notice}\n\n{text}", kb)

        if promoted_user and not is_demo_user(promoted_user):
            await max_client.send_message(
                promoted_user.max_id,
                f"⚡ **Отличные новости!**\n\nОсвободилось место на встречу '{meeting.title}'! Вы автоматически переведены в основной состав 🥳",
                keyboard=[[{"text": "Открыть встречу", "callback_data": f"meeting_{meeting.id}"}]]
            )

    elif create_flow.is_create_payload(data):
        await handle_create_flow(db, user, data, reply)


async def handle_create_flow(db, user, data: str, reply):
    state, publish = create_flow.parse(data)
    if state is None:
        await reply("Не удалось разобрать шаг создания 🤔 Начнём заново.",
                    [[{"text": "➕ Создать встречу", "callback_data": "create_meeting"}], BACK_TO_MENU])
        return

    comment = None
    if state.get("c") == create_flow.COMMENT_GIVEN:
        draft = await get_draft(db, user.id)
        comment = draft.comment if draft else None
        if not comment:
            # the stored comment is gone: ask for it again
            state = {k: v for k, v in state.items() if k != "c"}
            publish = False

    error = create_flow.validate(state)
    if error or not publish:
        text, kb = create_flow.render_step(state, error, comment)
        await _remember_awaited_input(db, user, state)
        await reply(text, kb)
        return

    meeting, created = await create_meeting(db, user.id, **create_flow.meeting_params(state, comment))
    await delete_draft(db, user.id)
    text, kb = _meeting_card(meeting, user.id, await _is_grouped(db))
    if created:
        text = "🎉 **Встреча опубликована!** Она уже видна в списке активностей.\n\n" + text
    else:
        text = "👌 Эта встреча уже опубликована.\n\n" + text
    await reply(text, kb)


async def _remember_awaited_input(db, user, state: dict):
    """If the current creation step accepts typed text, remember what we expect."""
    if create_flow.validate(state):
        state = {k: v for k, v in state.items() if k not in ("t", "h")}
    kind = create_flow.awaited_input(state)
    if kind:
        await await_input(db, user.id, kind, create_flow.encode(state))


async def handle_text_input(db, user, sender_id: str, draft, text: str):
    """Text typed at a creation step that accepts it: exact time, custom size or comment."""
    state, _ = create_flow.parse(draft.state)
    if state is None:
        await delete_draft(db, user.id)
        await send_main_menu(sender_id)
        return

    comment = None
    if draft.awaiting == "time":
        values, error = create_flow.parse_time_input(text, state)
    elif draft.awaiting == "size":
        values, error = create_flow.parse_size_input(text)
    else:
        comment, error = create_flow.clean_comment(text)
        values = {"c": create_flow.COMMENT_GIVEN}

    if error:
        # stay on the same step, the draft keeps waiting for input
        text, kb = create_flow.render_step(state, "⚠️ " + error)
        await max_client.send_message(sender_id, text, keyboard=kb)
        return

    if comment:
        await save_comment(db, draft, comment)
    state.update(values)
    if "t" in values:
        state.pop("h", None)

    text, kb = create_flow.render_step(state, comment=comment)
    await stop_awaiting_input(db, user.id)
    await _remember_awaited_input(db, user, state)
    await max_client.send_message(sender_id, text, keyboard=kb)
