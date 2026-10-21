"""AI тренер: один проход бота (GitHub Actions запускает его каждые ~10 минут).

1. Читает новые сообщения боту (только от владельца, TELEGRAM_CHAT_ID).
2. Отвечает на команды и на «провёл тренировку» — разбором последней тренировки.
3. Если тренировка ещё не появилась в intervals.icu — ждёт её до 6 часов.
4. Если AUTO_ANALYZE=1 — сам присылает разбор каждой новой тренировки.
"""
import json
import os
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
    "/again или «повтор» — ещё раз разобрать последнюю тренировку\n\n"
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


def say(text):
    coach.send_text(text)


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


def describe(a):
    d = coach.activity_date(a)
    km = (a.get("distance") or 0) / 1000
    return f"«{a.get('name', 'Тренировка')}» от {d:%d.%m.%Y}, {km:.1f} км"


def handle_message(text, st):
    t = text.strip()
    low = t.lower().lstrip("/")
    word = low.split()[0] if low else ""
    rest = t.split(maxsplit=1)[1] if len(t.split(maxsplit=1)) > 1 else ""

    if word in ("start", "help", "помощь", "старт"):
        say(HELP)
        return
    if word in ("week", "неделя", "неделю", "итоги"):
        say("Принял, готовлю итоги недели…")
        say(coach.week_report(rest))
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
    failed = False

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
