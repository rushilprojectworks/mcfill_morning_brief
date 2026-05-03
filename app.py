"""
McFill Morning Brief
=====================
Scheduled automation that runs every morning at 7:00 AM GST.
Fetches finance + luxury news -> summarises with Gemini -> emails digest to team.

Run options:
  python app.py --run-now     # run once immediately (for testing)
  python app.py --schedule    # start the daily 7am scheduler
"""

import time
import json
import logging
import argparse
import yaml
import feedparser
import smtplib
import schedule
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from google import genai

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "bot_run.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Load config ──────────────────────────────────────────────────────────────
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config.yaml — expected at: {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

# ─── Step 1: Fetch news from RSS feeds ───────────────────────────────────────
def fetch_articles(config: dict) -> list[dict]:
    feeds       = config["feeds"]
    max_per     = config["settings"]["max_articles_per_feed"]
    min_words   = config["settings"]["min_article_words"]
    articles    = []
    seen_titles = set()

    for feed_cfg in feeds:
        name = feed_cfg["name"]
        url  = feed_cfg["url"]
        log.info(f"Fetching: {name}")

        try:
            feed = feedparser.parse(url)

            # Skip only if bozo AND no entries — some feeds have minor XML
            # issues but still deliver valid entries (Arab News does this)
            if feed.bozo and not feed.entries:
                log.warning(f"  Skipping {name} — no entries: {feed.bozo_exception}")
                continue

            if not feed.entries:
                log.warning(f"  No entries for {name}.")
                continue

            count = 0
            for entry in feed.entries:
                if count >= max_per:
                    break

                title   = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                link    = entry.get("link", "")

                if not title or title.lower() in seen_titles:
                    continue
                seen_titles.add(title.lower())

                if len(summary.split()) < min_words:
                    continue

                articles.append({
                    "title":    title,
                    "summary":  summary[:1500],
                    "link":     link,
                    "source":   name,
                    "category": feed_cfg.get("category", "General")
                })
                count += 1

            log.info(f"  Got {count} articles from {name}")

        except Exception as e:
            log.error(f"  Failed to fetch {name}: {e}")

    log.info(f"Total articles fetched: {len(articles)}")
    return articles

# ─── Step 2: Summarise with Gemini ───────────────────────────────────────────
def summarise_article(article: dict, client: genai.Client, model_name: str,
                      delay_seconds: float, retries: int = 3) -> str:
    prompt = (
        "You are an editorial assistant for McFill Media Group, a UAE luxury media company.\n\n"
        f"Article title: {article['title']}\n"
        f"Source: {article['source']}\n\n"
        "Write exactly 2 sentences summarising this article.\n"
        "- Sentence 1: the key fact or development.\n"
        "- Sentence 2: why it matters for luxury/finance readers in the UAE.\n\n"
        "Return only the 2 sentences, nothing else."
    )

    for attempt in range(1, retries + 1):
        try:
            time.sleep(delay_seconds)   # pace every request to stay under RPM

            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response.text:
                return response.text.strip()
            return "[Summary unavailable — empty response]"

        except Exception as e:
            err_str = str(e).lower()

            # Wrong model name — hard stop, retrying won't help
            if "404" in err_str or "not found" in err_str:
                log.error(
                    f"  Model '{model_name}' not found (404). "
                    "Fix the model in config.yaml. "
                    "Valid: gemini-2.5-flash | gemini-2.0-flash | gemini-2.0-flash-lite"
                )
                return "[Summary unavailable — wrong model name in config.yaml]"

            # Rate limit — wait longer then retry
            if "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                wait = 60 * attempt   # 60s → 120s → 180s
                log.warning(f"  429 rate limit. Waiting {wait}s (attempt {attempt}/{retries})...")
                time.sleep(wait)
                continue

            # Any other transient error
            log.error(f"  Gemini error attempt {attempt}/{retries}: {e}")
            time.sleep(5)

    return "[Summary unavailable — Gemini API error after retries]"


def summarise_all(articles: list[dict], config: dict) -> list[dict]:
    api_key    = config["gemini"]["api_key"]
    model_name = config["gemini"].get("model", "gemini-2.5-flash")
    rpm        = config["gemini"].get("requests_per_minute", 14)
    delay      = 60.0 / rpm     # e.g. 14 RPM → 4.3s gap between calls

    client  = genai.Client(api_key=api_key)
    results = []

    log.info(
        f"Summarising {len(articles)} articles with {model_name} "
        f"({delay:.1f}s between requests to stay under {rpm} RPM limit)..."
    )

    for i, article in enumerate(articles, 1):
        log.info(f"  [{i}/{len(articles)}] {article['title'][:65]}...")
        summary = summarise_article(article, client, model_name, delay)

        # Wrong model — abort everything immediately
        if "wrong model name" in summary:
            log.error("  Aborting summarisation — fix model name in config.yaml.")
            results.append({**article, "ai_summary": summary})
            for a in articles[i:]:
                results.append({**a, "ai_summary": summary})
            return results

        results.append({**article, "ai_summary": summary})

    return results

# ─── Step 3: QA ──────────────────────────────────────────────────────────────
def run_digest_qa(articles: list[dict], config: dict) -> tuple[bool, list[str]]:
    issues   = []
    min_arts = config["settings"]["min_articles_to_send"]

    if len(articles) < min_arts:
        issues.append(f"Too few articles: {len(articles)} (need {min_arts})")

    good   = [a for a in articles if "[Summary unavailable" not in a["ai_summary"]]
    failed = len(articles) - len(good)
    if articles and failed > len(articles) * 0.5:
        issues.append(f"Too many failed summaries: {failed}/{len(articles)}")

    categories = {a["category"] for a in good}
    if "Finance" not in categories:
        issues.append("No Finance articles with valid summaries")
    if "Luxury" not in categories:
        issues.append("No Luxury articles with valid summaries")

    titles = [a["title"].lower() for a in articles]
    if len(titles) != len(set(titles)):
        issues.append("Duplicate article titles in digest")

    return len(issues) == 0, issues

# ─── Step 4: Build & send email ──────────────────────────────────────────────
def build_email_html(articles: list[dict], config: dict) -> str:
    brand  = config["email"]["brand_name"]
    accent = config["email"]["accent_color"]
    today  = datetime.now().strftime("%A, %d %B %Y")

    rows = ""
    for art in articles:
        if "[Summary unavailable" in art["ai_summary"]:
            continue
        rows += f"""
        <tr>
          <td style="padding:16px; border-bottom:1px solid #eee;">
            <strong style="font-size:15px; color:#222;">{art['title']}</strong><br>
            <small style="color:{accent}; font-weight:600;">{art['source']} &nbsp;|&nbsp; {art['category']}</small>
            <p style="font-size:14px; color:#444; margin:8px 0;">{art['ai_summary']}</p>
            <a href="{art['link']}" style="font-size:12px; color:{accent}; text-decoration:none;">Read full story →</a>
          </td>
        </tr>"""

    return f"""
<html>
<body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:24px; margin:0;">
  <table width="620" cellpadding="0" cellspacing="0"
         style="background:#fff; margin:auto; border-radius:6px; border:1px solid #ddd;">
    <tr style="background:{accent};">
      <td style="padding:24px; text-align:center; color:#fff;">
        <h1 style="margin:0; font-size:22px; letter-spacing:1px;">{brand} Morning Brief</h1>
        <p style="margin:6px 0 0; font-size:13px; opacity:0.85;">{today}</p>
      </td>
    </tr>
    {rows}
    <tr>
      <td style="padding:14px; text-align:center; font-size:11px; color:#aaa; border-top:1px solid #eee;">
        McFill Media Group · Automated Digest · Do not reply
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(html_body: str, config: dict) -> bool:
    cfg = config["email"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{cfg['brand_name']} Morning Brief — {datetime.now().strftime('%d %b %Y')}"
    msg["From"]    = cfg["sender_email"]
    msg["To"]      = cfg["recipient_email"]
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(cfg["sender_email"], cfg["sender_app_password"])
            server.sendmail(cfg["sender_email"], cfg["recipient_email"], msg.as_string())
        log.info(f"  Email sent → {cfg['recipient_email']}")
        return True
    except Exception as e:
        log.error(f"  Email failed: {e}")
        return False

# ─── Run history ─────────────────────────────────────────────────────────────
def save_run_history(articles: list, qa_passed: bool, qa_issues: list,
                     email_sent: bool, config: dict):
    path    = Path(__file__).parent / "run_history.json"
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text())
        except Exception:
            history = []

    failed = [a for a in articles if "[Summary unavailable" in a.get("ai_summary", "")]
    history.append({
        "timestamp":        datetime.now().isoformat(),
        "articles_fetched": len(articles),
        "failed_summaries": len(failed),
        "qa_passed":        qa_passed,
        "qa_issues":        qa_issues,
        "email_sent":       email_sent,
        "ai_model":         config["gemini"].get("model", "unknown"),
        "sources":          sorted({a["source"] for a in articles})
    })
    path.write_text(json.dumps(history[-30:], indent=2))
    log.info("  Run record saved to run_history.json")

# ─── Main pipeline ───────────────────────────────────────────────────────────
def run_pipeline():
    log.info("=" * 60)
    log.info("McFill Morning Brief — starting run")
    log.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    email_sent = False
    articles   = []
    qa_issues  = []
    qa_passed  = False

    try:
        config               = load_config()
        articles             = fetch_articles(config)

        if not articles:
            log.error("No articles fetched — skipping run.")
            save_run_history([], False, ["No articles fetched"], False, config)
            return

        articles             = summarise_all(articles, config)
        qa_passed, qa_issues = run_digest_qa(articles, config)

        if not qa_passed:
            log.warning(f"QA failed — email NOT sent. Issues: {qa_issues}")
        else:
            log.info("QA passed — building and sending email...")
            html       = build_email_html(articles, config)
            email_sent = send_email(html, config)

        save_run_history(articles, qa_passed, qa_issues, email_sent, config)
        log.info("Run complete.")

    except Exception as e:
        log.error(f"Pipeline crashed: {e}", exc_info=True)
        try:
            save_run_history(articles, False, [f"Crash: {e}"], False, load_config())
        except Exception:
            pass

# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="McFill Morning Brief")
    parser.add_argument("--run-now",  action="store_true", help="Run pipeline once immediately")
    parser.add_argument("--schedule", action="store_true", help="Start daily 7am scheduler")
    args = parser.parse_args()

    if args.run_now:
        log.info("Running pipeline immediately (--run-now)...")
        run_pipeline()
    elif args.schedule:
        config = load_config()
        t = config["settings"]["run_time"]
        schedule.every().day.at(t).do(run_pipeline)
        log.info(f"Scheduler active — will run daily at {t} GST. Press Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        parser.print_help()