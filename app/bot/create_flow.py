"""
Step-by-step meeting creation.

The flow is stateless: every button carries all choices made so far in its payload,
e.g. "new:a0.l2.d1.t1140.q4.m6" (activity 0, location 2, tomorrow, 19:00, 4-6 people).
The next step is the first missing field, so the flow survives bot restarts.

Some steps also accept typed text (exact time, custom size, comment). Text can't be
carried in a payload, so MeetingDraft remembers which input is expected; the parsed
value is put back into the state. Only the comment text itself is stored in the draft,
the payload records whether it was given ("c1") or skipped ("c0").
"""
import re
from datetime import datetime, timedelta, time
from typing import Optional, Tuple, List

from app.core.timeutils import now
from app.services.catalog import (
    ACTIVITIES, LOCATIONS, SKILL_LEVELS, DAYS_AHEAD, OTHER_ACTIVITY,
    MINUTE_OPTIONS, SIZE_PRESETS, MIN_PARTICIPANTS, MAX_PARTICIPANTS,
)

PREFIX = "new:"
PUBLISH = "ok"
NOOP = "noop"

# A meeting must start at least this long after it is created
MIN_LEAD_TIME = timedelta(minutes=30)

COMMENT_MAX_LEN = 50
COMMENT_SKIPPED, COMMENT_GIVEN = 0, 1
# Characters with a meaning in MAX markdown, stripped from user comments
MARKDOWN_CHARS = "*_~`^[]#>"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# payload key -> exclusive upper bound of its value
FIELDS = {
    "a": len(ACTIVITIES),
    "l": len(LOCATIONS),
    "d": DAYS_AHEAD,           # day offset from today
    "h": 24,                   # chosen hour (intermediate, before minutes)
    "t": 24 * 60,              # start time, minutes since midnight
    "q": MAX_PARTICIPANTS + 1,  # min quorum
    "m": MAX_PARTICIPANTS + 1,  # max participants, UNLIMITED = no limit
    "v": len(SKILL_LEVELS),
    "c": 2,
}

# "m0" in a payload: no participant limit
UNLIMITED = 0

# steps in order and the payload keys each one fills
STEPS = {
    "a": ("a",),
    "l": ("l",),
    "d": ("d",),
    "t": ("h", "t"),
    "s": ("q", "m"),
    "v": ("v",),
    "c": ("c",),
}
DONE_KEYS = {"a": "a", "l": "l", "d": "d", "t": "t", "s": "m", "v": "v", "c": "c"}

# steps that accept typed text -> draft.awaiting value
TEXT_STEPS = {"t": "time", "s": "size", "c": "comment"}

CANCEL = [{"text": "✖️ Отменить создание", "callback_data": "main_menu"}]


def is_create_payload(data: str) -> bool:
    return data == "create_meeting" or data.startswith(PREFIX)


def parse(data: str) -> Tuple[Optional[dict], bool]:
    """
    Returns (state, publish). state is None if the payload is malformed.
    """
    if data == "create_meeting":
        return {}, False

    body = data[len(PREFIX):]
    publish = body.endswith("." + PUBLISH)
    if publish:
        body = body[:-len(PUBLISH) - 1]

    state = {}
    for part in filter(None, body.split(".")):
        key, value = part[:1], part[1:]
        if key not in FIELDS or key in state or not value.isdigit():
            return None, False
        idx = int(value)
        if idx >= FIELDS[key]:
            return None, False
        state[key] = idx

    q, m = state.get("q"), state.get("m")
    if (q is None) != (m is None):
        return None, False
    if q is not None and not (q >= MIN_PARTICIPANTS and (m == UNLIMITED or q <= m)):
        return None, False
    return state, publish


def encode(state: dict, extra: str = None) -> str:
    # the chosen hour is only needed until the exact time is known
    parts = [f"{k}{state[k]}" for k in FIELDS if k in state and not (k == "h" and "t" in state)]
    if extra:
        parts.append(extra)
    return PREFIX + ".".join(parts)


