# bot.py - HAGU BOT - Complete Version 1.5.3 (Start Command Fixed)
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

import google.generativeai as genai

# ---------------- CONFIG ----------------
BOT_TOKEN = "6356360750:AAE7MpI223usUbTheLo1f6ccLK8zRrOMI1Q"
ADMIN_IDS = [5172723202]
DB_URI = "postgresql://postgres.bwwtazybszxiettvzrlv:SOUROVs768768@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
CHANNEL_ID = -1002439749625
CHANNEL_USERNAME = "QOUTEX_FACK"

# 🚨 WARNING: DO NOT USE YOUR REAL KEY HERE PUBLICLY. USE ENVIRONMENT VARIABLES.
GEMINI_API_KEY = "AIzaSyCOyPQ1a6eO0N_Fboz_msazG9qC9phufo0"

ENTRY_FEES = [10.0, 20.0, 40.0, 80.0, 100.0]
# ... (বাকি সব CONFIG আগের মতোই থাকবে)
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

logger.info("Initializing OCR Reader...")
reader = easyocr.Reader(['en'])
logger.info("OCR Reader initialized successfully.")

try:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-pro')
    logger.info("Gemini Pro model initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Gemini model: {e}")
    gemini_model = None

# ... (Helper functions, OCR, etc. আগের মতোই থাকবে)
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
        logger.error(f"Membership check fail for {user_id}: {e}")
        return False

async def analyze_screenshot_with_ocr(file_path: str, p1_ign: str, p2_ign: str) -> dict:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, reader.readtext, file_path)
        full_text = " ".join([res[1] for res in results]).lower()
        analysis = {"full_time": "full time" in full_text, "p1_found": p1_ign.lower() in full_text, "p2_found": p2_ign.lower() in full_text, "score": "Not Found", "winner": "Undetermined"}
        
        score_pattern = re.compile(r'(\d+)\s*[-–—]\s*(\d+)|(\d+)\s{2,}(\d+)')
        match = score_pattern.search(full_text)
        if match:
            s1_str = match.group(1) or match.group(3)
            s2_str = match.group(2) or match.group(4)
            s1, s2 = int(s1_str), int(s2_str)
            
            text_before_score = full_text[:match.start()]
            text_after_score = full_text[match.end():]

            if p1_ign.lower() in text_before_score and p2_ign.lower() in text_after_score:
                analysis["score"] = f"{p1_ign} {s1} - {s2} {p2_ign}"
                analysis["winner"] = p1_ign if s1 > s2 else p2_ign if s2 > s1 else "Draw"
            elif p2_ign.lower() in text_before_score and p1_ign.lower() in text_after_score:
                analysis["score"] = f"{p2_ign} {s1} - {s2} {p1_ign}"
                analysis["winner"] = p2_ign if s1 > s2 else p1_ign if s2 > s1 else "Draw"
                
        return analysis
    except Exception as e:
        logger.error(f"OCR analysis failed: {e}")
        return {"error": str(e)}

async def universal_pre_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, func, requires_registration=True):
    is_member = await check_channel_membership(update, context)
    if not is_member:
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https.me/{CHANNEL_USERNAME}")]]
        text = "You must join our channel to use the bot.\nPlease join and then try again."
        if update.callback_query: await update.callback_query.answer(text, show_alert=True)
        await safe_send_message(context, update.effective_chat.id, "Please join our channel to continue:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if requires_registration:
        is_registered_rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (update.effective_user.id,), 'one')
        if not is_registered_rec or not is_registered_rec[0]:
            await safe_send_message(context, update.effective_chat.id, "You are not registered. Please use /register to create an account.")
            return

    await func(update, context)


