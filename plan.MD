# GhostFrog Telegram Monetization Plan
*Generated: 2025-11-11 21:29:34 UTC*

---

## 🧠 1. Core Concept

You already have the hardest part done — you collect eBay auction data, estimates ROI, and sends alerts through Telegram.

Now we’ll monetize it by introducing paid tiers, affiliate tracking, and user personalization.

---

## 💰 2. Pricing Tiers

### **£4.99/month – Firehose (Basic Alerts)**
**Target user:** Curious flippers, beginners, or low-effort side hustlers.

**They get:**
- Access to a **Telegram broadcast channel** (e.g. `t.me/_alerts`)
- Automatic alerts for all profitable listings from curated niches
  - Title, image, buy price, est. sell price, ROI %, profit £, and eBay link
- Around **20–50 deals/day**
- Immediate value and FOMO to encourage upgrades

**They don’t get:**
- ❌ No custom filters or ROI thresholds
- ❌ No DM alerts or personalization
- ❌ No analytics or performance summaries

**Purpose:** Entry-level funnel for paid conversions.  
Encourages upgrading to Premium tiers once they see the value.

---

### **£9.99/month – Custom Filters**
- Alerts only for the niches and ROI thresholds users care about.
- Examples:
  - PS5 or 3dfx cards with ROI > 30%
  - “Only auctions under £250”
- Alerts delivered as **direct Telegram DMs** from the bot.
- Uses preferences stored in `user_prefs` table.

---

### **£24.99/month – Private Tier**
- Concierge-style setup.
- Users tell you their flipping strategy.
- You configure tailored watchlists + early alerts for them.
- Optionally include analytics, ROI summaries, or private Telegram groups.

---

## 🧩 3. MVP – No Interface Needed

Start manually:

1. User subscribes via Stripe or Patreon.
2. Bot asks: “What do you want alerts for?”
3. You record preferences manually in the database (`user_prefs`).
4. Alerts are filtered using these preferences before being sent.

This “done-for-you” model feels premium, builds trust, and teaches you what users actually want before you automate it.

---

## ⚙️ 4. `user_prefs` Table Schema

```sql
CREATE TABLE user_prefs (
    user_id         BIGINT PRIMARY KEY, -- Telegram user id
    tier            TEXT NOT NULL,      -- 'firehose', 'plus', 'private'
    min_roi         NUMERIC,
    max_buy_price   NUMERIC,
    categories      TEXT[],             -- ['bikes', 'consoles', 'retro_pc']
    model_keys      TEXT[],             -- ['ps5_disc', 'switch_oled', '3dfx_voodoo3']
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT now()
);
```

---

## 🧠 5. Telegram Command Interface (Phase 2)

Add self-service commands once a few users are onboarded:

- `/set_roi 30` → set minimum ROI
- `/set_max_buy 250` → set max buy price
- `/add_model ps5_disc` → add a model key
- `/remove_model ps5_disc`
- `/my_settings` → view current preferences

Later enhancements:
- Inline keyboards for category selection.
- Automatic tier detection from Stripe webhook data.

---

## 📤 6. Alert Flow Integration

Your alert code (e.g. `roi_listings.py`) should:

1. Query `user_prefs` for all active users.
2. For each user:
   - Filter listings where ROI ≥ `min_roi`
   - Filter listings where price ≤ `max_buy_price`
   - Filter by category/model key.
3. Send the message via Telegram to that user only.

This adds personalization without altering your ROI logic.

---

## 🧩 7. Affiliate Tracking

Add eBay Partner Network affiliate tags to links in messages.
- Each click/sale earns a small commission (1–3% typically).
- Can stack with paid subscriptions for passive income.

---

## 🚀 8. Growth Path

| Phase | Goal | Tools |
|-------|------|-------|
| 1 | Paid Telegram alerts, manual setup | Stripe, PostgreSQL |
| 2 | Telegram self-service commands | python-telegram-bot |
| 3 | Affiliate links | eBay Partner Network |
| 4 | Web dashboard | Flask + Render |
| 5 | Scale to 100+ paying users | Marketing + SEO + Partnerships |

---

## 🧨 9. Key Takeaways

- Start with **Firehose (Basic)** channel for reach.  
- Upgrade users to **Custom** and **Private** tiers for personalization.  
- You don’t need a GUI at first — a simple DB + Telegram commands works.  
- Affiliate links add passive income.

---

**Target milestone:** £1,000/month recurring (100 users × £10 avg).  
**Next step:** implement `user_prefs` table + basic Telegram commands.

---



Channel names:
GhostFrog eBay,
GhostFrog eBay Pro
GhostFrog eBay Elite