def _with(state: dict, **values) -> str:
    return encode({**state, **values})


def next_step(state: dict) -> Optional[str]:
    """Name of the next step, None when everything is filled."""
    return next((step for step, key in DONE_KEYS.items() if key not in state), None)


def awaited_input(state: dict) -> Optional[str]:
    """Kind of typed text the current step accepts (stored in the draft)."""
    return TEXT_STEPS.get(next_step(state))


def _back_state(state: dict) -> Optional[dict]:
    """State of the previous screen, None on the first step."""
    step = next_step(state)
    if step == "t" and "h" in state:
        # minutes screen -> hours screen
        return {k: v for k, v in state.items() if k != "h"}

    done = [s for s, key in DONE_KEYS.items() if key in state]
    if not done:
        return None
    drop = STEPS[done[-1]]
    return {k: v for k, v in state.items() if k not in drop}


def _back_button(state: dict) -> Optional[list]:
    prev = _back_state(state)
    if prev is None:
        return None
    return [{"text": "« Назад", "callback_data": encode(prev) if prev else "create_meeting"}]


def _day(idx: int) -> datetime:
    return datetime.combine(now().date() + timedelta(days=idx), time())


def _day_label(idx: int) -> str:
    d = _day(idx)
    name = {0: "Сегодня", 1: "Завтра"}.get(idx, WEEKDAYS[d.weekday()])
    return f"{name}, {d.strftime('%d.%m')}"


def _fmt_time(minutes: int) -> str:
    return "%02d:%02d" % divmod(minutes, 60)


def start_datetime(state: dict) -> datetime:
    return _day(state["d"]) + timedelta(minutes=state["t"])


def _time_ok(day_idx: int, minutes: int) -> bool:
    return _day(day_idx) + timedelta(minutes=minutes) - now() >= MIN_LEAD_TIME


def _available_hours(day_idx: int) -> List[int]:
    return [h for h in range(24) if any(_time_ok(day_idx, h * 60 + m) for m in MINUTE_OPTIONS)]


def _day_available(day_idx: int) -> bool:
    return _time_ok(day_idx, 24 * 60 - 1)


# ---------- typed input parsers: return (values, error) ----------

TIME_RE = re.compile(r"^\s*(\d{1,2})(?:\s*[:.\-\s]\s*(\d{2}))?\s*$")


def parse_time_input(text: str, state: dict) -> Tuple[Optional[dict], Optional[str]]:
    match = TIME_RE.match(text)
    if not match:
        return None, "Не понял время. Напишите, например, 19:40."
    hours, minutes = int(match.group(1)), int(match.group(2) or 0)
    if hours > 23 or minutes > 59:
        return None, "Такого времени нет. Напишите, например, 19:40."
    total = hours * 60 + minutes
    if not _time_ok(state["d"], total):
        return None, "Это время уже прошло или слишком близко. Выберите другое."
    return {"t": total}, None


UNLIMITED_RE = re.compile(r"\+|∞|без\s*огр|неогр|больше|безлимит|^\s*от\s*\d+\s*$|\d\s*-\s*$", re.IGNORECASE)


def parse_size_input(text: str) -> Tuple[Optional[dict], Optional[str]]:
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if UNLIMITED_RE.search(text) and len(numbers) <= 1:
        # "5+", "от 5", "5-", "без ограничений": minimum only
        q = numbers[0] if numbers else MIN_PARTICIPANTS
        if q < MIN_PARTICIPANTS:
            return None, f"Минимум участников — {MIN_PARTICIPANTS} (вместе с вами)."
        if q > MAX_PARTICIPANTS:
            return None, f"Минимум не может быть больше {MAX_PARTICIPANTS}."
        return {"q": q, "m": UNLIMITED}, None

    if len(numbers) == 1:
        q = m = numbers[0]
    elif len(numbers) == 2:
        q, m = sorted(numbers)
    else:
        return None, ("Напишите минимум и максимум через дефис, например 5-12, одно число — "
                      "или «5+», если без ограничения.")
    if q < MIN_PARTICIPANTS:
        return None, f"Минимум участников — {MIN_PARTICIPANTS} (вместе с вами)."
    if m > MAX_PARTICIPANTS:
        return None, f"Максимум участников — {MAX_PARTICIPANTS}."
    return {"q": q, "m": m}, None


