from __future__ import annotations

import os
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from infrastructure.db.schema import get_connection
from infrastructure.utils.logger import get_logger
from infrastructure.utils.emailer import send_email  # ⬅️ reuse this

from infrastructure.utils.telegram import send_telegram_message



logger = get_logger(__name__)
LONDON_TZ = ZoneInfo("Europe/London")

TO_EMAIL = os.environ.get("GF_REEM_EMAIL", "info@ghostfrog.co.uk")

# ============================================================
#  OPERATION REEM – DAILY PLAN (goes in every reminder email)
# ============================================================

REEM_DAILY_PLAN = """
OPERATION REEM – DAILY PLAN

Fasting:
- Eating window: 11:00–19:00.
- Outside that window: water, black coffee, or tea only.

Drinks:
- Aim for 3–4 pints of total water across the day.
- The Reem Shake™ = 1 scoop soy protein + 1 pint cold water.

----------------------------------------
🕛 DAILY STRUCTURE
----------------------------------------

11:00 – 🍳 Eggs + Toast
  - 2–3 eggs (boiled, poached, or scrambled with a little butter or spray oil)
  - 2 slices wholegrain or seeded bread
  - Optional veg (mushrooms, tomatoes, peppers, onions)
  - Coffee or tea as normal
  - Water: ½–1 pint

13:00 – 🥤 Reem Shake #1
  - 1 scoop soy protein + 1 pint water
  - Optional: 1–2 tbsp oats if you want it thicker or need extra energy

16:00 – 🥤 Reem Shake #2
  - 1 scoop soy protein + 1 pint water
  - Add a pinch of salt or small scoop of oats if you’ve trained

18:00 – 🍽 Main Meal (finish eating by 19:00)
  - Protein: 150–200 g mince, turkey, chicken, or eggs
  - Carbs: 1 cup cooked rice / noodles OR 2 slices of bread
  - Veg: peas, corn, onions, peppers, mushrooms – a generous handful
  - Fat: small drizzle of olive oil or bit of butter

19:00 onwards – 🚫 Kitchen Closed
  - Only water or herbal tea
  - If cravings hit: popcorn, pickles, or a Reem Shake tomorrow instead!

----------------------------------------
💧 HYDRATION
----------------------------------------
- 3–4 pints (≈2–2.5 L) total water per day.
- Add ½ pint of water per mug of coffee.
- Optional: one “Reem Pink” electrolyte drink if you’ve sweated or trained hard.

----------------------------------------
🏋️ TRAINING (Simple Daily Routine)
----------------------------------------
Every day (5–10 minutes total):

  - 3 × Push-ups (8–15 reps)
  - 3 × Kettlebell Squats (15 reps each set, using both 16 kg kettlebells)

Notes:
- Rest 30–60 seconds between sets.
- Do it daily, or skip a day if sore — consistency matters more than perfection.
- Optional: casual pull-ups any time (e.g. while making coffee).
- No gym, no setup — just get it done once a day.

----------------------------------------
🧠 MINDSET
----------------------------------------
- Coffee stays as normal.
- Stack small wins: water, shakes, one solid meal, quick daily routine.
- Every day from now to Dec 5 moves the dial a little more towards Reem.
"""


# =========================
#   EMAIL SENDER (simple)
# =========================

def _send_reminder_email(subject: str, instruction: str) -> None:
    """
    Use the shared send_email helper so Reem reminders behave like other alerts.
    """
    body_text = f"""{instruction}

----------------------------------------
DAILY PLAN (reminder every email)
----------------------------------------
{REEM_DAILY_PLAN}
"""

    try:
        send_email(
            subject=subject,
            body=body_text,
            to_addr=TO_EMAIL,
            is_html=False,  # plain text is fine; flip to True if you fancy HTML later
        )
        logger.info("[REEM] Email sent to %s: %s", TO_EMAIL, subject)

        send_telegram_message(f"📣 {subject}\n\n{body_text}")

        logger.info("[REEM] Telegram sent to %s: %s", TO_EMAIL, subject)

    except Exception as e:
        logger.exception("[REEM] Failed to send Operation Reem email: %s", e)


# =========================
#   REMINDER SCHEDULE
# =========================

REMINDERS = [
    {
        "key": "morning_water",
        "time": time(hour=9, minute=0),
        "text": "💧 Morning check: 1st pint of water. No food yet – fasting until 11:00.",
    },
    {
        "key": "break_fast_reem_shake",
        "time": time(hour=11, minute=0),
        "text": "🥤 Reem Shake #1 – break your fast: 1 scoop soy + 1 pint water.",
    },
    {
        "key": "afternoon_reem_shake_or_workout",
        "time": time(hour=15, minute=0),
        "text": (
            "🏋️ Afternoon block: Reem Shake #2 now, and if it’s a training day, "
            "get your weights session done (A/B rotation)."
        ),
    },
    {
        "key": "evening_meal",
        "time": time(hour=18, minute=0),
        "text": (
            "🍽️ Main meal window: protein + carbs + veg between 18:00–19:00. "
            "Remember: kitchen closes at 19:00."
        ),
    },
    {
        "key": "kitchen_closed",
        "time": time(hour=19, minute=15),
        "text": (
            "🚫 Kitchen closed: no food after 19:00. Water / herbal tea only now. "
            "If cravings hit, reach for a Reem Shake tomorrow, not snacks tonight."
        ),
    },
]


def _has_sent_reminder_today(conn, reminder_key: str, today: date) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM reem_reminder_log
            WHERE reminder_key = %s
              AND sent_on = %s
            """,
            (reminder_key, today),
        )
        return cur.fetchone() is not None


def _mark_reminder_sent(conn, reminder_key: str, today: date) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reem_reminder_log (reminder_key, sent_on)
            VALUES (%s, %s)
            ON CONFLICT (reminder_key, sent_on) DO NOTHING
            """,
            (reminder_key, today),
        )
    conn.commit()


def check_and_send_reem_reminders() -> None:
    """
    Entry point called from heartbeat. Uses current London time and only sends
    each reminder once per day, within a small time window.
    """
    try:
        conn = get_connection()
    except Exception as e:
        logger.exception("[REEM] Could not get DB connection for reminders: %s", e)
        return

    now = datetime.now(tz=LONDON_TZ)
    today = now.date()

    # If heartbeat is running very frequently, we don't want strict == HH:MM,
    # we allow e.g. ±15 minutes around the scheduled time.
    WINDOW_MINUTES = 15

    for r in REMINDERS:
        key = r["key"]
        target_t: time = r["time"]
        text = r["text"]

        target_dt = datetime.combine(today, target_t, tzinfo=LONDON_TZ)
        diff_min = abs((now - target_dt).total_seconds()) / 60.0

        if diff_min <= WINDOW_MINUTES:
            if _has_sent_reminder_today(conn, key, today):
                continue  # already sent this one today

            # Subject: try to keep it short but meaningful
            # e.g. "[Reem] 💧 Morning check"
            subject_prefix = text.split("–")[0].strip()  # before the en dash
            subject = f"[Reem] {subject_prefix}"

            _send_reminder_email(subject, text)
            _mark_reminder_sent(conn, key, today)

# agent/reminders/reem.py (at bottom of file)

def send_reem_test_email() -> None:
    instruction = (
        "🧪 TEST: This is a Reem reminder test email.\n\n"
        "If you can read this, send_email is working for Reem. "
        "Below is the full daily Operation Reem plan:"
    )
    subject = "[Reem] 🧪 Test reminder email"
    _send_reminder_email(subject, instruction)


if __name__ == "__main__":
    send_reem_test_email()