# ----------------- Command Handlers -----------------
# ... (help_command, rules_command, etc. আগের মতোই থাকবে)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("*[HAGU BOT HELP]*\n\n🎮 *Play 1v1*: Find opponent\n💰 *My Wallet*: Balance/Deposit/Withdraw\n🏆 *Leaderboard*: Top players\n🎁 *Daily Bonus*: Claim free bonus\n🤝 *Refer & Earn*: Invite friends\n🧠 */ask [Question]*: Ask our AI assistant\n\n*/profile*: View stats\n*/rules*: Read rules\n*/support*: Contact admin\n\n_New user? Use /register to start!_",parse_mode='Markdown')

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"*[RULES]*\n\n1. Be respectful\n2. No cheating\n3. Provide proof in disputes\n4. Admin decisions final\n5. Min deposit: {MIN_DEPOSIT:.2f} TK\n6. Min withdraw: {MIN_WITHDRAW:.2f} TK",parse_mode='Markdown')

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_to_forward=update.message.text.replace("/support","").strip()
    if not text_to_forward: await update.message.reply_text("Usage: `/support [Message]`"); return
    for admin_id in ADMIN_IDS: await safe_send_message(context,admin_id,f"🆘 Support from {update.effective_user.full_name} (`{update.effective_user.id}`):\n\n`{text_to_forward}`",parse_mode='Markdown')
    await update.message.reply_text("✅ Message sent to admins.")

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rec=execute_query("SELECT ingame_name,wins,losses,balance,referral_balance FROM users WHERE user_id=%s",(update.effective_user.id,),'one')
    ign,w,l,b,rb=rec; w,l=w or 0,l or 0; tm=w+l; wr=(w/tm*100) if tm>0 else 0
    txt=f"👤 *Profile*\n\nIGN: `{ign}`\n\n📊 *Stats*\n- Matches: {tm}, Wins: {w}, Losses: {l}\n- Win Rate: {wr:.2f}%\n\n💰 *Wallet*\n- Main Balance: {float(b):.2f} TK\n- Referral Balance: {float(rb):.2f} TK"
    await safe_send_message(context,update.effective_chat.id,txt,parse_mode='Markdown')

