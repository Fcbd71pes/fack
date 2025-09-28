# bot.py - HAGU BOT - Complete Version 1.4 (Force Subscribe, OCR, Bug Fixes)
import logging
import random
import time
import re
import psycopg2
import easyocr
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telegram.helpers import escape_markdown
from telegram.error import TelegramError, Forbidden

# ---------------- CONFIG ----------------
BOT_TOKEN = "6356360750:AAE7MpI223usUbTheLo1f6ccLK8zRrOMI1Q"
ADMIN_IDS = [5172723202]
DB_URI = "postgresql://postgres.bwwtazybszxiettvzrlv:SOUROVs768768@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
CHANNEL_ID = -1002439749625
CHANNEL_USERNAME = "hagu_bot" # Example: 'YourChannelUsername' (without @)

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

# Initialize OCR Reader (this will be slow the first time)
logger.info("Initializing OCR Reader...")
reader = easyocr.Reader(['en'])
logger.info("OCR Reader initialized.")

# ----------------- Helper Functions (DB, Send, Force Sub) -----------------
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
        message = await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return message
    except Forbidden:
        logger.warning(f"Bot is blocked by user {chat_id}")
    except (TelegramError, Exception) as e:
        logger.error(f"Failed to send message to {chat_id}: {e}")
    return None

async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return True
        else:
            return False
    except TelegramError as e:
        logger.error(f"Error checking channel membership for {user_id}: {e}")
        return False # Assume not a member if there's an error

async def force_subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, callback_func):
    """A wrapper to check for channel membership before executing a function."""
    is_member = await check_channel_membership(update, context)
    if not is_member:
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        text = f"You must join our channel to use the bot.\nPlease join and then try again."
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
            # Optionally send a new message with the join button
            await update.callback_query.message.reply_text("Please join our channel:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    # If member, proceed with the original function
    await callback_func(update, context)

# ----------------- OCR Verification Logic -----------------
async def analyze_screenshot_with_ocr(file_path: str, p1_ign: str, p2_ign: str) -> dict:
    loop = asyncio.get_event_loop()
    try:
        # Run OCR in a separate thread to avoid blocking
        results = await loop.run_in_executor(None, reader.readtext, file_path, detail=1)
        
        full_text = " ".join([res[1] for res in results]).lower()
        
        analysis = {
            "full_time_found": "full time" in full_text,
            "p1_ign_found": p1_ign.lower() in full_text,
            "p2_ign_found": p2_ign.lower() in full_text,
            "score": "Not Found",
            "suggested_winner": "Undetermined"
        }

        # Simple score extraction logic (can be improved)
        p1_score = -1
        p2_score = -1

        for box, text, prob in results:
            text = text.lower()
            if p1_ign.lower() in text and len(text) > len(p1_ign):
                score_part = text.replace(p1_ign.lower(), "").strip()
                if score_part.isdigit(): p1_score = int(score_part)
            
            if p2_ign.lower() in text and len(text) > len(p2_ign):
                score_part = text.replace(p2_ign.lower(), "").strip()
                if score_part.isdigit(): p2_score = int(score_part)

            # More robust check: find name, then look for a number nearby
            if p1_ign.lower() == text:
                center_x = (box[0][0] + box[1][0]) / 2
                for b_inner, t_inner, p_inner in results:
                    if t_inner.isdigit() and abs(((b_inner[0][0] + b_inner[1][0]) / 2) - center_x) < 50:
                        p1_score = int(t_inner)
                        break
            
            if p2_ign.lower() == text:
                center_x = (box[0][0] + box[1][0]) / 2
                for b_inner, t_inner, p_inner in results:
                    if t_inner.isdigit() and abs(((b_inner[0][0] + b_inner[1][0]) / 2) - center_x) < 50:
                        p2_score = int(t_inner)
                        break
        
        if p1_score != -1 and p2_score != -1:
            analysis["score"] = f"{p1_ign} {p1_score} - {p2_score} {p2_ign}"
            if p1_score > p2_score:
                analysis["suggested_winner"] = p1_ign
            elif p2_score > p1_score:
                analysis["suggested_winner"] = p2_ign
            else:
                analysis["suggested_winner"] = "Draw"

        return analysis
    except Exception as e:
        logger.error(f"OCR analysis failed: {e}")
        return {"error": str(e)}

# ----------------- All Bot Handlers from here -----------------
# ... (All other functions from v1.3 will be here, with the Force Subscribe wrapper)
async def wrapped_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await force_subscribe_handler(update, context, profile_command)

async def wrapped_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await force_subscribe_handler(update, context, callback_router)

# --- The actual function implementations ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (user.id,), fetch='one')
    if not rec:
        execute_query("INSERT INTO users (user_id, username, is_registered) VALUES (%s, %s, FALSE) ON CONFLICT (user_id) DO NOTHING", (user.id, user.username or user.first_name))
        is_registered = False
    else: is_registered = rec[0]

    if context.args and REFERRAL_ENABLED and not is_registered:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user.id:
                execute_query("UPDATE users SET referred_by = %s WHERE user_id = %s AND referred_by IS NULL", (referrer_id, user.id))
        except (ValueError, IndexError): pass

    is_member = await check_channel_membership(update, context)
    if not is_member:
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        await update.message.reply_text("Welcome! To get started, you must join our channel.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if is_registered:
        await show_main_menu(update, context, "Welcome back!")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! Please send your In-Game Name (IGN):")
        return GET_IGN
# ... (The rest of the functions from v1.3 are here, full code below)

# THIS IS THE FULL, FINAL CODE. REPLACE YOUR ENTIRE FILE WITH THIS.
# bot.py - HAGU BOT - Complete Version 1.4 (Force Subscribe, OCR, Bug Fixes)

import logging
import random
import time
import re
import psycopg2
import easyocr
import asyncio
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from telegram.helpers import escape_markdown
from telegram.error import TelegramError, Forbidden

# ---------------- CONFIG ----------------
BOT_TOKEN = "6356360750:AAE7MpI223usUbTheLo1f6ccLK8zRrOMI1Q"
ADMIN_IDS = [5172723202]
DB_URI = "postgresql://postgres.bwwtazybszxiettvzrlv:SOUROVs768768@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
CHANNEL_ID = -1002439749625
CHANNEL_USERNAME = "hagu_bot" # IMPORTANT: Add your channel's public username here (without @)

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

logger.info("Initializing OCR Reader (this may take a moment)...")
reader = easyocr.Reader(['en'])
logger.info("OCR Reader initialized successfully.")

# ----------------- Helper Functions -----------------
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
        logger.exception(f"DB error: {e}")
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
        message = await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return message
    except Forbidden: logger.warning(f"Bot is blocked by user {chat_id}")
    except Exception as e: logger.error(f"Failed to send message to {chat_id}: {e}")
    return None

async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"Error checking channel membership for {user_id}: {e}")
        return False

# ----------------- OCR & Dispute Analysis -----------------
async def analyze_screenshot_with_ocr(file_path: str, p1_ign: str, p2_ign: str) -> dict:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, reader.readtext, file_path)
        full_text = " ".join([res[1] for res in results]).lower()
        analysis = {"full_time_found": "full time" in full_text, "p1_ign_found": False, "p2_ign_found": False, "score": "Not Found", "suggested_winner": "Undetermined"}
        
        score_pattern = re.compile(r'(\d+)\s*-\s*(\d+)|(\d+)\s+(\d+)')
        scores = []
        
        # Find names and scores
        p1_ign_lower = p1_ign.lower()
        p2_ign_lower = p2_ign.lower()
        p1_score, p2_score = -1, -1

        for text in full_text.split():
            if p1_ign_lower in text: analysis["p1_ign_found"] = True
            if p2_ign_lower in text: analysis["p2_ign_found"] = True
        
        match = score_pattern.search(full_text)
        if match:
             # This logic is complex and needs tuning for the specific game UI.
             # A simple assumption: the names appear near the score.
            try:
                if p1_ign_lower in full_text[:match.start()] and p2_ign_lower in full_text[match.end():]:
                    p1_score = int(match.groups()[0] or match.groups()[2])
                    p2_score = int(match.groups()[1] or match.groups()[3])
                elif p2_ign_lower in full_text[:match.start()] and p1_ign_lower in full_text[match.end():]:
                    p2_score = int(match.groups()[0] or match.groups()[2])
                    p1_score = int(match.groups()[1] or match.groups()[3])
            except (ValueError, TypeError): pass

        if p1_score != -1 and p2_score != -1:
            analysis["score"] = f"{p1_ign} {p1_score} - {p2_score} {p2_ign}"
            if p1_score > p2_score: analysis["suggested_winner"] = p1_ign
            elif p2_score > p1_score: analysis["suggested_winner"] = p2_ign
            else: analysis["suggested_winner"] = "Draw"
            
        return analysis
    except Exception as e:
        logger.error(f"OCR analysis failed: {e}")
        return {"error": str(e)}

