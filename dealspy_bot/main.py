#!/usr/bin/env python3
"""
DealSpy Bot v3.0 — Telegram Deals & Coupon Bot for India
=========================================================
Deployment-ready for:
  ✅ Fly.io        — BEST free option, never sleeps, no card for basic use
  ✅ Render        — Free tier, sleeps (needs UptimeRobot ping)
  ✅ Replit        — Easy, needs UptimeRobot ping
  ✅ Railway       — $5 trial credit then $5/mo (not truly free long-term)

Built-in uptime:
  - /health and /ping endpoints for UptimeRobot / BetterUptime
  - Auto self-ping every 14 min (keeps Render/Replit awake without external tool)
  - Startup timestamp + last-ping logged

Sources:
  ✅ Amazon India RSS  (Movers & Shakers — no auth)
  ✅ DesiDime posts.atom  (fixed from broken deals.rss)
  ✅ CouponDunia blog RSS  (sale events & coupons)
  ✅ CashKaro offers page  (scraped — no RSS exists)
  ✅ FreeKaaMaal  (scraped — replaces dead Google Shopping RSS)

Replit / Render / Fly.io Secrets / Env Vars:
  BOT_TOKEN          — from @BotFather (required)
  ALLOWED_CHAT_ID    — your personal Telegram chat ID
  CHANNEL_ID         — your public channel e.g. -1001234567890
  AMAZON_PARTNER_TAG — e.g. mydeals-21  (free Associates signup)
  AMAZON_ACCESS_KEY  — optional, after PA API approved
  AMAZON_SECRET_KEY  — optional, after PA API approved
  APP_URL            — your deployed app URL for self-ping
                       e.g. https://mybot.fly.dev  or  https://mybot.onrender.com
"""

import os, json, logging, sys, hashlib, re, hmac, threading
from datetime import datetime
from typing import Dict, List, Optional
import pytz, requests, feedparser
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask, jsonify
from threading import Thread

# ═══════════════════════════════════════════════════════════
#  CONFIG — set via environment variables / secrets
# ═══════════════════════════════════════════════════════════

BOT_TOKEN          = os.getenv("BOT_TOKEN", "")
ALLOWED_CHAT_ID    = os.getenv("ALLOWED_CHAT_ID", "")
CHANNEL_ID         = os.getenv("CHANNEL_ID", "")
AMAZON_PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "")
AMAZON_ACCESS_KEY  = os.getenv("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET_KEY  = os.getenv("AMAZON_SECRET_KEY", "")
AMAZON_HOST        = "webservices.amazon.in"
AMAZON_REGION      = "eu-west-1"

# Self-ping URL — set this to YOUR deployed app URL
# e.g. https://myapp.onrender.com  or  https://myapp.fly.dev
APP_URL = os.getenv("APP_URL", "")

IST_TZ = pytz.timezone("Asia/Kolkata")

STORAGE_FILE       = "deals_storage.json"
MAX_DEALS_SHOWN    = 8
PRICE_CHECK_MINS   = 30
KEYWORD_CHECK_MINS = 15
DEFAULT_POST_FREQ  = 3    # hours between auto channel posts
MIN_FREQ           = 1
MAX_FREQ           = 24
SELF_PING_MINS     = 14   # keep Render/Replit awake (< 15 min threshold)

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Track uptime
START_TIME = datetime.now(IST_TZ)
LAST_PING  = {"time": None, "count": 0}

# ═══════════════════════════════════════════════════════════
#  STORAGE
# ═══════════════════════════════════════════════════════════

class Storage:
    def __init__(self):
        self.file = STORAGE_FILE
        self.data: Dict = {
            "tracked":         {},
            "seen_deals":      [],
            "watchlists":      {},
            "post_freq_hours": DEFAULT_POST_FREQ,
        }
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.file):
                loaded = json.load(open(self.file))
                self.data = loaded
                for k, d in [("tracked",{}),("seen_deals",[]),
                              ("watchlists",{}),("post_freq_hours",DEFAULT_POST_FREQ)]:
                    if k not in self.data:
                        self.data[k] = d
        except Exception as e:
            logger.error(f"Storage load: {e}")

    def _save(self):
        try:
            json.dump(self.data, open(self.file, "w"), indent=2)
        except Exception as e:
            logger.error(f"Storage save: {e}")

    def get_freq(self) -> int:
        return int(self.data.get("post_freq_hours", DEFAULT_POST_FREQ))

    def set_freq(self, h: int):
        self.data["post_freq_hours"] = h; self._save()

    def add_tracked(self, cid, item) -> bool:
        if cid not in self.data["tracked"]: self.data["tracked"][cid] = []
        if item["url"] not in [x["url"] for x in self.data["tracked"][cid]]:
            self.data["tracked"][cid].append(item); self._save(); return True
        return False

    def get_tracked(self, cid) -> List[dict]:
        return self.data["tracked"].get(cid, [])

    def remove_tracked(self, cid, idx) -> bool:
        items = self.data["tracked"].get(cid, [])
        if 0 <= idx < len(items):
            items.pop(idx); self.data["tracked"][cid] = items; self._save(); return True
        return False

    def all_tracked(self) -> Dict: return self.data["tracked"]

    def is_seen(self, h) -> bool: return h in self.data["seen_deals"]

    def mark_seen(self, h):
        self.data["seen_deals"].append(h)
        self.data["seen_deals"] = self.data["seen_deals"][-1000:]
        self._save()

    def add_keyword(self, cid, kw) -> bool:
        if cid not in self.data["watchlists"]: self.data["watchlists"][cid] = []
        kw = kw.lower().strip()
        if kw not in self.data["watchlists"][cid]:
            self.data["watchlists"][cid].append(kw); self._save(); return True
        return False

    def get_keywords(self, cid) -> List[str]:
        return self.data["watchlists"].get(cid, [])

    def remove_keyword(self, cid, kw) -> bool:
        kws = self.data["watchlists"].get(cid, [])
        if kw.lower() in kws:
            kws.remove(kw.lower()); self.data["watchlists"][cid] = kws; self._save(); return True
        return False

    def all_watchlists(self) -> Dict: return self.data["watchlists"]

# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def make_affiliate_link(url: str) -> str:
    if not AMAZON_PARTNER_TAG: return url
    if "amazon.in" not in url and "amzn" not in url: return url
    url = re.sub(r'[?&]tag=[^&]+', '', url)
    url = re.sub(r'[?&]linkCode=[^&]+', '', url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={AMAZON_PARTNER_TAG}"

def extract_asin(url: str) -> Optional[str]:
    m = re.search(r'/dp/([A-Z0-9]{10})', url)
    if m: return m.group(1)
    m = re.search(r'/gp/product/([A-Z0-9]{10})', url)
    return m.group(1) if m else None

def deal_hash(title: str, url: str) -> str:
    return hashlib.md5(f"{title[:50]}{url[:80]}".encode()).hexdigest()[:12]

def auth(cid: str) -> bool:
    return not ALLOWED_CHAT_ID or cid == ALLOWED_CHAT_ID

# ═══════════════════════════════════════════════════════════
#  AMAZON PA API  (optional)
# ═══════════════════════════════════════════════════════════

class AmazonAPI:
    @staticmethod
    def is_configured() -> bool:
        return bool(AMAZON_ACCESS_KEY and AMAZON_SECRET_KEY and AMAZON_PARTNER_TAG)

    @staticmethod
    def get_item_price(asin: str) -> Optional[Dict]:
        if not AmazonAPI.is_configured(): return None
        try:
            import json as _j
            payload = _j.dumps({
                "ItemIds":[asin],"Resources":["Offers.Listings.Price","ItemInfo.Title"],
                "PartnerTag":AMAZON_PARTNER_TAG,"PartnerType":"Associates",
                "Marketplace":"www.amazon.in","Operation":"GetItems",
            })
            now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"); date = now[:8]
            def _sign(k,m): return hmac.new(k,m.encode(),hashlib.sha256).digest()
            tgt = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"
            hdrs = {"content-encoding":"amz-1.0","content-type":"application/json; charset=utf-8",
                    "host":AMAZON_HOST,"x-amz-date":now,"x-amz-target":tgt}
            sh = "content-encoding;content-type;host;x-amz-date;x-amz-target"
            bh = hashlib.sha256(payload.encode()).hexdigest()
            canon = "\n".join(["POST","/paapi5/getitems","",
                "content-encoding:amz-1.0","content-type:application/json; charset=utf-8",
                f"host:{AMAZON_HOST}",f"x-amz-date:{now}",f"x-amz-target:{tgt}","",sh,bh])
            cs = f"{date}/{AMAZON_REGION}/ProductAdvertisingAPI/aws4_request"
            s2s = "\n".join(["AWS4-HMAC-SHA256",now,cs,hashlib.sha256(canon.encode()).hexdigest()])
            sk = _sign(_sign(_sign(_sign(f"AWS4{AMAZON_SECRET_KEY}".encode(),date),AMAZON_REGION),
                             "ProductAdvertisingAPI"),"aws4_request")
            sig = hmac.new(sk,s2s.encode(),hashlib.sha256).hexdigest()
            hdrs["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={AMAZON_ACCESS_KEY}/{cs}, "
                                     f"SignedHeaders={sh}, Signature={sig}")
            r = requests.post(f"https://{AMAZON_HOST}/paapi5/getitems",
                              headers=hdrs,data=payload,timeout=12)
            if r.status_code == 200:
                items = r.json().get("ItemsResult",{}).get("Items",[])
                if items:
                    i = items[0]; title = i.get("ItemInfo",{}).get("Title",{}).get("DisplayValue","")
                    offers = i.get("Offers",{}).get("Listings",[])
                    if offers:
                        price = offers[0].get("Price",{}).get("Amount")
                        return {"asin":asin,"title":title,"price":price,
                                "url":f"https://www.amazon.in/dp/{asin}"}
        except Exception as e:
            logger.error(f"PA API {asin}: {e}")
        return None

# ═══════════════════════════════════════════════════════════
#  REQUEST HEADERS
# ═══════════════════════════════════════════════════════════

_H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ═══════════════════════════════════════════════════════════
#  DEAL FETCHERS
# ═══════════════════════════════════════════════════════════

def fetch_amazon_rss(limit: int = 15) -> List[Dict]:
    """Amazon India Movers & Shakers RSS — free, no auth, always online."""
    deals = []
    feeds = [
        ("electronics","🔌"), ("computers","💻"), ("kitchen","🍳"),
        ("apparel","👕"),     ("sports","🏃"),    ("toys","🧸"), ("home","🏠"),
    ]
    for cat, emoji in feeds:
        try:
            feed = feedparser.parse(f"https://www.amazon.in/gp/rss/movers-and-shakers/{cat}/")
            for e in feed.entries[:3]:
                title = e.get("title","").strip(); link = e.get("link","")
                desc  = e.get("summary","")
                if not title or not link: continue
                pm    = re.search(r'₹\s*([\d,]+)', desc + title)
                price = int(pm.group(1).replace(",","")) if pm else None
                deals.append({"source":f"Amazon ({cat.title()})","title":title,
                              "url":make_affiliate_link(link),"asin":extract_asin(link),
                              "price":price,"desc":BeautifulSoup(desc,"html.parser").get_text()[:160],
                              "emoji":emoji})
                if len(deals) >= limit: return deals
        except Exception as ex:
            logger.debug(f"Amazon RSS [{cat}]: {ex}")
    return deals


def fetch_desidime(limit: int = 15) -> List[Dict]:
    """
    DesiDime via posts.atom (Atom feed — WORKING as of 2026).
    Note: deals.rss is 404 — DO NOT USE.
    """
    deals = []
    try:
        feed = feedparser.parse("https://www.desidime.com/posts.atom")
        if not feed.entries:
            logger.warning("DesiDime Atom: no entries returned"); return deals
        for e in feed.entries[:limit]:
            title = e.get("title","").strip()
            url   = e.get("link","") or e.get("id","")
            desc  = ""
            if hasattr(e,"summary"):       desc = e.summary
            elif hasattr(e,"content") and e.content: desc = e.content[0].get("value","")
            desc_text = BeautifulSoup(desc,"html.parser").get_text()[:200]
            pm    = re.search(r'₹\s*([\d,]+)', desc_text + title)
            price = int(pm.group(1).replace(",","")) if pm else None
            aff   = url
            am    = re.search(r'https?://(?:www\.)?amazon\.in/\S+', desc)
            if am: aff = make_affiliate_link(am.group(0))
            deals.append({"source":"DesiDime","title":title,"url":aff or url,
                          "price":price,"desc":desc_text,"emoji":"🔥"})
    except Exception as ex:
        logger.error(f"DesiDime: {ex}")
    return deals


def fetch_coupondunia(limit: int = 8) -> List[Dict]:
    """CouponDunia blog RSS — verified working 2026. Good for sale event alerts."""
    deals = []
    try:
        feed = feedparser.parse("https://www.coupondunia.in/blog/feed/")
        for e in feed.entries[:limit]:
            title = e.get("title","").strip(); url = e.get("link","")
            desc  = e.get("summary","")
            if not title: continue
            deals.append({"source":"CouponDunia","title":title,"url":url,"price":None,
                          "desc":BeautifulSoup(desc,"html.parser").get_text()[:160],"emoji":"🏷️"})
    except Exception as ex:
        logger.error(f"CouponDunia: {ex}")
    return deals


def fetch_cashkaro(limit: int = 8) -> List[Dict]:
    """CashKaro public offers page — scraped (no RSS exists). Legal public page."""
    deals = []
    try:
        r = requests.get("https://cashkaro.com/offers", headers=_H, timeout=15)
        if r.status_code != 200:
            logger.warning(f"CashKaro: {r.status_code}"); return deals
        soup  = BeautifulSoup(r.text, "html.parser")
        cards = (soup.find_all("div", class_=re.compile(r"offer.card|deal.card", re.I))
                 or soup.find_all("li", class_=re.compile(r"offer|deal", re.I))
                 or soup.find_all("div", class_=re.compile(r"card", re.I)))
        for card in cards[:limit*2]:
            t = (card.find(["h2","h3","h4","span"],
                           class_=re.compile(r"title|name|heading|offer.title",re.I))
                 or card.find("a"))
            l = card.find("a", href=True)
            if not t or not l: continue
            title = t.get_text(strip=True)
            if len(title) < 5: continue
            href = l["href"]
            if href.startswith("/"): href = "https://cashkaro.com" + href
            deals.append({"source":"CashKaro","title":title,"url":href,"price":None,
                          "desc":"Extra cashback via CashKaro","emoji":"💰"})
            if len(deals) >= limit: break
    except Exception as ex:
        logger.error(f"CashKaro: {ex}")
    return deals


def fetch_freekaamaal(limit: int = 8) -> List[Dict]:
    """
    FreeKaaMaal — popular Indian freebie/deal site.
    Replaces dead Google Shopping RSS.
    """
    deals = []
    try:
        r = requests.get("https://www.freekaamaal.com/", headers=_H, timeout=15)
        if r.status_code != 200:
            logger.warning(f"FreeKaaMaal: {r.status_code}"); return deals
        soup  = BeautifulSoup(r.text, "html.parser")
        posts = (soup.find_all("article")
                 or soup.find_all("div", class_=re.compile(r"post|deal|item",re.I)))
        for p in posts[:limit*2]:
            te = p.find(["h2","h3","h4","a"])
            le = p.find("a", href=True)
            if not te or not le: continue
            title = te.get_text(strip=True)
            if len(title) < 8: continue
            url = le["href"]
            if not url.startswith("http"): url = "https://www.freekaamaal.com" + url
            pm    = re.search(r'₹\s*([\d,]+)', p.get_text())
            price = int(pm.group(1).replace(",","")) if pm else None
            deals.append({"source":"FreeKaaMaal","title":title,"url":url,
                          "price":price,"desc":"","emoji":"🎁"})
            if len(deals) >= limit: break
    except Exception as ex:
        logger.error(f"FreeKaaMaal: {ex}")
    return deals


def fetch_all_deals(keyword: Optional[str] = None) -> List[Dict]:
    all_deals: List[Dict] = []
    all_deals += fetch_amazon_rss(10)
    all_deals += fetch_desidime(10)
    all_deals += fetch_coupondunia(5)
    all_deals += fetch_cashkaro(5)
    all_deals += fetch_freekaamaal(5)
    if keyword:
        kw = keyword.lower()
        all_deals = [d for d in all_deals
                     if kw in d["title"].lower() or kw in d.get("desc","").lower()]
    seen, unique = set(), []
    for d in all_deals:
        k = d["title"][:40].lower().strip()
        if k not in seen and len(k) > 3:
            seen.add(k); unique.append(d)
    return unique

# ═══════════════════════════════════════════════════════════
#  PRICE SCRAPER
# ═══════════════════════════════════════════════════════════

def scrape_amazon_price(url: str) -> Optional[float]:
    try:
        r = requests.get(url, headers=_H, timeout=12)
        if r.status_code != 200: return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag, attrs in [("span",{"id":"priceblock_ourprice"}),
                           ("span",{"id":"priceblock_dealprice"}),
                           ("span",{"class":"a-price-whole"}),
                           ("span",{"id":"price_inside_buybox"})]:
            el = soup.find(tag, attrs)
            if el:
                text = el.get_text(strip=True).replace(",","").replace("₹","").strip()
                m = re.search(r'[\d.]+', text)
                if m: return float(m.group())
    except Exception as e: logger.debug(f"Scrape price {url}: {e}")
    return None

def scrape_product_title(url: str) -> str:
    try:
        soup = BeautifulSoup(requests.get(url,headers=_H,timeout=10).text,"html.parser")
        el = soup.find("span",{"id":"productTitle"})
        if el: return el.get_text(strip=True)[:100]
    except Exception: pass
    return url[:60] + "..."

# ═══════════════════════════════════════════════════════════
#  FORMATTERS
# ═══════════════════════════════════════════════════════════

def format_deal(deal: Dict, idx: Optional[int] = None) -> str:
    num      = f"{idx}. " if idx else ""
    url      = deal.get("url","")
    aff      = make_affiliate_link(url) if "amazon" in url.lower() else url
    p_str    = f"\n   💰 ₹{deal['price']:,}" if deal.get("price") else ""
    desc_raw = deal.get("desc","")
    d_str    = f"\n   <i>{desc_raw[:120]}</i>" if desc_raw else ""
    return (f"{num}{deal['emoji']} <b>{deal['title'][:80]}</b>\n"
            f"   📌 {deal.get('source','')}{p_str}{d_str}\n"
            f"   🔗 <a href='{aff}'>View Deal</a>")

def build_deals_message(deals: List[Dict], title: str) -> str:
    now    = datetime.now(IST_TZ).strftime("%d %b %Y  %I:%M %p IST")
    header = f"<b>{title}</b>\n🕐 {now}\n\n"
    lines  = [format_deal(d, i) for i, d in enumerate(deals[:MAX_DEALS_SHOWN], 1)]
    text   = header + "\n\n".join(lines)
    if len(text) > 4000: text = text[:3950] + "\n\n<i>…use /deals for more</i>"
    return text

# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid     = str(update.effective_chat.id)
    storage: Storage = ctx.bot_data["storage"]
    freq    = storage.get_freq()
    pa_stat = "✅ Connected" if AmazonAPI.is_configured() else "⚠️ Optional"
    ch_stat = f"✅ <code>{CHANNEL_ID}</code>" if CHANNEL_ID else "⚠️ Not set"
    await update.message.reply_text(
        f"🛍️ <b>DealSpy Bot v3.0</b>\n\n"
        f"Indian deals from Amazon, DesiDime, CouponDunia, CashKaro & FreeKaaMaal.\n"
        f"Auto-posts to channel every <b>{freq}h</b>.\n\n"
        f"<b>📋 Commands:</b>\n"
        f"/deals — All sources\n"
        f"/hotdeals — Amazon Movers & Shakers\n"
        f"/desidime — DesiDime community deals\n"
        f"/search KEYWORD — Search deals\n"
        f"/postdeals — Manually push to channel now\n"
        f"/setfreq N — Auto-post every N hours (1–24)\n"
        f"/track URL PRICE — Track Amazon price drop\n"
        f"/tracking — View tracked products\n"
        f"/untrack N — Remove item N\n"
        f"/watch KEYWORD — Alert on keyword match\n"
        f"/watching — Your keyword list\n"
        f"/unwatch KEYWORD — Remove keyword\n"
        f"/uptime — Bot uptime & health\n"
        f"/status — Full status\n\n"
        f"<b>⚙️ Status:</b>\n"
        f"📱 Chat ID: <code>{cid}</code>\n"
        f"📢 Channel: {ch_stat}\n"
        f"🔑 PA API: {pa_stat}\n"
        f"⏰ Auto posts: every {freq}h",
        parse_mode="HTML", disable_web_page_preview=True
    )

async def cmd_deals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg = await update.message.reply_text("⏳ Fetching from all sources…")
    deals = fetch_all_deals()
    if not deals: await msg.edit_text("😕 No deals right now. Try again shortly."); return
    await msg.edit_text(build_deals_message(deals,"🔥 Hot Deals — All Sources"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_hotdeals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg = await update.message.reply_text("⏳ Fetching Amazon Movers & Shakers…")
    deals = fetch_amazon_rss(15)
    if not deals: await msg.edit_text("😕 Amazon RSS unavailable."); return
    await msg.edit_text(build_deals_message(deals,"🛒 Amazon Movers & Shakers"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_desidime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg = await update.message.reply_text("⏳ Fetching DesiDime deals…")
    deals = fetch_desidime(15)
    if not deals: await msg.edit_text("😕 DesiDime unavailable right now."); return
    await msg.edit_text(build_deals_message(deals,"🔥 DesiDime Community Deals"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /search KEYWORD"); return
    kw  = " ".join(ctx.args)
    msg = await update.message.reply_text(f"🔍 Searching '{kw}'…")
    deals = fetch_all_deals(keyword=kw)
    if not deals:
        await msg.edit_text(f"😕 No deals for '<b>{kw}</b>'. Try broader term.",parse_mode="HTML"); return
    await msg.edit_text(build_deals_message(deals,f"🔍 Deals: '{kw}'"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_postdeals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg = await update.message.reply_text("⏳ Posting deals to channel…")
    await _do_post_deals(ctx.application, force=True)
    ch = f"<code>{CHANNEL_ID}</code>" if CHANNEL_ID else "your chat"
    await msg.edit_text(f"✅ Posted to {ch}!\n<i>Use /setfreq to control frequency.</i>",
                        parse_mode="HTML")

async def cmd_setfreq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage = ctx.bot_data["storage"]
    if not ctx.args:
        freq = storage.get_freq()
        await update.message.reply_text(
            f"⏰ Current frequency: every <b>{freq}h</b> (~{round(24/freq,1)}/day)\n\n"
            f"Usage: /setfreq N  (1–24 hours)\n\n"
            f"Examples:\n"
            f"• /setfreq 1  → every hour (24/day)\n"
            f"• /setfreq 2  → every 2h (12/day)\n"
            f"• /setfreq 3  → every 3h (8/day) ← default\n"
            f"• /setfreq 6  → every 6h (4/day)\n"
            f"• /setfreq 12 → twice a day\n"
            f"• /setfreq 24 → once a day",
            parse_mode="HTML"); return
    try: n = int(ctx.args[0])
    except: await update.message.reply_text("❌ Enter a number. E.g. /setfreq 2"); return
    if not (MIN_FREQ <= n <= MAX_FREQ):
        await update.message.reply_text(f"❌ Must be {MIN_FREQ}–{MAX_FREQ}."); return
    storage.set_freq(n)
    try:
        scheduler: AsyncIOScheduler = ctx.bot_data["scheduler"]
        scheduler.reschedule_job("auto_deals", trigger="interval", hours=n)
        logger.info(f"Auto-deals rescheduled → every {n}h")
    except Exception as e: logger.error(f"Reschedule: {e}")
    await update.message.reply_text(
        f"✅ Frequency set: every <b>{n}h</b> (~{round(24/n,1)}/day)\n"
        f"<i>Takes effect immediately.</i>", parse_mode="HTML")

async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /track AMAZON_URL TARGET_PRICE\n"
            "Example: /track https://www.amazon.in/dp/B09XYZ 15000"); return
    url = ctx.args[0]
    try: target = float(ctx.args[1])
    except: await update.message.reply_text("❌ Price must be a number."); return
    if "amazon.in" not in url and "amzn" not in url:
        await update.message.reply_text("❌ Only Amazon India URLs."); return
    msg   = await update.message.reply_text("⏳ Fetching product…")
    asin  = extract_asin(url); price = None; title = None
    if asin and AmazonAPI.is_configured():
        d = AmazonAPI.get_item_price(asin)
        if d: price = d["price"]; title = d["title"]
    if price is None: price = scrape_amazon_price(url)
    if title is None: title = scrape_product_title(url)
    storage: Storage = ctx.bot_data["storage"]
    storage.add_tracked(cid, {"url":make_affiliate_link(url),"title":title,
                               "target_price":target,"current_price":price or 0,
                               "asin":asin,"added_at":datetime.now(IST_TZ).isoformat()})
    ps = f"₹{price:,.0f}" if price else "Could not fetch"
    await msg.edit_text(f"✅ <b>Tracking!</b>\n📦 {title[:80]}\n"
                        f"🎯 Alert ≤ ₹{target:,.0f}\n💰 Now: {ps}\n"
                        f"<i>Checked every {PRICE_CHECK_MINS}min</i>", parse_mode="HTML")

async def cmd_tracking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage = ctx.bot_data["storage"]
    items = storage.get_tracked(cid)
    if not items: await update.message.reply_text("📭 Nothing tracked. /track URL PRICE"); return
    lines = [f"<b>📦 Tracking ({len(items)})</b>\n"]
    for i, it in enumerate(items,1):
        cur = f"₹{it['current_price']:,.0f}" if it.get("current_price") else "?"
        lines.append(f"{i}. <b>{it['title'][:60]}</b>\n"
                     f"   🎯 ₹{it['target_price']:,.0f}  |  Now: {cur}\n"
                     f"   🔗 <a href='{it['url']}'>View</a>\n")
    lines.append("<i>/untrack N — remove</i>")
    await update.message.reply_text("\n".join(lines),parse_mode="HTML",disable_web_page_preview=True)

async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /untrack N"); return
    try: idx = int(ctx.args[0]) - 1
    except: await update.message.reply_text("❌ Number required."); return
    storage: Storage = ctx.bot_data["storage"]
    if storage.remove_tracked(cid,idx): await update.message.reply_text(f"✅ Removed item {idx+1}.")
    else: await update.message.reply_text("❌ Not found. Use /tracking.")

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args:
        await update.message.reply_text("Usage: /watch KEYWORD\nExample: /watch iphone"); return
    kw = " ".join(ctx.args).lower().strip()
    storage: Storage = ctx.bot_data["storage"]
    if storage.add_keyword(cid,kw):
        await update.message.reply_text(f"✅ Watching: <b>{kw}</b>",parse_mode="HTML")
    else: await update.message.reply_text(f"ℹ️ Already watching '<b>{kw}</b>'.",parse_mode="HTML")

async def cmd_watching(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage = ctx.bot_data["storage"]
    kws = storage.get_keywords(cid)
    if not kws: await update.message.reply_text("📭 None. /watch KEYWORD to add."); return
    lines = ["<b>👁️ Watchlist</b>\n"] + [f"• {k}" for k in kws]
    lines.append("\n<i>/unwatch KEYWORD</i>")
    await update.message.reply_text("\n".join(lines),parse_mode="HTML")

async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /unwatch KEYWORD"); return
    kw = " ".join(ctx.args).lower().strip()
    storage: Storage = ctx.bot_data["storage"]
    if storage.remove_keyword(cid,kw):
        await update.message.reply_text(f"✅ Removed: <b>{kw}</b>",parse_mode="HTML")
    else: await update.message.reply_text(f"❌ Not watching '<b>{kw}</b>'.",parse_mode="HTML")

async def cmd_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now     = datetime.now(IST_TZ)
    delta   = now - START_TIME
    hours   = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    lp      = LAST_PING["time"]
    lp_str  = lp.strftime("%I:%M %p IST") if lp else "Not pinged yet"
    await update.message.reply_text(
        f"<b>📡 Bot Uptime</b>\n\n"
        f"✅ Running for: <b>{hours}h {minutes}m</b>\n"
        f"🕐 Started: {START_TIME.strftime('%d %b %Y  %I:%M %p IST')}\n"
        f"🔔 Last ping: {lp_str}  (×{LAST_PING['count']})\n\n"
        f"🌐 App URL: {APP_URL or '⚠️ APP_URL not set'}\n"
        f"🔁 Self-ping: every {SELF_PING_MINS}min",
        parse_mode="HTML"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    storage: Storage = ctx.bot_data["storage"]
    freq    = storage.get_freq()
    await update.message.reply_text(
        f"<b>⚙️ DealSpy v3.0 Status</b>\n\n"
        f"🔑 PA API:    {'✅' if AmazonAPI.is_configured() else '⚠️ Not set'}\n"
        f"🏷️ Affiliate: {'✅ '+AMAZON_PARTNER_TAG if AMAZON_PARTNER_TAG else '⚠️ Not set'}\n"
        f"📢 Channel:  {CHANNEL_ID or '⚠️ Not set'}\n"
        f"⏰ Frequency: every {freq}h\n"
        f"📦 Tracked:  {sum(len(v) for v in storage.all_tracked().values())}\n"
        f"👁️ Keywords: {sum(len(v) for v in storage.all_watchlists().values())}\n\n"
        f"<b>📡 Sources:</b>\n"
        f"✅ Amazon RSS\n"
        f"✅ DesiDime posts.atom\n"
        f"✅ CouponDunia RSS\n"
        f"✅ CashKaro (scraped)\n"
        f"✅ FreeKaaMaal (scraped)\n\n"
        f"<b>🖥️ Hosting:</b>\n"
        f"🌐 {APP_URL or '⚠️ Set APP_URL env var'}\n"
        f"🔁 Self-ping: every {SELF_PING_MINS}min\n\n"
        f"<i>/setfreq to change frequency · /uptime for health</i>",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

# ═══════════════════════════════════════════════════════════
#  CORE DEAL POSTER
# ═══════════════════════════════════════════════════════════

async def _do_post_deals(app: Application, force: bool = False):
    storage: Storage = app.bot_data["storage"]
    recipients = set()
    if CHANNEL_ID:      recipients.add(CHANNEL_ID)
    if ALLOWED_CHAT_ID: recipients.add(ALLOWED_CHAT_ID)
    if not recipients:  return

    deals = fetch_all_deals()
    if not deals: return

    if force:
        new_deals = deals[:MAX_DEALS_SHOWN]
    else:
        new_deals = []
        for d in deals:
            h = deal_hash(d["title"], d["url"])
            if not storage.is_seen(h):
                new_deals.append(d); storage.mark_seen(h)

    if not new_deals: logger.info("Auto-deals: all seen"); return

    text = build_deals_message(new_deals, "🛍️ Deal Alert")
    for cid in recipients:
        try:
            await app.bot.send_message(int(cid), text,
                parse_mode="HTML", disable_web_page_preview=True)
            logger.info(f"Deals → {cid} ({len(new_deals)})")
        except Exception as e:
            logger.error(f"Post error {cid}: {e}")

# ═══════════════════════════════════════════════════════════
#  SCHEDULER JOBS
# ═══════════════════════════════════════════════════════════

async def job_price_check(app: Application):
    storage: Storage = app.bot_data["storage"]
    for cid, items in storage.all_tracked().items():
        for it in list(items):
            url = it["url"]; target = it["target_price"]; asin = it.get("asin")
            price = None
            if asin and AmazonAPI.is_configured():
                d = AmazonAPI.get_item_price(asin)
                if d: price = d["price"]
            if price is None: price = scrape_amazon_price(url)
            if price is None: continue
            it["current_price"] = price; storage._save()
            if price <= target:
                try:
                    await app.bot.send_message(int(cid),
                        f"🚨 <b>PRICE DROP!</b>\n\n"
                        f"📦 {it['title'][:80]}\n"
                        f"💰 Now: ₹{price:,.0f}\n"
                        f"🎯 Target: ₹{target:,.0f}\n"
                        f"💸 Save: ₹{target-price:,.0f}\n\n"
                        f"🔗 <a href='{make_affiliate_link(url)}'>Buy Now</a>",
                        parse_mode="HTML", disable_web_page_preview=True)
                    logger.info(f"Price alert: {it['title'][:40]}")
                except Exception as e: logger.error(f"Price alert: {e}")

async def job_keyword_scan(app: Application):
    storage: Storage = app.bot_data["storage"]
    if not storage.all_watchlists(): return
    deals = fetch_all_deals()
    if not deals: return
    for cid, keywords in storage.all_watchlists().items():
        for kw in keywords:
            matched = [d for d in deals
                       if kw in d["title"].lower() or kw in d.get("desc","").lower()]
            new = []
            for d in matched:
                h = deal_hash(d["title"],d["url"])
                if not storage.is_seen(h): new.append(d); storage.mark_seen(h)
            if not new: continue
            try:
                await app.bot.send_message(int(cid),
                    build_deals_message(new[:3], f"👁️ Keyword: '{kw}'"),
                    parse_mode="HTML", disable_web_page_preview=True)
            except Exception as e: logger.error(f"Keyword alert: {e}")

def job_self_ping():
    """
    Self-ping the Flask /ping endpoint to prevent Render/Replit sleeping.
    Runs in a background thread every SELF_PING_MINS minutes.
    Not needed on Fly.io (never sleeps), but harmless.
    """
    if not APP_URL:
        logger.debug("Self-ping skipped: APP_URL not set")
        return
    try:
        url = APP_URL.rstrip("/") + "/ping"
        r   = requests.get(url, timeout=10)
        LAST_PING["time"]  = datetime.now(IST_TZ)
        LAST_PING["count"] += 1
        logger.info(f"Self-ping OK: {url} → {r.status_code}")
    except Exception as e:
        logger.warning(f"Self-ping failed: {e}")

# ═══════════════════════════════════════════════════════════
#  FLASK  — keepalive + health endpoints
# ═══════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    now   = datetime.now(IST_TZ)
    delta = now - START_TIME
    hours = int(delta.total_seconds() // 3600)
    return (
        f"<h1>✅ DealSpy Bot v3.0</h1>"
        f"<p>IST: {now.strftime('%d %b %Y  %I:%M:%S %p')}</p>"
        f"<p>Uptime: {hours}h</p>"
        f"<p>Channel: {CHANNEL_ID or 'not set'}</p>"
        f"<p>Sources: Amazon RSS · DesiDime Atom · CouponDunia · CashKaro · FreeKaaMaal</p>"
        f"<p><a href='/health'>Health JSON</a></p>"
    )

@flask_app.route("/ping")
def ping():
    """UptimeRobot / BetterUptime monitor this endpoint."""
    return "pong", 200

@flask_app.route("/health")
def health():
    """JSON health check — for monitoring services."""
    now   = datetime.now(IST_TZ)
    delta = now - START_TIME
    return jsonify({
        "status":   "ok",
        "uptime_h": round(delta.total_seconds() / 3600, 2),
        "started":  START_TIME.strftime("%Y-%m-%d %H:%M IST"),
        "channel":  CHANNEL_ID or "not set",
        "ping_count": LAST_PING["count"],
    })

def run_flask():
    port = int(os.getenv("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("\n❌ BOT_TOKEN missing! Set it as an environment variable.\n")
        sys.exit(1)

    # Clear stale Telegram session
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true",
            timeout=10)
        logger.info(f"deleteWebhook: {r.json()}")
    except Exception as e:
        logger.warning(f"deleteWebhook: {e}")

    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask on port {os.getenv('PORT', 8080)}")

    storage = Storage()
    freq    = storage.get_freq()

    app = (Application.builder().token(BOT_TOKEN)
           .connect_timeout(30).read_timeout(30)
           .write_timeout(30).pool_timeout(30).build())
    app.bot_data["storage"] = storage

    for cmd, fn in [
        ("start","cmd_start"), ("help","cmd_help"), ("deals","cmd_deals"),
        ("hotdeals","cmd_hotdeals"), ("desidime","cmd_desidime"),
        ("search","cmd_search"), ("postdeals","cmd_postdeals"),
        ("setfreq","cmd_setfreq"), ("track","cmd_track"),
        ("tracking","cmd_tracking"), ("untrack","cmd_untrack"),
        ("watch","cmd_watch"), ("watching","cmd_watching"),
        ("unwatch","cmd_unwatch"), ("uptime","cmd_uptime"), ("status","cmd_status"),
    ]:
        app.add_handler(CommandHandler(cmd, eval(fn)))

    scheduler = AsyncIOScheduler(timezone=IST_TZ)
    app.bot_data["scheduler"] = scheduler

    async def _prices():   await job_price_check(app)
    async def _deals():    await _do_post_deals(app, force=False)
    async def _keywords(): await job_keyword_scan(app)

    scheduler.add_job(_prices,   "interval", minutes=PRICE_CHECK_MINS,   id="price_check")
    scheduler.add_job(_keywords, "interval", minutes=KEYWORD_CHECK_MINS,  id="keyword_scan")
    scheduler.add_job(_deals,    "interval", hours=freq,                   id="auto_deals")
    # Self-ping — keeps Render/Replit awake without needing UptimeRobot
    scheduler.add_job(job_self_ping, "interval", minutes=SELF_PING_MINS,  id="self_ping")

    scheduler.start()
    logger.info(f"Scheduler started — deals every {freq}h, prices every {PRICE_CHECK_MINS}min")

    print("\n" + "="*62)
    print("✅  DealSpy Bot v3.0 — READY")
    print("="*62)
    print(f"  Channel:      {CHANNEL_ID or '⚠️  Set CHANNEL_ID'}")
    print(f"  Affiliate:    {AMAZON_PARTNER_TAG or '⚠️  Set AMAZON_PARTNER_TAG'}")
    print(f"  PA API:       {'✅' if AmazonAPI.is_configured() else '⚠️  Optional'}")
    print(f"  Auto-posts:   every {freq}h  (change with /setfreq)")
    print(f"  Self-ping:    every {SELF_PING_MINS}min → {APP_URL or '⚠️  Set APP_URL'}")
    print(f"  Port:         {os.getenv('PORT', 8080)}")
    print()
    print("  Sources:")
    print("  ✅ Amazon RSS   ✅ DesiDime Atom   ✅ CouponDunia")
    print("  ✅ CashKaro     ✅ FreeKaaMaal")
    print("  ❌ Google Shopping RSS  (dead, removed)")
    print("="*62 + "\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()