async def ask_gemini_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not gemini_model:
        await update.message.reply_text("Sorry, the AI model is currently unavailable.")
        return

    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Usage: /ask [Your Question]")
        return

    if not await check_channel_membership(update, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]
        await safe_send_message(context, update.effective_chat.id, "Please join our channel to use the AI assistant:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    thinking_message = await update.message.reply_text("🤔 Thinking...")
    try:
        response = await asyncio.to_thread(gemini_model.generate_content, prompt)
        
        await context.bot.edit_message_text(
            text=response.text,
            chat_id=update.effective_chat.id,
            message_id=thinking_message.message_id
        )
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        await context.bot.edit_message_text(
            text="Sorry, something went wrong. The AI might be busy or the API key might be invalid.",
            chat_id=update.effective_chat.id,
            message_id=thinking_message.message_id
        )


# ----------------- Main and Registration Logic (CHANGED) -----------------

##### CHANGED LOGIC START #####
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command for both new and registered users."""
    user = update.effective_user
    is_registered_rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (user.id,), 'one')
    is_registered = is_registered_rec[0] if is_registered_rec else False

    if is_registered:
        await show_main_menu(update, context, "Welcome back! Here is the main menu:")
    else:
        # Save referrer if any
        if context.args:
            try:
                referrer_id = int(context.args[0])
                if referrer_id != user.id:
                    # Insert user row first to avoid foreign key violation if it doesn't exist
                    execute_query("INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user.id, user.username or user.first_name))
                    execute_query("UPDATE users SET referred_by = %s WHERE user_id = %s AND referred_by IS NULL", (referrer_id, user.id))
            except (ValueError, IndexError):
                pass
        
        await update.message.reply_text(
            "Welcome! You are not registered yet.\n"
            "Please use the /register command to create your account."
        )

async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the registration conversation for new users."""
    user = update.effective_user
    
    # First, check if they are in the channel
    if not await check_channel_membership(update, context):
        await update.message.reply_text("To register, you must first join our channel.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]]))
        return ConversationHandler.END

    # Then, check if they are already registered
    is_registered_rec = execute_query("SELECT is_registered FROM users WHERE user_id = %s", (user.id,), 'one')
    if is_registered_rec and is_registered_rec[0]:
        await update.message.reply_text("You are already registered! Use /start to see the menu.")
        return ConversationHandler.END

    # If not registered and is a channel member, start the process
    await update.message.reply_text("Welcome to registration! Please send your In-Game Name (IGN):")
    return GET_IGN

##### CHANGED LOGIC END #####

async def get_ign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This function is now part of the /register conversation
    execute_query("INSERT INTO users (user_id, username, ingame_name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET ingame_name = EXCLUDED.ingame_name", (update.effective_user.id, update.effective_user.username or update.effective_user.first_name, update.message.text.strip()))
    await update.message.reply_text("Got it. Now, send your phone number:")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    execute_query("UPDATE users SET phone_number=%s,is_registered=TRUE WHERE user_id=%s",(update.message.text.strip(),user.id))
    rec=execute_query("SELECT welcome_given FROM users WHERE user_id=%s",(user.id,),'one')
    if rec and not rec[0]:
        bonus=10.0
        execute_query("UPDATE users SET balance=balance+%s,welcome_given=TRUE WHERE user_id=%s",(bonus,user.id))
        log_transaction(user.id,bonus,'bonus','Welcome Bonus')
        await update.message.reply_text(f"✅ Registration complete! You got a {bonus:.2f} TK welcome bonus.")
    else: await update.message.reply_text("✅ Registration complete!")
    await show_main_menu(update,context,"You can now use all my features. Here is the main menu:")
    return ConversationHandler.END

# ... (বাকি সব ফাংশন যেমন withdraw, callbacks, matchmaking ইত্যাদি আগের মতোই থাকবে)
async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance=float(execute_query("SELECT balance FROM users WHERE user_id=%s",(update.effective_user.id,),'one')[0] or 0.0)
    if balance<MIN_WITHDRAW: await update.callback_query.answer(f"❌ Min {MIN_WITHDRAW:.2f} TK to withdraw.",show_alert=True); return ConversationHandler.END
    await update.callback_query.message.reply_text(f"Available: {balance:.2f} TK.\nHow much to withdraw?\nMin: {MIN_WITHDRAW:.2f} TK\n\n/cancel to abort."); return ASK_WITHDRAW_AMOUNT

async def ask_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: amount=float(update.message.text)
    except: await update.message.reply_text("Invalid number."); return ASK_WITHDRAW_AMOUNT
    balance=float(execute_query("SELECT balance FROM users WHERE user_id=%s",(update.effective_user.id,),'one')[0] or 0.0)
    if amount<MIN_WITHDRAW: await update.message.reply_text(f"Minimum withdrawal is {MIN_WITHDRAW:.2f} TK."); return ASK_WITHDRAW_AMOUNT
    if amount>balance: await update.message.reply_text("Insufficient balance."); return ASK_WITHDRAW_AMOUNT
    context.user_data['withdraw_amount']=amount
    await update.message.reply_text("OK. Send payment details.\nEx: `Bkash 017...`")
    return ASK_WITHDRAW_DETAILS

async def ask_withdraw_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; details=update.message.text.strip().split(); amount=context.user_data['withdraw_amount']
    if len(details)!=2 or not details[1].isdigit(): await update.message.reply_text("Invalid format. Use `Method Number`"); return ASK_WITHDRAW_DETAILS
    method,number=details[0].capitalize(),details[1]
    req_id=execute_query("INSERT INTO withdrawal_requests(user_id,amount,method,account_number,status,created_at)VALUES(%s,%s,%s,%s,'pending',%s)RETURNING id",(user.id,amount,method,number,int(time.time())),'one')[0]
    await update.message.reply_text("✅ Withdrawal request submitted.",reply_markup=ReplyKeyboardRemove())
    keyboard=[[InlineKeyboardButton("✅ Approve",f"approve_wd|{req_id}|{user.id}|{amount}"),InlineKeyboardButton("❌ Reject",f"reject_wd|{req_id}|{user.id}|{amount}")]]
    for admin_id in ADMIN_IDS: await safe_send_message(context,admin_id,f"➖ *New Withdraw Request* ➖\n\nUser: {user.full_name}(`{user.id}`)\nAmount: *{amount:.2f} TK*\nMethod: *{method}*\nNumber: `{number}`",parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.clear(); return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Canceled.",reply_markup=ReplyKeyboardRemove()); context.user_data.clear(); return ConversationHandler.END

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str):
    kb=[[InlineKeyboardButton("🎮 Play 1v1",'play_1v1')],[InlineKeyboardButton("💰 My Wallet",'my_wallet'),InlineKeyboardButton("➕ Deposit",'deposit')],[InlineKeyboardButton("🏆 Leaderboard",'leaderboard'),InlineKeyboardButton("🎁 Daily Bonus",'daily_bonus')],[InlineKeyboardButton("🤝 Refer & Earn",'my_referrals')]]
    rm=InlineKeyboardMarkup(kb)
    if update.callback_query:
        try: await update.callback_query.edit_message_text(msg,reply_markup=rm)
        except: pass
    else: await safe_send_message(context,update.effective_user.id,msg,reply_markup=rm)

async def wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(update.effective_user.id,),'one')
    b,rb=(rec[0],rec[1]) if rec else (0.0,0.0); txt=f"💰 *Wallet*\n\nMain: `{float(b):.2f} TK`\nReferral: `{float(rb):.2f} TK`"
    kb=[[InlineKeyboardButton("➕ Deposit",'deposit'),InlineKeyboardButton("➖ Withdraw",'withdraw_start')],[InlineKeyboardButton("📜 History",'tx_history')],[InlineKeyboardButton("⬅️ Back",'back_to_main')]]
    await update.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(kb))

