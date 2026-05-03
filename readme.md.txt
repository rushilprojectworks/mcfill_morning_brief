# McFill Morning Brief

Scheduled automation that runs every morning at 700 AM GST — no button, no human trigger.

Fetches today's finance and luxury news → summarises each story with Google Gemini AI → emails a structured digest to the McFill editorial team.

---

## What it does automatically

1. Pulls articles from 9 RSS feeds (Reuters, Gulf News, Yahoo Finance, Luxe Digital, etc.)
2. Filters duplicates and low-quality articles
3. Summarises each story in 2 editorial sentences using Google Gemini
4. Runs 5 QA checks on the assembled digest
5. Sends a formatted HTML email to the team
6. Logs every run to `bot_run.log` and `run_history.json`

---

## Setup (5 minutes)

1. Install dependencies
```bash
pip install -r requirements.txt
```

2. Get a Gemini API key (free)

Go to aistudio.google.com → sign in with your Google account → click Get API Key → Create API Key. It looks like
```
AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

3. Add your credentials to config.yaml
```yaml
gemini
  api_key AIzaSy_YOUR_KEY_HERE
  model gemini-1.5-flash

email
  sender_email your_gmail@gmail.com
  sender_app_password your_16char_app_password
  recipient_email team@mcfillmedia.com
```

For Gmail App Password myaccount.google.comapppasswords (requires 2FA enabled)

4. Test immediately
```bash
python bot.py --run-now
```

5. Start the daily scheduler
```bash
python bot.py --schedule
```

---

## File structure

```
mcfill_morning_brief
├── bot.py            # Main pipeline (fetch → summarise → QA → email)
├── config.yaml       # Gemini key, RSS feeds, schedule time, email settings
├── requirements.txt  # Python dependencies
├── bot_run.log       # Auto-generated detailed log of every run
└── run_history.json  # Auto-generated JSON record of last 30 runs
```

---

## Why Gemini

- Free tier — Google AI Studio gives generous free quota (60 requestsminute on Gemini 1.5 Flash)
- Fast — Gemini 1.5 Flash is optimised for high-volume, low-latency tasks like batch summarisation
- Simple setup — one API key from aistudio.google.com, no credit card needed to start

---

## QA checks (run before every send)

 Check  What it tests 
------
 Minimum article count  At least 4 articles fetched 
 Failed summary rate  Less than 50% of summaries failed 
 Finance coverage  At least 1 finance article present 
 Luxury coverage  At least 1 luxury article present 
 No duplicates  No duplicate titles in digest 

If any check fails → email is not sent + issue is logged to run_history.json.

---

## Changing the model

In config.yaml, you can swap the Gemini model

```yaml
gemini
  model gemini-1.5-flash    # fast, free, ideal for batch tasks
  model gemini-1.5-pro      # more powerful, higher quality summaries
  model gemini-2.0-flash    # latest, fastest
```

---

## Troubleshooting

InvalidArgument  API key error Check your Gemini API key in config.yaml. Make sure it starts with `AIzaSy` and is inside quotes.

Quota exceeded You've hit the free tier limit (60 reqmin). The bot auto-retries with backoff. Or switch to `gemini-1.5-pro` which has a different quota bucket.

SMTP auth error Use a Gmail App Password, not your main password. Create one at myaccount.google.comapppasswords after enabling 2FA.

No articles fetched Some RSS feeds may be temporarily down. Check bot_run.log — each feed logs individually.

---

## Before uploading to GitHub

Create a `.gitignore` file with
```
config.yaml
bot_run.log
run_history.json
```

Upload a `config.yaml.example` file instead (with placeholder values) so your API key never goes public.

---

McFill Morning Brief — Built by Rushil  McFill Media AI Apps & Automation portfolio