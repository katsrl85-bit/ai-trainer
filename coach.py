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
## 🏃 Разбор по ходу тренировки — по километрам из «раскладка_по_километрам» (число \
километров считай только по ней, не по автоинтервалам): старт, ровность, где ускорился \
или подсел, раскладка, поведение пульса, дрейф пульса.
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


# ---------------------------------------------------------------- цель (старт)
GOAL = None   # {"text": ..., "date": "YYYY-MM-DD" | None}; выставляет bot.py из состояния

MONTHS_RU = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
             "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
             "декабря": 12, "янв": 1, "фев": 2, "мар": 3, "апр": 4, "июн": 6, "июл": 7,
             "авг": 8, "сен": 9, "сент": 9, "окт": 10, "ноя": 11, "дек": 12}

GOAL_RULES = """

У спортсмена есть цель — старт (поле «цель_спортсмена»). Учитывай её во всём:
- Скажи, сколько дней до старта и на каком этапе подготовки он сейчас: набор базы (больше \
~10 недель), специальный блок, подводка (последние 7–10 дней, объём снижается), предстартовая \
неделя.
- Если в цели есть желаемый результат, посчитай целевой темп (мин/км) из дистанции и результата \
и сверяй с ним тренировки. Честно оцени по его темпу, пульсу и объёму, насколько цель \
реалистична. Соревновательных результатов у тебя нет, поэтому оценка осторожная; скажи об этом.
- Планы строй от даты старта: ключевые тренировки, длительные, темповые, подводка. В последние \
2–3 дня: сон, питание, разминка, раскладка по километрам с учётом времени старта.
- Если даты старта в цели нет, один раз вежливо попроси написать её.
- Если старт уже прошёл, попроси прислать результат, разбери забег и предложи план \
восстановления.
- В разборе тренировки добавь раздел «## 🏁 К старту» перед планом: что эта тренировка \
дала для цели и что менять."""


def parse_goal_date(text, today=None):
    """Ищет дату в тексте цели: 15.11.2026, 15.11, 15 ноября [2026]. None, если не нашёл."""
    import re
    today = today or date.today()
    t = text.lower()

    def build(d, m, y):
        try:
            if y is None:
                cand = date(today.year, m, d)
                return cand if cand >= today - timedelta(days=1) else date(today.year + 1, m, d)
            y = y + 2000 if y < 100 else y
            return date(y, m, d)
        except ValueError:
            return None

    m = re.search(r"(?<![\d:.,])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:]|\s*(?:км|km|k\b|м\b))", t)
    if m:
        r = build(int(m[1]), int(m[2]), int(m[3]) if m[3] else None)
        if r:
            return r
    names = "|".join(sorted(MONTHS_RU, key=len, reverse=True))
    m = re.search(rf"(?<!\d)(\d{{1,2}})\s+({names})\w*\.?(?:\s+(\d{{4}}))?", t)
    if m:
        return build(int(m[1]), MONTHS_RU[m[2]], int(m[3]) if m[3] else None)
    return None


GOAL_HINTS = ("марафон", "забег", "хочу", "цель", "трейл", "ультра", "полумарафон", "соревнован")


def looks_like_goal(text):
    """Похоже ли сообщение на описание старта: есть дата или «марафон/забег/хочу…»."""
    if parse_goal_date(text):
        return True
    t = text.lower()
    return any(h in t for h in GOAL_HINTS)


def goal_block(today=None):
    """Данные цели для Claude или None."""
    if not GOAL or not GOAL.get("text"):
        return None
    today = today or date.today()
    out = {"текст": GOAL["text"]}
    if GOAL.get("date"):
        d = date.fromisoformat(GOAL["date"])
        left = (d - today).days
        out["дата_старта"] = d.isoformat()
        out["дней_до_старта"] = left
        out["недель_до_старта"] = round(left / 7, 1)
        if left < 0:
            out["старт_уже_прошёл_дней_назад"] = -left
    else:
        out["дата_старта"] = "не указана"
    return out


def system_for(base):
    return base + GOAL_RULES if goal_block() else base


def with_goal(payload):
    g = goal_block()
    if g:
        payload["цель_спортсмена"] = g
    return payload


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
    if sec > 3600:
        return None
    m, s = divmod(int(round(sec)), 60)   # сначала округляем, потом делим: без «4:60» и «4:00» вместо «5:00»
    return f"{m}:{s:02d} /км"


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


def _mean(vals):
    vals = [v for v in vals if v is not None and v > 0]
    return sum(vals) / len(vals) if vals else None