async def tx_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txs=execute_query("SELECT type,amount,created_at FROM transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",(update.effective_user.id,),'all')
    txt="📜 *Last 10 Transactions*\n\n"+("No transactions." if not txs else "".join([f"`{datetime.fromtimestamp(ts).strftime('%d %b, %I:%M%p')}`\n_{typ.replace('_',' ').title()}_: *{'+' if float(amt)>0 else ''}{float(amt):.2f} TK*\n\n" for typ,amt,ts in txs]))
    await update.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Wallet",'my_wallet')]]))

async def deposit_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await (update.callback_query.message if update.callback_query else update.message).reply_text(f"➕ *Deposit*\n\nSend to:\n• Bkash/Nagad: `{BKASH_NUMBER}`\n\nThen reply: `TXID Amount`",parse_mode='Markdown')

async def deposit_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query;
    if query.from_user.id not in ADMIN_IDS: await query.answer("Unauthorized.",show_alert=True); return
    action,uid,txid,amt_s=query.data.split("|"); uid,amt=int(uid),float(amt_s)
    if "approve" in action:
        rec=execute_query("SELECT id FROM deposit_requests WHERE user_id=%s AND txid=%s AND status='pending' LIMIT 1",(uid,txid),'one')
        if not rec: await query.answer("Processed.",show_alert=True); return
        execute_query("UPDATE deposit_requests SET status='approved',processed_by=%s,processed_at=%s WHERE id=%s",(query.from_user.id,int(time.time()),rec[0]))
        execute_query("UPDATE users SET balance=balance+%s WHERE user_id=%s",(amt,uid)); log_transaction(uid,amt,'deposit',f'TXID: {txid}')
        await query.edit_message_text(f"✅ Approved {amt:.2f} TK for {uid}."); await safe_send_message(context,uid,f"✅ Your deposit of {amt:.2f} TK approved.")
        if REFERRAL_ENABLED and amt>=MIN_DEPOSIT_FOR_REFERRAL and (execute_query("SELECT COUNT(*) FROM deposit_requests WHERE user_id=%s AND status='approved'",(uid,),'one')[0])==1:
            ref=execute_query("SELECT referred_by FROM users WHERE user_id=%s AND referred_by IS NOT NULL",(uid,),'one')
            if ref: ref_id=ref[0]; execute_query("UPDATE users SET referral_balance=referral_balance+%s WHERE user_id=%s",(REFERRAL_COMMISSION_TK,ref_id)); log_transaction(ref_id,REFERRAL_COMMISSION_TK,'referral_bonus',f'From {uid}'); await safe_send_message(context,ref_id,f"🎉 You earned {REFERRAL_COMMISSION_TK:.2f} TK from a referral!")
    else: execute_query("UPDATE deposit_requests SET status='rejected',processed_by=%s,processed_at=%s WHERE user_id=%s AND txid=%s AND status='pending'",(query.from_user.id,int(time.time()),uid,txid)); await query.edit_message_text(f"❌ Rejected deposit for {uid}."); await safe_send_message(context,uid,f"❌ Your deposit of {amt:.2f} TK rejected.")

async def withdrawal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query;
    if query.from_user.id not in ADMIN_IDS: await query.answer("Unauthorized.",show_alert=True); return
    action,req_id,uid,amt_s=query.data.split("|"); req_id,uid,amt=int(req_id),int(uid),float(amt_s)
    rec=execute_query("SELECT status FROM withdrawal_requests WHERE id=%s",(req_id,),'one')
    if not rec or rec[0]!='pending': await query.answer("Processed.",show_alert=True); return
    if "approve" in action:
        execute_query("UPDATE users SET balance=balance-%s WHERE user_id=%s",(amt,uid)); execute_query("UPDATE withdrawal_requests SET status='approved',processed_by=%s,processed_at=%s WHERE id=%s",(query.from_user.id,int(time.time()),req_id)); log_transaction(uid,-amt,'withdrawal',f'Req ID: {req_id}')
        await query.edit_message_text(f"✅ Approved withdraw for {uid}."); await safe_send_message(context,uid,f"✅ Your withdraw of {amt:.2f} TK approved.")
    else: execute_query("UPDATE withdrawal_requests SET status='rejected',processed_by=%s,processed_at=%s WHERE id=%s",(query.from_user.id,int(time.time()),req_id)); await query.edit_message_text(f"❌ Rejected withdraw for {uid}."); await safe_send_message(context,uid,f"❌ Your withdraw of {amt:.2f} TK rejected.")

