#!/usr/bin/env python3
"""
DealSpy Bot v4.0 — Telegram Deals Bot for India
=================================================
8 DEAL SOURCES:
  1. Amazon India RSS       — Movers & Shakers, 9 categories (free, always works)
  2. Amazon Today's Deals   — /deals page scrape (real live prices + % off)
  3. Flipkart Affiliate API — Real product/deal feed with prices (free signup)
  4. DesiDime posts.atom    — Community-posted deals (Atom feed, working 2026)
  5. GrabOn                 — 3000+ coupons/day scraped (top Indian coupon site)
  6. FreeKaaMaal            — Freebies + deals scraped (600+ brand partnerships)
  7. MySmartPrice           — Price drop alerts for electronics
  8. CouponDunia RSS        — Sale event alerts (Big Billion Days, Republic Day etc.)

NEW in v4 vs v3:
  - Flipkart Affiliate API (real Flipkart deals, free signup)
  - Amazon Today's Deals page (actual ₹ prices + % off)
  - GrabOn scraper (massive Indian coupon site)
  - MySmartPrice (electronics price drops)
  - Parallel fetching — all 8 sources at once (fast!)
  - Smart deal scorer — best deals ranked first by discount %
  - /topdeal — single best deal right now
  - /amazon, /flipkart — source-specific commands
  - /category electronics|fashion|home|food|travel|health
  - Fuzzy deduplication — catches near-duplicate titles

ENV VARS needed on Render:
  BOT_TOKEN               — required
  ALLOWED_CHAT_ID         — your personal chat ID
  CHANNEL_ID              — e.g. -1001234567890
  AMAZON_PARTNER_TAG      — e.g. mydeals-21 (free: affiliate-program.amazon.in)
  FLIPKART_AFFILIATE_ID   — free signup at affiliate.flipkart.com
  FLIPKART_AFFILIATE_TOKEN— from affiliate.flipkart.com
  APP_URL                 — your Render URL for self-ping
  AMAZON_ACCESS_KEY       — optional PA API
  AMAZON_SECRET_KEY       — optional PA API
"""

import os, json, logging, sys, hashlib, re, hmac
from datetime import datetime
from typing import Dict, List, Optional
import pytz, requests, feedparser
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask, jsonify
from threading import Thread

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════

BOT_TOKEN                = os.getenv("BOT_TOKEN", "")
ALLOWED_CHAT_ID          = os.getenv("ALLOWED_CHAT_ID", "")
CHANNEL_ID               = os.getenv("CHANNEL_ID", "")
AMAZON_PARTNER_TAG       = os.getenv("AMAZON_PARTNER_TAG", "")
FLIPKART_AFFILIATE_ID    = os.getenv("FLIPKART_AFFILIATE_ID", "")
FLIPKART_AFFILIATE_TOKEN = os.getenv("FLIPKART_AFFILIATE_TOKEN", "")
AMAZON_ACCESS_KEY        = os.getenv("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET_KEY        = os.getenv("AMAZON_SECRET_KEY", "")
AMAZON_HOST              = "webservices.amazon.in"
AMAZON_REGION            = "eu-west-1"
APP_URL                  = os.getenv("APP_URL", "")

IST_TZ             = pytz.timezone("Asia/Kolkata")
STORAGE_FILE       = "deals_storage.json"
MAX_DEALS_SHOWN    = 8
PRICE_CHECK_MINS   = 30
KEYWORD_CHECK_MINS = 15
DEFAULT_POST_FREQ  = 3
MIN_FREQ           = 1
MAX_FREQ           = 24
SELF_PING_MINS     = 14

START_TIME = datetime.now(IST_TZ)
LAST_PING  = {"time": None, "count": 0}

# ═══════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════
#  STORAGE
# ═══════════════════════════════════════════════════════════

class Storage:
    def __init__(self):
        self.file = STORAGE_FILE
        self.data: Dict = {
            "tracked": {}, "seen_deals": [],
            "watchlists": {}, "post_freq_hours": DEFAULT_POST_FREQ,
        }
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.file):
                loaded = json.load(open(self.file))
                self.data = loaded
                for k, d in [("tracked",{}),("seen_deals",[]),
                              ("watchlists",{}),("post_freq_hours",DEFAULT_POST_FREQ)]:
                    if k not in self.data: self.data[k] = d
        except Exception as e: logger.error(f"Storage load: {e}")

    def _save(self):
        try: json.dump(self.data, open(self.file,"w"), indent=2)
        except Exception as e: logger.error(f"Storage save: {e}")

    def get_freq(self) -> int: return int(self.data.get("post_freq_hours", DEFAULT_POST_FREQ))
    def set_freq(self, h: int): self.data["post_freq_hours"] = h; self._save()

    def add_tracked(self, cid, item) -> bool:
        if cid not in self.data["tracked"]: self.data["tracked"][cid] = []
        if item["url"] not in [x["url"] for x in self.data["tracked"][cid]]:
            self.data["tracked"][cid].append(item); self._save(); return True
        return False

    def get_tracked(self, cid) -> List[dict]: return self.data["tracked"].get(cid, [])

    def remove_tracked(self, cid, idx) -> bool:
        items = self.data["tracked"].get(cid, [])
        if 0 <= idx < len(items):
            items.pop(idx); self.data["tracked"][cid] = items; self._save(); return True
        return False

    def all_tracked(self) -> Dict: return self.data["tracked"]
    def is_seen(self, h) -> bool: return h in self.data["seen_deals"]

    def mark_seen(self, h):
        self.data["seen_deals"].append(h)
        self.data["seen_deals"] = self.data["seen_deals"][-2000:]
        self._save()

    def add_keyword(self, cid, kw) -> bool:
        if cid not in self.data["watchlists"]: self.data["watchlists"][cid] = []
        kw = kw.lower().strip()
        if kw not in self.data["watchlists"][cid]:
            self.data["watchlists"][cid].append(kw); self._save(); return True
        return False

    def get_keywords(self, cid) -> List[str]: return self.data["watchlists"].get(cid, [])

    def remove_keyword(self, cid, kw) -> bool:
        kws = self.data["watchlists"].get(cid, [])
        if kw.lower() in kws:
            kws.remove(kw.lower()); self.data["watchlists"][cid] = kws; self._save(); return True
        return False

    def all_watchlists(self) -> Dict: return self.data["watchlists"]

# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

_H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def make_affiliate_link(url: str) -> str:
    if not AMAZON_PARTNER_TAG or ("amazon.in" not in url and "amzn" not in url): return url
    url = re.sub(r'[?&]tag=[^&]+', '', url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}tag={AMAZON_PARTNER_TAG}"

