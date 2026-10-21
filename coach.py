"""Данные из intervals.icu, разбор Claude, отправка в Telegram."""
import json
import os
from datetime import date, timedelta

import requests

INTERVALS_API_KEY = os.environ.get("INTERVALS_API_KEY", "")
ATHLETE = os.environ.get("INTERVALS_ATHLETE_ID", "0") or "0"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "") or "claude-sonnet-5-5"
ICU = "https://intervals.icu/api/v1"
TG = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------- промпты
STYLE = """Ты личный тренер по бегу. Ты давно ведёшь этого спортсмена-любителя, знаешь его \
зоны и историю. Пишешь ему лично на русском, на «ты», тепло и по делу, как живой тренер, \
а не как отчёт из приложения. Не пересказывай цифры списком: каждую цифру объясняй — что \
она значит, хорошо это или нет, почему так вышло и что с этим делать. Связывай данные \
между собой. Хвали конкретно, критикуй конструктивно.

Оформление: заголовки разделов строкой вида «## 👋 Название», пункты списков строкой \
«- текст», выделение **жирным** изредка. Других markdown-символов не используй. \
Абзацы короткие. Темп в мин/км (готовые значения в полях «темп»), пульс в уд/мин.
Опирайся только на присланные данные, ничего не выдумывай. Ты не врач: при тревожных \
признаках (очень высокий пульс на лёгком беге, рост пульса покоя, падение HRV, боль) \
советуй снизить нагрузку и при необходимости обратиться к врачу."""

WORKOUT_PROMPT = STYLE + """

Задача: подробный разбор одной тренировки, примерно 2500–4500 символов. Разделы:
## 👋 Первое впечатление — пару живых фраз о тренировке в целом.
## 🎯 Какая была задача — определи тип (восстановительный, лёгкий, длительный, темповый, \
интервалы, фартлек, забег) по структуре, темпу и пульсу; попал ли в задачу.
## 🏃 Разбор по ходу тренировки — по кругам/отрезкам: старт, ровность, где ускорился или \
подсел, раскладка, поведение пульса, дрейф пульса.
## ❤️ Пульс и зоны — время в зонах и что это даёт; не было ли «серой зоны».
## 🦶 Техника и экономичность — каденс, длина шага и т.п., если есть; 1–2 подсказки.
## 📅 Контекст недели — объём, чередование нагрузок, восстановление (сон, пульс покоя, HRV, \
форма), если есть данные.
## ✅ Что получилось и над чем работать — 2–3 сильных стороны и 2–3 точки роста списком.
## 🗓 План на ближайшие дни — 2–3 дня списком: день, тип тренировки, длительность, коридор \
темпа и пульса по его зонам; если нужен отдых — так и скажи.
## 💬 Совет тренера — одна мысль на прощание.

Если есть «комментарий_спортсмена» (самочувствие, боль, усталость), обязательно учти его \
в разборе и плане. Если тренировка была давно (сравни с полем «сегодня»), отметь перерыв. \
Если тренировка не беговая, разбери её с точки зрения пользы для бега."""

WEEK_PROMPT = STYLE + """

Задача: итоги последних 7 дней, примерно 1500–2500 символов. Разделы:
## 📊 Неделя в цифрах — объём, количество тренировок, нагрузка, сравнение с прошлой неделей.
## ⚖️ Баланс нагрузки — чередование лёгких и тяжёлых, распределение по зонам.
## 😴 Восстановление — сон, пульс покоя, HRV, форма/усталость, если есть.
## ✅ Главное за неделю — что получилось и что поправить, списком.
## 🗓 Фокус на следующую неделю — 3–4 пункта."""

PLAN_PROMPT = STYLE + """

Задача: план на ближайшие 3 дня с учётом последних тренировок и восстановления, \
примерно 1000–2000 символов. Разделы:
## 🔎 Где ты сейчас — коротко: усталость/свежесть, что было в последние дни.
## 🗓 План — по дням списком: тип тренировки, длительность или дистанция, коридор темпа \
и пульса по его зонам, на что обратить внимание.
## 💬 Совет тренера — одна мысль."""


# ---------------------------------------------------------------- intervals.icu
def icu_get(path, **params):
    r = requests.get(f"{ICU}{path}", params=params, auth=("API_KEY", INTERVALS_API_KEY),
                     timeout=30)
    r.raise_for_status()
    return r.json()


def activities_between(oldest, newest):
    return icu_get(f"/athlete/{ATHLETE}/activities", oldest=oldest.isoformat(),
                   newest=newest.isoformat())


