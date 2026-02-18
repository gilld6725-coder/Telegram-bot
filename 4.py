import json
import os
import asyncio
from datetime import datetime, time as dtime
from telegram import Update # pyright: ignore[reportMissingImports]
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters # pyright: ignore[reportMissingImports]

# =============================
# CONFIG
# =============================
DATA_FILE = "attendance_records.json"
SALARY_FILE = "salary_records.json"
# Put your admin Telegram user IDs here:
ADMIN_IDS = [7958117532, 7432006334, 8382453545, 5752123952, 7435176780, 7938235371, 6076562401, 8380524265, 1596100597, 7889757304, 6003851152, 6826941532, 7488322832]


# Attendance windows (inclusive)
MORNING_START = dtime(10, 0, 0)
MORNING_END   = dtime(10, 30, 0)
EVENING_START = dtime(16, 30, 0)
EVENING_END   = dtime(17, 00, 0)
LATE_PENALTY = 50  # numeric amount; displayed as PKR 50

# =============================
# UTIL: load/save robustly+
# =============================
def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        try:
            os.rename(path, path + ".bak")
        except Exception:
            pass
        return {}

attendance_data = load_json(DATA_FILE)
salary_data = load_json(SALARY_FILE)

def save_all():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(attendance_data, f, indent=4, ensure_ascii=False)
    with open(SALARY_FILE, "w", encoding="utf-8") as f:
        json.dump(salary_data, f, indent=4, ensure_ascii=False)

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

# =============================
# HELPERS
# =============================
def current_session():
    t = datetime.now().time()
    if t < dtime(13, 0, 0):
        return "morning"
    return "evening"

def is_within_window(dt):
    t = dt.time()
    if MORNING_START <= t <= MORNING_END:
        return True
    if EVENING_START <= t <= EVENING_END:
        return True
    return False

def ensure_user_salary(group_salary: dict, uid: int, username: str):
    key = str(uid)
    if key not in group_salary:
        group_salary[key] = {"username": username or "unknown", "deductions": 0, "history": []}
    else:
        if username and not group_salary[key].get("username"):
            group_salary[key]["username"] = username
    return group_salary[key]

def ensure_day_structure(group_attendance: dict, date: str):
    if date not in group_attendance or not isinstance(group_attendance[date], dict):
        group_attendance[date] = {"morning": [], "evening": []}
    else:
        if "morning" not in group_attendance[date] or not isinstance(group_attendance[date]["morning"], list):
            group_attendance[date]["morning"] = []
        if "evening" not in group_attendance[date] or not isinstance(group_attendance[date]["evening"], list):
            group_attendance[date]["evening"] = []

def format_pkr(amount):
    return f"PKR {amount}"

# =============================
# COMMAND HANDLERS
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome!\n"
        "1 → Mark Attendance\n"
        "2 → Count Attendance\n"
        "3 → Show Attendance List\n"
        "4 → Show Salary Deductions (Admins only)\n"
        "5 → Clear Salary Deductions (Admins only)\n"
        "6 → Find Missing Attendance (Admins only — will deduct PKR 50 per missing)\n"
        "7 → Clear Today's 'missing' deductions (Admins only)\n"
        "0 → Clear Attendance (Admins only)\n"
        "/admins → View Admins"
    )