async def daily_bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; rec=execute_query("SELECT last_daily_at FROM users WHERE user_id=%s",(uid,),'one')
    if rec and rec[0] and int(time.time())-rec[0]<86400: await update.callback_query.answer("Already claimed.",show_alert=True); return
    b=2.0; execute_query("UPDATE users SET balance=balance+%s,last_daily_at=%s WHERE user_id=%s",(b,int(time.time()),uid)); log_transaction(uid,b,'bonus','Daily Bonus')
    await update.callback_query.edit_message_text(f"🎁 You got a daily bonus of {b:.2f} TK!"); await asyncio.sleep(2); await show_main_menu(update,context,"Main Menu:")

async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows=execute_query("SELECT ingame_name,wins FROM users WHERE wins>0 ORDER BY wins DESC NULLS LAST LIMIT 10",'all')
    txt="🏆 *Leaderboard (Top 10 by Wins)*\n\n"+("No data." if not rows else "".join([f"*{i+1}.* `{ign or 'N/A'}` — Wins: {wins or 0}\n" for i,(ign,wins) in enumerate(rows)]))
    await update.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",'back_to_main')]]))

async def my_referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; bot_user=(await context.bot.get_me()).username; link=f"https://t.me/{bot_user}?start={uid}"; stats=execute_query("SELECT referral_balance,(SELECT COUNT(*)FROM users WHERE referred_by=%s)FROM users WHERE user_id=%s",(uid,uid),'one'); rb,rc=(stats[0],stats[1]) if stats else (0.0,0)
    txt=f"🤝 *Refer & Earn*\n\nShare your link. When friend's 1st deposit >= `{MIN_DEPOSIT_FOR_REFERRAL:.2f} TK`, you get `{REFERRAL_COMMISSION_TK:.2f} TK`!\n\n🔗 *Your Link:*\n`{link}`\n\n📊 *Stats:*\n- Referrals: *{rc}*\n- Earnings: *{float(rb):.2f} TK*"
    await update.callback_query.edit_message_text(txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",'back_to_main')]]))

async def play_1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb=[[InlineKeyboardButton(f"{int(f)} TK",f"fee|{int(f)}")] for f in ENTRY_FEES]+[[InlineKeyboardButton("⬅️ Back",'back_to_main')]]; await update.callback_query.edit_message_text("Choose entry fee:",reply_markup=InlineKeyboardMarkup(kb))

async def fee_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; uid=query.from_user.id; _,fee_s=query.data.split("|",1); fee=float(fee_s); rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(uid,),'one'); tot_bal=float(rec[0] or 0.0)+float(rec[1] or 0.0)
    if tot_bal<fee: await query.answer("Insufficient balance.",show_alert=True); return
    await query.edit_message_text(f"✅ Balance checked!\n\n⏳ Searching for a {int(fee)} TK match...\n\nFee deducted on match found.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Search",'cancel_search')]]))
    opp=execute_query("SELECT user_id FROM matchmaking_queue WHERE fee=%s AND user_id!=%s LIMIT 1",(fee,uid),'one')
    if opp:
        opp_id=opp[0]; execute_query("DELETE FROM matchmaking_queue WHERE user_id IN (%s,%s)",(uid,opp_id)); mid=f"m_{int(time.time())}"
        for pid in (uid,opp_id):
            p_rec=execute_query("SELECT balance,referral_balance FROM users WHERE user_id=%s",(pid,),'one'); b,rb=float(p_rec[0] or 0.0),float(p_rec[1] or 0.0)
            if rb>=fee: execute_query("UPDATE users SET referral_balance=referral_balance-%s WHERE user_id=%s",(fee,pid))
            else: need=fee-rb; execute_query("UPDATE users SET referral_balance=0,balance=balance-%s WHERE user_id=%s",(need,pid))
            log_transaction(pid,-fee,'match_fee',f'Match {mid}')
        execute_query("INSERT INTO active_matches(match_id,player1_id,player2_id,fee,status,created_at)VALUES(%s,%s,%s,%s,'waiting_for_code',%s)",(mid,uid,opp_id,fee,int(time.time())))
        await safe_send_message(context,uid,f"✅ Match found with `{opp_id}`.\nPlease create room & send Room Code."); await safe_send_message(context,opp_id,f"✅ Match found with `{uid}`.\nPlease wait for Room Code.")
        try:
            opp_msg_id=context.user_data.pop(f'search_msg_{opp_id}',None)
            if opp_msg_id: await context.bot.edit_message_text("✅ Match found! Check DMs.",chat_id=opp_id,message_id=opp_msg_id)
        except: pass
    else: execute_query("INSERT INTO matchmaking_queue(user_id,fee,timestamp)VALUES(%s,%s,%s)ON CONFLICT(user_id)DO UPDATE SET fee=EXCLUDED.fee,timestamp=EXCLUDED.timestamp",(uid,fee,int(time.time()))); context.user_data[f'search_msg_{uid}']=query.message.message_id; bot_user=(await context.bot.get_me()).username; await safe_send_message(context,CHANNEL_ID,f"🔥 Player looking for a {int(fee)} TK match! Join @{bot_user}")

