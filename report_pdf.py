"""PDF-отчёт о тренировке: шапка, ключевые цифры, графики, разбор тренера."""
import io
import os
import re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, KeepTogether)

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts") + os.sep


def _otf_to_ttf(src, dst):
    """reportlab понимает только TrueType: переводим CFF-контуры OTF в TrueType."""
    from fontTools.ttLib import TTFont as FT, newTable
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    f = FT(src)
    if "glyf" in f:
        f.save(dst)
        return
    gs, order, glyphs = f.getGlyphSet(), f.getGlyphOrder(), {}
    for g in order:
        pen = TTGlyphPen(gs)
        gs[g].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyphs[g] = pen.glyph()
    f["loca"] = newTable("loca")
    f["glyf"] = t = newTable("glyf")
    t.glyphOrder, t.glyphs = order, glyphs
    for tag in ("CFF ", "CFF2", "VORG", "DSIG"):
        if tag in f:
            del f[tag]
    m = f["maxp"]
    m.tableVersion = 0x00010000
    for a in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs",
              "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions",
              "maxComponentElements", "maxPoints", "maxContours", "maxCompositePoints",
              "maxCompositeContours", "maxComponentDepth"):
        setattr(m, a, 0)
    m.maxZones = 1
    f["head"].glyphDataFormat = 0
    f["post"].formatType = 3.0
    f.sfntVersion = "\x00\x01\x00\x00"
    f.save(dst)


def _custom_font(var_b64, var_file, name):
    """Шрифт из секрета (base64 от gzip-файла) или из локального файла. None, если нет."""
    import base64
    import gzip
    import tempfile
    try:
        if os.environ.get(var_b64):
            raw = base64.b64decode("".join(os.environ[var_b64].split()))
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        elif os.environ.get(var_file) and os.path.exists(os.environ[var_file]):
            with open(os.environ[var_file], "rb") as fh:
                raw = fh.read()
        else:
            return None
        d = tempfile.mkdtemp()
        src, dst = os.path.join(d, name + ".otf"), os.path.join(d, name + ".ttf")
        with open(src, "wb") as fh:
            fh.write(raw)
        _otf_to_ttf(src, dst)
        return dst
    except Exception as e:
        print(f"Шрифт {name} не подключён ({e}), использую стандартный")
        return None


_reg = _custom_font("LM_FONT_B64", "LM_FONT_FILE", "lm-regular")
_bold = _custom_font("LM_FONT_BOLD_B64", "LM_FONT_BOLD_FILE", "lm-bold")
if _reg:
    REG, BOLD = _reg, _bold or _reg   # нет жирного начертания — заголовки обычным
else:
    REG, BOLD = FONT_DIR + "DejaVuSans.ttf", FONT_DIR + "DejaVuSans-Bold.ttf"
pdfmetrics.registerFont(TTFont("Sans", REG))
pdfmetrics.registerFont(TTFont("Sans-Bold", BOLD))
from reportlab.lib.fonts import addMapping
addMapping("Sans", 0, 0, "Sans"); addMapping("Sans", 1, 0, "Sans-Bold")
addMapping("Sans", 0, 1, "Sans"); addMapping("Sans", 1, 1, "Sans-Bold")
for _f in {REG, BOLD}:
    try:
        font_manager.fontManager.addfont(_f)
    except Exception:
        pass
plt.rcParams["font.family"] = font_manager.FontProperties(fname=REG).get_name()

ACCENT = colors.HexColor("#E84C3D")
INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#6B7280")
PANEL = colors.HexColor("#F4F5F7")
PACE_C, HR_C = "#2563EB", "#E84C3D"
ZONE_C = ["#9CA3AF", "#60A5FA", "#34D399", "#FBBF24", "#F87171", "#DC2626", "#7F1D1D"]

