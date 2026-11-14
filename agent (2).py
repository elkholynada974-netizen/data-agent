import os

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or None
RECIPIENT = os.environ.get("RECIPIENT_EMAIL") or GMAIL_ADDRESS

TOPIC = os.environ.get("TOPIC") or "الذكاء الاصطناعي"
LANG = os.environ.get("LANG_CODE") or "ar"
MAX_ARTICLES = 30
FETCH_FULL_TEXT = True
EXTRA_URLS = []


import re, time, html, urllib.parse
import requests, feedparser, pandas as pd
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DataReportAgent/1.0)",
           "Accept-Language": "ar,en;q=0.8"}

def http_get(url, retries=3, timeout=15):
    # بيحاول كذا مرة مع انتظار متزايد — حل لمشاكل الشبكة المؤقتة وال429
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404, 410):
                return None          # مفيش فايدة من إعادة المحاولة
        except requests.RequestException:
            pass
        time.sleep(2 ** i)
    return None

def extract_text(page_html):
    soup = BeautifulSoup(page_html, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    return " ".join(p for p in paras if len(p) > 40)

def collect_news(topic, lang, limit):
    loc = {"ar": ("ar", "EG", "EG:ar"), "en": ("en-US", "US", "US:en")}[lang]
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(topic) +
           f"&hl={loc[0]}&gl={loc[1]}&ceid={loc[2]}")
    feed = feedparser.parse(url)
    rows = []
    for e in feed.entries[:limit]:
        rows.append({
            "title": e.get("title", ""),
            "source": (e.get("source") or {}).get("title", "unknown"),
            "published": e.get("published", ""),
            "link": e.get("link", ""),
            "snippet": BeautifulSoup(e.get("summary", ""), "lxml").get_text(" ", strip=True),
        })
    return rows

def enrich_with_full_text(rows):
    for r in rows:
        r["text"] = r["snippet"]            # fallback لو السحب فشل
        resp = http_get(r["link"])
        if resp:
            txt = extract_text(resp.text)
            if len(txt) > 300:
                r["text"] = txt[:6000]
        time.sleep(0.5)                     # احترام الموقع (rate limit)
    return rows

def scrape_urls(urls):
    rows = []
    for u in urls:
        resp = http_get(u)
        if resp:
            soup = BeautifulSoup(resp.text, "lxml")
            rows.append({"title": (soup.title.string or u).strip() if soup.title else u,
                         "source": urllib.parse.urlparse(u).netloc, "published": "",
                         "link": u, "snippet": "", "text": extract_text(resp.text)[:6000]})
    return rows

def collect(topic):
    rows = collect_news(topic, LANG, MAX_ARTICLES)
    if not rows:
        raise RuntimeError("مفيش نتايج — جرّب كلمة بحث تانية أو لغة تانية")
    rows = enrich_with_full_text(rows) if FETCH_FULL_TEXT else [dict(r, text=r["snippet"]) for r in rows]
    rows += scrape_urls(EXTRA_URLS)
    return pd.DataFrame(rows)

from collections import Counter

STOP = set("""في من على إلى الى عن مع هذا هذه ذلك التي الذي أن إن كان كانت يكون هو هي ما لا لم لن قد كل بعد قبل بين حتى أو و ثم
عند أي كما التى الذى هناك وقد أكثر اكثر خلال عبر ضد لدى حول دون the and for with that this from are was were have has will
not but you your they their about into over more than its been can also after before new says said one""".split())

def process(df):
    df = df.copy()
    df["title"] = df["title"].str.replace(r"\s+-\s+[^-]+$", "", regex=True)   # شيل اسم المصدر من آخر العنوان
    df = df.drop_duplicates(subset="title").reset_index(drop=True)
    df["published"] = pd.to_datetime(df["published"], errors="coerce", utc=True)
    df["text"] = df["text"].fillna("").str.replace(r"\s+", " ", regex=True)
    df["words"] = df["text"].str.split().str.len()
    return df

def top_keywords(df, n=15):
    words = []
    for t in (df["title"] + " " + df["text"]):
        words += [w.lower() for w in re.findall(r"[\w\u0600-\u06FF]{3,}", t)
                  if w.lower() not in STOP and not w.isdigit()]
    return Counter(words).most_common(n)

def analyze(df):
    dated = df.dropna(subset=["published"])
    return {
        "total": len(df),
        "sources": df["source"].value_counts().head(10),
        "per_day": dated.groupby(dated["published"].dt.date).size(),
        "keywords": top_keywords(df),
        "avg_words": int(df["words"].mean()) if len(df) else 0,
    }

import io, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import arabic_reshaper

AR_CHARS = r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]"

def ar(s):
    # shape Arabic letters, then reverse direction manually (no bidi library); English words stay as they are
    s = str(s)
    if not re.search(AR_CHARS, s):
        return s
    words = arabic_reshaper.reshape(s).split(" ")
    words = [w[::-1] if re.search(AR_CHARS, w) else w for w in words]
    return " ".join(reversed(words))

def fig_bytes(fig):
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=130, bbox_inches="tight"); plt.close(fig)
    return buf.getvalue()