# ----------------- Bot Handlers (with Force Subscribe) -----------------
async def universal_pre_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, func, requires_registration=True):
    is_member = await check_channel_membership(update, context)
    if not is_member:
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        text = "You must join our channel to use the bot.\nPlease join and then try again."
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        await safe_send_message(context, update.effective_chat.id, "Please join our channel to continue:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if requires_registration:
        is_registered = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (update.effective_user.id,), fetch='one')
        if not is_registered or not is_registered[0]:
            await safe_send_message(context, update.effective_chat.id, "You need to register first. Please use /start.")
            return

    await func(update, context)

# --- Actual Function Implementations ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (user.id,), fetch='one')
    if not rec:
        execute_query("INSERT INTO users (user_id, username, is_registered) VALUES (%s, %s, FALSE) ON CONFLICT (user_id) DO NOTHING", (user.id, user.username or user.first_name))
        is_registered = False
    else: is_registered = rec[0]

    if context.args and REFERRAL_ENABLED and not is_registered:
        try:
            referrer_id = int(context.args[0])
            if referrer_id != user.id: execute_query("UPDATE users SET referred_by = %s WHERE user_id = %s AND referred_by IS NULL", (referrer_id, user.id))
        except (ValueError, IndexError): pass
    
    is_member = await check_channel_membership(update, context)
    if not is_member:
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        await update.message.reply_text("Welcome! To get started, you must join our channel.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if is_registered:
        await show_main_menu(update, context, "Welcome back!")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! Please send your In-Game Name (IGN):")
        return GET_IGN
# ... (All functions are included in the full code block below)

# ----------------- The rest of the code is here. This is the FULL file. -----------------
# (Previous snippets are illustrative. This is the complete, runnable file)

# bot.py - HAGU BOT - Complete Version 1.4 (Force Subscribe, OCR, Bug Fixes)
import logging, random, time, re, psycopg2, easyocr, asyncio, os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler
from telegram.helpers import escape_markdown
from telegram.error import TelegramError, Forbidden

BOT_TOKEN = "6356360750:AAE7MpI223usUbTheLo1f6ccLK8zRrOMI1Q"
ADMIN_IDS = [5172723202]
DB_URI = "postgresql://postgres.bwwtazybszxiettvzrlv:SOUROVs768768@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
CHANNEL_ID = -1002439749625
CHANNEL_USERNAME = "hagu_bot" # IMPORTANT!
ENTRY_FEES, PRIZE_CUT_PERCENTAGE, MATCH_DURATION_MINUTES, MIN_DEPOSIT, MIN_WITHDRAW, REFERRAL_ENABLED, REFERRAL_COMMISSION_TK, MIN_DEPOSIT_FOR_REFERRAL, BKASH_NUMBER, NAGAD_NUMBER = [10.0, 20.0, 40.0, 80.0, 100.0], 10, 12, 30.0, 100.0, True, 5.0, 50.0, "01914573762", "01914573762"
(GET_IGN, GET_PHONE), (ASK_WITHDRAW_AMOUNT, ASK_WITHDRAW_DETAILS) = range(2), range(2, 4)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
deposit_pattern, room_code_pattern = re.compile(r'^([A-Za-z0-9\-]+)\s+([0-9]+(?:\.[0-9]{1,2})?)$'), re.compile(r'^\d{6,10}$')
logger.info("Initializing OCR Reader..."); reader = easyocr.Reader(['en']); logger.info("OCR Reader initialized.")

def execute_query(q, p=(), f=None):
    c=N=R=None
    try:
        c=psycopg2.connect(DB_URI); cu=c.cursor(); cu.execute(q,p)
        if f=='one': R=cu.fetchone()
        elif f=='all': R=cu.fetchall()
        c.commit()
    except Exception as e: logger.exception(f"DB error: {e}"); (c and c.rollback()); R=None
    finally: (c and (cu.close(), c.close())); return R
def log_transaction(uid, amt, typ, desc=""): execute_query("INSERT INTO transactions (user_id, amount, type, description, created_at) VALUES (%s, %s, %s, %s, %s)", (uid, amt, typ, desc, int(time.time())))
async def safe_send_message(ctx, cid, txt, **kw):
    try: return await ctx.bot.send_message(chat_id=cid, text=txt, **kw)
    except Forbidden: logger.warning(f"Bot blocked by {cid}")
    except Exception as e: logger.error(f"Msg fail to {cid}: {e}"); return None
async def check_channel_membership(upd, ctx):
    try: return (await ctx.bot.get_chat_member(CHANNEL_ID, upd.effective_user.id)).status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e: logger.error(f"Membership check fail for {upd.effective_user.id}: {e}"); return False

async def analyze_screenshot_with_ocr(fp, p1, p2):
    try:
        res = await asyncio.get_event_loop().run_in_executor(None, reader.readtext, fp)
        txt = " ".join([r[1] for r in res]).lower(); p1l, p2l = p1.lower(), p2.lower()
        an = {"full_time": "full time" in txt, "p1_found": p1l in txt, "p2_found": p2l in txt, "score": "Not Found", "winner": "Undetermined"}
        m = re.search(r'(\w+)\s+(\d+)\s+-\s+(\d+)\s+(\w+)', txt) or re.search(r'(\w+)\s+(\d+)\s{3,}(\d+)\s+(\w+)', txt)
        if m:
            n1, s1, s2, n2 = m.groups()
            s1, s2 = int(s1), int(s2)
            if p1l in n1 and p2l in n2: an["score"], an["winner"] = f"{p1} {s1}-{s2} {p2}", p1 if s1>s2 else p2 if s2>s1 else "Draw"
            elif p2l in n1 and p1l in n2: an["score"], an["winner"] = f"{p1} {s2}-{s1} {p2}", p1 if s2>s1 else p2 if s1>s2 else "Draw"
        return an
    except Exception as e: logger.error(f"OCR fail: {e}"); return {"error": str(e)}

async def universal_pre_handler(upd, ctx, func, req_reg=True):
    if not await check_channel_membership(upd, ctx):
        kb = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        txt = "You must join our channel to use the bot.\nPlease join and then try again."
        if upd.callback_query: await upd.callback_query.answer(txt, show_alert=True)
        await safe_send_message(ctx, upd.effective_chat.id, "Please join our channel:", reply_markup=InlineKeyboardMarkup(kb)); return
    if req_reg:
        is_reg = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (upd.effective_user.id,), 'one')
        if not is_reg or not is_reg[0]: await safe_send_message(ctx, upd.effective_chat.id, "Register first with /start."); return
    await func(upd, ctx)