st = {
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=20, leading=24, textColor=INK),
    "sub": ParagraphStyle("sub", fontName="Sans", fontSize=10, leading=14, textColor=MUTED),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12.5, leading=16, textColor=INK,
                         spaceBefore=12, spaceAfter=5, keepWithNext=1, borderPadding=(0, 0, 0, 7),
                         leftIndent=9),
    "p": ParagraphStyle("p", fontName="Sans", fontSize=10, leading=14.5, textColor=INK, spaceAfter=5),
    "li": ParagraphStyle("li", fontName="Sans", fontSize=10, leading=14.5, textColor=INK,
                         leftIndent=12, bulletIndent=2, spaceAfter=2),
    "kv": ParagraphStyle("kv", fontName="Sans-Bold", fontSize=15, leading=18, textColor=INK),
    "kl": ParagraphStyle("kl", fontName="Sans", fontSize=8.5, leading=11, textColor=MUTED),
    "note": ParagraphStyle("note", fontName="Sans", fontSize=8, leading=11, textColor=MUTED),
}


def fmt_pace(sec):
    return f"{int(sec // 60)}:{int(round(sec % 60)):02d}"


def chart_laps(laps):
    """Темп (столбики) и пульс (линия) по отрезкам; пустые ряды пропускаются."""
    x = list(range(1, len(laps) + 1))
    labels = [l["label"] for l in laps]
    has_pace = sum(1 for l in laps if l.get("pace_s")) >= 2
    has_hr = sum(1 for l in laps if l.get("hr")) >= 2
    rows = int(has_pace) + int(has_hr)
    if not rows:
        return None
    fig, axes = plt.subplots(rows, 1, figsize=(7.2, 1.7 * rows + 0.2), dpi=200, sharex=True,
                             gridspec_kw={"hspace": 0.35}, squeeze=False)
    axes = [a[0] for a in axes]
    k = 0
    small = len(laps) <= 16
    if has_pace:
        ax = axes[k]; k += 1
        pace = [l.get("pace_s") for l in laps]
        vals = [p for p in pace if p]
        lo, hi = min(vals), min(max(vals), min(vals) * 1.8)
        base = hi + 15
        ax.bar(x, [base - min(p, hi) if p else 0 for p in pace],
               bottom=[min(p, hi) if p else base for p in pace], color=PACE_C, width=0.62)
        ax.set_ylim(base, lo - 12)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda v, _: f"{int(v // 60)}:{int(v % 60):02d}"))
        if small:
            for xi, p in zip(x, pace):
                if p:
                    ax.text(xi, min(p, hi) - 1.5, fmt_pace(p), ha="center", va="bottom",
                            fontsize=6.5, color="#1F2328")
        ax.set_title("Темп по отрезкам, мин/км (выше — быстрее)", fontsize=9, loc="left",
                     color="#1F2328")
    if has_hr:
        ax2 = axes[k]
        pts = [(xi, l["hr"]) for xi, l in zip(x, laps) if l.get("hr")]
        ax2.plot([p[0] for p in pts], [p[1] for p in pts], color=HR_C, lw=2.2, marker="o", ms=4)
        if small:
            for xi, h in pts:
                ax2.text(xi, h + 1.2, str(round(h)), ha="center", va="bottom", fontsize=6.5,
                         color="#1F2328")
        hv = [p[1] for p in pts]
        ax2.set_ylim(min(hv) - 6, max(hv) + 9)
        ax2.set_title("Пульс по отрезкам, уд/мин", fontsize=9, loc="left", color="#1F2328")
    last = axes[-1]
    last.set_xticks(x)
    step = 1 if len(x) <= 16 else max(1, len(x) // 12)
    last.set_xticklabels([lb if (i % step == 0) else "" for i, lb in enumerate(labels)])
    for a in axes:
        a.tick_params(labelsize=6.8, length=0)
        a.grid(axis="y", color="#EEF0F3", lw=0.8)
        a.set_axisbelow(True)
        for sp in a.spines.values():
            sp.set_visible(False)
    fig.subplots_adjust(left=0.07, right=0.99, top=0.9, bottom=0.12 if rows == 1 else 0.08)
    return fig


def chart_zones(zones):
    if not zones or not any(z[1] for z in zones):
        return None
    names = [z[0] for z in zones][::-1]
    mins = [z[1] for z in zones][::-1]
    total = sum(mins) or 1
    fig, ax = plt.subplots(figsize=(7.2, 1.9), dpi=200)
    bars = ax.barh(names, mins, color=ZONE_C[:len(zones)][::-1], height=0.62)
    for b, m in zip(bars, mins):
        if True:
            ax.text(b.get_width() + 0.6, b.get_y() + b.get_height() / 2,
                    (f"{m:.0f} мин · {m / total * 100:.0f}%" if m else "—"), va="center", fontsize=7.5,
                    color="#374151")
    ax.set_xlim(0, max(mins) * 1.28)
    ax.set_xticks([])
    ax.tick_params(labelsize=7.5, length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Время в пульсовых зонах", fontsize=9.5, loc="left", color="#1F2328")
    fig.tight_layout()
    return fig


def fig_to_image(fig, width_mm):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * mm
    img.drawHeight = width_mm * mm * ratio
    return img


def kpi_table(kpis, width):
    cells = [[Paragraph(v, st["kv"]), ] for v, _ in kpis]
    row_v = [Paragraph(v, st["kv"]) for v, _ in kpis]
    row_l = [Paragraph(l, st["kl"]) for _, l in kpis]
    n = len(kpis) // 2
    data = [row_v[:n], row_l[:n], row_v[n:], row_l[n:]]
    t = Table(data, colWidths=[width / n] * n)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 9), ("TOPPADDING", (0, 2), (-1, 2), 9),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 9), ("BOTTOMPADDING", (0, 3), (-1, 3), 9),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("TOPPADDING", (0, 3), (-1, 3), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.6, colors.white),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.6, colors.white),
    ]))
    return t


