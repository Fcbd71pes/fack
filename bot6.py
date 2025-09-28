# bot.py - HAGU BOT - Complete Version 1.3 (User-Friendly Match Flow)
import logging
import random
import time
import re
import psycopg2
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telegram.helpers import escape_markdown
from telegram.error import TelegramError

# ---------------- CONFIG ----------------
BOT_TOKEN = "6356360750:AAE7MpI223usUbTheLo1f6ccLK8zRrOMI1Q"
ADMIN_IDS = [5172723202]
DB_URI = "postgresql://postgres.bwwtazybszxiettvzrlv:SOUROVs768768@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
CHANNEL_ID = -1002439749625

ENTRY_FEES = [10.0, 20.0, 40.0, 80.0, 100.0]
PRIZE_CUT_PERCENTAGE = 10
MATCH_DURATION_MINUTES = 12
MIN_DEPOSIT = 30.0
MIN_WITHDRAW = 100.0

REFERRAL_ENABLED = True
REFERRAL_COMMISSION_TK = 5.0
MIN_DEPOSIT_FOR_REFERRAL = 50.0

BKASH_NUMBER = "01914573762"
NAGAD_NUMBER = "01914573762"

(GET_IGN, GET_PHONE) = range(2)
(ASK_WITHDRAW_AMOUNT, ASK_WITHDRAW_DETAILS) = range(2, 4)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

deposit_pattern = re.compile(r'^([A-Za-z0-9\-]+)\s+([0-9]+(?:\.[0-9]{1,2})?)$')
room_code_pattern = re.compile(r'^\d{6,10}$')

# ----------------- Database & Helper Functions -----------------
def execute_query(query, params=(), fetch=None):
    conn = None
    try:
        conn = psycopg2.connect(DB_URI)
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch == 'one': result = cursor.fetchone()
        elif fetch == 'all': result = cursor.fetchall()
        else: result = None
        conn.commit()
    except Exception as e:
        logger.exception("DB error: %s", e)
        if conn: conn.rollback()
        result = None
    finally:
        if conn:
            cursor.close()
            conn.close()
    return result

def log_transaction(user_id, amount, tx_type, description=""):
    execute_query("INSERT INTO transactions (user_id, amount, type, description, created_at) VALUES (%s, %s, %s, %s, %s)", (user_id, amount, tx_type, description, int(time.time())))

async def safe_send_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return True
    except (TelegramError, Exception) as e:
        logger.error(f"Failed to send message to {chat_id}: {e}")
        return False