def wellness(end, days=7):
    try:
        return icu_get(f"/athlete/{ATHLETE}/wellness",
                       oldest=(end - timedelta(days=days)).isoformat(), newest=end.isoformat())
    except Exception:
        return []


def activity_date(a):
    try:
        return date.fromisoformat(str(a.get("start_date_local", ""))[:10])
    except ValueError:
        return date.today()


def with_data(acts):
    out = [a for a in acts if a.get("moving_time")]
    out.sort(key=lambda a: a.get("start_date_local", ""))
    return out


def find_latest():
    """Самая свежая тренировка с данными на любую давность. None, если нет вообще."""
    today = date.today()
    for days in (14, 60, 365, 3650):
        acts = with_data(activities_between(today - timedelta(days=days), today))
        if acts:
            return acts[-1]
    return None


def recent(days=14):
    today = date.today()
    return with_data(activities_between(today - timedelta(days=days), today))


def no_workouts_reason():
    today = date.today()
    raw = activities_between(today - timedelta(days=3650), today)
    if not raw:
        return ("В intervals.icu не найдено ни одной тренировки. Проверьте, что Garmin Connect "
                "подключён в intervals.icu (Settings → Connections).")
    return ("Тренировки в intervals.icu есть, но без данных: скорее всего, они пришли из Strava, "
            "а такие intervals.icu не отдаёт через API. Подключите Garmin Connect напрямую.")


# ---------------------------------------------------------------- форматирование
def pace(speed):
    try:
        sec = 1000 / float(speed)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None if sec > 3600 else f"{int(sec // 60)}:{int(round(sec % 60)) % 60:02d} /км"