class HeadingBar(Paragraph):
    """Заголовок раздела с красной полоской слева (вместо эмодзи)."""
    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, 1, 3, self.height - 2, stroke=0, fill=1)
        c.restoreState()
        super().draw()


def text_to_flowables(text):
    """Разбор тренера -> абзацы. Строка '## Заголовок' — раздел, '- ' — пункт списка."""
    out = []
    for block in text.strip().split("\n\n"):
        for line in block.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
            if line.startswith("## "):
                title = re.sub(r"^[^\wА-Яа-яЁё]+", "", esc[3:]).strip()
                out.append(HeadingBar(title, st["h2"]))
            elif line.startswith("- "):
                out.append(Paragraph(esc[2:], st["li"], bulletText="•"))
            else:
                out.append(Paragraph(esc, st["p"]))
    return out


def build_pdf(path, w, coach_text, demo=False):
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Разбор: {w['name']}", author="AI тренер")
    width = A4[0] - 32 * mm

    def decor(c, d):
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, A4[1] - 5 * mm, A4[0], 5 * mm, stroke=0, fill=1)
        c.setFont("Sans", 7.5)
        c.setFillColor(MUTED)
        c.drawString(16 * mm, 8 * mm, "AI тренер · разбор подготовлен Claude по данным Garmin / intervals.icu")
        c.drawRightString(A4[0] - 16 * mm, 8 * mm, f"стр. {d.page}")
        c.restoreState()

    story = [
        Paragraph("РАЗБОР ТРЕНИРОВКИ", ParagraphStyle("k", fontName="Sans-Bold", fontSize=8.5,
                                                      textColor=ACCENT, leading=12)),
        Paragraph(w["name"], st["h1"]),
        Paragraph(w["subtitle"], st["sub"]),
    ]
    if demo:
        story.append(Spacer(1, 3))
        story.append(Paragraph("Пример отчёта на демонстрационных данных.", st["note"]))
    story += [Spacer(1, 8), kpi_table(w["kpis"], width), Spacer(1, 8)]
    for fig in (chart_laps(w["laps"]) if w["laps"] else None, chart_zones(w["zones"])):
        if fig is not None:
            story += [fig_to_image(fig, width / mm), Spacer(1, 2)]

    if w["laps"]:
        head = ["Отрезок", "Дистанция", "Время", "Темп", "Пульс", "Каденс"]
        rows = [head] + [[l["label"], l.get("dist", "—"), l.get("time", "—"),
                          fmt_pace(l["pace_s"]) if l.get("pace_s") else "—",
                          str(round(l["hr"])) if l.get("hr") else "—",
                          str(round(l["cad"])) if l.get("cad") else "—"] for l in w["laps"]]
        tbl = Table(rows, colWidths=[width / 6] * 6, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Sans-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Sans", 8.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#D1D5DB")),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        title = Paragraph("Круги и отрезки", st["h2"])
        story += [KeepTogether([title, tbl])] if len(rows) <= 25 else [title, tbl]
    story += text_to_flowables(coach_text)
    doc.build(story, onFirstPage=decor, onLaterPages=decor)