# ----------------- Static Commands, Registration & Main Menu -----------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*[HAGU BOT HELP]*\n\n"
        "🎮 *Play 1v1*: Find an opponent.\n"
        "💰 *My Wallet*: Check balance, deposit, or withdraw.\n"
        "🏆 *Leaderboard*: See top players.\n"
        "🎁 *Daily Bonus*: Claim daily bonus.\n"
        "🤝 *Refer & Earn*: Invite friends & earn.\n\n"
        "*/profile*: View your stats.\n"
        "*/rules*: Read game rules.\n"
        "*/support*: Contact admin for help."
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*[HAGU BOT RULES]*\n\n"
        "1. Be respectful.\n"
        "2. No cheating/hacking.\n"
        "3. Provide proof in disputes.\n"
        "4. Admin decisions are final.\n"
        f"5. Min deposit: {MIN_DEPOSIT:.2f} TK.\n"
        f"6. Min withdrawal: {MIN_WITHDRAW:.2f} TK."
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text_to_forward = update.message.text.replace("/support", "").strip()
    if not text_to_forward:
        await update.message.reply_text("Usage: `/support [Your Message]`")
        return
    for admin_id in ADMIN_IDS:
        await safe_send_message(context, admin_id, f"🆘 Support Request from {user.full_name} (`{user.id}`):\n\n`{text_to_forward}`", parse_mode='Markdown')
    await update.message.reply_text("✅ Your message has been sent to the admins.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (user.id,), fetch='one')
    if not rec:
        execute_query("INSERT INTO users (user_id, username, is_registered) VALUES (%s, %s, FALSE) ON CONFLICT (user_id) DO NOTHING", (user.id, user.username or user.first_name))
        is_registered = False
    else:
        is_registered = rec[0]
    if context.args and REFERRAL_ENABLED and not is_registered:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user.id:
                execute_query("UPDATE users SET referred_by = %s WHERE user_id = %s AND referred_by IS NULL", (referrer_id, user.id))
        except (ValueError, IndexError): pass
    if is_registered:
        await show_main_menu(update, context, "Welcome back!")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! Please send your In-Game Name (IGN):")
        return GET_IGN

async def get_ign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ign = update.message.text.strip()
    execute_query("UPDATE users SET ingame_name = %s WHERE user_id = %s", (ign, update.effective_user.id))
    await update.message.reply_text("Got it. Now, send your phone number (for payments):")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    phone = update.message.text.strip()
    execute_query("UPDATE users SET phone_number = %s, is_registered = TRUE WHERE user_id = %s", (phone, user.id))
    rec = execute_query("SELECT welcome_given FROM users WHERE user_id = %s", (user.id,), fetch='one')
    if rec and not rec[0]:
        bonus = 10.0
        execute_query("UPDATE users SET balance = balance + %s, welcome_given = TRUE WHERE user_id = %s", (bonus, user.id))
        log_transaction(user.id, bonus, 'bonus', 'Welcome Bonus')
        await update.message.reply_text(f"✅ Registration complete! You got a welcome bonus of {bonus:.2f} TK.")
    else:
        await update.message.reply_text("✅ Registration complete!")
    await show_main_menu(update, context, "Here is the main menu:")
    return ConversationHandler.END

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str):
    keyboard = [
        [InlineKeyboardButton("🎮 Play 1v1", callback_data='play_1v1')],
        [InlineKeyboardButton("💰 My Wallet", callback_data='my_wallet'), InlineKeyboardButton("➕ Deposit", callback_data='deposit')],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data='leaderboard'), InlineKeyboardButton("🎁 Daily Bonus", callback_data='daily_bonus')],
        [InlineKeyboardButton("🤝 Refer & Earn", callback_data='my_referrals')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        try: await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
        except TelegramError: pass
    else:
        await safe_send_message(context, update.effective_user.id, message, reply_markup=reply_markup)

# ----------------- Profile, Wallet, Deposit, Withdraw etc. -----------------
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rec = execute_query("SELECT ingame_name, wins, losses, balance, referral_balance FROM users WHERE user_id = %s", (user_id,), fetch='one')
    if not rec:
        await update.message.reply_text("You are not registered. Please /start."); return
    ign, wins, losses, balance, r_balance = rec
    wins, losses = wins or 0, losses or 0
    total_matches = wins + losses
    win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
    text = (
        f"👤 *Your Profile*\n\n"
        f"IGN: `{ign}`\n\n"
        f"📊 *Stats*\n"
        f"  - Total Matches: {total_matches}\n"
        f"  - Wins: {wins}, Losses: {losses}\n"
        f"  - Win Rate: {win_rate:.2f}%\n\n"
        f"💰 *Wallet*\n"
        f"  - Main Balance: {float(balance):.2f} TK\n"
        f"  - Referral Balance: {float(r_balance):.2f} TK"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    rec = execute_query("SELECT balance, referral_balance FROM users WHERE user_id = %s", (user_id,), fetch='one')
    balance, r_balance = (rec[0], rec[1]) if rec else (0.0, 0.0)
    text = f"💰 *Your Wallet*\n\nMain Balance: `{float(balance):.2f} TK`\nReferral Balance: `{float(r_balance):.2f} TK`"
    keyboard = [
        [InlineKeyboardButton("➕ Deposit", callback_data='deposit'), InlineKeyboardButton("➖ Withdraw", callback_data='withdraw_start')],
        [InlineKeyboardButton("📜 History", callback_data='tx_history')],
        [InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def transaction_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    txs = execute_query("SELECT type, amount, created_at FROM transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (user_id,), fetch='all')
    text = "📜 *Your Last 10 Transactions*\n\n"
    if not txs: text += "No transactions found."
    else:
        for tx_type, amount, ts in txs:
            dt = datetime.fromtimestamp(ts).strftime('%d %b, %I:%M %p')
            sign = '+' if float(amount) > 0 else ''
            text += f"`{dt}`\n_{tx_type.replace('_', ' ').title()}_: *{sign}{float(amount):.2f} TK*\n\n"
    keyboard = [[InlineKeyboardButton("⬅️ Back to Wallet", callback_data='my_wallet')]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_src = update.callback_query.message if update.callback_query else update.message
    await msg_src.reply_text(f"➕ *Deposit Instructions*\n\nSend to:\n• Bkash: `{BKASH_NUMBER}`\n• Nagad: `{NAGAD_NUMBER}`\n\nThen, reply with: `TXID Amount`\nEx: `ABC123XYZ 500`", parse_mode='Markdown')

async def handle_referral_commission(context: ContextTypes.DEFAULT_TYPE, depositor_id: int, deposit_amount: float):
    if not REFERRAL_ENABLED or deposit_amount < MIN_DEPOSIT_FOR_REFERRAL: return
    count_rec = execute_query("SELECT COUNT(*) FROM deposit_requests WHERE user_id = %s AND status = 'approved'", (depositor_id,), fetch='one')
    if count_rec and count_rec[0] == 1:
        ref_rec = execute_query("SELECT referred_by FROM users WHERE user_id = %s AND referred_by IS NOT NULL", (depositor_id,), fetch='one')
        if ref_rec:
            referrer_id = ref_rec[0]
            execute_query("UPDATE users SET referral_balance = referral_balance + %s WHERE user_id = %s", (REFERRAL_COMMISSION_TK, referrer_id))
            log_transaction(referrer_id, REFERRAL_COMMISSION_TK, 'referral_bonus', f'From user {depositor_id}')
            await safe_send_message(context, referrer_id, f"🎉 You earned {REFERRAL_COMMISSION_TK:.2f} TK! Your referral made their first qualifying deposit.")

async def deposit_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS: await query.answer("Unauthorized.", show_alert=True); return
    parts = query.data.split("|")
    action, target_user_id, txid, amount_str = parts[0], int(parts[1]), parts[2], float(parts[3])
    
    if action == "approve_dep":
        rec = execute_query("SELECT id FROM deposit_requests WHERE user_id = %s AND txid = %s AND status = 'pending' LIMIT 1", (target_user_id, txid), fetch='one')
        if not rec: await query.answer("Request already processed.", show_alert=True); return
        execute_query("UPDATE deposit_requests SET status = 'approved', processed_by = %s, processed_at = %s WHERE id = %s", (query.from_user.id, int(time.time()), rec[0]))
        execute_query("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount_str, target_user_id))
        log_transaction(target_user_id, amount_str, 'deposit', f'TXID: {txid}')
        await query.edit_message_text(f"✅ Approved deposit for user {target_user_id} ({amount_str:.2f} TK)")
        await safe_send_message(context, target_user_id, f"✅ Your deposit of {amount_str:.2f} TK has been approved.")
        await handle_referral_commission(context, target_user_id, amount_str)
    elif action == "reject_dep":
        execute_query("UPDATE deposit_requests SET status = 'rejected', processed_by = %s, processed_at = %s WHERE user_id = %s AND txid = %s AND status = 'pending'", (query.from_user.id, int(time.time()), target_user_id, txid))
        await query.edit_message_text(f"❌ Rejected deposit for user {target_user_id}")
        await safe_send_message(context, target_user_id, f"❌ Your deposit of {amount_str:.2f} TK was rejected. Contact /support for help.")

async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    balance = float(execute_query("SELECT balance FROM users WHERE user_id = %s", (user_id,), fetch='one')[0] or 0.0)
    if balance < MIN_WITHDRAW:
        await query.answer(f"❌ You need at least {MIN_WITHDRAW:.2f} TK to withdraw.", show_alert=True)
        return ConversationHandler.END
    await query.message.reply_text(f"Available for withdrawal: {balance:.2f} TK.\nHow much to withdraw?\n(Min: {MIN_WITHDRAW:.2f} TK)\n\nType /cancel to abort.")
    return ASK_WITHDRAW_AMOUNT

async def ask_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try: amount = float(update.message.text)
    except ValueError:
        await update.message.reply_text("Please enter a valid number."); return ASK_WITHDRAW_AMOUNT
    balance = float(execute_query("SELECT balance FROM users WHERE user_id = %s", (user_id,), fetch='one')[0] or 0.0)
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"Minimum withdrawal is {MIN_WITHDRAW:.2f} TK."); return ASK_WITHDRAW_AMOUNT
    if amount > balance:
        await update.message.reply_text("Cannot withdraw more than your balance."); return ASK_WITHDRAW_AMOUNT
    context.user_data['withdraw_amount'] = amount
    await update.message.reply_text("Great. Now, send payment details.\nExample: `Bkash 01712345678`")
    return ASK_WITHDRAW_DETAILS

async def ask_withdraw_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    details = update.message.text.strip().split()
    amount = context.user_data['withdraw_amount']
    if len(details) != 2 or not details[1].isdigit():
        await update.message.reply_text("Invalid format. Use: `Method Number`\nExample: `Nagad 01812345678`"); return ASK_WITHDRAW_DETAILS
    method, number = details[0].capitalize(), details[1]
    req_id = execute_query("INSERT INTO withdrawal_requests (user_id, amount, method, account_number, status, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id", (user.id, amount, method, number, 'pending', int(time.time())), fetch='one')[0]
    await update.message.reply_text("✅ Withdrawal request submitted.", reply_markup=ReplyKeyboardRemove())
    keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_wd|{req_id}|{user.id}|{amount}")], [InlineKeyboardButton("❌ Reject", callback_data=f"reject_wd|{req_id}|{user.id}|{amount}")]]
    for admin_id in ADMIN_IDS:
        await safe_send_message(context, admin_id, f"➖ *New Withdrawal Request* ➖\n\nUser: {user.full_name} (`{user.id}`)\nAmount: *{amount:.2f} TK*\nMethod: *{method}*\nNumber: `{number}`", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.clear()
    return ConversationHandler.END

async def withdrawal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS: await query.answer("Unauthorized.", show_alert=True); return
    parts = query.data.split("|")
    action, req_id, target_user_id, amount_str = parts[0], int(parts[1]), int(parts[2]), float(parts[3])
    
    rec = execute_query("SELECT status FROM withdrawal_requests WHERE id = %s", (req_id,), fetch='one')
    if not rec or rec[0] != 'pending':
        await query.answer("Request already processed.", show_alert=True); return
    
    if action == "approve_wd":
        execute_query("UPDATE users SET balance = balance - %s WHERE user_id = %s", (amount_str, target_user_id))
        execute_query("UPDATE withdrawal_requests SET status = 'approved', processed_by = %s, processed_at = %s WHERE id = %s", (query.from_user.id, int(time.time()), req_id))
        log_transaction(target_user_id, -amount_str, 'withdrawal', f'Request ID: {req_id}')
        await query.edit_message_text(f"✅ Approved withdrawal of {amount_str:.2f} TK for user {target_user_id}.")
        await safe_send_message(context, target_user_id, f"✅ Your withdrawal request for {amount_str:.2f} TK has been approved.")
    elif action == "reject_wd":
        execute_query("UPDATE withdrawal_requests SET status = 'rejected', processed_by = %s, processed_at = %s WHERE id = %s", (query.from_user.id, int(time.time()), req_id))
        await query.edit_message_text(f"❌ Rejected withdrawal for user {target_user_id}.")
        await safe_send_message(context, target_user_id, f"❌ Your withdrawal request for {amount_str:.2f} TK was rejected.")

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation canceled.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

async def daily_bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    rec = execute_query("SELECT last_daily_at FROM users WHERE user_id = %s", (user_id,), fetch='one')
    now_ts = int(time.time())
    if rec and rec[0] and now_ts - rec[0] < 24 * 3600:
        await query.answer("Already claimed in the last 24 hours.", show_alert=True); return
    bonus = 2.0
    execute_query("UPDATE users SET balance = balance + %s, last_daily_at = %s WHERE user_id = %s", (bonus, now_ts, user_id))
    log_transaction(user_id, bonus, 'bonus', 'Daily Bonus')
    await query.edit_message_text(f"🎁 You received a daily bonus of {bonus:.2f} TK!")
    await show_main_menu(update, context, "Main Menu:")

async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rows = execute_query("SELECT ingame_name, wins FROM users WHERE wins > 0 ORDER BY wins DESC NULLS LAST LIMIT 10", fetch='all')
    text = "🏆 *Leaderboard (Top 10 by Wins)*\n\n"
    if not rows: text += "No data yet."
    else:
        for i, (ign, wins) in enumerate(rows, 1): text += f"*{i}.* `{ign or 'N/A'}` — Wins: {wins or 0}\n"
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def my_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    stats = execute_query("SELECT referral_balance, (SELECT COUNT(*) FROM users WHERE referred_by = %s) FROM users WHERE user_id = %s", (user_id, user_id), fetch='one')
    r_balance, ref_count = (stats[0], stats[1]) if stats else (0.0, 0)
    text = (f"🤝 *Refer & Earn*\n\nShare your link. When your friend makes a first deposit of at least `{MIN_DEPOSIT_FOR_REFERRAL:.2f} TK`, you get `{REFERRAL_COMMISSION_TK:.2f} TK`!\n\n🔗 *Your Link:*\n`{referral_link}`\n\n📊 *Stats:*\n  - Total Referrals: *{ref_count}*\n  - Referral Earnings: *{float(r_balance):.2f} TK*")
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------- NEW/MODIFIED Matchmaking & Result Flow -----------------
async def play_1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [[InlineKeyboardButton(f"{int(f)} TK", callback_data=f"fee|{int(f)}")] for f in ENTRY_FEES]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='back_to_main')])
    await query.edit_message_text("Choose entry fee:", reply_markup=InlineKeyboardMarkup(keyboard))

async def fee_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    _, fee_str = query.data.split("|", 1)
    fee = float(fee_str)
    rec = execute_query("SELECT balance, referral_balance FROM users WHERE user_id = %s", (user_id,), fetch='one')
    total_balance = float(rec[0] or 0.0) + float(rec[1] or 0.0)
    if total_balance < fee:
        await query.answer("Insufficient balance.", show_alert=True)
        return
    await query.edit_message_text(
        f"✅ Balance checked! You have enough to play.\n\n⏳ Searching for a {int(fee)} TK match...\n\nYour fee will be deducted automatically when an opponent is found.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Search", callback_data='cancel_search')]])
    )
    opp = execute_query("SELECT user_id FROM matchmaking_queue WHERE fee = %s AND user_id != %s LIMIT 1", (fee, user_id), fetch='one')
    if opp:
        opp_id = opp[0]
        execute_query("DELETE FROM matchmaking_queue WHERE user_id IN (%s, %s)", (user_id, opp_id))
        match_id = f"m_{int(time.time())}"
        for pid in (user_id, opp_id):
            p_rec = execute_query("SELECT balance, referral_balance FROM users WHERE user_id = %s", (pid,), fetch='one')
            b, rb = float(p_rec[0] or 0.0), float(p_rec[1] or 0.0)
            if rb >= fee:
                execute_query("UPDATE users SET referral_balance = referral_balance - %s WHERE user_id = %s", (fee, pid))
            else:
                needed = fee - rb
                execute_query("UPDATE users SET referral_balance = 0, balance = balance - %s WHERE user_id = %s", (needed, pid))
            log_transaction(pid, -fee, 'match_fee', f'Match {match_id}')
        execute_query("INSERT INTO active_matches (match_id, player1_id, player2_id, fee, status, created_at) VALUES (%s, %s, %s, %s, %s, %s)", (match_id, user_id, opp_id, fee, 'waiting_for_code', int(time.time())))
        await safe_send_message(context, user_id, f"✅ Match found with user `{opp_id}`.\nPlease create the room and send the Room Code here.")
        await safe_send_message(context, opp_id, f"✅ Match found with user `{user_id}`.\nPlease wait for the room code.")
        try:
            opp_search_msg_id = context.user_data.pop(f'search_msg_{opp_id}', None)
            if opp_search_msg_id:
                await context.bot.edit_message_text("✅ Match found! Check your private messages.", chat_id=opp_id, message_id=opp_search_msg_id)
        except Exception: pass
    else:
        execute_query("INSERT INTO matchmaking_queue (user_id, fee, timestamp) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET fee = EXCLUDED.fee, timestamp = EXCLUDED.timestamp", (user_id, fee, int(time.time())))
        context.user_data[f'search_msg_{user_id}'] = query.message.message_id
        bot_username = (await context.bot.get_me()).username
        await safe_send_message(context, CHANNEL_ID, f"🔥 A player is looking for a {int(fee)} TK match! Join via @{bot_username}")

