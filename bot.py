"""AI тренер: один проход бота (GitHub Actions запускает его каждые ~10 минут).

1. Читает новые сообщения боту (только от владельца, TELEGRAM_CHAT_ID).
2. Отвечает на команды и на «провёл тренировку» — разбором последней тренировки.
3. Если тренировка ещё не появилась в intervals.icu — ждёт её до 6 часов.
4. Если AUTO_ANALYZE=1 — сам присылает разбор каждой новой тренировки.
"""
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

import coach

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
REPORT_FORMAT = (os.environ.get("REPORT_FORMAT") or "pdf").lower()   # pdf | text | both
AUTO_ANALYZE = (os.environ.get("AUTO_ANALYZE") or "1") not in ("0", "false", "no", "нет")
PENDING_HOURS = 6

HELP = (
    "Привет! Я твой AI-тренер по бегу 🏃\n\n"
    "Просто напиши мне после тренировки, например: «провёл тренировку, к концу устал, "
    "немного тянуло икру». Я возьму её из intervals.icu и пришлю подробный разбор "
    "с учётом твоего самочувствия.\n\n"
    "Команды:\n"
    "/week или «неделя» — итоги недели\n"
    "/plan или «план» — план на ближайшие дни\n"
    "/again или «повтор» — ещё раз разобрать последнюю тренировку\n"
    "/goal — цель-старт: «/goal полумарафон 15 ноября 2026, старт в 10:00, хочу 1:45». "
    "Дальше учитываю её в разборах и планах. «/goal» без текста — показать цель, "
    "«/goal сброс» — удалить\n\n"
    "Те же команды есть кнопками под полем ввода.\n\n"
    "Отвечаю не мгновенно: я проверяю сообщения примерно раз в 10 минут."
)