async def help(upd,ctx): await upd.message.reply_text("*[HAGU BOT HELP]*\n\n🎮 *Play 1v1*: Find opponent\n💰 *My Wallet*: Balance/Deposit/Withdraw\n🏆 *Leaderboard*: Top players\n🎁 *Daily Bonus*: Claim free bonus\n🤝 *Refer & Earn*: Invite friends\n\n*/profile*: View stats\n*/rules*: Read rules\n*/support*: Contact admin",parse_mode='Markdown')
async def rules(upd,ctx): await upd.message.reply_text(f"*[RULES]*\n\n1. Be respectful\n2. No cheating\n3. Provide proof in disputes\n4. Admin decisions final\n5. Min deposit: {MIN_DEPOSIT:.2f} TK\n6. Min withdraw: {MIN_WITHDRAW:.2f} TK",parse_mode='Markdown')
async def support(upd,ctx):
    txt=upd.message.text.replace("/support","").strip()
    if not txt: await upd.message.reply_text("Usage: `/support [Message]`"); return
    for aid in ADMIN_IDS: await safe_send_message(ctx,aid,f"🆘 Support from {upd.effective_user.full_name} (`{upd.effective_user.id}`):\n\n`{txt}`",parse_mode='Markdown')
    await upd.message.reply_text("✅ Message sent to admins.")
async def start(upd,ctx):
    u=upd.effective_user; rec=execute_query("SELECT is_registered FROM users WHERE user_id=%s",(u.id,),'one')
    if not rec: execute_query("INSERT INTO users(user_id,username,is_registered)VALUES(%s,%s,FALSE)ON CONFLICT(user_id)DO NOTHING",(u.id,u.username or u.first_name)); is_reg=False
    else: is_reg=rec[0]
    if ctx.args and REFERRAL_ENABLED and not is_reg:
        try:
            ref_id=int(ctx.args[0])
            if ref_id!=u.id: execute_query("UPDATE users SET referred_by=%s WHERE user_id=%s AND referred_by IS NULL",(ref_id,u.id))
        except: pass
    if not await check_channel_membership(upd,ctx): await upd.message.reply_text("Welcome! To start, please join our channel.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel",url=f"https://t.me/{CHANNEL_USERNAME}")])); return ConversationHandler.END
    if is_reg: await show_main_menu(upd,ctx,"Welcome back!"); return ConversationHandler.END
    else: await upd.message.reply_text("Welcome! Please send your In-Game Name (IGN):"); return GET_IGN