async def cancel_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    execute_query("DELETE FROM matchmaking_queue WHERE user_id = %s", (query.from_user.id,))
    await show_main_menu(update, context, "Search canceled.")

async def submit_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, match_id = query.data.split("|")
    rec = execute_query("SELECT player1_id, player2_id, status FROM active_matches WHERE match_id = %s", (match_id,), fetch='one')
    if not rec:
        await query.answer("Match not found.", show_alert=True); return
    p1, p2, status = rec
    if status != 'in_progress':
        await query.answer("Result submission is already active or finished.", show_alert=True); return
    await ask_for_result_logic(context, match_id, p1, p2)
    await query.message.edit_reply_markup(None)
    try: # Also remove the button from the opponent's chat
        opp_id = p2 if query.from_user.id == p1 else p1
        opp_msg_id = context.user_data.pop(f'submit_btn_msg_{opp_id}', None)
        if opp_msg_id:
            await context.bot.edit_message_reply_markup(chat_id=opp_id, message_id=opp_msg_id, reply_markup=None)
    except Exception: pass


async def ask_for_result_logic(context: ContextTypes.DEFAULT_TYPE, match_id, p1, p2):
    updated = execute_query("UPDATE active_matches SET status = 'waiting_for_result' WHERE match_id = %s AND status = 'in_progress' RETURNING status", (match_id,), fetch='one')
    if not updated: return # Already triggered
    jobs = context.job_queue.get_jobs_by_name(f"result_{match_id}")
    for job in jobs: job.schedule_removal()
    keyboard = [[InlineKeyboardButton("✅ I Won", callback_data=f"result|won|{match_id}")], [InlineKeyboardButton("❌ I Lost", callback_data=f"result|lost|{match_id}")]]
    await safe_send_message(context, p1, "🏁 Match finished! Please submit the result:", reply_markup=InlineKeyboardMarkup(keyboard))
    await safe_send_message(context, p2, "🏁 Match finished! Please submit the result:", reply_markup=InlineKeyboardMarkup(keyboard))