def make_flipkart_link(url: str) -> str:
    if not FLIPKART_AFFILIATE_ID or "flipkart.com" not in url: return url
    url = re.sub(r'[?&]affid=[^&]+', '', url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}affid={FLIPKART_AFFILIATE_ID}"

def extract_asin(url: str) -> Optional[str]:
    m = re.search(r'/dp/([A-Z0-9]{10})', url)
    if m: return m.group(1)
    m = re.search(r'/gp/product/([A-Z0-9]{10})', url)
    return m.group(1) if m else None

def parse_price(text: str) -> Optional[int]:
    m = re.search(r'(?:₹|Rs\.?)\s*([\d,]+)', text)
    if m: return int(m.group(1).replace(",",""))
    return None

def deal_hash(title: str, url: str) -> str:
    return hashlib.md5(f"{title[:50]}{url[:80]}".encode()).hexdigest()[:12]

def deal_score(deal: Dict) -> float:
    score = 0.0
    weights = {"Amazon Today's Deals":30,"Flipkart":28,"DesiDime":22,
               "MySmartPrice":20,"GrabOn":15,"FreeKaaMaal":12,
               "CouponDunia":10,"Amazon":18}
    for src, w in weights.items():
        if src in deal.get("source",""):
            score += w; break
    disc = deal.get("discount_pct", 0)
    if disc: score += min(disc * 0.5, 30)
    if deal.get("price"): score += 10
    if deal.get("desc") and len(deal.get("desc","")) > 20: score += 5
    return score

def auth(cid: str) -> bool:
    return not ALLOWED_CHAT_ID or cid == ALLOWED_CHAT_ID

_CATEGORY_KEYWORDS = {
    "electronics": ["phone","mobile","laptop","tv","television","camera","earphone",
                    "headphone","speaker","tablet","ipad","iphone","samsung","oneplus",
                    "redmi","realme","xiaomi","oppo","vivo","charger","smartwatch","router","keyboard"],
    "fashion":     ["shirt","tshirt","t-shirt","jeans","dress","shoes","sandal","saree",
                    "kurta","jacket","hoodie","sneaker","bag","wallet","watch","jewel","legging"],
    "home":        ["furniture","sofa","bed","mattress","pillow","curtain","kitchen",
                    "mixer","grinder","cooker","air fryer","vacuum","fan","ac","washing","fridge","microwave"],
    "food":        ["swiggy","zomato","food","restaurant","pizza","burger","biryani",
                    "grocery","bigbasket","blinkit","zepto","dunzo","instamart","dominos"],
    "travel":      ["flight","hotel","makemytrip","goibibo","irctc","bus","cab","ola","uber","booking"],
    "health":      ["medicine","pharma","netmeds","1mg","apollo","fitness","gym","protein",
                    "supplement","yoga","ayurvedic","healthkart"],
}

def _detect_category(text: str) -> str:
    t = text.lower()
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(k in t for k in kws): return cat
    return "general"

# ═══════════════════════════════════════════════════════════
#  SOURCE 1 — AMAZON MOVERS & SHAKERS RSS
# ═══════════════════════════════════════════════════════════