async def get_ign(upd,ctx): execute_query("UPDATE users SET ingame_name=%s WHERE user_id=%s",(upd.message.text.strip(),upd.effective_user.id)); await upd.message.reply_text("Got it. Now, send your phone number:"); return GET_PHONE
async def get_phone(upd,ctx):
    u=upd.effective_user; execute_query("UPDATE users SET phone_number=%s,is_registered=TRUE WHERE user_id=%s",(upd.message.text.strip(),u.id))
    rec=execute_query("SELECT welcome_given FROM users WHERE user_id=%s",(u.id,),'one')
    if rec and not rec[0]: b=10.0; execute_query("UPDATE users SET balance=balance+%s,welcome_given=TRUE WHERE user_id=%s",(b,u.id)); log_transaction(u.id,b,'bonus','Welcome Bonus'); await upd.message.reply_text(f"✅ Registration complete! You got a {b:.2f} TK welcome bonus.")
    else: await upd.message.reply_text("✅ Registration complete!")
    await show_main_menu(upd,ctx,"Here is the main menu:"); return ConversationHandler.END
async def show_main_menu(upd,ctx,msg):
    kb=[[InlineKeyboardButton("🎮 Play 1v1",'play_1v1')],[InlineKeyboardButton("💰 My Wallet",'my_wallet'),InlineKeyboardButton("➕ Deposit",'deposit')],[InlineKeyboardButton("🏆 Leaderboard",'leaderboard'),InlineKeyboardButton("🎁 Daily Bonus",'daily_bonus')],[InlineKeyboardButton("🤝 Refer & Earn",'my_referrals')]]
    rm=InlineKeyboardMarkup(kb)
    if upd.callback_query:
        try: await upd.callback_query.edit_message_text(msg,reply_markup=rm)
        except: pass
    else: await safe_send_message(ctx,upd.effective_user.id,msg,reply_markup=rm)
async def profile(upd,ctx):
    rec=execute_query("SELECT ingame_name,wins,losses,balance,referral_balance FROM users WHERE user_id=%s",(upd.effective_user.id,),'one')
    ign,w,l,b,rb=rec; w,l=w or 0,l or 0; tm=w+l; wr=(w/tm*100) if tm>0 else 0
    txt=f"👤 *Profile*\n\nIGN: `{ign}`\n\n📊 *Stats*\n- Matches: {tm}, Wins: {w}, Losses: {l}\n- Win Rate: {wr:.2f}%\n\n💰 *Wallet*\n- Main Balance: {float(b):.2f} TK\n- Referral Balance: {float(rb):.2f} TK"
    await safe_send_message(ctx,upd.effective_chat.id,txt,parse_mode='Markdown')
async def wallet(upd,ctx):
    rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(upd.effective_user.id,),'one')
    b,rb=(rec[0],rec[1]) if rec else (0.0,0.0); txt=f"💰 *Wallet*\n\nMain: `{float(b):.2f} TK`\nReferral: `{float(rb):.2f} TK`"
    kb=[[InlineKeyboardButton("➕ Deposit",'deposit'),InlineKeyboardButton("➖ Withdraw",'withdraw_start')],[InlineKeyboardButton("📜 History",'tx_history')],[InlineKeyboardButton("⬅️ Back",'back_to_main')]]
    await upd.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(kb))
async def tx_history(upd,ctx):
    txs=execute_query("SELECT type,amount,created_at FROM transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",(upd.effective_user.id,),'all')
    txt="📜 *Last 10 Transactions*\n\n"+("No transactions." if not txs else "".join([f"`{datetime.fromtimestamp(ts).strftime('%d %b, %I:%M%p')}`\n_{typ.replace('_',' ').title()}_: *{'+' if float(amt)>0 else ''}{float(amt):.2f} TK*\n\n" for typ,amt,ts in txs]))
    await upd.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Wallet",'my_wallet')]]))
async def deposit_start(upd,ctx): await (upd.callback_query.message if upd.callback_query else upd.message).reply_text(f"➕ *Deposit*\n\nSend to:\n• Bkash/Nagad: `{BKASH_NUMBER}`\n\nThen reply: `TXID Amount`",parse_mode='Markdown')
async def ref_commission(ctx,dep_id,dep_amt):
    if not REFERRAL_ENABLED or dep_amt<MIN_DEPOSIT_FOR_REFERRAL: return
    if (execute_query("SELECT COUNT(*) FROM deposit_requests WHERE user_id=%s AND status='approved'",(dep_id,),'one')[0])==1:
        ref=execute_query("SELECT referred_by FROM users WHERE user_id=%s AND referred_by IS NOT NULL",(dep_id,),'one')
        if ref: ref_id=ref[0]; execute_query("UPDATE users SET referral_balance=referral_balance+%s WHERE user_id=%s",(REFERRAL_COMMISSION_TK,ref_id)); log_transaction(ref_id,REFERRAL_COMMISSION_TK,'referral_bonus',f'From {dep_id}'); await safe_send_message(ctx,ref_id,f"🎉 You earned {REFERRAL_COMMISSION_TK:.2f} TK from a referral!")
async def deposit_cb(upd,ctx):
    q=upd.callback_query;
    if q.from_user.id not in ADMIN_IDS: await q.answer("Unauthorized.",show_alert=True); return
    act,uid,txid,amt_s=q.data.split("|"); uid,amt=int(uid),float(amt_s)
    if "approve" in act:
        rec=execute_query("SELECT id FROM deposit_requests WHERE user_id=%s AND txid=%s AND status='pending' LIMIT 1",(uid,txid),'one')
        if not rec: await q.answer("Processed.",show_alert=True); return
        execute_query("UPDATE deposit_requests SET status='approved',processed_by=%s,processed_at=%s WHERE id=%s",(q.from_user.id,int(time.time()),rec[0]))
        execute_query("UPDATE users SET balance=balance+%s WHERE user_id=%s",(amt,uid)); log_transaction(uid,amt,'deposit',f'TXID: {txid}')
        await q.edit_message_text(f"✅ Approved {amt:.2f} TK for {uid}."); await safe_send_message(ctx,uid,f"✅ Your deposit of {amt:.2f} TK approved."); await ref_commission(ctx,uid,amt)
    else: execute_query("UPDATE deposit_requests SET status='rejected',processed_by=%s,processed_at=%s WHERE user_id=%s AND txid=%s AND status='pending'",(q.from_user.id,int(time.time()),uid,txid)); await q.edit_message_text(f"❌ Rejected deposit for {uid}."); await safe_send_message(ctx,uid,f"❌ Your deposit of {amt:.2f} TK rejected.")