async def request_result_job(context: ContextTypes.DEFAULT_TYPE):
    match_id = context.job.data['match_id']
    rec = execute_query("SELECT player1_id, player2_id, status FROM active_matches WHERE match_id = %s", (match_id,), fetch='one')
    if rec and rec[2] == 'in_progress':
        logger.info(f"Timer expired for match {match_id}. Forcing result submission.")
        await ask_for_result_logic(context, match_id, rec[0], rec[1])

async def handle_text_or_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    disputed_match = execute_query("SELECT match_id, player1_id, player2_id FROM active_matches WHERE (player1_id = %s OR player2_id = %s) AND status = 'disputed_pending_screenshots'", (user.id, user.id), fetch='one')
    if disputed_match and update.message.photo:
        match_id, p1_id, p2_id = disputed_match
        await update.message.reply_text("✅ Screenshot received. Admin will review it.")
        p1_info = execute_query("SELECT ingame_name FROM users WHERE user_id = %s", (p1_id,), fetch='one')
        p2_info = execute_query("SELECT ingame_name FROM users WHERE user_id = %s", (p2_id,), fetch='one')
        ign = p1_info[0] if user.id == p1_id else p2_info[0]
        for admin_id in ADMIN_IDS:
            await safe_send_message(context, admin_id, f"🖼️ Screenshot for Disputed Match `{match_id}` from *{user.full_name}* (IGN: `{ign}`)", parse_mode='Markdown')
            await context.bot.forward_message(chat_id=admin_id, from_chat_id=user.id, message_id=update.message.message_id)
        return
    match_rec = execute_query("SELECT match_id, player1_id, player2_id FROM active_matches WHERE player1_id = %s AND status = 'waiting_for_code'", (user.id,), fetch='one')
    if match_rec and room_code_pattern.match(text):
        match_id, p1_id, p2_id = match_rec
        room_code = text
        execute_query("UPDATE active_matches SET status = 'in_progress', room_code = %s WHERE match_id = %s", (room_code, match_id))
        await update.message.reply_text(f"✅ Code `{room_code}` sent. Match started!", parse_mode='Markdown')
        await safe_send_message(context, p2_id, f"⚔️ Opponent sent the room code: `{room_code}`\n\nGood luck!", parse_mode='Markdown')
        keyboard = [[InlineKeyboardButton("🏁 Submit Result", callback_data=f"submit_result|{match_id}")]]
        msg1 = await safe_send_message(context, p1_id, "Your match is in progress. When finished, click below.", reply_markup=InlineKeyboardMarkup(keyboard))
        msg2 = await safe_send_message(context, p2_id, "Your match is in progress. When finished, click below.", reply_markup=InlineKeyboardMarkup(keyboard))
        if msg1: context.user_data[f'submit_btn_msg_{p1_id}'] = msg1.message_id
        if msg2: context.user_data[f'submit_btn_msg_{p2_id}'] = msg2.message_id
        context.job_queue.run_once(request_result_job, MATCH_DURATION_MINUTES * 60, data={'match_id': match_id}, name=f"result_{match_id}")
        return
    m = deposit_pattern.match(text)
    if m:
        txid, amount = m.group(1), float(m.group(2))
        execute_query("INSERT INTO deposit_requests (user_id, txid, amount, status, created_at) VALUES (%s, %s, %s, %s, %s)", (user.id, txid, amount, 'pending', int(time.time())))
        await update.message.reply_text("✅ Deposit request received. Admin will verify it shortly.")
        keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep|{user.id}|{txid}|{amount}")], [InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep|{user.id}|{txid}|{amount}")]]
        for aid in ADMIN_IDS:
            await safe_send_message(context, aid, f"🔔 New deposit request\nUser: {user.full_name} ({user.id})\nTXID: {txid}\nAmount: {amount:.2f} TK", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if not disputed_match:
        await safe_send_message(context, user.id, "Unknown command or format. Use /help to see available commands.")

async def result_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, outcome, match_id = query.data.split("|")
    user_id = query.from_user.id
    rec = execute_query("SELECT player1_id, player2_id, fee, status FROM active_matches WHERE match_id = %s", (match_id,), fetch='one')
    if not rec: await query.answer("Match not found.", show_alert=True); return
    p1, p2, fee, status = rec
    if status not in ('waiting_for_result', 'disputed_pending_screenshots'):
        await query.answer("Result submission is not active.", show_alert=True); return
    col_to_update = 'p1_result' if user_id == p1 else 'p2_result'
    execute_query(f"UPDATE active_matches SET {col_to_update} = %s WHERE match_id = %s", (outcome, match_id))
    await query.message.edit_text("Result recorded. Waiting for opponent...")
    rec2 = execute_query("SELECT p1_result, p2_result FROM active_matches WHERE match_id = %s", (match_id,), fetch='one')
    if rec2 and rec2[0] and rec2[1]:
        p1_res, p2_res = rec2
        winner, loser = (None, None)
        if (p1_res, p2_res) == ('won', 'lost'): winner, loser = p1, p2
        elif (p1_res, p2_res) == ('lost', 'won'): winner, loser = p2, p1
        if winner:
            prize = float(fee) * 2 * (1 - PRIZE_CUT_PERCENTAGE / 100)
            execute_query("UPDATE users SET balance = balance + %s, wins = wins + 1 WHERE user_id = %s", (prize, winner))
            execute_query("UPDATE users SET losses = losses + 1 WHERE user_id = %s", (loser,))
            log_transaction(winner, prize, 'prize_won', f'Match vs {loser}')
            await safe_send_message(context, winner, f"🏆 You won! Prize: {prize:.2f} TK")
            await safe_send_message(context, loser, "😔 You lost. Better luck next time.")
            execute_query("DELETE FROM active_matches WHERE match_id = %s", (match_id,))
        else:
            execute_query("UPDATE active_matches SET status = 'disputed_pending_screenshots' WHERE match_id = %s", (match_id,))
            msg = "❗️Result dispute! Please send a screenshot of the victory screen as proof. An admin will review it."
            await safe_send_message(context, p1, msg)
            await safe_send_message(context, p2, msg)
            p1_info = execute_query("SELECT username, ingame_name FROM users WHERE user_id = %s", (p1,), fetch='one')
            p2_info = execute_query("SELECT username, ingame_name FROM users WHERE user_id = %s", (p2,), fetch='one')
            p1_ign, p2_ign = p1_info[1] or "Player 1", p2_info[1] or "Player 2"
            admin_text = (f"🚨 *DISPUTE ALERT* 🚨\n\nMatch ID: `{match_id}`\nFee: {float(fee):.2f} TK\n\nPlayer 1: {p1_info[0] or 'N/A'} (IGN: *{p1_ign}*)\n`{p1}`\nPlayer 2: {p2_info[0] or 'N/A'} (IGN: *{p2_ign}*)\n`{p2}`\n\nChoose a winner:")
            keyboard = [[InlineKeyboardButton(f"🏆 {p1_ign} Won", callback_data=f"resolve|{match_id}|{p1}")], [InlineKeyboardButton(f"🏆 {p2_ign} Won", callback_data=f"resolve|{match_id}|{p2}")], [InlineKeyboardButton(f"✖️ Cancel Match (Refund)", callback_data=f"resolve|{match_id}|refund")]]
            for admin_id in ADMIN_IDS:
                await safe_send_message(context, admin_id, admin_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def resolve_dispute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_user = query.from_user
    if admin_user.id not in ADMIN_IDS: await query.answer("Unauthorized.", show_alert=True); return
    _, match_id, decision = query.data.split("|")
    rec = execute_query("SELECT player1_id, player2_id, fee FROM active_matches WHERE match_id = %s", (match_id,), fetch='one')
    if not rec:
        await query.edit_message_text(f"Match `{match_id}` already resolved.", parse_mode='Markdown'); return
    p1, p2, fee = rec
    fee = float(fee)
    if decision == "refund":
        for pid in [p1, p2]:
            execute_query("UPDATE users SET balance = balance + %s WHERE user_id = %s", (fee, pid))
            log_transaction(pid, fee, 'refund', f'Disputed match {match_id} canceled')
            await safe_send_message(context, pid, f"⚖️ Match `{match_id}` was canceled by an admin. Your fee of {fee:.2f} TK has been refunded.", parse_mode='Markdown')
        await query.edit_message_text(f"✅ Match `{match_id}` canceled. Players refunded by {admin_user.full_name}.", parse_mode='Markdown')
    else:
        winner_id = int(decision)
        loser_id = p2 if winner_id == p1 else p1
        prize = fee * 2 * (1 - PRIZE_CUT_PERCENTAGE / 100)
        execute_query("UPDATE users SET balance = balance + %s, wins = wins + 1 WHERE user_id = %s", (prize, winner_id))
        log_transaction(winner_id, prize, 'prize_won', f'Disputed match {match_id} won')
        execute_query("UPDATE users SET losses = losses + 1 WHERE user_id = %s", (loser_id,))
        await safe_send_message(context, winner_id, f"🏆 Admin declared you winner of match `{match_id}`! You received {prize:.2f} TK.", parse_mode='Markdown')
        await safe_send_message(context, loser_id, f"😔 Admin declared you lost match `{match_id}`.", parse_mode='Markdown')
        winner_info = execute_query("SELECT ingame_name FROM users WHERE user_id = %s", (winner_id,), fetch='one')
        await query.edit_message_text(f"✅ Dispute for `{match_id}` resolved by {admin_user.full_name}. Winner: *{winner_info[0]}*", parse_mode='Markdown')
    execute_query("DELETE FROM active_matches WHERE match_id = %s", (match_id,))

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'play_1v1': await play_1v1_callback(update, context)
    elif data.startswith('fee|'): await fee_choice_handler(update, context)
    elif data == 'my_wallet': await my_wallet(update, context)
    elif data == 'deposit': await deposit_start(update, context)
    elif data == 'tx_history': await transaction_history_callback(update, context)
    elif data.startswith('approve_dep') or data.startswith('reject_dep'): await deposit_callback_handler(update, context)
    elif data.startswith('approve_wd') or data.startswith('reject_wd'): await withdrawal_callback_handler(update, context)
    elif data == 'daily_bonus': await daily_bonus_callback(update, context)
    elif data == 'leaderboard': await leaderboard_callback(update, context)
    elif data == 'my_referrals': await my_referrals_callback(update, context)
    elif data.startswith('submit_result|'): await submit_result_callback(update, context)
    elif data.startswith('result|'): await result_callback_handler(update, context)
    elif data.startswith('resolve|'): await resolve_dispute_callback(update, context)
    elif data == 'cancel_search': await cancel_search_callback(update, context)
    elif data == 'back_to_main': await show_main_menu(update, context, "Main Menu:")
    else: logger.warning(f"Unknown callback: {data}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            GET_IGN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ign)],
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(withdraw_start, pattern='^withdraw_start$')],
        states={
            ASK_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_withdraw_amount)],
            ASK_WITHDRAW_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_withdraw_details)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    app.add_handler(reg_conv)
    app.add_handler(withdraw_conv)
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO & ~filters.COMMAND, handle_text_or_photo_messages))
    logger.info("Bot is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()