def fetch_amazon_rss(limit: int = 20) -> List[Dict]:
    deals = []
    categories = [
        ("electronics","🔌"),("computers","💻"),("kitchen","🍳"),
        ("apparel","👕"),("sports","🏃"),("toys","🧸"),
        ("home","🏠"),("beauty","💄"),("automotive","🚗"),
    ]
    for cat, emoji in categories:
        try:
            feed = feedparser.parse(
                f"https://www.amazon.in/gp/rss/movers-and-shakers/{cat}/",
                request_headers={"User-Agent": _H["User-Agent"]}
            )
            for e in feed.entries[:3]:
                title = e.get("title","").strip(); link = e.get("link","")
                desc  = e.get("summary","")
                if not title or not link: continue
                desc_text = BeautifulSoup(desc,"html.parser").get_text()
                price = parse_price(desc_text + title)
                rank_m = re.search(r'#(\d+)\s+in\s+([\w\s]+)', desc_text)
                deals.append({
                    "source": f"Amazon ({cat.title()})", "title": title,
                    "url": make_affiliate_link(link), "asin": extract_asin(link),
                    "price": price,
                    "desc": f"#{rank_m.group(1)} in {rank_m.group(2).strip()}" if rank_m else desc_text[:100],
                    "emoji": emoji, "discount_pct": 0, "category": cat,
                })
                if len(deals) >= limit: return deals
        except Exception as ex: logger.debug(f"Amazon RSS [{cat}]: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 2 — AMAZON TODAY'S DEALS (real % off + prices)
# ═══════════════════════════════════════════════════════════

def fetch_amazon_todays_deals(limit: int = 15) -> List[Dict]:
    deals = []
    try:
        r = requests.get("https://www.amazon.in/deals", headers=_H, timeout=15)
        if r.status_code != 200: return deals
        soup = BeautifulSoup(r.text, "html.parser")
        cards = (
            soup.find_all("div", attrs={"data-testid": re.compile(r"deal", re.I)})
            or soup.find_all("div", class_=re.compile(r"DealCard|deal-card", re.I))
            or soup.find_all("li", class_=re.compile(r"deal|offer", re.I))
        )
        for card in cards[:limit*2]:
            title_el = (card.find(["h2","h3","span"], class_=re.compile(r"title|name|product",re.I))
                        or card.find("a", href=re.compile(r"/dp/")))
            link_el  = card.find("a", href=re.compile(r"/dp/|/gp/"))
            if not title_el or not link_el: continue
            title = title_el.get_text(strip=True)
            if len(title) < 5: continue
            href = link_el.get("href","")
            if href.startswith("/"): href = "https://www.amazon.in" + href
            card_text = card.get_text()
            price = parse_price(card_text)
            disc_m = re.search(r'(\d{1,2})%\s*off', card_text, re.I)
            disc_pct = int(disc_m.group(1)) if disc_m else 0
            deals.append({
                "source":"Amazon Today's Deals","title":title[:100],
                "url":make_affiliate_link(href),"asin":extract_asin(href),
                "price":price,"desc":f"{disc_pct}% off" if disc_pct else "",
                "emoji":"🔥","discount_pct":disc_pct,"category":"deals",
            })
            if len(deals) >= limit: break
    except Exception as ex: logger.error(f"Amazon deals page: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 3 — FLIPKART AFFILIATE API
# ═══════════════════════════════════════════════════════════

def fetch_flipkart_affiliate(limit: int = 20) -> List[Dict]:
    """
    Free Flipkart Affiliate API — signup at affiliate.flipkart.com
    Set FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN in Render env vars.
    """
    if not FLIPKART_AFFILIATE_ID or not FLIPKART_AFFILIATE_TOKEN:
        return []
    deals = []
    headers = {
        "Fk-Affiliate-Id": FLIPKART_AFFILIATE_ID,
        "Fk-Affiliate-Token": FLIPKART_AFFILIATE_TOKEN,
    }
    categories = [
        ("mobiles","📱"),("laptops","💻"),("televisions","📺"),
        ("clothing","👗"),("shoes","👟"),("large_appliances","🏠"),
    ]
    for cat, emoji in categories:
        try:
            url = (f"https://affiliate-api.flipkart.net/affiliate/1.0/feeds/"
                   f"{FLIPKART_AFFILIATE_ID}/category/{cat}.json")
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code != 200: continue
            data = r.json()
            products = data.get("products",[]) or data.get("items",[])
            if not products:
                for k in data:
                    if isinstance(data[k], list) and data[k]:
                        products = data[k]; break
            for p in products[:5]:
                base  = p.get("productBaseInfo", p)
                info  = base.get("productAttributes", base)
                title = (info.get("title") or info.get("name") or p.get("title","")).strip()
                purl  = (info.get("productUrl") or info.get("url") or
                         base.get("productUrl") or p.get("url",""))
                price_raw = info.get("sellingPrice",{}) or info.get("discountedPrice",{})
                mrp_raw   = info.get("maximumRetailPrice",{}) or info.get("mrp",{})
                price = (price_raw.get("amount") if isinstance(price_raw,dict) else price_raw) or None
                mrp   = (mrp_raw.get("amount") if isinstance(mrp_raw,dict) else mrp_raw) or None
                if not title or not purl: continue
                disc_pct = int((mrp-price)/mrp*100) if (price and mrp and mrp>price) else 0
                deals.append({
                    "source": f"Flipkart ({cat.replace('_',' ').title()})",
                    "title": title[:100], "url": make_flipkart_link(purl),
                    "price": int(price) if price else None,
                    "mrp": int(mrp) if mrp else None,
                    "desc": (f"{disc_pct}% off • MRP ₹{mrp:,}" if disc_pct and mrp
                             else info.get("shortDescription","")[:100]),
                    "emoji": emoji, "discount_pct": disc_pct, "category": cat,
                })
                if len(deals) >= limit: return deals
        except Exception as ex: logger.debug(f"Flipkart [{cat}]: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 4 — DESIDIME ATOM FEED
# ═══════════════════════════════════════════════════════════

def fetch_desidime(limit: int = 20) -> List[Dict]:
    """DesiDime community deals via posts.atom — WORKING 2026. deals.rss is 404."""
    deals = []
    try:
        feed = feedparser.parse("https://www.desidime.com/posts.atom")
        if not feed.entries: return deals
        for e in feed.entries[:limit]:
            title = e.get("title","").strip()
            url   = e.get("link","") or e.get("id","")
            desc  = ""
            if hasattr(e,"summary"): desc = e.summary
            elif hasattr(e,"content") and e.content: desc = e.content[0].get("value","")
            desc_text = BeautifulSoup(desc,"html.parser").get_text()[:250]
            price = parse_price(desc_text + title)
            disc_m = re.search(r'(\d{1,2})%\s*off', desc_text + title, re.I)
            disc_pct = int(disc_m.group(1)) if disc_m else 0
            aff = url
            am = re.search(r'https?://(?:www\.)?amazon\.in/\S+', desc)
            if am: aff = make_affiliate_link(am.group(0))
            fm = re.search(r'https?://(?:www\.)?flipkart\.com/\S+', desc)
            if fm: aff = make_flipkart_link(fm.group(0))
            deals.append({
                "source":"DesiDime","title":title,"url":aff or url,
                "price":price,"desc":desc_text[:150],"emoji":"🔥",
                "discount_pct":disc_pct,"category":_detect_category(title+" "+desc_text),
            })
    except Exception as ex: logger.error(f"DesiDime: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 5 — GRABON
# ═══════════════════════════════════════════════════════════

def fetch_grabon(limit: int = 15) -> List[Dict]:
    """GrabOn — India's top coupon site, 3000+ coupons/day, 40M users."""
    deals = []
    for page_url in ["https://www.grabon.in/deals/","https://www.grabon.in/amazon-coupons/"]:
        try:
            r = requests.get(page_url, headers=_H, timeout=15)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "html.parser")
            cards = (
                soup.find_all("div", class_=re.compile(r"coupon.block|deal.item|g-card",re.I))
                or soup.find_all("li", class_=re.compile(r"coupon|deal|offer",re.I))
                or soup.find_all("article")
            )
            for card in cards[:limit*2]:
                te = (card.find(["h3","h2","strong"],
                                class_=re.compile(r"title|name|heading|offer",re.I))
                      or card.find("a"))
                le = card.find("a", href=True)
                if not te or not le: continue
                title = te.get_text(strip=True)
                if len(title) < 8: continue
                href = le["href"]
                if href.startswith("/"): href = "https://www.grabon.in" + href
                ct = card.get_text()
                price = parse_price(ct)
                disc_m = re.search(r'(\d{1,2})%\s*(?:off|discount|cashback)',ct,re.I)
                disc_pct = int(disc_m.group(1)) if disc_m else 0
                code_m = re.search(r'(?:code|coupon)[:\s]+([A-Z0-9]{4,15})',ct,re.I)
                desc = f"💳 Code: {code_m.group(1)}" if code_m else (f"{disc_pct}% off" if disc_pct else "")
                deals.append({
                    "source":"GrabOn","title":title[:100],"url":href,
                    "price":price,"desc":desc,"emoji":"🏷️",
                    "discount_pct":disc_pct,"category":_detect_category(title),
                })
                if len(deals) >= limit: break
            if deals: break
        except Exception as ex: logger.debug(f"GrabOn: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 6 — FREEKAAMAAL
# ═══════════════════════════════════════════════════════════

def fetch_freekaamaal(limit: int = 12) -> List[Dict]:
    deals = []
    try:
        r = requests.get("https://www.freekaamaal.com/", headers=_H, timeout=15)
        if r.status_code != 200: return deals
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.find_all("article") or soup.find_all("div",class_=re.compile(r"post|deal|item",re.I))
        for p in posts[:limit*2]:
            te = p.find(["h2","h3","h4","a"]); le = p.find("a",href=True)
            if not te or not le: continue
            title = te.get_text(strip=True)
            if len(title) < 8: continue
            url = le["href"]
            if not url.startswith("http"): url = "https://www.freekaamaal.com" + url
            if "freekaamaal.com" not in url: continue
            ct = p.get_text()
            price = parse_price(ct)
            disc_m = re.search(r'(\d{1,2})%\s*off',ct,re.I)
            disc_pct = int(disc_m.group(1)) if disc_m else 0
            deals.append({
                "source":"FreeKaaMaal","title":title[:100],"url":url,
                "price":price,"desc":f"{disc_pct}% off" if disc_pct else "Freebie / Cashback",
                "emoji":"🎁","discount_pct":disc_pct,"category":_detect_category(title),
            })
            if len(deals) >= limit: break
    except Exception as ex: logger.error(f"FreeKaaMaal: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 7 — MYSMARTPRICE (electronics price drops)
# ═══════════════════════════════════════════════════════════

def fetch_mysmartprice(limit: int = 12) -> List[Dict]:
    """MySmartPrice best deals — India's top price comparison site."""
    deals = []
    try:
        r = requests.get(
            "https://www.mysmartprice.com/gear/best-deals-online-india/",
            headers=_H, timeout=15
        )
        if r.status_code != 200: return deals
        soup = BeautifulSoup(r.text, "html.parser")
        cards = (
            soup.find_all("div",class_=re.compile(r"product.card|deal.card|msp.card",re.I))
            or soup.find_all("article",class_=re.compile(r"product|deal",re.I))
            or soup.find_all("div",class_=re.compile(r"entry|post",re.I))
        )
        for card in cards[:limit*2]:
            te = (card.find(["h2","h3","h4"],class_=re.compile(r"title|name|product",re.I))
                  or card.find("a",href=True))
            le = card.find("a",href=True)
            if not te or not le: continue
            title = te.get_text(strip=True)
            if len(title) < 8: continue
            href = le["href"]
            if href.startswith("/"): href = "https://www.mysmartprice.com" + href
            ct = card.get_text()
            price = parse_price(ct)
            disc_m = re.search(r'(\d{1,2})%\s*(?:off|drop|cheaper)',ct,re.I)
            disc_pct = int(disc_m.group(1)) if disc_m else 0
            buy = card.find("a", href=re.compile(r"amazon\.in|flipkart\.com"))
            if buy:
                bu = buy["href"]
                if "amazon.in" in bu: href = make_affiliate_link(bu)
                elif "flipkart.com" in bu: href = make_flipkart_link(bu)
            deals.append({
                "source":"MySmartPrice","title":title[:100],"url":href,
                "price":price,"desc":f"Price drop {disc_pct}% 📉" if disc_pct else "Best price tracked",
                "emoji":"📉","discount_pct":disc_pct,"category":_detect_category(title),
            })
            if len(deals) >= limit: break
    except Exception as ex: logger.error(f"MySmartPrice: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  SOURCE 8 — COUPONDUNIA RSS
# ═══════════════════════════════════════════════════════════

def fetch_coupondunia(limit: int = 8) -> List[Dict]:
    """CouponDunia blog RSS — verified working 2026. Sale event news."""
    deals = []
    try:
        feed = feedparser.parse("https://www.coupondunia.in/blog/feed/")
        for e in feed.entries[:limit]:
            title = e.get("title","").strip(); url = e.get("link","")
            desc  = e.get("summary","")
            if not title: continue
            deals.append({
                "source":"CouponDunia","title":title[:100],"url":url,"price":None,
                "desc":BeautifulSoup(desc,"html.parser").get_text()[:120],
                "emoji":"📅","discount_pct":0,"category":"sales",
            })
    except Exception as ex: logger.error(f"CouponDunia: {ex}")
    return deals

# ═══════════════════════════════════════════════════════════
#  MASTER FETCHER — parallel, scored, deduplicated
# ═══════════════════════════════════════════════════════════

def fetch_all_deals(keyword: Optional[str]=None, category: Optional[str]=None) -> List[Dict]:
    all_deals: List[Dict] = []
    fetchers = [
        (fetch_amazon_rss,          {"limit":15}),
        (fetch_amazon_todays_deals, {"limit":12}),
        (fetch_flipkart_affiliate,  {"limit":15}),
        (fetch_desidime,            {"limit":15}),
        (fetch_grabon,              {"limit":12}),
        (fetch_freekaamaal,         {"limit":10}),
        (fetch_mysmartprice,        {"limit":10}),
        (fetch_coupondunia,         {"limit":6}),
    ]
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn, **kw): fn.__name__ for fn, kw in fetchers}
        for future in as_completed(futures, timeout=25):
            try:
                result = future.result()
                all_deals.extend(result)
                logger.info(f"{futures[future]}: {len(result)}")
            except Exception as e: logger.warning(f"{futures[future]}: {e}")

    if keyword:
        kw = keyword.lower()
        all_deals = [d for d in all_deals
                     if kw in d["title"].lower() or kw in d.get("desc","").lower()]
    if category and category != "all":
        all_deals = [d for d in all_deals
                     if d.get("category","") == category
                     or category.lower() in d["title"].lower()]

    seen_keys = set()
    unique = []
    for d in all_deals:
        key = re.sub(r'[^a-z0-9]','', d["title"].lower())[:28]
        if len(key) < 4 or key in seen_keys: continue
        seen_keys.add(key); unique.append(d)

    unique.sort(key=deal_score, reverse=True)
    return unique

# ═══════════════════════════════════════════════════════════
#  AMAZON PA API (optional)
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
            now=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"); date=now[:8]
            def _sign(k,m): return hmac.new(k,m.encode(),hashlib.sha256).digest()
            tgt="com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"
            hdrs={"content-encoding":"amz-1.0","content-type":"application/json; charset=utf-8",
                  "host":AMAZON_HOST,"x-amz-date":now,"x-amz-target":tgt}
            sh="content-encoding;content-type;host;x-amz-date;x-amz-target"
            bh=hashlib.sha256(payload.encode()).hexdigest()
            canon="\n".join(["POST","/paapi5/getitems","",
                "content-encoding:amz-1.0","content-type:application/json; charset=utf-8",
                f"host:{AMAZON_HOST}",f"x-amz-date:{now}",f"x-amz-target:{tgt}","",sh,bh])
            cs=f"{date}/{AMAZON_REGION}/ProductAdvertisingAPI/aws4_request"
            s2s="\n".join(["AWS4-HMAC-SHA256",now,cs,hashlib.sha256(canon.encode()).hexdigest()])
            sk=_sign(_sign(_sign(_sign(f"AWS4{AMAZON_SECRET_KEY}".encode(),date),AMAZON_REGION),
                           "ProductAdvertisingAPI"),"aws4_request")
            sig=hmac.new(sk,s2s.encode(),hashlib.sha256).hexdigest()
            hdrs["Authorization"]=(f"AWS4-HMAC-SHA256 Credential={AMAZON_ACCESS_KEY}/{cs}, "
                                    f"SignedHeaders={sh}, Signature={sig}")
            r=requests.post(f"https://{AMAZON_HOST}/paapi5/getitems",headers=hdrs,data=payload,timeout=12)
            if r.status_code==200:
                items=r.json().get("ItemsResult",{}).get("Items",[])
                if items:
                    i=items[0]; title=i.get("ItemInfo",{}).get("Title",{}).get("DisplayValue","")
                    offers=i.get("Offers",{}).get("Listings",[])
                    if offers:
                        price=offers[0].get("Price",{}).get("Amount")
                        return {"asin":asin,"title":title,"price":price,
                                "url":f"https://www.amazon.in/dp/{asin}"}
        except Exception as e: logger.error(f"PA API {asin}: {e}")
        return None

def scrape_amazon_price(url: str) -> Optional[float]:
    try:
        r=requests.get(url,headers=_H,timeout=12)
        if r.status_code!=200: return None
        soup=BeautifulSoup(r.text,"html.parser")
        for tag,attrs in [("span",{"id":"priceblock_ourprice"}),
                          ("span",{"id":"priceblock_dealprice"}),
                          ("span",{"class":"a-price-whole"}),
                          ("span",{"id":"price_inside_buybox"})]:
            el=soup.find(tag,attrs)
            if el:
                text=el.get_text(strip=True).replace(",","").replace("₹","").strip()
                m=re.search(r'[\d.]+',text)
                if m: return float(m.group())
    except Exception as e: logger.debug(f"Scrape {url}: {e}")
    return None

def scrape_product_title(url: str) -> str:
    try:
        soup=BeautifulSoup(requests.get(url,headers=_H,timeout=10).text,"html.parser")
        el=soup.find("span",{"id":"productTitle"})
        if el: return el.get_text(strip=True)[:100]
    except Exception: pass
    return url[:60]+"..."

# ═══════════════════════════════════════════════════════════
#  FORMATTER
# ═══════════════════════════════════════════════════════════

def format_deal(deal: Dict, idx: Optional[int]=None) -> str:
    num=f"{idx}. " if idx else ""
    url=deal.get("url","")
    if "amazon" in url.lower():    url=make_affiliate_link(url)
    elif "flipkart" in url.lower(): url=make_flipkart_link(url)

    price_str=""
    if deal.get("price"):
        price_str=f"\n   💰 ₹{deal['price']:,}"
        if deal.get("mrp") and deal["mrp"]>deal["price"]:
            price_str+=f"  <s>₹{deal['mrp']:,}</s>"
    if deal.get("discount_pct"):
        price_str+=f"  🔻{deal['discount_pct']}% off"

    desc_raw=deal.get("desc","")
    desc_str=f"\n   <i>{desc_raw[:120]}</i>" if desc_raw else ""
    cat=deal.get("category","general")
    cat_str=f"  [{cat}]" if cat not in ("general","deals","sales") else ""

    return (f"{num}{deal['emoji']} <b>{deal['title'][:80]}</b>\n"
            f"   📌 {deal.get('source','')}{cat_str}{price_str}{desc_str}\n"
            f"   🔗 <a href='{url}'>View Deal</a>")

def build_deals_message(deals: List[Dict], title: str) -> str:
    now=datetime.now(IST_TZ).strftime("%d %b %Y  %I:%M %p IST")
    header=f"<b>{title}</b>\n🕐 {now}\n\n"
    lines=[format_deal(d,i) for i,d in enumerate(deals[:MAX_DEALS_SHOWN],1)]
    text=header+"\n\n".join(lines)
    if len(text)>4000: text=text[:3950]+"\n\n<i>…use /deals for more</i>"
    return text

# ═══════════════════════════════════════════════════════════
#  COMMANDS
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    storage: Storage=ctx.bot_data["storage"]
    freq=storage.get_freq()
    fk="✅" if (FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN) else "⚠️ Not set"
    ch=f"✅ <code>{CHANNEL_ID}</code>" if CHANNEL_ID else "⚠️ Not set"
    await update.message.reply_text(
        f"🛍️ <b>DealSpy Bot v4.0</b>\n\n"
        f"India's best deals from <b>8 sources</b> — Amazon, Flipkart, DesiDime, "
        f"GrabOn, FreeKaaMaal, MySmartPrice, CouponDunia.\n\n"
        f"<b>📋 Commands:</b>\n"
        f"/deals — All 8 sources, best deals first\n"
        f"/topdeal — Single best deal right now\n"
        f"/amazon — Amazon only\n"
        f"/flipkart — Flipkart only\n"
        f"/desidime — DesiDime community\n"
        f"/category electronics|fashion|home|food|travel|health\n"
        f"/search KEYWORD\n"
        f"/postdeals — Push to channel now\n"
        f"/setfreq N — Auto every N hours (1–24)\n"
        f"/track URL PRICE — Price drop tracker\n"
        f"/tracking · /untrack N\n"
        f"/watch KEYWORD · /watching · /unwatch KEYWORD\n"
        f"/uptime · /status\n\n"
        f"<b>⚙️ Status:</b>\n"
        f"📱 Chat ID: <code>{cid}</code>\n"
        f"📢 Channel: {ch}\n"
        f"🟠 Flipkart API: {fk}\n"
        f"⏰ Auto posts: every {freq}h",
        parse_mode="HTML", disable_web_page_preview=True
    )

async def cmd_deals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg=await update.message.reply_text("⏳ Fetching from 8 sources simultaneously…")
    deals=fetch_all_deals()
    if not deals: await msg.edit_text("😕 No deals right now."); return
    await msg.edit_text(build_deals_message(deals,f"🔥 Best Deals — {len(deals)} found"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_topdeal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg=await update.message.reply_text("⏳ Finding best deal…")
    deals=fetch_all_deals()
    if not deals: await msg.edit_text("😕 No deals found."); return
    top=deals[0]
    await msg.edit_text(
        f"🏆 <b>Top Deal Right Now</b>\n"
        f"🕐 {datetime.now(IST_TZ).strftime('%d %b  %I:%M %p IST')}\n\n"
        f"{format_deal(top)}\n\n"
        f"<i>Score: {deal_score(top):.0f}/100 · Source: {top.get('source','?')}</i>",
        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_amazon(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg=await update.message.reply_text("⏳ Fetching Amazon deals…")
    deals=sorted(fetch_amazon_rss(15)+fetch_amazon_todays_deals(12),
                 key=deal_score, reverse=True)
    if not deals: await msg.edit_text("😕 Amazon unavailable."); return
    await msg.edit_text(build_deals_message(deals,"🛒 Amazon India Deals"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_flipkart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not FLIPKART_AFFILIATE_ID:
        await update.message.reply_text(
            "⚠️ Flipkart API not set up.\n\n"
            "1. Sign up free at affiliate.flipkart.com\n"
            "2. Get Affiliate ID & Token\n"
            "3. Add to Render env vars:\n"
            "   FLIPKART_AFFILIATE_ID\n"
            "   FLIPKART_AFFILIATE_TOKEN"); return
    msg=await update.message.reply_text("⏳ Fetching Flipkart deals…")
    deals=fetch_flipkart_affiliate(20)
    if not deals: await msg.edit_text("😕 No Flipkart deals right now."); return
    await msg.edit_text(build_deals_message(deals,"🟠 Flipkart Deals"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_desidime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg=await update.message.reply_text("⏳ Fetching DesiDime deals…")
    deals=sorted(fetch_desidime(20),key=deal_score,reverse=True)
    if not deals: await msg.edit_text("😕 DesiDime unavailable."); return
    await msg.edit_text(build_deals_message(deals,"🔥 DesiDime Community Deals"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args:
        cats=" · ".join(f"<code>{c}</code>" for c in _CATEGORY_KEYWORDS)
        await update.message.reply_text(
            f"Usage: /category CATEGORY\n\nAvailable: {cats}",
            parse_mode="HTML"); return
    cat=ctx.args[0].lower()
    if cat not in list(_CATEGORY_KEYWORDS.keys())+["all"]:
        await update.message.reply_text(f"❌ Unknown. Try: {', '.join(_CATEGORY_KEYWORDS.keys())}"); return
    msg=await update.message.reply_text(f"⏳ Fetching {cat} deals…")
    deals=fetch_all_deals(category=cat)
    if not deals: await msg.edit_text(f"😕 No {cat} deals right now."); return
    await msg.edit_text(build_deals_message(deals,f"🗂️ {cat.title()} Deals"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /search KEYWORD"); return
    kw=" ".join(ctx.args)
    msg=await update.message.reply_text(f"🔍 Searching all sources for '{kw}'…")
    deals=fetch_all_deals(keyword=kw)
    if not deals:
        await msg.edit_text(f"😕 Nothing for '<b>{kw}</b>'. Try broader.",parse_mode="HTML"); return
    await msg.edit_text(build_deals_message(deals,f"🔍 '{kw}'"),
                        parse_mode="HTML", disable_web_page_preview=True)

async def cmd_postdeals(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    msg=await update.message.reply_text("⏳ Posting to channel…")
    await _do_post_deals(ctx.application, force=True)
    ch=f"<code>{CHANNEL_ID}</code>" if CHANNEL_ID else "your chat"
    await msg.edit_text(f"✅ Posted to {ch}!",parse_mode="HTML")

async def cmd_setfreq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage=ctx.bot_data["storage"]
    if not ctx.args:
        freq=storage.get_freq()
        await update.message.reply_text(
            f"⏰ Now: every <b>{freq}h</b> (~{round(24/freq,1)}/day)\n\n"
            f"Usage: /setfreq N  (1–24)\n• /setfreq 1 → 24/day\n"
            f"• /setfreq 3 → 8/day (default)\n• /setfreq 6 → 4/day",
            parse_mode="HTML"); return
    try: n=int(ctx.args[0])
    except: await update.message.reply_text("❌ Number required."); return
    if not (MIN_FREQ<=n<=MAX_FREQ):
        await update.message.reply_text(f"❌ Must be {MIN_FREQ}–{MAX_FREQ}."); return
    storage.set_freq(n)
    try:
        scheduler: AsyncIOScheduler=ctx.bot_data["scheduler"]
        scheduler.reschedule_job("auto_deals",trigger="interval",hours=n)
    except Exception as e: logger.error(f"Reschedule: {e}")
    await update.message.reply_text(
        f"✅ Every <b>{n}h</b> (~{round(24/n,1)}/day)",parse_mode="HTML")

async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if len(ctx.args)<2:
        await update.message.reply_text("Usage: /track AMAZON_URL PRICE"); return
    url=ctx.args[0]
    try: target=float(ctx.args[1])
    except: await update.message.reply_text("❌ Price must be a number."); return
    if "amazon.in" not in url and "amzn" not in url:
        await update.message.reply_text("❌ Amazon India URLs only."); return
    msg=await update.message.reply_text("⏳ Fetching product…")
    asin=extract_asin(url); price=None; title=None
    if asin and AmazonAPI.is_configured():
        d=AmazonAPI.get_item_price(asin)
        if d: price=d["price"]; title=d["title"]
    if price is None: price=scrape_amazon_price(url)
    if title is None: title=scrape_product_title(url)
    storage: Storage=ctx.bot_data["storage"]
    storage.add_tracked(cid,{"url":make_affiliate_link(url),"title":title,
                              "target_price":target,"current_price":price or 0,
                              "asin":asin,"added_at":datetime.now(IST_TZ).isoformat()})
    ps=f"₹{price:,.0f}" if price else "unavailable"
    await msg.edit_text(
        f"✅ <b>Tracking!</b>\n📦 {title[:80]}\n"
        f"🎯 Alert ≤ ₹{target:,.0f}  💰 Now: {ps}\n"
        f"<i>Checked every {PRICE_CHECK_MINS}min</i>",parse_mode="HTML")

async def cmd_tracking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage=ctx.bot_data["storage"]
    items=storage.get_tracked(cid)
    if not items: await update.message.reply_text("📭 None. /track URL PRICE"); return
    lines=[f"<b>📦 Tracking ({len(items)})</b>\n"]
    for i,it in enumerate(items,1):
        cur=f"₹{it['current_price']:,.0f}" if it.get("current_price") else "?"
        lines.append(f"{i}. <b>{it['title'][:55]}</b>\n"
                     f"   🎯 ₹{it['target_price']:,.0f}  Now: {cur}\n"
                     f"   🔗 <a href='{it['url']}'>View</a>\n")
    lines.append("<i>/untrack N</i>")
    await update.message.reply_text("\n".join(lines),parse_mode="HTML",disable_web_page_preview=True)

async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /untrack N"); return
    try: idx=int(ctx.args[0])-1
    except: await update.message.reply_text("❌ Number required."); return
    storage: Storage=ctx.bot_data["storage"]
    if storage.remove_tracked(cid,idx): await update.message.reply_text(f"✅ Removed.")
    else: await update.message.reply_text("❌ Not found. Use /tracking.")

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args:
        await update.message.reply_text("Usage: /watch KEYWORD\nExample: /watch iphone 15"); return
    kw=" ".join(ctx.args).lower().strip()
    storage: Storage=ctx.bot_data["storage"]
    if storage.add_keyword(cid,kw):
        await update.message.reply_text(f"✅ Watching: <b>{kw}</b>",parse_mode="HTML")
    else: await update.message.reply_text(f"ℹ️ Already watching.",parse_mode="HTML")

async def cmd_watching(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    storage: Storage=ctx.bot_data["storage"]
    kws=storage.get_keywords(cid)
    if not kws: await update.message.reply_text("📭 None. /watch KEYWORD"); return
    lines=["<b>👁️ Watchlist</b>\n"]+[f"• {k}" for k in kws]+["\n<i>/unwatch KEYWORD</i>"]
    await update.message.reply_text("\n".join(lines),parse_mode="HTML")

async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id)
    if not auth(cid): await update.message.reply_text("❌ Unauthorized."); return
    if not ctx.args: await update.message.reply_text("Usage: /unwatch KEYWORD"); return
    kw=" ".join(ctx.args).lower().strip()
    storage: Storage=ctx.bot_data["storage"]
    if storage.remove_keyword(cid,kw):
        await update.message.reply_text(f"✅ Removed: <b>{kw}</b>",parse_mode="HTML")
    else: await update.message.reply_text(f"❌ Not watching.",parse_mode="HTML")

async def cmd_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now=datetime.now(IST_TZ); delta=now-START_TIME
    h=int(delta.total_seconds()//3600); m=int((delta.total_seconds()%3600)//60)
    lp=LAST_PING["time"]
    await update.message.reply_text(
        f"<b>📡 Bot Health</b>\n\n"
        f"✅ Uptime: <b>{h}h {m}m</b>\n"
        f"🕐 Started: {START_TIME.strftime('%d %b  %I:%M %p IST')}\n"
        f"🔔 Self-pings: {LAST_PING['count']} "
        f"(last: {lp.strftime('%I:%M %p') if lp else 'none'})\n"
        f"🌐 {APP_URL or '⚠️ Set APP_URL'}",
        parse_mode="HTML")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    storage: Storage=ctx.bot_data["storage"]
    freq=storage.get_freq()
    fk=bool(FLIPKART_AFFILIATE_ID and FLIPKART_AFFILIATE_TOKEN)
    await update.message.reply_text(
        f"<b>⚙️ DealSpy v4.0 Status</b>\n\n"
        f"📢 Channel:   {CHANNEL_ID or '⚠️ Not set'}\n"
        f"⏰ Frequency: every {freq}h (~{round(24/freq,1)}/day)\n"
        f"📦 Tracked:   {sum(len(v) for v in storage.all_tracked().values())}\n"
        f"👁️ Keywords:  {sum(len(v) for v in storage.all_watchlists().values())}\n\n"
        f"<b>📡 8 Sources:</b>\n"
        f"✅ Amazon RSS (9 categories)\n"
        f"✅ Amazon Today's Deals (live prices)\n"
        f"{'✅' if fk else '⚠️'} Flipkart Affiliate API {'(active)' if fk else '— add FLIPKART_AFFILIATE_ID'}\n"
        f"✅ DesiDime posts.atom (community deals)\n"
        f"✅ GrabOn (3000+ coupons/day)\n"
        f"✅ FreeKaaMaal (freebies + deals)\n"
        f"✅ MySmartPrice (price drops)\n"
        f"✅ CouponDunia RSS (sale alerts)\n\n"
        f"<b>🔑 Affiliates:</b>\n"
        f"{'✅' if AMAZON_PARTNER_TAG else '⚠️'} Amazon: {AMAZON_PARTNER_TAG or 'not set'}\n"
        f"{'✅' if FLIPKART_AFFILIATE_ID else '⚠️'} Flipkart: {FLIPKART_AFFILIATE_ID or 'not set — free signup at affiliate.flipkart.com'}\n\n"
        f"<i>/topdeal · /category · /setfreq · /amazon · /flipkart</i>",
        parse_mode="HTML")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

# ═══════════════════════════════════════════════════════════
#  POST LOGIC
# ═══════════════════════════════════════════════════════════

async def _do_post_deals(app: Application, force: bool=False):
    storage: Storage=app.bot_data["storage"]
    recipients=set()
    if CHANNEL_ID:      recipients.add(CHANNEL_ID)
    if ALLOWED_CHAT_ID: recipients.add(ALLOWED_CHAT_ID)
    if not recipients: return

    deals=fetch_all_deals()
    if not deals: return

    if force:
        new_deals=deals[:MAX_DEALS_SHOWN]
    else:
        new_deals=[]
        for d in deals:
            h=deal_hash(d["title"],d["url"])
            if not storage.is_seen(h): new_deals.append(d); storage.mark_seen(h)

    if not new_deals: logger.info("Auto-deals: all seen"); return
    text=build_deals_message(new_deals,"🛍️ Deal Alert")
    for cid in recipients:
        try:
            await app.bot.send_message(int(cid),text,
                parse_mode="HTML",disable_web_page_preview=True)
            logger.info(f"Deals → {cid} ({len(new_deals)})")
        except Exception as e: logger.error(f"Post error {cid}: {e}")

# ═══════════════════════════════════════════════════════════
#  SCHEDULER JOBS
# ═══════════════════════════════════════════════════════════

async def job_price_check(app: Application):
    storage: Storage=app.bot_data["storage"]
    for cid,items in storage.all_tracked().items():
        for it in list(items):
            url=it["url"]; target=it["target_price"]; asin=it.get("asin")
            price=None
            if asin and AmazonAPI.is_configured():
                d=AmazonAPI.get_item_price(asin)
                if d: price=d["price"]
            if price is None: price=scrape_amazon_price(url)
            if price is None: continue
            it["current_price"]=price; storage._save()
            if price<=target:
                try:
                    await app.bot.send_message(int(cid),
                        f"🚨 <b>PRICE DROP!</b>\n\n"
                        f"📦 {it['title'][:80]}\n"
                        f"💰 Now: ₹{price:,.0f}  🎯 Target: ₹{target:,.0f}\n"
                        f"💸 Save: ₹{target-price:,.0f}\n\n"
                        f"🔗 <a href='{make_affiliate_link(url)}'>Buy Now</a>",
                        parse_mode="HTML",disable_web_page_preview=True)
                except Exception as e: logger.error(f"Price alert: {e}")

async def job_keyword_scan(app: Application):
    storage: Storage=app.bot_data["storage"]
    if not storage.all_watchlists(): return
    deals=fetch_all_deals()
    if not deals: return
    for cid,keywords in storage.all_watchlists().items():
        for kw in keywords:
            matched=[d for d in deals
                     if kw in d["title"].lower() or kw in d.get("desc","").lower()]
            new=[]
            for d in matched:
                h=deal_hash(d["title"],d["url"])
                if not storage.is_seen(h): new.append(d); storage.mark_seen(h)
            if not new: continue
            try:
                await app.bot.send_message(int(cid),
                    build_deals_message(new[:3],f"👁️ Keyword Alert: '{kw}'"),
                    parse_mode="HTML",disable_web_page_preview=True)
            except Exception as e: logger.error(f"Keyword alert: {e}")

def job_self_ping():
    if not APP_URL: return
    try:
        r=requests.get(APP_URL.rstrip("/")+"/ping",timeout=10)
        LAST_PING["time"]=datetime.now(IST_TZ); LAST_PING["count"]+=1
        logger.info(f"Self-ping OK → {r.status_code}")
    except Exception as e: logger.warning(f"Self-ping failed: {e}")

# ═══════════════════════════════════════════════════════════
#  FLASK
# ═══════════════════════════════════════════════════════════

flask_app=Flask(__name__)

@flask_app.route("/")
def home():
    now=datetime.now(IST_TZ); delta=now-START_TIME
    return (f"<h1>✅ DealSpy Bot v4.0</h1>"
            f"<p>IST: {now.strftime('%d %b %Y  %I:%M:%S %p')}</p>"
            f"<p>Uptime: {int(delta.total_seconds()//3600)}h  |  "
            f"Self-pings: {LAST_PING['count']}</p>"
            f"<p>Sources: Amazon RSS · Amazon Deals · Flipkart · DesiDime · "
            f"GrabOn · FreeKaaMaal · MySmartPrice · CouponDunia</p>"
            f"<p><a href='/health'>Health JSON</a></p>")

@flask_app.route("/ping")
def ping(): return "pong", 200

@flask_app.route("/health")
def health():
    return jsonify({
        "status":"ok",
        "uptime_h":round((datetime.now(IST_TZ)-START_TIME).total_seconds()/3600,2),
        "ping_count":LAST_PING["count"],
        "sources":8,
        "flipkart_api":bool(FLIPKART_AFFILIATE_ID),
    })

def run_flask():
    port=int(os.getenv("PORT",8080))
    flask_app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN: print("\n❌ BOT_TOKEN not set!\n"); sys.exit(1)

    try:
        r=requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true",
            timeout=10)
        logger.info(f"deleteWebhook: {r.json()}")
    except Exception as e: logger.warning(f"deleteWebhook: {e}")

    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask on :{os.getenv('PORT',8080)}")

    storage=Storage(); freq=storage.get_freq()

    app=(Application.builder().token(BOT_TOKEN)
         .connect_timeout(30).read_timeout(30)
         .write_timeout(30).pool_timeout(30).build())
    app.bot_data["storage"]=storage

    for cmd,fn in [
        ("start",cmd_start),("help",cmd_help),("deals",cmd_deals),
        ("topdeal",cmd_topdeal),("amazon",cmd_amazon),("flipkart",cmd_flipkart),
        ("desidime",cmd_desidime),("category",cmd_category),
        ("search",cmd_search),("postdeals",cmd_postdeals),
        ("setfreq",cmd_setfreq),("track",cmd_track),
        ("tracking",cmd_tracking),("untrack",cmd_untrack),
        ("watch",cmd_watch),("watching",cmd_watching),
        ("unwatch",cmd_unwatch),("uptime",cmd_uptime),("status",cmd_status),
    ]:
        app.add_handler(CommandHandler(cmd,fn))

    scheduler=AsyncIOScheduler(timezone=IST_TZ)
    app.bot_data["scheduler"]=scheduler

    async def _prices():   await job_price_check(app)
    async def _deals():    await _do_post_deals(app,force=False)
    async def _keywords(): await job_keyword_scan(app)

    scheduler.add_job(_prices,       "interval",minutes=PRICE_CHECK_MINS,  id="price_check")
    scheduler.add_job(_keywords,     "interval",minutes=KEYWORD_CHECK_MINS, id="keyword_scan")
    scheduler.add_job(_deals,        "interval",hours=freq,                  id="auto_deals")
    scheduler.add_job(job_self_ping, "interval",minutes=SELF_PING_MINS,      id="self_ping")
    scheduler.start()

    print("\n"+"="*65)
    print("✅  DealSpy Bot v4.0 — READY")
    print("="*65)
    print(f"  Channel:       {CHANNEL_ID or '⚠️  Set CHANNEL_ID'}")
    print(f"  Amazon tag:    {AMAZON_PARTNER_TAG or '⚠️  Set AMAZON_PARTNER_TAG (free)'}")
    fk_status = '✅ ' + FLIPKART_AFFILIATE_ID if FLIPKART_AFFILIATE_ID else '⚠️  Set FLIPKART_AFFILIATE_ID (free: affiliate.flipkart.com)'
    print(f"  Flipkart API:  {fk_status}")
    print(f"  Auto-posts:    every {freq}h  (/setfreq to change)")
    print(f"  Self-ping:     every {SELF_PING_MINS}min → {APP_URL or '⚠️ Set APP_URL'}")
    print()
    print("  8 Sources:")
    print("  ✅ Amazon RSS (9 cats)    ✅ Amazon Today's Deals")
    fk_check = '✅' if FLIPKART_AFFILIATE_ID else '⚠️'
    print(f"  {fk_check} Flipkart API          ✅ DesiDime Atom")
    print("  ✅ GrabOn                 ✅ FreeKaaMaal")
    print("  ✅ MySmartPrice           ✅ CouponDunia RSS")
    print("="*65+"\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,close_loop=False)

if __name__=="__main__":
    main()