async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = []
    for aid in ADMIN_IDS:
        try:
            user = await context.bot.get_chat(aid)
            name = f"@{user.username}" if user.username else user.first_name
        except Exception:
            name = f"(ID:{aid})"
        lines.append(name)
    await update.message.reply_text("👑 Admins:\n" + "\n".join(lines))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user
    chat = update.effective_chat
    if not chat:
        return
    group_id = str(chat.id)
    date = today_str()
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S")
    session = current_session()

    # ensure structures
    group_att = attendance_data.setdefault(group_id, {})
    ensure_day_structure(group_att, date)
    day_rec = group_att[date]

    group_sal = salary_data.setdefault(group_id, {})

    # ensure user's salary record (but don't auto-deduct admins later)
    user_sal = ensure_user_salary(group_sal, user.id, user.username or user.first_name)

    # ===== 1: Mark attendance =====
    if text == "1":
        if any(e.get("user_id") == user.id for e in day_rec[session]):
            await update.message.reply_text(f"✅ You already marked {session} attendance today.")
            return

        entry = {
            "user_id": user.id,
            "username": user.username or user.first_name,
            "date": date,
            "time": time_str,
            "late": False,
            "session": session
        }

        # Admins are never deducted
        if user.id in ADMIN_IDS:
            msg = f"🎯 {session.capitalize()} attendance marked at {time_str} (admin — no deductions)."
        else:
            if not is_within_window(now):
                entry["late"] = True
                user_sal["deductions"] = user_sal.get("deductions", 0) + LATE_PENALTY
                user_sal.setdefault("history", []).append({"date": date, "amount": LATE_PENALTY, "reason": "late"})
                msg = f"⚠️ You are late for {session} session — {format_pkr(LATE_PENALTY)} deducted. Marked at {time_str}"
            else:
                msg = f"🎯 {session.capitalize()} attendance marked at {time_str}"

        day_rec[session].append(entry)
        save_all()
        await update.message.reply_text(msg)
        return

    # ===== 2: Count =====
    if text == "2":
        total = len(day_rec["morning"]) + len(day_rec["evening"])
        await update.message.reply_text(f"📊 Total attendance today: {total}")
        return

    # ===== 3: Show attendance list =====
    if text == "3":
        lines = [f"🗓 Attendance — {date}"]
        for s in ("morning", "evening"):
            ents = day_rec.get(s, [])
            lines.append(f"\n🔹 {s.capitalize()} ({len(ents)}):")
            for i, e in enumerate(ents, 1):
                status = "⏰ Late" if e.get("late") else "✅ On time"
                lines.append(f"{i}. @{e.get('username','unknown')} — {e.get('time','-')} ({status})")
        await update.message.reply_text("\n".join(lines))
        return

    # ===== 4: Show deductions (admins only) =====
    if text == "4":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Only admins can view deductions.")
            return
        if not group_sal:
            await update.message.reply_text("No deductions recorded for this group.")
            return
        lines = ["💰 Deductions for this group:"]
        total = 0
        for uid, rec in group_sal.items():
            lines.append(f"@{rec.get('username','unknown')}: {format_pkr(rec.get('deductions',0))}")
            total += rec.get('deductions',0)
        lines.append(f"\n🧾 Total deducted (group): {format_pkr(total)}")
        await update.message.reply_text("\n".join(lines))
        return

    # ===== 5: Clear all deductions (admins only) =====
    if text == "5":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Only admins can clear deductions.")
            return
        for rec in group_sal.values():
            rec["deductions"] = 0
            rec["history"] = []
        save_all()
        await update.message.reply_text("💸 All deductions cleared for this group.")
        return

    # ===== 6: Find missing attendance (admins only) & deduct =====
    if text == "6":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Only admins can use this command.")
            return

        # Build known members list (prefer salary_data)
        members = []
        if group_sal:
            for uid_str, rec in group_sal.items():
                members.append((int(uid_str), rec.get("username","unknown")))
        else:
            for day, recs in group_att.items():
                if isinstance(recs, dict):
                    for s in ("morning","evening"):
                        for e in recs.get(s, []):
                            members.append((e.get("user_id"), e.get("username")))
            members = list({(int(u), n) for u, n in members})

        if not members:
            await update.message.reply_text("No known members for this group (no historical data).")
            return

        marked_ids = {e.get("user_id") for e in day_rec.get(session, [])}
        missing = [(uid, uname) for uid, uname in members if uid not in marked_ids and uid not in ADMIN_IDS]

        if not missing:
            await update.message.reply_text(f"✅ Everyone (non-admins) marked {session} attendance today.")
            return

        # Deduct for missing and add history reason 'missing'
        for uid, uname in missing:
            rec = ensure_user_salary(group_sal, uid, uname)
            rec["deductions"] = rec.get("deductions",0) + LATE_PENALTY
            rec.setdefault("history", []).append({"date": today_str(), "amount": LATE_PENALTY, "reason": "missing"})

        save_all()

        lines = [f"⚠️ Missing {session.capitalize()} Attendance — {today_str()} (admins excluded)"]
        for i, (_, name) in enumerate(missing, 1):
            lines.append(f"{i}. @{name} — {format_pkr(LATE_PENALTY)} deducted (reason: missing)")
        await update.message.reply_text("\n".join(lines))
        return

    # ===== 7: Clear today's missing deductions (admins only) =====
    if text == "7":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Only admins can clear missing deductions.")
            return

        cleared = []
        for uid_str, rec in group_sal.items():
            hist = rec.get("history", [])
            to_remove = [h for h in hist if h.get("date") == today_str() and h.get("reason") == "missing"]
            if not to_remove:
                continue
            amount = sum(h.get("amount",0) for h in to_remove)
            rec["deductions"] = max(0, rec.get("deductions",0) - amount)
            rec["history"] = [h for h in hist if not (h.get("date")==today_str() and h.get("reason")=="missing")]
            cleared.append((rec.get("username","unknown"), amount))

        if not cleared:
            await update.message.reply_text("ℹ️ No today's 'missing' deductions found to clear.")
            return

        save_all()
        lines = ["✅ Cleared today's missing deductions:"]
        total_cleared = 0
        for uname, amt in cleared:
            lines.append(f"@{uname}: {format_pkr(amt)} cleared")
            total_cleared += amt
        lines.append(f"\n🧾 Total cleared: {format_pkr(total_cleared)}")
        await update.message.reply_text("\n".join(lines))
        return

    # ===== 0: Clear attendance (admins only) =====
    if text == "0":
        if user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ Only admins can clear attendance.")
            return
        attendance_data[group_id] = {}
        save_all()
        await update.message.reply_text("🧹 Attendance cleared for this group.")
        await asyncio.sleep(5)
        await update.message.reply_text("⏰ 5 seconds passed — new marks after this may be late / deducted.")
        return

# =============================
# MAIN
# =============================
def main():
    BOT_TOKEN = "8123610400:AAGqDgVHMcglf_6PEKwi-YX5E64h3phHHAs" or "YOUR_BOT_TOKEN_HERE"
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️ Warning: BOT_TOKEN is not set. Replace in code or set BOT_TOKEN env var.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admins", admins_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Attendance Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
