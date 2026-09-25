import logging
from app.api.max_client import max_client
from app.services.meeting_service import (
    get_or_create_user, get_active_meetings, join_meeting, 
    leave_meeting, get_my_meetings
)
from app.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

async def handle_update(update: dict):
    message = update.get("message")
    callback_query = update.get("callback_query")
    
    async with AsyncSessionLocal() as db:
        if message:
            await handle_message(db, message)
        elif callback_query:
            await handle_callback(db, callback_query)

async def handle_message(db, message: dict):
    text = message.get("text", "")
    sender_id = str(message.get("from", {}).get("id"))
    username = message.get("from", {}).get("username", "Unknown")
    
    if not sender_id:
        return

    await get_or_create_user(db, max_id=sender_id, username=username)
    
    if text == "/start":
        keyboard = [
            [{"text": "🔍 Найти активности", "callback_data": "find_activities"}],
            [{"text": "📅 Мои встречи", "callback_data": "my_meetings"}],
            [{"text": "➕ Создать встречу (Демо)", "callback_data": "create_meeting"}]
        ]
        await max_client.send_message(
            chat_id=sender_id,
            text="👋 *Привет! Я бот «В сборе».*\n\nЯ помогаю находить компанию для спорта и досуга на кампусе. Больше не нужно искать людей по чатам — я соберу кворум и пришлю подтверждение!\n\nЧто будем делать?",
            keyboard=keyboard
        )
    else:
        await max_client.send_message(
            chat_id=sender_id,
            text="Извини, я пока понимаю только нажатия на кнопки и команду /start ⚙️"
        )

async def handle_callback(db, callback_query: dict):
    data = callback_query.get("data", "")
    sender_id = str(callback_query.get("from", {}).get("id"))
    username = callback_query.get("from", {}).get("username", "Unknown")
    
    if not sender_id:
        return
        
    user = await get_or_create_user(db, max_id=sender_id, username=username)

    if data == "find_activities":
        meetings = await get_active_meetings(db)
        if not meetings:
            await max_client.send_message(sender_id, "Пока нет запланированных встреч 😔\nХочешь создать свою?", keyboard=[
                [{"text": "➕ Создать встречу", "callback_data": "create_meeting"}]
            ])
            return
            
        for m in meetings:
            active_participants = sum(1 for p in m.participants if p.status == 'joined')
            text = (f"🎯 *{m.activity_type}* — {m.title}\n\n"
                    f"📍 Место: {m.location}\n"
                    f"🕒 Время: {m.date_time.strftime('%d.%m.%Y %H:%M')}\n"
                    f"👥 Участники: {active_participants}/{m.max_participants} (Нужно для игры: {m.min_quorum})\n")
            if m.is_confirmed:
                text += "\n✅ *Встреча подтверждена!*"
            else:
                text += "\n⏳ *Идет сбор состава*"
                
            kb = [[{"text": "✅ Записаться", "callback_data": f"join_{m.id}"}]]
            await max_client.send_message(sender_id, text, keyboard=kb)

    elif data == "my_meetings":
        meetings = await get_my_meetings(db, user.id)
        if not meetings:
            await max_client.send_message(sender_id, "Вы еще не записаны ни на одну встречу 🕸️")
            return
            
        for m in meetings:
            participant = next((p for p in m.participants if p.user_id == user.id), None)
            status_text = "✅ В основном составе" if participant and participant.status == "joined" else "⏳ В листе ожидания"
            text = (f"📌 Ваша запись: *{m.activity_type}* — {m.title}\n\n"
                    f"📍 Место: {m.location}\n"
                    f"🕒 Время: {m.date_time.strftime('%d.%m.%Y %H:%M')}\n"
                    f"🏷 Статус: {status_text}\n")
            if m.is_confirmed:
                text += "\n✅ *Встреча подтверждена!*"
                
            kb = [[{"text": "❌ Отменить участие", "callback_data": f"leave_{m.id}"}]]
            await max_client.send_message(sender_id, text, keyboard=kb)

    elif data.startswith("join_"):
        meeting_id = int(data.split("_")[1])
        status, meeting, promoted_users, active_p = await join_meeting(db, meeting_id, user.id)
        
        if status == "already_joined":
            await max_client.send_message(sender_id, "Вы уже записаны на эту встречу или находитесь в листе ожидания 👌", keyboard=[
                [{"text": "❌ Отменить участие", "callback_data": f"leave_{meeting.id}"}]
            ])
        elif status == "joined":
            msg = f"🎉 Вы успешно записаны!\nСейчас участников: {active_p} из {meeting.max_participants}."
            await max_client.send_message(sender_id, msg, keyboard=[
                [{"text": "❌ Отменить участие", "callback_data": f"leave_{meeting.id}"}]
            ])
            
            # Notify everyone if quorum was just reached
            if promoted_users:
                for pu in promoted_users:
                    await max_client.send_message(
                        pu.max_id, 
                        f"🔥 *Ура! Минимальный состав собран* ({active_p}/{meeting.max_participants}).\n\n"
                        f"Встреча '{meeting.title}' гарантированно состоится! До встречи на площадке 🏆"
                    )
        elif status == "waitlist":
            await max_client.send_message(sender_id, "К сожалению, мест нет 😔\n\nНо вы добавлены в ⏳ *лист ожидания*. Мы мгновенно сообщим, если кто-то отменит запись, и переведем вас в основной состав!", keyboard=[
                [{"text": "❌ Покинуть лист ожидания", "callback_data": f"leave_{meeting.id}"}]
            ])
            
    elif data.startswith("leave_"):
        meeting_id = int(data.split("_")[1])
        status, meeting, promoted_user = await leave_meeting(db, meeting_id, user.id)
        
        if status == "not_in_meeting":
            await max_client.send_message(sender_id, "Вы не были записаны на эту встречу 🤔")
        elif status == "left":
            await max_client.send_message(sender_id, "Вы успешно отменили свое участие. Будем рады видеть вас на других активностях! 🌟")
            if promoted_user:
                await max_client.send_message(
                    promoted_user.max_id,
                    f"⚡ *Отличные новости!*\n\nОсвободилось место на встречу '{meeting.title}'! Вы автоматически переведены в основной состав 🥳",
                    keyboard=[[{"text": "❌ Отменить участие", "callback_data": f"leave_{meeting.id}"}]]
                )

    elif data == "create_meeting":
        await max_client.send_message(
            sender_id, 
            "В MVP версии создание встречи доступно только администраторам (демо-данные уже загружены). Смотри README для инструкций по seed-данным 📋"
        )