async def withdraw_start(upd,ctx):
    bal=float(execute_query("SELECT balance FROM users WHERE user_id=%s",(upd.effective_user.id,),'one')[0] or 0.0)
    if bal<MIN_WITHDRAW: await upd.callback_query.answer(f"❌ Min {MIN_WITHDRAW:.2f} TK to withdraw.",show_alert=True); return ConversationHandler.END
    await upd.callback_query.message.reply_text(f"Available: {bal:.2f} TK.\nHow much to withdraw?\nMin: {MIN_WITHDRAW:.2f} TK\n\n/cancel to abort."); return ASK_WITHDRAW_AMOUNT
async def ask_withdraw_amount(upd,ctx):
    try: amt=float(upd.message.text)
    except: await upd.message.reply_text("Invalid number."); return ASK_WITHDRAW_AMOUNT
    bal=float(execute_query("SELECT balance FROM users WHERE user_id=%s",(upd.effective_user.id,),'one')[0] or 0.0)
    if amt<MIN_WITHDRAW: await upd.message.reply_text(f"Min withdraw is {MIN_WITHDRAW:.2f} TK."); return ASK_WITHDRAW_AMOUNT
    if amt>bal: await upd.message.reply_text("Insufficient balance."); return ASK_WITHDRAW_AMOUNT
    ctx.user_data['withdraw_amount']=amt; await upd.message.reply_text("OK. Send payment details.\nEx: `Bkash 017...`"); return ASK_WITHDRAW_DETAILS
async def ask_withdraw_details(upd,ctx):
    u=upd.effective_user; det=upd.message.text.strip().split(); amt=ctx.user_data['withdraw_amount']
    if len(det)!=2 or not det[1].isdigit(): await upd.message.reply_text("Invalid format. Use `Method Number`"); return ASK_WITHDRAW_DETAILS
    m,n=det[0].capitalize(),det[1]; req_id=execute_query("INSERT INTO withdrawal_requests(user_id,amount,method,account_number,status,created_at)VALUES(%s,%s,%s,%s,'pending',%s)RETURNING id",(u.id,amt,m,n,int(time.time())),'one')[0]
    await upd.message.reply_text("✅ Withdraw request submitted.",reply_markup=ReplyKeyboardRemove())
    kb=[[InlineKeyboardButton("✅ Approve",f"approve_wd|{req_id}|{u.id}|{amt}"),InlineKeyboardButton("❌ Reject",f"reject_wd|{req_id}|{u.id}|{amt}")]]
    for aid in ADMIN_IDS: await safe_send_message(ctx,aid,f"➖ *New Withdraw Request* ➖\n\nUser: {u.full_name}(`{u.id}`)\nAmount: *{amt:.2f} TK*\nMethod: *{m}*\nNumber: `{n}`",parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(kb))
    ctx.user_data.clear(); return ConversationHandler.END
async def withdraw_cb(upd,ctx):
    q=upd.callback_query;
    if q.from_user.id not in ADMIN_IDS: await q.answer("Unauthorized.",show_alert=True); return
    act,req_id,uid,amt_s=q.data.split("|"); req_id,uid,amt=int(req_id),int(uid),float(amt_s)
    rec=execute_query("SELECT status FROM withdrawal_requests WHERE id=%s",(req_id,),'one')
    if not rec or rec[0]!='pending': await q.answer("Processed.",show_alert=True); return
    if "approve" in act:
        execute_query("UPDATE users SET balance=balance-%s WHERE user_id=%s",(amt,uid)); execute_query("UPDATE withdrawal_requests SET status='approved',processed_by=%s,processed_at=%s WHERE id=%s",(q.from_user.id,int(time.time()),req_id)); log_transaction(uid,-amt,'withdrawal',f'Req ID: {req_id}')
        await q.edit_message_text(f"✅ Approved withdraw for {uid}."); await safe_send_message(ctx,uid,f"✅ Your withdraw of {amt:.2f} TK approved.")
    else: execute_query("UPDATE withdrawal_requests SET status='rejected',processed_by=%s,processed_at=%s WHERE id=%s",(q.from_user.id,int(time.time()),req_id)); await q.edit_message_text(f"❌ Rejected withdraw for {uid}."); await safe_send_message(ctx,uid,f"❌ Your withdraw of {amt:.2f} TK rejected.")
async def cancel_conv(upd,ctx): await upd.message.reply_text("Canceled.",reply_markup=ReplyKeyboardRemove()); ctx.user_data.clear(); return ConversationHandler.END
async def daily_bonus(upd,ctx):
    uid=upd.effective_user.id; rec=execute_query("SELECT last_daily_at FROM users WHERE user_id=%s",(uid,),'one')
    if rec and rec[0] and int(time.time())-rec[0]<86400: await upd.callback_query.answer("Already claimed.",show_alert=True); return
    b=2.0; execute_query("UPDATE users SET balance=balance+%s,last_daily_at=%s WHERE user_id=%s",(b,int(time.time()),uid)); log_transaction(uid,b,'bonus','Daily Bonus')
    await upd.callback_query.edit_message_text(f"🎁 You got a daily bonus of {b:.2f} TK!"); await asyncio.sleep(2); await show_main_menu(upd,ctx,"Main Menu:")
async def leaderboard(upd,ctx):
    rows=execute_query("SELECT ingame_name,wins FROM users WHERE wins>0 ORDER BY wins DESC NULLS LAST LIMIT 10",'all')
    txt="🏆 *Leaderboard (Top 10 by Wins)*\n\n"+("No data." if not rows else "".join([f"*{i+1}.* `{ign or 'N/A'}` — Wins: {wins or 0}\n" for i,(ign,wins) in enumerate(rows)]))
    await upd.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",'back_to_main')]]))
