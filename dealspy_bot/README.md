# 🛍️ DealSpy Bot v2.0
### Telegram Deals & Coupon Bot for India

Auto-posts deals from Amazon, DesiDime, CouponDunia & CashKaro
to your Telegram channel — 3 times a day, with price tracking.

---

## ⚡ Quick Setup (5 minutes)

### Step 1 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 2 — Create your Telegram Bot
1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Follow prompts → copy the **BOT_TOKEN**

### Step 3 — Create your Telegram Channel
1. Telegram → New Channel → set it **Public**
2. Choose a username e.g. `@IndiaHotDeals2025`
3. Add your bot as **Admin** (Post Messages permission)
4. Forward any channel message to **@userinfobot** to get Channel ID
   (looks like `-1001234567890`)

### Step 4 — Amazon Associates (free, instant)
1. Go to **affiliate-program.amazon.in**
2. Sign in with Amazon account → Join free
3. Your **Partner Tag** is shown on dashboard (e.g. `mydeals-21`)

### Step 5 — Set environment variables

**On Replit** — add these in Secrets (lock icon):
```
BOT_TOKEN          = 7123456:AAFxxx...
ALLOWED_CHAT_ID    = 123456789          ← your personal chat ID
CHANNEL_ID         = -1001234567890     ← your channel ID
AMAZON_PARTNER_TAG = mydeals-21         ← from Associates dashboard
```

**Optional** (after Amazon approves PA API access):
```
AMAZON_ACCESS_KEY  = AKIAXXXXXXXX
AMAZON_SECRET_KEY  = xxxxxxxxxxxx
```

### Step 6 — Run
```bash
python main.py
```

Send `/start` to your bot in Telegram to get your Chat ID.

---

## 📋 All Commands

| Command | What it does |
|---|---|
| `/deals` | Latest deals from all 4 sources |
| `/hotdeals` | Amazon Movers & Shakers only |
| `/desidime` | DesiDime deals only |
| `/search laptop` | Search all sources for keyword |
| `/track URL 15000` | Track Amazon product price |
| `/tracking` | View your tracked products |
| `/untrack 2` | Stop tracking item 2 |
| `/watch iphone` | Alert when iPhone deals appear |
| `/watching` | Your keyword watchlist |
| `/unwatch iphone` | Remove keyword |
| `/status` | Bot status & API config |

---

## 🔄 Auto Schedule

| Time (IST) | Action |
|---|---|
| 9:00 AM | Deal digest → channel + your chat |
| 1:00 PM | Deal digest → channel + your chat |
| 6:00 PM | Deal digest → channel + your chat |
| Every 15 min | Keyword scan → private alerts |
| Every 30 min | Price drop check → private alert |

---

## 📦 Data Sources (all free)

| Source | Type | Auth needed |
|---|---|---|
| Amazon India RSS | Movers & Shakers feeds | None |
| DesiDime | RSS feed | None |
| CouponDunia | RSS feed | None |
| CashKaro | Public page | None |
| Amazon PA API | Price tracking | After 3 affiliate sales |

---

## 🚀 Deploy Free (Zero Downtime)

### Option A — Railway (recommended, never sleeps)
1. Push code to GitHub
2. railway.app → New Project → Deploy from GitHub
3. Add environment variables in Railway dashboard
4. Done — no UptimeRobot needed

### Option B — Replit + UptimeRobot
1. Upload files to Replit
2. Add Secrets (Step 5 above)
3. Click Run
4. Go to uptimerobot.com → Add Monitor
   - Type: HTTP(s)
   - URL: `https://your-replit-url.replit.app/ping`
   - Interval: 5 minutes

---

## 💡 Tips

- Bot works immediately without Amazon PA API — RSS feeds provide deals
- PA API unlocks accurate real-time price tracking (apply after 3 sales)
- Affiliate links earn commission when channel subscribers buy via your links
- Pin a message in your channel: "Search deals: @YourBotUsername"
- Use `/watch` for specific products you want — e.g. `/watch ps5`