# ---------------------------------------------------------------- данные intervals.icu -> отчёт
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
RUN_TYPES = {"Run", "VirtualRun", "TrailRun", "Treadmill"}


def _num(v, nd=0):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def cadence(v, sport):
    c = _num(v)
    if c and sport in RUN_TYPES and c < 120:  # часть устройств пишет шаги одной ноги
        c *= 2
    return c


def km_str(m):
    m = _num(m, 2)
    if not m:
        return "—"
    return f"{m / 1000:.2f} км".replace(".", ",") if m >= 1000 else f"{int(m)} м"


def time_str(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "—"
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pace_sec(speed):
    sp = _num(speed, 4)
    return 1000 / sp if sp and sp > 0.3 else None


def build_report_data(activity, laps, profile):
    a = activity
    sport = a.get("type", "")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(a.get("start_date_local"))[:19])
        when = f"{WEEKDAYS[dt.weekday()]}, {dt.day} {MONTHS[dt.month - 1]} {dt.year} · {dt:%H:%M}"
    except ValueError:
        when = str(a.get("start_date_local", ""))[:16]
    extra = []
    if _num(a.get("average_temp")) is not None:
        extra.append(f"{_num(a.get('average_temp')):+.0f} °C")
    if a.get("device_name"):
        extra.append(str(a["device_name"]))
    sub = " · ".join([when] + extra)

    p = pace_sec(a.get("average_speed"))
    cad = cadence(a.get("average_cadence"), sport)
    kpis = [
        (km_str(a.get("distance")), "Дистанция"),
        (time_str(a.get("moving_time")), "Время в движении"),
        (f"{fmt_pace(p)} /км" if p else "—", "Средний темп"),
        (str(int(_num(a["average_heartrate"]))) if a.get("average_heartrate") else "—",
         "Средний пульс, уд/мин"),
        (str(int(cad)) if cad else "—", "Каденс, шаг/мин"),
        (str(int(_num(a["icu_training_load"]))) if a.get("icu_training_load") is not None else "—",
         "Нагрузка"),
    ]

    rows = []
    for n, l in enumerate(laps, 1):
        lab = str(l.get("label") or n)
        rows.append({
            "label": lab if len(lab) <= 10 else str(n),
            "dist": km_str(l.get("distance")),
            "time": time_str(l.get("moving_time")),
            "pace_s": pace_sec(l.get("average_speed")),
            "hr": _num(l.get("average_heartrate")),
            "cad": cadence(l.get("average_cadence"), sport),
        })

    zones = []
    zt = a.get("icu_hr_zone_times") or []
    if zt:
        names = None
        for ss in profile.get("зоны_по_видам_спорта", []):
            if sport in (ss.get("types") or []) and ss.get("hr_zone_names"):
                names = ss["hr_zone_names"]
                break
        for i, sec in enumerate(zt):
            nm = f"Z{i + 1}" + (f" {names[i]}" if names and i < len(names) else "")
            zones.append((nm[:22], (_num(sec) or 0) / 60))

    return {"name": a.get("name") or "Тренировка", "subtitle": sub, "kpis": kpis,
            "laps": rows, "zones": zones}


def short_caption(activity, text):
    """Подпись в Telegram: главные цифры + первое впечатление тренера."""
    d = build_report_data(activity, [], {})
    k = {lab: val for val, lab in d["kpis"]}
    nums = " · ".join(v for v in (k["Дистанция"], k["Время в движении"], k["Средний темп"])
                      if v != "—")
    if k["Средний пульс, уд/мин"] != "—":
        nums += f" · пульс {k['Средний пульс, уд/мин']}"
    first = ""
    blocks = text.split("## ")
    if len(blocks) > 1:
        body = blocks[1].split("\n", 1)[1] if "\n" in blocks[1] else ""
        first = body.strip().replace("**", "")
    if len(first) > 600:
        first = first[:600].rsplit(" ", 1)[0] + "…"
    return f"🏃 {d['name']}\n{nums}\n\n{first}\n\nПолный разбор — в PDF 👇".strip()