def make_charts(stats):
    charts = {}
    s = stats["sources"].iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 4)); ax.barh([ar(i) for i in s.index], s.values, color="#2563eb")
    ax.set_title(ar("أكثر المصادر نشرًا")); charts["sources"] = fig_bytes(fig)

    kw = stats["keywords"][::-1]
    fig, ax = plt.subplots(figsize=(7, 4)); ax.barh([ar(k) for k, _ in kw], [v for _, v in kw], color="#16a34a")
    ax.set_title(ar("أكثر الكلمات تكرارًا")); charts["keywords"] = fig_bytes(fig)

    if len(stats["per_day"]) > 1:
        fig, ax = plt.subplots(figsize=(7, 3.5)); stats["per_day"].plot(ax=ax, marker="o", color="#dc2626")
        ax.set_title(ar("عدد المقالات يوميًا")); ax.set_xlabel(""); charts["timeline"] = fig_bytes(fig)
    return charts

def ai_summary(df, topic):
    items = "\n".join(f"- {r.title} | {r.source} | {r.text[:300]}" for r in df.head(25).itertuples())
    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model="claude-sonnet-5-5", max_tokens=1200,
                messages=[{"role": "user", "content":
                    f"أنت محلل بيانات. دي أحدث الأخبار عن '{topic}':\n{items}\n\n"
                    "اكتب بنفس لغة الموضوع: ملخص تنفيذي من 3 جمل، ثم 5 نقاط رئيسية، ثم أهم الاتجاهات والمخاطر. "
                    "اعتمد فقط على البيانات المعطاة ولا تخترع معلومات."}])
            return msg.content[0].text
        except Exception as e:
            print("⚠️ فشل الملخص الذكي، هنستخدم ملخص بسيط:", e)
    return "أهم العناوين:\n" + "\n".join(f"• {t}" for t in df["title"].head(8))

import smtplib, datetime
from email.message import EmailMessage

def html_bars(title, pairs, color):
    # رسم شرايط بـ HTML و CSS (نص عادي، فالعربي بيتعرض مظبوط بدون أي معالجة)
    pairs = list(pairs)
    mx = max([v for _, v in pairs] or [1]) or 1
    rows = ""
    for label, v in pairs:
        pct = max(3, int(v / mx * 100))
        rows += ('<tr>'
                 f'<td style="padding:4px 0;font-size:14px;width:38%">{html.escape(str(label))}</td>'
                 f'<td style="padding:4px 8px"><div style="background:{color};width:{pct}%;height:18px;border-radius:3px"></div></td>'
                 f'<td style="padding:4px 0;font-size:14px;width:36px">{v}</td>'
                 '</tr>')
    return f'<h3>{html.escape(title)}</h3><table style="width:100%;border-collapse:collapse">{rows}</table>'

def build_charts_html(stats):
    parts = [
        html_bars("أكثر المصادر نشرًا", stats["sources"].items(), "#2563eb"),
        html_bars("أكثر الكلمات تكرارًا", stats["keywords"], "#16a34a"),
    ]
    if len(stats["per_day"]) > 1:
        parts.append(html_bars("عدد المقالات يوميًا", [(str(d), int(n)) for d, n in stats["per_day"].items()], "#dc2626"))
    return "".join(parts)

def build_html(topic, stats, summary, charts, df):
    direction = "rtl" if LANG == "ar" else "ltr"
    imgs = build_charts_html(stats)
    links = "".join(f'<li><a href="{html.escape(r.link)}">{html.escape(r.title)}</a> — {html.escape(str(r.source))}</li>'
                    for r in df.head(15).itertuples())
    return f"""<html><body dir="{direction}" style="font-family:Arial,sans-serif;max-width:720px;margin:auto;line-height:1.7">
<h1>📊 تقرير: {html.escape(topic)}</h1>
<p>{datetime.date.today()} — عدد المقالات: <b>{stats['total']}</b> — متوسط الكلمات: <b>{stats['avg_words']}</b></p>
<h2>الملخص</h2><p>{html.escape(summary).replace(chr(10), '<br>')}</p>
<h2>الرسومات</h2>{imgs}
<h2>أهم الروابط</h2><ul>{links}</ul>
<hr><small>تم إنشاؤه آليًا بواسطة الأيجنت.</small></body></html>"""

def send_email(subject, html_body, charts, df):
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD):
        raise RuntimeError("ضيف GMAIL_ADDRESS و GMAIL_APP_PASSWORD في Colab Secrets")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, GMAIL_ADDRESS, RECIPIENT
    msg.set_content("التقرير بصيغة HTML — افتحه من عميل بريد يدعم HTML.")
    msg.add_alternative(html_body, subtype="html")
    for cid, data in charts.items():
        msg.get_payload()[1].add_related(data, maintype="image", subtype="png", cid=f"<{cid}>")
    msg.add_attachment(df.drop(columns=["words"]).to_csv(index=False).encode("utf-8-sig"),
                       maintype="text", subtype="csv", filename="data.csv")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        print("✅ اتبعت التقرير بنجاح")
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("Gmail رفض الدخول: استخدم App Password (مش باسورد الحساب) وتأكد إن التحقق بخطوتين شغال")

def run_agent(topic=TOPIC):
    print("1/5 جمع البيانات..."); df = collect(topic)
    print("2/5 معالجة...");        df = process(df)
    print("3/5 تحليل...");         stats = analyze(df)
    print("4/5 رسومات وملخص..."); charts = {}; summary = ai_summary(df, topic)
    print("5/5 إرسال...")
    send_email(f"📊 تقرير {topic} — {datetime.date.today()}", build_html(topic, stats, summary, charts, df), charts, df)
    return df

if __name__ == "__main__":
    run_agent()