def clean_comment(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (comment, error)."""
    comment = " ".join(text.translate({ord(c): None for c in MARKDOWN_CHARS}).split())
    if not comment:
        return None, "Комментарий пустой. Напишите пару слов или нажмите «Без комментария»."
    if len(comment) > COMMENT_MAX_LEN:
        return None, (f"Слишком длинно: {len(comment)} символов, а можно до {COMMENT_MAX_LEN}. "
                      "Сократите, пожалуйста.")
    return comment, None


# ---------- result ----------

def meeting_params(state: dict, comment: Optional[str] = None) -> dict:
    activity, _ = ACTIVITIES[state["a"]]
    location = LOCATIONS[state["l"]].name
    comment = comment if state.get("c") == COMMENT_GIVEN else None
    # For "Other" the comment is the only description of what the meeting is about
    title = comment if activity == OTHER_ACTIVITY and comment else f"{activity}: {location}"
    return {
        "title": title,
        "activity_type": activity,
        "location": location,
        "date_time": start_datetime(state),
        "skill_level": SKILL_LEVELS[state["v"]],
        "min_quorum": state["q"],
        "max_participants": None if state["m"] == UNLIMITED else state["m"],
        "comment": comment,
    }


def validate(state: dict) -> Optional[str]:
    """Error message if the chosen date/time is no longer available."""
    if "t" in state and not _time_ok(state["d"], state["t"]):
        return "⌛ Это время уже прошло или слишком близко. Выберите другое."
    return None


def _size_label(q: int, m: int) -> str:
    if m == UNLIMITED:
        return f"от {q} человек, без ограничения"
    return f"ровно {m} человек" if q == m else f"от {q} до {m} человек"


def _summary(state: dict, comment: Optional[str]) -> str:
    lines = []
    if "a" in state:
        name, emoji = ACTIVITIES[state["a"]]
        lines.append(f"{emoji} Активность: {name}")
    if "l" in state:
        lines.append(f"📍 Место: {LOCATIONS[state['l']].name}")
    if "d" in state:
        when = _day_label(state["d"])
        if "t" in state:
            when += " " + _fmt_time(state["t"])
        lines.append(f"🕒 Время: {when}")
    if "m" in state:
        lines.append(f"👥 Состав: {_size_label(state['q'], state['m'])}")
    if "v" in state:
        lines.append(f"🏷 Уровень: {SKILL_LEVELS[state['v']]}")
    if state.get("c") == COMMENT_GIVEN and comment:
        lines.append(f"💬 Комментарий: {comment}")
    return "\n".join(lines)


def _calendar(state: dict) -> List[list]:
    """Month-style grid of the next DAYS_AHEAD days, weeks as rows."""
    noop = lambda text: {"text": text, "callback_data": NOOP}
    rows = [[noop(d) for d in WEEKDAYS]]
    week = [noop("·")] * _day(0).weekday()
    for idx in range(DAYS_AHEAD):
        day = _day(idx)
        label = str(day.day) if day.day != 1 and idx != 0 else day.strftime("%d.%m").lstrip("0")
        if _day_available(idx):
            week.append({"text": label, "callback_data": _with(state, d=idx)})
        else:
            week.append(noop("·"))
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        rows.append(week + [noop("·")] * (7 - len(week)))
    return rows


def render_step(state: dict, error: str = None, comment: str = None) -> Tuple[str, list]:
    """Text and keyboard for the next unfilled field (or the confirmation screen)."""
    if "t" in state and validate(state):
        # drop the invalid time and ask again
        state = {k: v for k, v in state.items() if k not in ("t", "h")}

    summary = _summary(state, comment)
    header = "➕ **Создание встречи**\n\n" + (summary + "\n\n" if summary else "")
    if error:
        header += error + "\n\n"

    step = next_step(state)
    step_label = f"Шаг {list(STEPS).index(step) + 1} из {len(STEPS)}. " if step else ""
    back = _back_button(state)
    footer = ([back] if back else []) + [CANCEL]

    if step == "a":
        rows = [[{"text": f"{emoji} {name}", "callback_data": _with(state, a=i)}]
                for i, (name, emoji) in enumerate(ACTIVITIES)]
        return header + step_label + "Выберите активность:", rows + footer

    if step == "l":
        rows = [[{"text": f"📍 {loc.name}", "callback_data": _with(state, l=i)}]
                for i, loc in enumerate(LOCATIONS)]
        return header + step_label + "Где встречаемся?", rows + footer

    if step == "d":
        first, last = _day(0), _day(DAYS_AHEAD - 1)
        return (header + step_label + f"Выберите день ({first.strftime('%d.%m')} – {last.strftime('%d.%m')}):",
                _calendar(state) + footer)

    if step == "t" and "h" not in state:
        hours = _available_hours(state["d"])
        buttons = [{"text": f"{h:02d}:__", "callback_data": _with(state, h=h)} for h in hours]
        rows = [buttons[i:i + 6] for i in range(0, len(buttons), 6)]
        return (header + step_label + "Во сколько начало? Выберите час "
                "или напишите точное время в чат, например 19:40."), rows + footer

    if step == "t":
        h = state["h"]
        buttons = [{"text": _fmt_time(h * 60 + m), "callback_data": _with(state, t=h * 60 + m)}
                   for m in MINUTE_OPTIONS if _time_ok(state["d"], h * 60 + m)]
        if not buttons:
            # the hour has passed while the user was choosing
            state = {k: v for k, v in state.items() if k != "h"}
            return render_step(state, "⌛ Это время уже прошло. Выберите другой час.", comment)
        rows = [buttons]
        return (header + step_label + "Выберите минуты или напишите точное время в чат, например "
                f"{h:02d}:40."), rows + footer

    if step == "s":
        rows = [[{"text": f"👥 {_size_label(q, m)}", "callback_data": _with(state, q=q, m=m)}]
                for q, m in SIZE_PRESETS]
        rows.append([{"text": f"♾ Без ограничения (от {MIN_PARTICIPANTS} человек)",
                      "callback_data": _with(state, q=MIN_PARTICIPANTS, m=UNLIMITED)}])
        return (header + step_label + "Сколько нужно участников? Выберите вариант или напишите в чат "
                "минимум и максимум, например **5-12** (встреча подтвердится, когда наберётся минимум), "
                "одно число, если нужен ровно такой состав, или «5+» — минимум 5, без ограничения сверху."), rows + footer

    if step == "v":
        rows = [[{"text": level, "callback_data": _with(state, v=i)}]
                for i, level in enumerate(SKILL_LEVELS)]
        return header + step_label + "Уровень игры:", rows + footer

    if step == "c":
        rows = [[{"text": "Без комментария »", "callback_data": _with(state, c=COMMENT_SKIPPED)}]]
        return (header + step_label + f"💬 Напишите в чат короткий комментарий к встрече (до {COMMENT_MAX_LEN} символов), "
                "например: «берём свой мяч» или «играем 3 на 3»."), rows + footer

    rows = [
        [{"text": "✅ Опубликовать", "callback_data": encode(state, PUBLISH)}],
        back,
        [{"text": "🔄 Начать заново", "callback_data": "create_meeting"}],
        CANCEL,
    ]
    return header + "Всё верно? Вы будете записаны на встречу первым участником.", rows