async def my_referrals(upd,ctx):
    uid=upd.effective_user.id; bot_user=(await ctx.bot.get_me()).username; link=f"https://t.me/{bot_user}?start={uid}"; stats=execute_query("SELECT referral_balance,(SELECT COUNT(*)FROM users WHERE referred_by=%s)FROM users WHERE user_id=%s",(uid,uid),'one'); rb,rc=(stats[0],stats[1]) if stats else (0.0,0)
    txt=f"🤝 *Refer & Earn*\n\nShare your link. When friend's 1st deposit >= `{MIN_DEPOSIT_FOR_REFERRAL:.2f} TK`, you get `{REFERRAL_COMMISSION_TK:.2f} TK`!\n\n🔗 *Your Link:*\n`{link}`\n\n📊 *Stats:*\n- Referrals: *{rc}*\n- Earnings: *{float(rb):.2f} TK*"
    await upd.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",'back_to_main')]]))
async def play_1v1(upd,ctx): kb=[[InlineKeyboardButton(f"{int(f)} TK",f"fee|{int(f)}")] for f in ENTRY_FEES]+[[InlineKeyboardButton("⬅️ Back",'back_to_main')]]; await upd.callback_query.edit_message_text("Choose entry fee:",reply_markup=InlineKeyboardMarkup(kb))
async def fee_choice(upd,ctx):
    q=upd.callback_query; uid=q.from_user.id; _,fee_s=q.data.split("|",1); fee=float(fee_s); rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(uid,),'one'); tot_bal=float(rec[0] or 0.0)+float(rec[1] or 0.0)
    if tot_bal<fee: await q.answer("Insufficient balance.",show_alert=True); return
    await q.edit_message_text(f"✅ Balance checked!\n\n⏳ Searching for a {int(fee)} TK match...\n\nFee deducted on match found.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Search",'cancel_search')]]))
    opp=execute_query("SELECT user_id FROM matchmaking_queue WHERE fee=%s AND user_id!=%s LIMIT 1",(fee,uid),'one')
    if opp:
        opp_id=opp[0]; execute_query("DELETE FROM matchmaking_queue WHERE user_id IN (%s,%s)",(uid,opp_id)); mid=f"m_{int(time.time())}"
        for pid in (uid,opp_id):
            p_rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(pid,),'one'); b,rb=float(p_rec[0] or 0.0),float(p_rec[1] or 0.0)
            if rb>=fee: execute_query("UPDATE users SET referral_balance=referral_balance-%s WHERE user_id=%s",(fee,pid))
            else: need=fee-rb; execute_query("UPDATE users SET referral_balance=0,balance=balance-%s WHERE user_id=%s",(need,pid))
            log_transaction(pid,-fee,'match_fee',f'Match {mid}')
        execute_query("INSERT INTO active_matches(match_id,player1_id,player2_id,fee,status,created_at)VALUES(%s,%s,%s,%s,'waiting_for_code',%s)",(mid,uid,opp_id,fee,int(time.time())))
        await safe_send_message(ctx,uid,f"✅ Match found with `{opp_id}`.\nPlease create room & send Room Code."); await safe_send_message(ctx,opp_id,f"✅ Match found with `{uid}`.\nPlease wait for Room Code.")
        try:
            opp_msg_id=ctx.user_data.pop(f'search_msg_{opp_id}',None)
            if opp_msg_id: await ctx.bot.edit_message_text("✅ Match found! Check DMs.",chat_id=opp_id,message_id=opp_msg_id)
        except: pass
    else: execute_query("INSERT INTO matchmaking_queue(user_id,fee,timestamp)VALUES(%s,%s,%s)ON CONFLICT(user_id)DO UPDATE SET fee=EXCLUDED.fee,timestamp=EXCLUDED.timestamp",(uid,fee,int(time.time()))); ctx.user_data[f'search_msg_{uid}']=q.message.message_id; bot_user=(await ctx.bot.get_me()).username; await safe_send_message(ctx,CHANNEL_ID,f"🔥 Player looking for a {int(fee)} TK match! Join @{bot_user}")