async def cancel_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    execute_query("DELETE FROM matchmaking_queue WHERE user_id=%s",(update.effective_user.id,)); await show_main_menu(update,context,"Search canceled.")

async def submit_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; _,mid=query.data.split("|"); rec=execute_query("SELECT player1_id,player2_id,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await query.answer("Match not found.",show_alert=True); return
    p1,p2,stat=rec;
    if stat!='in_progress': await query.answer("Result submission not active.",show_alert=True); return
    await ask_for_result_logic(context,mid,p1,p2)
    await query.message.edit_reply_markup(None)
    try:
        opp_id=p2 if query.from_user.id==p1 else p1; opp_msg_id=context.user_data.pop(f'submit_btn_msg_{opp_id}',None)
        if opp_msg_id: await context.bot.edit_message_reply_markup(opp_id,opp_msg_id,reply_markup=None)
    except: pass

async def ask_for_result_logic(context,mid,p1,p2):
    if not execute_query("UPDATE active_matches SET status='waiting_for_result' WHERE match_id=%s AND status='in_progress' RETURNING status",(mid,),'one'): return
    for j in context.job_queue.get_jobs_by_name(f"result_{mid}"): j.schedule_removal()
    kb=[[InlineKeyboardButton("✅ I Won",f"result|won|{mid}")],[InlineKeyboardButton("❌ I Lost",f"result|lost|{mid}")]]
    await safe_send_message(context,p1,"🏁 Match finished! Submit result:",reply_markup=InlineKeyboardMarkup(kb)); await safe_send_message(context,p2,"🏁 Match finished! Submit result:",reply_markup=InlineKeyboardMarkup(kb))

async def request_result_job(context):
    mid=context.job.data['match_id']; rec=execute_query("SELECT player1_id,player2_id,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if rec and rec[2]=='in_progress': logger.info(f"Timer expired for {mid}. Forcing result submission."); await ask_for_result_logic(context,mid,rec[0],rec[1])

async def handle_text_or_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; text=(update.message.text or "").strip()
    disp_m=execute_query("SELECT match_id,player1_id,player2_id,fee FROM active_matches WHERE (player1_id=%s OR player2_id=%s) AND status='disputed_pending_screenshots'",(user.id,user.id),'one')
    if disp_m and update.message.photo:
        mid,p1,p2,fee=disp_m; await update.message.reply_text("✅ Screenshot received. Admin is reviewing.")
        p1i,p2i=execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(p1,),'one')[0],execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(p2,),'one')[0]
        ign=p1i if user.id==p1 else p2i
        ocr_path=f"temp_{mid}_{user.id}.jpg"; ss=await(await update.message.photo[-1].get_file()).download_to_drive(ocr_path)
        an=await analyze_screenshot_with_ocr(ss.name,p1i,p2i)
        report=f"🤖 *Bot Analysis for {user.full_name}'s SS:*\n- Full Time: {'✅' if an.get('full_time') else '❌'}\n- P1 IGN Found: {'✅' if an.get('p1_found') else '❌'}\n- P2 IGN Found: {'✅' if an.get('p2_found') else '❌'}\n- Score: `{an.get('score')}`\n- Suggested Winner: *{an.get('winner')}*"
        for aid in ADMIN_IDS: await safe_send_message(context,aid,f"🖼️ SS for Disputed Match `{mid}` from *{user.full_name}* (IGN: `{ign}`)",parse_mode='Markdown'); await context.bot.forward_message(aid,user.id,update.message.message_id); await safe_send_message(context,aid,report,parse_mode='Markdown')
        os.remove(ss.name); return
    
    match_rec=execute_query("SELECT match_id,player1_id,player2_id FROM active_matches WHERE player1_id=%s AND status='waiting_for_code'",(user.id,),'one')
    if match_rec and room_code_pattern.match(text):
        mid,p1,p2=match_rec; execute_query("UPDATE active_matches SET status='in_progress',room_code=%s WHERE match_id=%s",(text,mid))
        await update.message.reply_text(f"✅ Code `{text}` sent. Match started!",parse_mode='Markdown'); await safe_send_message(context,p2,f"⚔️ Room code: `{text}`\n\nGood luck!",parse_mode='Markdown')
        kb=[[InlineKeyboardButton("🏁 Submit Result",f"submit_result|{mid}")]]
        m1=await safe_send_message(context,p1,"Match in progress. Click below when finished.",reply_markup=InlineKeyboardMarkup(kb)); m2=await safe_send_message(context,p2,"Match in progress. Click below when finished.",reply_markup=InlineKeyboardMarkup(kb))
        if m1: context.user_data[f'submit_btn_msg_{p1}']=m1.message_id
        if m2: context.user_data[f'submit_btn_msg_{p2}']=m2.message_id
        context.job_queue.run_once(request_result_job,MATCH_DURATION_MINUTES*60,data={'match_id':mid},name=f"result_{mid}"); return
    
    m=deposit_pattern.match(text)
    if m:
        txid,amt=m.group(1),float(m.group(2)); execute_query("INSERT INTO deposit_requests(user_id,txid,amount,status,created_at)VALUES(%s,%s,%s,'pending',%s)",(user.id,txid,amt,int(time.time())))
        await update.message.reply_text("✅ Deposit request received."); kb=[[InlineKeyboardButton("✅ Approve",f"approve_dep|{user.id}|{txid}|{amt}"),InlineKeyboardButton("❌ Reject",f"reject_dep|{user.id}|{txid}|{amt}")]];
        for aid in ADMIN_IDS: await safe_send_message(context,aid,f"🔔 New deposit\nUser: {user.full_name}({user.id})\nTXID: {txid}\nAmount: {amt:.2f} TK",reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if not disp_m: await safe_send_message(context,user.id,"Unknown command. Use /help.")

async def result_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; _,out,mid=query.data.split("|"); uid=query.from_user.id; rec=execute_query("SELECT player1_id,player2_id,fee,status FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await query.answer("Match not found.",show_alert=True); return
    p1,p2,fee,stat=rec;
    if stat not in ('waiting_for_result','disputed_pending_screenshots'): await query.answer("Submission not active.",show_alert=True); return
    col='p1_result' if uid==p1 else 'p2_result'; execute_query(f"UPDATE active_matches SET {col}=%s WHERE match_id=%s",(out,mid)); await query.message.edit_text("Result recorded. Waiting for opponent...")
    rec2=execute_query("SELECT p1_result,p2_result FROM active_matches WHERE match_id=%s",(mid,),'one')
    if rec2 and rec2[0] and rec2[1]:
        p1r,p2r=rec2; w,l= (p1,p2) if (p1r,p2r)==('won','lost') else (p2,p1) if (p1r,p2r)==('lost','won') else (None,None)
        if w:
            prz=float(fee)*2*(1-PRIZE_CUT_PERCENTAGE/100); execute_query("UPDATE users SET balance=balance+%s,wins=wins+1 WHERE user_id=%s",(prz,w)); execute_query("UPDATE users SET losses=losses+1 WHERE user_id=%s",(l,)); log_transaction(w,prz,'prize_won',f'Match vs {l}');
            await safe_send_message(context,w,f"🏆 You won! Prize: {prz:.2f} TK"); await safe_send_message(context,l,"😔 You lost."); execute_query("DELETE FROM active_matches WHERE match_id=%s",(mid,))
        else:
            execute_query("UPDATE active_matches SET status='disputed_pending_screenshots' WHERE match_id=%s",(mid,)); msg="❗️Result dispute! Send screenshot proof."; await safe_send_message(context,p1,msg); await safe_send_message(context,p2,msg)
            p1i,p2i=execute_query("SELECT username,ingame_name FROM users WHERE user_id=%s",(p1,),'one'),execute_query("SELECT username,ingame_name FROM users WHERE user_id=%s",(p2,),'one'); p1ign,p2ign=p1i[1] or "P1",p2i[1] or "P2"
            admin_txt=f"🚨 *DISPUTE* 🚨\n\nID: `{mid}`\nFee: {float(fee):.2f} TK\n\nP1: {p1i[0]}(IGN: *{p1ign}*)\n`{p1}`\nP2: {p2i[0]}(IGN: *{p2ign}*)\n`{p2}`\n\nChoose winner:"; kb=[[InlineKeyboardButton(f"🏆 {p1ign} Won",f"resolve|{mid}|{p1}")],[InlineKeyboardButton(f"🏆 {p2ign} Won",f"resolve|{mid}|{p2}")],[InlineKeyboardButton(f"✖️ Refund",f"resolve|{mid}|refund")]]
            for aid in ADMIN_IDS: await safe_send_message(context,aid,admin_txt,parse_mode='Markdown',reply_markup=InlineKeyboardMarkup(kb))

async def resolve_dispute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; admin=query.from_user;
    if admin.id not in ADMIN_IDS: await query.answer("Unauthorized.",show_alert=True); return
    _,mid,dec=query.data.split("|"); rec=execute_query("SELECT player1_id,player2_id,fee FROM active_matches WHERE match_id=%s",(mid,),'one')
    if not rec: await query.edit_message_text(f"Match `{mid}` resolved.",parse_mode='Markdown'); return
    p1,p2,fee=rec; fee=float(fee)
    if dec=="refund":
        for pid in (p1,p2): execute_query("UPDATE users SET balance=balance+%s WHERE user_id=%s",(fee,pid)); log_transaction(pid,fee,'refund',f'Disputed match {mid}'); await safe_send_message(context,pid,f"⚖️ Match `{mid}` canceled. Fee refunded.",parse_mode='Markdown')
        await query.edit_message_text(f"✅ `{mid}` canceled. Players refunded by {admin.full_name}.",parse_mode='Markdown')
    else:
        wid,lid=int(dec),(p2 if int(dec)==p1 else p1); prz=fee*2*(1-PRIZE_CUT_PERCENTAGE/100)
        execute_query("UPDATE users SET balance=balance+%s,wins=wins+1 WHERE user_id=%s",(prz,wid)); log_transaction(wid,prz,'prize_won',f'Disputed match {mid}'); execute_query("UPDATE users SET losses=losses+1 WHERE user_id=%s",(lid,))
        await safe_send_message(context,wid,f"🏆 Admin declared you winner of `{mid}`! Prize: {prz:.2f} TK.",parse_mode='Markdown'); await safe_send_message(context,lid,f"😔 Admin declared you lost `{mid}`.",parse_mode='Markdown')
        w_info=execute_query("SELECT ingame_name FROM users WHERE user_id=%s",(wid,),'one'); await query.edit_message_text(f"✅ Dispute for `{mid}` resolved by {admin.full_name}. Winner: *{w_info[0]}*",parse_mode='Markdown')
    execute_query("DELETE FROM active_matches WHERE match_id=%s",(mid,))

async def main_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data=update.callback_query.data
    if data=='play_1v1': await play_1v1_callback(update,context)
    elif data.startswith('fee|'): await fee_choice_handler(update,context)
    elif data=='my_wallet': await wallet_callback(update,context)
    elif data=='deposit': await deposit_start_callback(update,context)
    elif data=='tx_history': await tx_history_callback(update,context)
    elif data.startswith(('approve_dep','reject_dep')): await deposit_callback_handler(update,context)
    elif data.startswith(('approve_wd','reject_wd')): await withdrawal_callback_handler(update,context)
    elif data=='daily_bonus': await daily_bonus_callback(update,context)
    elif data=='leaderboard': await leaderboard_callback(update,context)
    elif data=='my_referrals': await my_referrals_callback(update,context)
    elif data.startswith('submit_result|'): await submit_result_callback(update,context)
    elif data.startswith('result|'): await result_callback_handler(update,context)
    elif data.startswith('resolve|'): await resolve_dispute_callback(update,context)
    elif data=='cancel_search': await cancel_search_callback(update,context)
    elif data=='back_to_main': await show_main_menu(update,context,"Main Menu:")
    else: logger.warning(f"Unknown callback: {data}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    ##### CHANGED LOGIC: SEPARATE /start AND /register #####
    
    # Registration Conversation Handler, now triggered by /register
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("register", register_command)],
        states={
            GET_IGN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ign)],
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )

    # Withdraw Conversation Handler (needs pre-handler for checks)
    wd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(lambda u,c: universal_pre_handler(u,c, withdraw_start), pattern='^withdraw_start$')],
        states={
            ASK_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_withdraw_amount)],
            ASK_WITHDRAW_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_withdraw_details)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    app.add_handler(reg_conv)
    app.add_handler(wd_conv)

    # Simple commands that don't need any checks
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("ask", ask_gemini_command))

    # Commands that require user to be registered and in the channel
    app.add_handler(CommandHandler("profile", lambda u,c: universal_pre_handler(u,c, profile_command)))
    app.add_handler(CommandHandler("support", lambda u,c: universal_pre_handler(u,c, support_command)))
    
    # Callback and Message handlers also require checks
    app.add_handler(CallbackQueryHandler(lambda u,c: universal_pre_handler(u,c, main_callback_router)))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
        lambda u,c: universal_pre_handler(u,c, handle_text_or_photo_messages)
    ))
    
    logger.info("Bot is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()