def hms(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return None
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


ACT_KEYS = [
    "id", "start_date_local", "name", "type", "sub_type", "distance", "moving_time",
    "elapsed_time", "total_elevation_gain", "average_speed", "max_speed", "gap",
    "average_heartrate", "max_heartrate", "average_cadence", "average_stride", "calories",
    "icu_training_load", "icu_intensity", "trimp", "icu_hr_zone_times", "icu_hr_zones",
    "pace_zone_times", "gap_zone_times", "decoupling", "icu_efficiency_factor", "icu_hrr",
    "average_temp", "feel", "perceived_exertion", "session_rpe", "description", "race",
    "lthr", "athlete_max_hr", "icu_resting_hr", "icu_atl", "icu_ctl", "device_name",
    "icu_average_watts", "icu_weighted_avg_watts", "trainer", "polarization_index",
]


def slim_activity(a):
    out = {k: a[k] for k in ACT_KEYS if a.get(k) is not None}
    if pace(a.get("average_speed")):
        out["темп"] = pace(a.get("average_speed"))
    if pace(a.get("gap")):
        out["темп_с_поправкой_на_рельеф"] = pace(a.get("gap"))
    if a.get("moving_time"):
        out["время_в_движении"] = hms(a["moving_time"])
    if a.get("distance"):
        out["дистанция_км"] = round(a["distance"] / 1000, 2)
    cad = a.get("average_cadence")
    if cad and a.get("type") in ("Run", "VirtualRun", "TrailRun", "Treadmill"):
        out["каденс_шаг_мин"] = round(cad * 2 if cad < 120 else cad)
    return out


def slim_wellness(w):
    keys = ["id", "ctl", "atl", "restingHR", "hrv", "sleepSecs", "sleepScore", "weight",
            "readiness", "soreness", "fatigue", "stress", "mood"]
    return {k: w[k] for k in keys if w.get(k) is not None}


def intervals_of(activity_id):
    try:
        data = icu_get(f"/activity/{activity_id}/intervals")
    except Exception:
        return []
    items = data.get("icu_intervals", []) if isinstance(data, dict) else data
    keys = ["type", "label", "distance", "moving_time", "average_speed", "gap",
            "average_heartrate", "max_heartrate", "average_cadence", "average_stride",
            "total_elevation_gain", "zone", "decoupling"]
    out = []
    for n, it in enumerate(items or [], 1):
        row = {"№": n, **{k: it[k] for k in keys if it.get(k) is not None}}
        if pace(it.get("average_speed")):
            row["темп"] = pace(it.get("average_speed"))
        if it.get("moving_time"):
            row["время"] = hms(it["moving_time"])
        cad = it.get("average_cadence")
        if cad:
            row["каденс_шаг_мин"] = round(cad * 2 if cad < 120 else cad)
        out.append(row)
    return out[:60]


def athlete_profile():
    try:
        a = icu_get(f"/athlete/{ATHLETE}")
    except Exception:
        return {}
    keep = ["types", "lthr", "max_hr", "threshold_pace", "hr_zones", "hr_zone_names",
            "pace_zones", "pace_zone_names"]
    sports = []
    for ss in a.get("sportSettings", []) or []:
        row = {k: ss[k] for k in keep if ss.get(k) is not None}
        if pace(ss.get("threshold_pace")):
            row["пороговый_темп"] = pace(ss.get("threshold_pace"))
        if row:
            sports.append(row)
    prof = {k: a[k] for k in ("icu_resting_hr", "icu_weight") if a.get(k) is not None}
    if sports:
        prof["зоны_по_видам_спорта"] = sports
    return prof


# ---------------------------------------------------------------- Claude
def claude(system, payload, max_tokens=6000):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": ANTHROPIC_MODEL, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user",
                            "content": json.dumps(payload, ensure_ascii=False, default=str)}]},
        timeout=300,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            err = r.text
        raise RuntimeError(f"Claude API ответил {r.status_code}: {err[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()


UNITS = ("distance в метрах, время в секундах, скорость м/с; поля «темп» уже в мин/км; "
         "*_zone_times — секунды по зонам; каденс бери из «каденс_шаг_мин» (шагов в минуту)")


def workout_report(activity, comment=""):
    """Возвращает (текст разбора, данные для PDF)."""
    d = activity_date(activity)
    hist = [a for a in with_data(activities_between(d - timedelta(days=14), d))
            if a.get("id") != activity.get("id")]
    laps = intervals_of(activity.get("id"))
    profile = athlete_profile()
    payload = {
        "сегодня": date.today().isoformat(),
        "текущая_тренировка": slim_activity(activity),
        "круги_и_отрезки": laps,
        "профиль_и_зоны_спортсмена": profile,
        "тренировки_за_14_дней_до_этой": [slim_activity(a) for a in hist],
        "восстановление_7_дней_до_тренировки": [slim_wellness(w) for w in wellness(d)],
        "единицы": UNITS,
    }
    if comment:
        payload["комментарий_спортсмена"] = comment
    text = claude(WORKOUT_PROMPT, payload)
    return text, {"activity": activity, "laps": laps, "profile": profile}


def week_report(comment=""):
    today = date.today()
    acts = with_data(activities_between(today - timedelta(days=14), today))
    payload = {
        "сегодня": today.isoformat(),
        "тренировки_за_14_дней": [slim_activity(a) for a in acts],
        "профиль_и_зоны_спортсмена": athlete_profile(),
        "восстановление_7_дней": [slim_wellness(w) for w in wellness(today)],
        "единицы": UNITS,
    }
    if comment:
        payload["комментарий_спортсмена"] = comment
    return claude(WEEK_PROMPT, payload, 4000)


def plan_report(comment=""):
    today = date.today()
    acts = with_data(activities_between(today - timedelta(days=14), today))
    payload = {
        "сегодня": today.isoformat(),
        "тренировки_за_14_дней": [slim_activity(a) for a in acts],
        "профиль_и_зоны_спортсмена": athlete_profile(),
        "восстановление_7_дней": [slim_wellness(w) for w in wellness(today)],
        "единицы": UNITS,
    }
    if comment:
        payload["комментарий_спортсмена"] = comment
    return claude(PLAN_PROMPT, payload, 3000)


# ---------------------------------------------------------------- Telegram
def tg(method, **kw):
    r = requests.post(f"{TG}/{method}", timeout=60, **kw)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram {method} ответил {r.status_code}: {r.text[:300]}")
    return r.json()


def get_updates(offset):
    return tg("getUpdates", json={"offset": offset, "timeout": 0,
                                  "allowed_updates": ["message"]}).get("result", [])


def to_plain(text):
    """Разметка разбора -> обычный текст для Telegram."""
    lines = []
    for line in text.splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            s = s[3:]
        elif s.startswith("- "):
            s = "• " + s[2:]
        lines.append(s.replace("**", ""))
    return "\n".join(lines).strip()


def split_message(text, limit=3900):
    parts, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut <= 0:
                cut = para.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        cand = f"{cur}\n\n{para}" if cur else para
        if len(cand) > limit:
            parts.append(cur)
            cur = para
        else:
            cur = cand
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def send_text(text, chat_id=None):
    for chunk in split_message(to_plain(text)):
        tg("sendMessage", json={"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": chunk})


def send_document(path, filename, caption="", chat_id=None):
    with open(path, "rb") as f:
        tg("sendDocument", data={"chat_id": chat_id or TELEGRAM_CHAT_ID, "caption": caption[:1000]},
           files={"document": (filename, f, "application/pdf")})