def now():
    return datetime.now(timezone.utc)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def save_state(st):
    st["seen"] = st["seen"][-500:]
    st["analysed"] = st["analysed"][-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def say(text, keyboard=False):
    coach.send_text(text, keyboard=keyboard)


UI_VERSION = 1


def ensure_ui(st):
    """Один раз: меню команд («/») и кнопки под полем ввода."""
    if st.get("ui_v") == UI_VERSION:
        return
    try:
        coach.tg("setMyCommands", json={"commands": coach.COMMANDS})
    except Exception:
        traceback.print_exc()
    say("Добавил кнопки внизу 👇\n\n🎯 Цель — задать старт, к которому ты готовишься\n"
        "📅 План — план на ближайшие дни\n📊 Неделя — итоги недели\n"
        "🔁 Повтор — ещё раз разобрать последнюю тренировку\n\n"
        "А после тренировки можно просто написать мне, как она прошла.", keyboard=True)
    st["ui_v"] = UI_VERSION


def deliver_workout(activity, comment, st):
    """Разбор тренировки -> Telegram (PDF и/или текст)."""
    text, data = coach.workout_report(activity, comment)
    sent = False
    if REPORT_FORMAT in ("pdf", "both"):
        try:
            import report_pdf
            w = report_pdf.build_report_data(data["activity"], data["laps"], data["profile"])
            d = coach.activity_date(activity)
            path = os.path.join(tempfile.gettempdir(), "report.pdf")
            report_pdf.build_pdf(path, w, text)
            coach.send_document(path, f"Разбор {d:%d.%m.%Y} — {w['name'][:40]}.pdf",
                                caption=report_pdf.short_caption(activity, text))
            sent = True
        except Exception:
            traceback.print_exc()
            say("Не получилось собрать PDF, присылаю разбор текстом.")
    if REPORT_FORMAT in ("text", "both") or not sent:
        say(text)
    aid = str(activity.get("id"))
    if aid not in st["analysed"]:
        st["analysed"].append(aid)
    print(f"Разбор отправлен: {activity.get('name')} ({aid})")


def deliver_week(comment):
    """Итоги недели -> Telegram (PDF и/или текст)."""
    text, data = coach.week_report(comment)
    sent = False
    if REPORT_FORMAT in ("pdf", "both"):
        try:
            import report_pdf
            wk = report_pdf.build_week_data(data["acts"], data["today"], data["profile"],
                                            data["wellness"])
            if wk:
                path = os.path.join(tempfile.gettempdir(), "week.pdf")
                report_pdf.build_week_pdf(path, wk, text)
                coach.send_document(path, f"Итоги недели {wk['short_period']}.pdf",
                                    caption=report_pdf.short_week_caption(wk, text))
                sent = True
        except Exception:
            traceback.print_exc()
            say("Не получилось собрать PDF, присылаю итоги текстом.")
    if REPORT_FORMAT in ("text", "both") or not sent:
        say(text)
    print("Итоги недели отправлены")


def describe(a):
    d = coach.activity_date(a)
    km = (a.get("distance") or 0) / 1000
    return f"«{a.get('name', 'Тренировка')}» от {d:%d.%m.%Y}, {km:.1f} км"


GOAL_WORDS = ("goal", "цель", "старт")
GOAL_OFF = ("off", "сброс", "удалить", "нет", "clear", "delete")
GOAL_CANCEL = ("отмена", "cancel")
BUTTON_WORDS = ("цель", "план", "неделя", "повтор")
GOAL_EXPIRE_DAYS = 21


def apply_goal(st):
    """Передаёт цель из состояния в coach; через 21 день после старта убирает её."""
    g = st.get("goal")
    coach.GOAL = g
    if g and g.get("date"):
        past = (datetime.now().date() - datetime.fromisoformat(g["date"]).date()).days
        if past > GOAL_EXPIRE_DAYS:
            st["goal"] = coach.GOAL = None
            say(f"Прошло больше {GOAL_EXPIRE_DAYS} дней после твоего старта, цель сняла. "
                "Напиши /goal, когда выберешь следующий.")


def handle_goal(rest, st):
    if not rest:
        g = st.get("goal")
        if g:
            b = coach.goal_block()
            left = f" До старта {b['дней_до_старта']} дн." if "дней_до_старта" in b else ""
            say(f"Твоя цель: {g['text']}.{left}\n\nЧтобы изменить, отправь новую цель следующим "
                "сообщением. «сброс» — удалить цель, «отмена» — оставить как есть.")
        else:
            say("Цель пока не задана. Отправь её следующим сообщением, например: «полумарафон "
                "15 ноября 2026, старт в 10:00, хочу 1:45». «отмена» — не задавать.")
        st["awaiting_goal"] = now().isoformat()
        return
    if rest.strip().lower() in GOAL_CANCEL:
        say("Ок, цель не меняю.")
        return
    if rest.strip().lower() in GOAL_OFF:
        st["goal"] = coach.GOAL = None
        say("Цель удалена.")
        return
    d = coach.parse_goal_date(rest)
    st["goal"] = coach.GOAL = {"text": rest.strip(), "date": d.isoformat() if d else None,
                               "set": now().isoformat()}
    if d:
        left = (d - datetime.now().date()).days
        when = (f"До старта {left} дн." if left > 0 else
                "Старт сегодня!" if left == 0 else f"Старт был {-left} дн. назад.")
        say(f"Принял! Цель: {rest.strip()}.\n{when} Буду учитывать её в разборах и планах.")
    else:
        say(f"Записал цель: {rest.strip()}.\n\nДату старта я не нашёл. Напиши её, например: "
            "«/goal полумарафон 15 ноября 2026, хочу 1:45», тогда смогу считать дни до старта "
            "и строить план от него.")


def handle_message(text, st):
    t = re.sub(r"^[^\w/]+", "", text.strip()).strip() or text.strip()   # «🎯 Цель» -> «Цель»
    low = t.lower().lstrip("/")
    word = low.split()[0] if low else ""
    rest = t.split(maxsplit=1)[1] if len(t.split(maxsplit=1)) > 1 else ""

    # ждём текст цели после нажатия «🎯 Цель» (2 часа)
    waiting = st.get("awaiting_goal")
    if waiting:
        st["awaiting_goal"] = None
        fresh = now() - datetime.fromisoformat(waiting) < timedelta(hours=2)
        is_cmd = t.startswith("/") or t.lower() in BUTTON_WORDS
        answer = t.lower() in GOAL_OFF + GOAL_CANCEL or coach.looks_like_goal(t)
        # обычный комментарий о тренировке цель не затирает: дальше пойдёт как комментарий
        if fresh and not is_cmd and answer:
            handle_goal(t, st)
            return

    # цель: «/goal …», «/цель …» или «цель: …» / «старт: …» (с двоеточием, чтобы не путать
    # с обычным комментарием вроде «старт был тяжёлый»)
    explicit = t.startswith("/") or word.endswith(":")
    gword = word.rstrip(":")
    bare_goal = not rest and gword in ("goal", "цель")      # кнопка «🎯 Цель» или /goal
    if bare_goal or (explicit and gword in GOAL_WORDS and rest):
        handle_goal(rest, st)
        return
    if word in ("start", "help", "помощь", "старт") and not rest:
        say(HELP)
        return
    if word in ("week", "неделя", "неделю", "итоги"):
        say("Принял, готовлю итоги недели…")
        deliver_week(rest)
        return
    if word in ("plan", "план"):
        say("Принял, составляю план на ближайшие дни…")
        say(coach.plan_report(rest))
        return

    latest = coach.find_latest()
    if latest is None:
        say(coach.no_workouts_reason())
        return
    again = word in ("again", "повтор", "повтори", "ещё", "еще")
    comment = rest if again else t
    if again or str(latest.get("id")) not in st["analysed"]:
        say(f"Принял! Разбираю {describe(latest)}. Пришлю через минуту-две…")
        deliver_workout(latest, comment, st)
        return
    st["pending"] = {"comment": t, "since": now().isoformat()}
    say(f"Последняя тренировка в intervals.icu — {describe(latest)}, её я уже разбирал.\n\n"
        "Новая пока не появилась: синхронизируй часы с телефоном. Я проверяю примерно раз "
        f"в 10 минут и пришлю разбор, как только она появится (жду до {PENDING_HOURS} часов). "
        "Твой комментарий учту.\n\nЕсли нужен повторный разбор этой тренировки — напиши «повтор».")


def check_new_workouts(st, first_run):
    acts = coach.recent(14)
    new = [a for a in acts if str(a.get("id")) not in st["seen"]]
    for a in acts:
        if str(a.get("id")) not in st["seen"]:
            st["seen"].append(str(a.get("id")))
    if first_run:
        return
    pending = st.get("pending")
    if new and (AUTO_ANALYZE or pending):
        latest = new[-1]  # только самую свежую, без спама старыми
        if str(latest.get("id")) not in st["analysed"]:
            deliver_workout(latest, (pending or {}).get("comment", ""), st)
        st["pending"] = None
        return
    if pending:
        since = datetime.fromisoformat(pending["since"])
        if now() - since > timedelta(hours=PENDING_HOURS):
            st["pending"] = None
            say(f"Я ждал новую тренировку {PENDING_HOURS} часов, но она так и не появилась в "
                "intervals.icu. Проверь синхронизацию Garmin → intervals.icu и напиши мне ещё раз.")


def main():
    missing = [k for k in ("INTERVALS_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                           "ANTHROPIC_API_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Не заданы секреты GitHub: {', '.join(missing)}")

    st = load_state()
    first_run = st is None
    st = st or {"offset": 0, "seen": [], "analysed": [], "pending": None}
    st.setdefault("pending", None)
    st.setdefault("goal", None)
    st.setdefault("awaiting_goal", None)
    failed = False
    try:
        apply_goal(st)
        ensure_ui(st)
    except Exception:
        traceback.print_exc()

    # 1) сообщения
    try:
        updates = coach.get_updates(st["offset"])
    except Exception:
        traceback.print_exc()
        updates = []
        failed = True
    for u in updates:
        st["offset"] = max(st["offset"], u["update_id"] + 1)
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != coach.TELEGRAM_CHAT_ID:
            print(f"Сообщение из чужого чата {chat} пропущено")
            continue
        text = msg.get("text") or msg.get("caption") or ""
        if not text:
            continue
        print(f"Сообщение: {text[:80]}")
        try:
            handle_message(text, st)
        except Exception as e:
            traceback.print_exc()
            failed = True
            try:
                say(f"Не получилось выполнить запрос: {e}")
            except Exception:
                pass
        save_state(st)

    # 2) новые тренировки
    try:
        check_new_workouts(st, first_run)
    except Exception as e:
        traceback.print_exc()
        failed = True
        if st.get("pending"):
            st["pending"] = None  # чтобы не повторять ошибку каждые 10 минут
            try:
                say(f"Не получилось подготовить разбор: {e}")
            except Exception:
                pass
    save_state(st)
    if first_run:
        print(f"Первый запуск: запомнил {len(st['seen'])} тренировок.")
    # ошибки уже в логе и в Telegram; код 0, чтобы GitHub не слал письма о каждом сбое сети
    print("Были ошибки, см. выше" if failed else "OK")


if __name__ == "__main__":
    main()