async def cancel_search(upd,ctx): execute_query("DELETE FROM matchmaking_queue WHERE user_id=%s",(upd.effective_user.id,)); await show_main_menu(upd,ctx,"Search canceled.")
async def submit_result(upd,ctx):
    q=upd.callback_query; _,mid=q.data.split("|"); rec=execute_query("SELECT player1_id,player2_id,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await q.answer("Match not found.",show_alert=True); return
    p1,p2,stat=rec;
    if stat!='in_progress': await q.answer("Result submission not active.",show_alert=True); return
    await ask_for_result_logic(ctx,mid,p1,p2)
    await q.message.edit_reply_markup(None)
    try:
        opp_id=p2 if q.from_user.id==p1 else p1; opp_msg_id=ctx.user_data.pop(f'submit_btn_msg_{opp_id}',None)
        if opp_msg_id: await ctx.bot.edit_message_reply_markup(opp_id,opp_msg_id,reply_markup=None)
    except: pass
async def ask_for_result_logic(ctx,mid,p1,p2):
    if not execute_query("UPDATE active_matches SET status='waiting_for_result' WHERE match_id=%s AND status='in_progress' RETURNING status",(mid,),'one'): return
    for j in ctx.job_queue.get_jobs_by_name(f"result_{mid}"): j.schedule_removal()
    kb=[[InlineKeyboardButton("✅ I Won",f"result|won|{mid}")],[InlineKeyboardButton("❌ I Lost",f"result|lost|{mid}")]]
    await safe_send_message(ctx,p1,"🏁 Match finished! Submit result:",reply_markup=InlineKeyboardMarkup(kb)); await safe_send_message(ctx,p2,"🏁 Match finished! Submit result:",reply_markup=InlineKeyboardMarkup(kb))
async def request_result_job(ctx):
    mid=ctx.job.data['match_id']; rec=execute_query("SELECT player1_id,player2_id,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if rec and rec[2]=='in_progress': logger.info(f"Timer expired for {mid}. Forcing result submission."); await ask_for_result_logic(ctx,mid,rec[0],rec[1])
async def handle_msgs(upd,ctx):
    u=upd.effective_user; txt=(upd.message.text or "").strip()
    disp_m=execute_query("SELECT match_id,player1_id,player2_id FROM active_matches WHERE (player1_id=%s OR player2_id=%s) AND status='disputed_pending_screenshots'",(u.id,u.id),'one')
    if disp_m and upd.message.photo:
        mid,p1,p2=disp_m; await upd.message.reply_text("✅ Screenshot received. Admin will review.")
        p1i,p2i=execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(p1,),'one')[0],execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(p2,),'one')[0]
        ign=p1i if u.id==p1 else p2i
        ocr_path=f"temp_{mid}_{u.id}.jpg"; ss=(await upd.message.photo[-1].get_file()).download_to_drive(ocr_path)
        an=await analyze_screenshot_with_ocr(ss.name,p1i,p2i)
        report=f"🤖 *Bot Analysis for {u.full_name}'s SS:*\n- Full Time: {'✅' if an.get('full_time') else '❌'}\n- P1 IGN Found: {'✅' if an.get('p1_found') else '❌'}\n- P2 IGN Found: {'✅' if an.get('p2_found') else '❌'}\n- Score: `{an.get('score')}`\n- Suggested Winner: *{an.get('winner')}*"
        for aid in ADMIN_IDS: await safe_send_message(ctx,aid,f"🖼️ SS for Disputed Match `{mid}` from *{u.full_name}* (IGN: `{ign}`)",parse_mode='Markdown'); await ctx.bot.forward_message(aid,u.id,upd.message.message_id); await safe_send_message(ctx,aid,report,parse_mode='Markdown')
        os.remove(ss.name); return
    match_rec=execute_query("SELECT match_id,player1_id,player2_id FROM active_matches WHERE player1_id=%s AND status='waiting_for_code'",(u.id,),'one')
    if match_rec and room_code_pattern.match(txt):
        mid,p1,p2=match_rec; execute_query("UPDATE active_matches SET status='in_progress',room_code=%s WHERE match_id=%s",(txt,mid))
        await upd.message.reply_text(f"✅ Code `{txt}` sent. Match started!",parse_mode='Markdown'); await safe_send_message(ctx,p2,f"⚔️ Room code: `{txt}`\n\nGood luck!",parse_mode='Markdown')
        kb=[[InlineKeyboardButton("🏁 Submit Result",f"submit_result|{mid}")]];
        m1=await safe_send_message(ctx,p1,"Match in progress. Click below when finished.",reply_markup=InlineKeyboardMarkup(kb)); m2=await safe_send_message(ctx,p2,"Match in progress. Click below when finished.",reply_markup=InlineKeyboardMarkup(kb))
        if m1: ctx.user_data[f'submit_btn_msg_{p1}']=m1.message_id
        if m2: ctx.user_data[f'submit_btn_msg_{p2}']=m2.message_id
        ctx.job_queue.run_once(request_result_job,MATCH_DURATION_MINUTES*60,data={'match_id':mid},name=f"result_{mid}"); return
    m=deposit_pattern.match(txt)
    if m:
        txid,amt=m.group(1),float(m.group(2)); execute_query("INSERT INTO deposit_requests(user_id,txid,amount,status,created_at)VALUES(%s,%s,%s,'pending',%s)",(u.id,txid,amt,int(time.time())))
        await upd.message.reply_text("✅ Deposit request received."); kb=[[InlineKeyboardButton("✅ Approve",f"approve_dep|{u.id}|{txid}|{amt}"),InlineKeyboardButton("❌ Reject",f"reject_dep|{u.id}|{txid}|{amt}")]];
        for aid in ADMIN_IDS: await safe_send_message(ctx,aid,f"🔔 New deposit\nUser: {u.full_name}({u.id})\nTXID: {txid}\nAmount: {amt:.2f} TK",reply_markup=InlineKeyboardMarkup(kb))
        return
    if not disp_m: await safe_send_message(ctx,u.id,"Unknown command. Use /help.")