def km_splits(activity_id, split_m=1000.0):
    """Раскладка строго по километрам из посекундных данных.

    6,2 км -> 6 полных километров + седьмой отрезок 0,2 км. Возвращает [] если
    потоков нет. Строки в том же формате, что и у intervals.icu (distance,
    moving_time, average_speed, average_heartrate, average_cadence)."""
    try:
        data = icu_get(f"/activity/{activity_id}/streams",
                       types=["time", "distance", "heartrate", "cadence", "moving"])
    except Exception:
        return []
    streams = {}
    items = data if isinstance(data, list) else data.get("streams", [])
    for s in items or []:
        if isinstance(s, dict) and s.get("type") and isinstance(s.get("data"), list):
            streams[s["type"]] = s["data"]
    t, dist = streams.get("time"), streams.get("distance")
    if not t or not dist or len(t) != len(dist) or len(t) < 2:
        return []
    moving = streams.get("moving")
    hr, cad = streams.get("heartrate"), streams.get("cadence")

    # накопленное время в движении (паузы светофоров и остановки не считаем)
    cm = [0.0]
    for i in range(1, len(t)):
        step = t[i] - t[i - 1]
        cm.append(cm[-1] + (step if (not moving or moving[i]) else 0))

    def at(target):  # время в движении в точке target метров (линейная интерполяция)
        lo, hi = 0, len(dist) - 1
        if target <= dist[0]:
            return cm[0]
        if target >= dist[-1]:
            return cm[-1]
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if dist[mid] <= target:
                lo = mid
            else:
                hi = mid
        span = dist[hi] - dist[lo]
        frac = (target - dist[lo]) / span if span else 0
        return cm[lo] + (cm[hi] - cm[lo]) * frac

    total = dist[-1]
    if total < 200:
        return []
    out, start, n = [], 0.0, 0
    while start < total - 1:
        end = min(start + split_m, total)
        seg_m = end - start
        if seg_m < 50 and n:  # крошечный хвост (<50 м) не показываем отдельной строкой
            break
        n += 1
        seg_t = at(end) - at(start)
        idx = [i for i in range(len(dist)) if start <= dist[i] <= end
               and (not moving or moving[i])]
        label = f"{n} км" if seg_m >= split_m - 1 else f"{n} ({seg_m / 1000:.2f})".replace(".", ",")
        row = {"№": n, "label": label, "distance": round(seg_m)}
        if seg_t > 0:
            row["moving_time"] = round(seg_t)
            row["average_speed"] = round(seg_m / seg_t, 4)
            row["темп"] = pace(seg_m / seg_t)
            row["время"] = hms(seg_t)
        h = _mean([hr[i] for i in idx]) if hr else None
        c = _mean([cad[i] for i in idx]) if cad else None
        if h:
            row["average_heartrate"] = round(h)
        if c:
            row["average_cadence"] = c
            row["каденс_шаг_мин"] = round(c * 2 if c < 120 else c)
        out.append(row)
        start = end
    return out[:120]


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
    splits = km_splits(activity.get("id"))
    auto = intervals_of(activity.get("id"))
    laps = splits or auto   # для таблицы и графиков в PDF: километры, а без них — интервалы
    profile = athlete_profile()
    payload = {
        "сегодня": date.today().isoformat(),
        "текущая_тренировка": slim_activity(activity),
        "раскладка_по_километрам": splits or "недоступна (нет посекундных данных)",
        "автоопределённые_интервалы_intervals_icu": auto,
        "пояснение_к_раскладке": ("раскладка_по_километрам — точные километры тренировки; "
                                  "последний отрезок может быть короче километра, его длина "
                                  "указана в скобках. Автоопределённые интервалы делит сам "
                                  "intervals.icu по изменению темпа/пульса, это НЕ километры: "
                                  "используй их только чтобы понять структуру (разминка, "
                                  "отрезки, заминка), и не называй их километрами."),
        "профиль_и_зоны_спортсмена": profile,
        "тренировки_за_14_дней_до_этой": [slim_activity(a) for a in hist],
        "восстановление_7_дней_до_тренировки": [slim_wellness(w) for w in wellness(d)],
        "единицы": UNITS,
    }
    if comment:
        payload["комментарий_спортсмена"] = comment
    text = claude(system_for(WORKOUT_PROMPT), with_goal(payload))
    return text, {"activity": activity, "laps": laps, "profile": profile}


def week_report(comment=""):
    """Возвращает (текст, данные для PDF)."""
    today = date.today()
    acts = with_data(activities_between(today - timedelta(days=14), today))
    profile = athlete_profile()
    wl = wellness(today)
    payload = {
        "сегодня": today.isoformat(),
        "тренировки_за_14_дней": [slim_activity(a) for a in acts],
        "профиль_и_зоны_спортсмена": profile,
        "восстановление_7_дней": [slim_wellness(w) for w in wl],
        "единицы": UNITS,
    }
    if comment:
        payload["комментарий_спортсмена"] = comment
    text = claude(system_for(WEEK_PROMPT), with_goal(payload), 4000)
    return text, {"acts": acts, "today": today, "profile": profile, "wellness": wl}


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
    return claude(system_for(PLAN_PROMPT), with_goal(payload), 3000)


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


# постоянные кнопки под полем ввода
KEYBOARD = {
    "keyboard": [[{"text": "🎯 Цель"}, {"text": "🔁 Повтор"}],
                 [{"text": "📅 План"}, {"text": "📊 Неделя"}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Напиши, как прошла тренировка…",
}

COMMANDS = [
    {"command": "goal", "description": "🎯 Цель-старт"},
    {"command": "plan", "description": "📅 План на ближайшие дни"},
    {"command": "week", "description": "📊 Итоги недели"},
    {"command": "again", "description": "🔁 Разобрать последнюю тренировку ещё раз"},
    {"command": "start", "description": "ℹ️ Справка"},
]


def send_text(text, chat_id=None, keyboard=False):
    chunks = split_message(to_plain(text))
    for i, chunk in enumerate(chunks):
        body = {"chat_id": chat_id or TELEGRAM_CHAT_ID, "text": chunk}
        if keyboard and i == len(chunks) - 1:   # кнопки цепляем к последнему сообщению
            body["reply_markup"] = KEYBOARD
        tg("sendMessage", json=body)


def send_document(path, filename, caption="", chat_id=None):
    with open(path, "rb") as f:
        tg("sendDocument", data={"chat_id": chat_id or TELEGRAM_CHAT_ID, "caption": caption[:1000]},
           files={"document": (filename, f, "application/pdf")})