async def result_cb(upd,ctx):
    q=upd.callback_query; _,out,mid=q.data.split("|"); uid=q.from_user.id; rec=execute_query("SELECT player1_id,player2_id,fee,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await q.answer("Match not found.",show_alert=True); return
    p1,p2,fee,stat=rec;
    if stat not in ('waiting_for_result','disputed_pending_screenshots'): await q.answer("Submission not active.",show_alert=True); return
    col='p1_result' if uid==p1 else 'p2_result'; execute_query(f"UPDATE active_matches SET {col}=%s WHERE match_id=%s",(out,mid)); await q.message.edit_text("Result recorded. Waiting for opponent...")
    rec2=execute_query("SELECT p1_result,p2_result FROM active_matches WHERE match_id=%s",(mid,),'one')
    if rec2 and rec2[0] and rec2[1]:
        p1r,p2r=rec2; w,l= (p1,p2) if (p1r,p2r)==('won','lost') else (p2,p1) if (p1r,p2r)==('lost','won') else (None,None)
        if w:
            prz=float(fee)*2*(1-PRIZE_CUT_PERCENTAGE/100); execute_query("UPDATE users SET balance=balance+%s,wins=wins+1 WHERE user_id=%s",(prz,w)); execute_query("UPDATE users SET losses=losses+1 WHERE user_id=%s",(l,)); log_transaction(w,prz,'prize_won',f'Match vs {l}');
            await safe_send_message(ctx,w,f"🏆 You won! Prize: {prz:.2f} TK"); await safe_send_message(ctx,l,"😔 You lost."); execute_query("DELETE FROM active_matches WHERE match_id=%s",(mid,))
        else:
            execute_query("UPDATE active_matches SET status='disputed_pending_screenshots' WHERE match_id=%s",(mid,)); msg="❗️Result dispute! Send screenshot proof."; await safe_send_message(ctx,p1,msg); await safe_send_message(ctx,p2,msg)
            p1i,p2i=execute_query("SELECT username,ingame_name FROM users WHERE user_id=%s",(p1,),'one'),execute_query("SELECT username,ingame_name FROM users WHERE user_id=%s",(p2,),'one'); p1ign,p2ign=p1i[1] or "P1",p2i[1] or "P2"
            admin_txt=f"🚨 *DISPUTE* 🚨\n\nID: `{mid}`\nFee: {float(fee):.2f} TK\n\nP1: {p1i[0]}(IGN: *{p1ign}*)\n`{p1}`\nP2: {p2i[0]}(IGN: *{p2ign}*)\n`{p2}`\n\nChoose winner:"; kb=[[InlineKeyboardButton(f"🏆 {p1ign} Won",f"resolve|{mid}|{p1}")],[InlineKeyboardButton(f"🏆 {p2ign} Won",f"resolve|{mid}|{p2}")],[InlineKeyboardButton(f"✖️ Refund",f"resolve|{mid}|refund")]]
            for aid in ADMIN_IDS: await safe_send_message(ctx,aid,admin_txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(kb))
async def resolve_dispute_cb(upd,ctx):
    q=upd.callback_query; admin=q.from_user;
    if admin.id not in ADMIN_IDS: await q.answer("Unauthorized.",show_alert=True); return
    _,mid,dec=q.data.split("|"); rec=execute_query("SELECT player1_id,player2_id,fee FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await q.edit_message_text(f"Match `{mid}` resolved.",parse_mode='Markdown'); return
    p1,p2,fee=rec; fee=float(fee)
    if dec=="refund":
        for pid in (p1,p2): execute_query("UPDATE users SET balance=balance+%s WHERE user_id=%s",(fee,pid)); log_transaction(pid,fee,'refund',f'Disputed match {mid}'); await safe_send_message(ctx,pid,f"⚖️ Match `{mid}` canceled. Fee refunded.",parse_mode='Markdown')
        await q.edit_message_text(f"✅ `{mid}` canceled. Players refunded by {admin.full_name}.",parse_mode='Markdown')
    else:
        wid,lid=int(dec),(p2 if int(dec)==p1 else p1); prz=fee*2*(1-PRIZE_CUT_PERCENTAGE/100)
        execute_query("UPDATE users SET balance=balance+%s,wins=wins+1 WHERE user_id=%s",(prz,wid)); log_transaction(wid,prz,'prize_won',f'Disputed match {mid}'); execute_query("UPDATE users SET losses=losses+1 WHERE user_id=%s",(lid,))
        await safe_send_message(ctx,wid,f"🏆 Admin declared you winner of `{mid}`! Prize: {prz:.2f} TK.",parse_mode='Markdown'); await safe_send_message(ctx,lid,f"😔 Admin declared you lost `{mid}`.",parse_mode='Markdown')
        w_info=execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(wid,),'one'); await q.edit_message_text(f"✅ Dispute for `{mid}` resolved by {admin.full_name}. Winner: *{w_info[0]}*",parse_mode='Markdown')
    execute_query("DELETE FROM active_matches WHERE match_id=%s",(mid,))
async def main_cb_router(upd,ctx):
    d=upd.callback_query.data
    if d=='play_1v1': await play_1v1(upd,ctx)
    elif d.startswith('fee|'): await fee_choice(upd,ctx)
    elif d=='my_wallet': await wallet(upd,ctx)
    elif d=='deposit': await deposit_start(upd,ctx)
    elif d=='tx_history': await tx_history(upd,ctx)
    elif d.startswith('approve_dep') or d.startswith('reject_dep'): await deposit_cb(upd,ctx)
    elif d.startswith('approve_wd') or d.startswith('reject_wd'): await withdraw_cb(upd,ctx)
    elif d=='daily_bonus': await daily_bonus(upd,ctx)
    elif d=='leaderboard': await leaderboard(upd,ctx)
    elif d=='my_referrals': await my_referrals(upd,ctx)
    elif d.startswith('submit_result|'): await submit_result(upd,ctx)
    elif d.startswith('result|'): await result_cb(upd,ctx)
    elif d.startswith('resolve|'): await resolve_dispute_cb(upd,ctx)
    elif d=='cancel_search': await cancel_search(upd,ctx)
    elif d=='back_to_main': await show_main_menu(upd,ctx,"Main Menu:")
    else: logger.warning(f"Unknown callback: {d}")

def main():
    app=Application.builder().token(BOT_TOKEN).build()
    reg_conv=ConversationHandler(entry_points=[CommandHandler("start",start)],states={GET_IGN:[MessageHandler(filters.TEXT & ~filters.COMMAND,get_ign)],GET_PHONE:[MessageHandler(filters.TEXT & ~filters.COMMAND,get_phone)]},fallbacks=[CommandHandler("cancel",cancel_conv)])
    wd_conv=ConversationHandler(entry_points=[CallbackQueryHandler(lambda u,c: universal_pre_handler(u,c,withdraw_start),pattern='^withdraw_start$')],states={ASK_WITHDRAW_AMOUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND,ask_withdraw_amount)],ASK_WITHDRAW_DETAILS:[MessageHandler(filters.TEXT & ~filters.COMMAND,ask_withdraw_details)]},fallbacks=[CommandHandler("cancel",cancel_conv)])
    app.add_handler(reg_conv); app.add_handler(wd_conv)
    app.add_handler(CommandHandler("profile",lambda u,c: universal_pre_handler(u,c,profile))); app.add_handler(CommandHandler("help",help)); app.add_handler(CommandHandler("rules",rules)); app.add_handler(CommandHandler("support",lambda u,c: universal_pre_handler(u,c,support)))
    app.add_handler(CallbackQueryHandler(lambda u,c: universal_pre_handler(u,c,main_cb_router)))
    app.add_handler(MessageHandler(filters.TEXT|filters.PHOTO&~filters.COMMAND,lambda u,c: universal_pre_handler(u,c,handle_msgs)))
    logger.info("Bot is starting..."); app.run_polling()
if __name__=='__main__': main()