import os
import logging
import pg8000.native
from urllib.parse import urlparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from datetime import datetime

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ['BOT_TOKEN']
ADMIN_ID       = int(os.environ['ADMIN_ID'])
DATABASE_URL   = os.environ['DATABASE_URL']
UPI_ID         = os.environ.get('UPI_ID', 'yourname@upi')
USDT_ADDRESS   = os.environ.get('USDT_ADDRESS', 'TYourUSDTAddressHere')
USDT_NETWORK   = os.environ.get('USDT_NETWORK', 'TRC20')
SUPPORT_USER   = os.environ.get('SUPPORT_USERNAME', 'YourUsername')

# ─── Countries ────────────────────────────────────────────────────────────────
COUNTRIES = {
    'iraq':          {'name': '🇮🇶 Iraq',           'price': 79},
    'fresh_iraq':    {'name': '🇮🇶 Fresh Iraq',      'price': 99},
    'new_iraq':      {'name': '🇮🇶 New Iraq',        'price': 99},
    'zambia':        {'name': '🇿🇲 Zambia',          'price': 79},
    'tunisia':       {'name': '🇹🇳 Tunisia',         'price': 79},
    'indonesia':     {'name': '🇮🇩 Indonesia',       'price': 79},
    'germany':       {'name': '🇩🇪 Germany',         'price': 99},
    'ghana':         {'name': '🇬🇭 Ghana',           'price': 79},
    'sudan':         {'name': '🇸🇩 Sudan',           'price': 79},
    'venezuela':     {'name': '🇻🇪 Venezuela N',     'price': 79},
    'saudi':         {'name': '🇸🇦 Saudi Arabia',    'price': 99},
    'fresh_russia':  {'name': '🇷🇺 Fresh Russia',    'price': 99},
    'new_russia':    {'name': '🇷🇺 New Russia',      'price': 99},
    'kyrgyzstan':    {'name': '🇰🇬 New Kyrgyzstan',  'price': 99},
    'nigeria':       {'name': '🇳🇬 Nigeria',         'price': 79},
    'fresh_nigeria': {'name': '🇳🇬 Fresh Nigeria',   'price': 99},
    'new_nigeria':   {'name': '🇳🇬 New Nigeria',     'price': 99},
    'timor':         {'name': '🇹🇱 Timor-Leste',     'price': 79},
}

WAITING_PROOF = 1

# ─── Database ─────────────────────────────────────────────────────────────────
def parse_db_url(url):
    """Parse DATABASE_URL into pg8000 connection params."""
    r = urlparse(url)
    params = {
        'host':     r.hostname,
        'port':     r.port or 5432,
        'database': r.path.lstrip('/'),
        'user':     r.username,
        'password': r.password,
    }
    # Railway uses SSL
    if 'railway' in (r.hostname or ''):
        params['ssl_context'] = True
    return params

def get_db():
    params = parse_db_url(DATABASE_URL)
    return pg8000.native.Connection(**params)

def init_db():
    conn = get_db()
    conn.run("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    BIGINT PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.run("""
        CREATE TABLE IF NOT EXISTS orders (
            id               SERIAL PRIMARY KEY,
            user_id          BIGINT NOT NULL,
            country_key      TEXT NOT NULL,
            country_name     TEXT NOT NULL,
            price            INTEGER NOT NULL,
            payment_method   TEXT NOT NULL,
            status           TEXT DEFAULT 'pending',
            proof_file_id    TEXT,
            number_delivered TEXT,
            created_at       TIMESTAMP DEFAULT NOW(),
            updated_at       TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.close()
    logger.info("✅ Database initialized")

def save_user(user_id, username, first_name):
    conn = get_db()
    conn.run("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (:uid, :uname, :fname)
        ON CONFLICT (user_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name
    """, uid=user_id, uname=username, fname=first_name)
    conn.close()

def create_order(user_id, country_key, payment_method, proof_file_id):
    c = COUNTRIES[country_key]
    conn = get_db()
    rows = conn.run("""
        INSERT INTO orders (user_id, country_key, country_name, price, payment_method, proof_file_id)
        VALUES (:uid, :ckey, :cname, :price, :method, :proof)
        RETURNING id
    """, uid=user_id, ckey=country_key, cname=c['name'],
         price=c['price'], method=payment_method, proof=proof_file_id)
    order_id = rows[0][0]
    conn.close()
    return order_id

def get_order(order_id):
    conn = get_db()
    rows = conn.run("SELECT * FROM orders WHERE id = :oid", oid=order_id)
    cols = [c['name'] for c in conn.columns]
    conn.close()
    if not rows:
        return None
    return dict(zip(cols, rows[0]))

def update_order(order_id, status, number=None):
    conn = get_db()
    if number:
        conn.run("""
            UPDATE orders SET status = :s, number_delivered = :n, updated_at = NOW()
            WHERE id = :oid
        """, s=status, n=number, oid=order_id)
    else:
        conn.run("""
            UPDATE orders SET status = :s, updated_at = NOW()
            WHERE id = :oid
        """, s=status, oid=order_id)
    conn.close()

def get_user_orders(user_id):
    conn = get_db()
    rows = conn.run("""
        SELECT * FROM orders WHERE user_id = :uid
        ORDER BY created_at DESC LIMIT 10
    """, uid=user_id)
    cols = [c['name'] for c in conn.columns]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]

def get_pending_orders():
    conn = get_db()
    rows = conn.run("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC")
    cols = [c['name'] for c in conn.columns]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]

def get_stats():
    conn = get_db()
    rows = conn.run("SELECT COUNT(*), COALESCE(SUM(price),0) FROM orders WHERE status = 'delivered'")
    total, revenue = rows[0]
    rows2 = conn.run("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    pending = rows2[0][0]
    rows3 = conn.run("SELECT COUNT(*) FROM users")
    users = rows3[0][0]
    conn.close()
    return total, revenue, pending, users

# ─── Keyboards ────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Buy Number", callback_data="browse")],
        [InlineKeyboardButton("📋 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("📞 Support", url=f"https://t.me/{SUPPORT_USER}")],
    ])

def countries_kb():
    buttons = [[InlineKeyboardButton(
        f"{d['name']} — ₹{d['price']}", callback_data=f"c_{k}"
    )] for k, d in COUNTRIES.items()]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)

def payment_kb(country_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 UPI / Bank Transfer", callback_data=f"pay_upi_{country_key}")],
        [InlineKeyboardButton("💰 USDT Crypto",         callback_data=f"pay_usdt_{country_key}")],
        [InlineKeyboardButton("🔙 Back",                callback_data="browse")],
    ])

def paid_kb(method, country_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I've Paid — Send Screenshot", callback_data=f"paid_{method}_{country_key}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")],
    ])

def admin_kb(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"reject_{order_id}"),
    ]])

STATUS_EMOJI = {
    'pending': '⏳', 'approved': '🔄',
    'delivered': '✅', 'rejected': '❌'
}

# ─── User Handlers ────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"👋 Welcome, *{user.first_name}*!\n\n"
        "🌍 *International Virtual Numbers* — Premium Quality\n\n"
        "✅ Instant Delivery after payment approval\n"
        "✅ UPI & USDT both accepted\n"
        "✅ Trusted Seller | Fast Response\n\n"
        "Tap below to get started 👇",
        parse_mode='Markdown', reply_markup=main_menu_kb()
    )

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🏠 *Main Menu*\n\nWhat would you like to do?",
                               parse_mode='Markdown', reply_markup=main_menu_kb())

async def cb_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🌍 *Available Countries*\n\n✅ Standard — ₹79\n🔥 Fresh/New — ₹99\n\nSelect a country:",
        parse_mode='Markdown', reply_markup=countries_kb()
    )

async def cb_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    country_key = q.data[2:]
    if country_key not in COUNTRIES:
        await q.answer("Invalid!", show_alert=True); return
    c = COUNTRIES[country_key]
    await q.edit_message_text(
        f"📦 *Order Summary*\n\nCountry: *{c['name']}*\nPrice: *₹{c['price']}*\n\nSelect payment method:",
        parse_mode='Markdown', reply_markup=payment_kb(country_key)
    )

async def cb_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split('_', 2)
    method, country_key = parts[1], parts[2]
    if country_key not in COUNTRIES:
        await q.answer("Invalid!", show_alert=True); return
    c = COUNTRIES[country_key]
    if method == 'upi':
        text = (
            f"💳 *UPI Payment Details*\n\nAmount: *₹{c['price']}*\nUPI ID: `{UPI_ID}`\n\n"
            f"1️⃣ Send ₹{c['price']} to above UPI ID\n"
            f"2️⃣ Take screenshot\n3️⃣ Click button below & send screenshot\n\n"
            f"⚠️ Don't close chat after paying!"
        )
    else:
        usdt_amt = round(c['price'] / 85, 2)
        text = (
            f"💰 *USDT Payment ({USDT_NETWORK})*\n\nAmount: *~${usdt_amt} USDT* (≈₹{c['price']})\n"
            f"Address:\n`{USDT_ADDRESS}`\n\n"
            f"1️⃣ Send USDT to above address\n"
            f"2️⃣ Take screenshot\n3️⃣ Click button below & send screenshot\n\n"
            f"⚠️ Only {USDT_NETWORK} network!"
        )
    await q.edit_message_text(text, parse_mode='Markdown', reply_markup=paid_kb(method, country_key))

async def cb_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split('_', 2)
    method, country_key = parts[1], parts[2]
    context.user_data['pending'] = {'method': method, 'country_key': country_key}
    await q.edit_message_text(
        "📸 *Send Payment Screenshot*\n\n"
        "Send a clear screenshot of your payment.\nAdmin will verify and approve.\n\n"
        "Type /cancel to cancel.",
        parse_mode='Markdown'
    )
    return WAITING_PROOF

async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pending = context.user_data.get('pending')
    if not pending:
        await update.message.reply_text("❌ Session expired. /start again.")
        return ConversationHandler.END

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        is_photo = True
    elif update.message.document:
        file_id = update.message.document.file_id
        is_photo = False
    else:
        await update.message.reply_text("⚠️ Please send a photo/screenshot.")
        return WAITING_PROOF

    method = pending['method']
    country_key = pending['country_key']
    c = COUNTRIES[country_key]
    order_id = create_order(user.id, country_key, method, file_id)

    await update.message.reply_text(
        f"✅ *Order Placed!*\n\nOrder ID: `#{order_id}`\nCountry: {c['name']}\n"
        f"Amount: ₹{c['price']}\nPayment: {method.upper()}\nStatus: ⏳ Pending\n\n"
        f"You'll be notified once approved!\nTrack: /myorders",
        parse_mode='Markdown', reply_markup=main_menu_kb()
    )

    uname = f"@{user.username}" if user.username else f"ID:{user.id}"
    caption = (
        f"🔔 *New Order!*\n\nOrder: `#{order_id}`\nUser: {user.first_name} ({uname})\n"
        f"User ID: `{user.id}`\nCountry: {c['name']}\nPrice: ₹{c['price']}\n"
        f"Payment: {method.upper()}\nTime: {datetime.now().strftime('%d/%m %H:%M')}"
    )
    if is_photo:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id,
                                      caption=caption, parse_mode='Markdown',
                                      reply_markup=admin_kb(order_id))
    else:
        await context.bot.send_document(chat_id=ADMIN_ID, document=file_id,
                                         caption=caption, parse_mode='Markdown',
                                         reply_markup=admin_kb(order_id))
    context.user_data.pop('pending', None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending', None)
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_kb())
    return ConversationHandler.END

async def my_orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_orders(update.effective_user.id, update.message.reply_text)

async def cb_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _send_orders(q.from_user.id, q.edit_message_text,
                       extra_kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))

async def _send_orders(user_id, send_fn, extra_kb=None):
    orders = get_user_orders(user_id)
    if not orders:
        text = "📋 *My Orders*\n\nNo orders yet. Buy your first number! 🚀"
    else:
        text = "📋 *My Orders* (Last 10)\n\n"
        for o in orders:
            em = STATUS_EMOJI.get(o['status'], '❓')
            text += f"{em} *Order #{o['id']}*\n   {o['country_name']} — ₹{o['price']} | {o['payment_method'].upper()}\n   {o['status'].upper()}\n"
            if o['number_delivered']:
                text += f"   📱 `{o['number_delivered']}`\n"
            text += "\n"
    kwargs = {'text': text, 'parse_mode': 'Markdown'}
    if extra_kb:
        kwargs['reply_markup'] = extra_kb
    await send_fn(**kwargs)

# ─── Admin Handlers ───────────────────────────────────────────────────────────
async def cb_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Unauthorized", show_alert=True); return
    await q.answer()
    order_id = int(q.data.split('_')[1])
    order = get_order(order_id)
    if not order:
        await q.answer("Order not found", show_alert=True); return
    if order['status'] != 'pending':
        await q.answer(f"Already {order['status']}", show_alert=True); return

    update_order(order_id, 'approved')
    if 'pending_number' not in context.bot_data:
        context.bot_data['pending_number'] = {}
    context.bot_data['pending_number'][ADMIN_ID] = order_id

    new_cap = (q.message.caption or '') + f"\n\n✅ *APPROVED* — Send number for Order #{order_id}"
    await q.edit_message_caption(caption=new_cap, parse_mode='Markdown')
    await context.bot.send_message(
        chat_id=order['user_id'],
        text=f"✅ *Payment Approved!*\n\nOrder `#{order_id}` — {order['country_name']}\nYour number coming shortly! 🚀",
        parse_mode='Markdown'
    )

async def cb_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Unauthorized", show_alert=True); return
    await q.answer()
    order_id = int(q.data.split('_')[1])
    order = get_order(order_id)
    if not order:
        await q.answer("Order not found", show_alert=True); return

    update_order(order_id, 'rejected')
    await q.edit_message_caption(
        caption=(q.message.caption or '') + "\n\n❌ *REJECTED*",
        parse_mode='Markdown'
    )
    await context.bot.send_message(
        chat_id=order['user_id'],
        text=f"❌ *Order Rejected*\n\nOrder `#{order_id}` payment not verified.\nContact support if this is an error.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📞 Support", url=f"https://t.me/{SUPPORT_USER}")
        ]])
    )

async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    pending = context.bot_data.get('pending_number', {})
    order_id = pending.get(ADMIN_ID)
    if not order_id:
        return

    number = update.message.text.strip()
    order = get_order(order_id)
    if not order:
        await update.message.reply_text("❌ Order not found!"); return

    update_order(order_id, 'delivered', number=number)
    context.bot_data['pending_number'].pop(ADMIN_ID, None)

    await context.bot.send_message(
        chat_id=order['user_id'],
        text=(
            f"📱 *Your Number is Ready!*\n\n"
            f"Order `#{order_id}` — {order['country_name']}\n\n"
            f"Number: `{number}`\n\nThank you! 🎉 Order again anytime 👇"
        ),
        parse_mode='Markdown', reply_markup=main_menu_kb()
    )
    await update.message.reply_text(f"✅ Number delivered for Order #{order_id}!")

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    orders = get_pending_orders()
    if not orders:
        await update.message.reply_text("🎉 No pending orders!"); return
    text = f"📋 *Pending Orders ({len(orders)})*\n\n"
    for o in orders:
        text += f"#️⃣ *#{o['id']}* | {o['country_name']} — ₹{o['price']} | {o['payment_method'].upper()}\n   User: {o['user_id']}\n\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    total, revenue, pending, users = get_stats()
    await update.message.reply_text(
        f"📊 *Stats*\n\n👥 Users: {users}\n✅ Delivered: {total}\n💰 Revenue: ₹{revenue}\n⏳ Pending: {pending}",
        parse_mode='Markdown'
    )

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    context.bot_data.get('pending_number', {}).pop(ADMIN_ID, None)
    await update.message.reply_text("✅ Skipped.")

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_paid, pattern='^paid_')],
        states={WAITING_PROOF: [MessageHandler(filters.PHOTO | filters.Document.ALL, receive_proof)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('myorders', my_orders_cmd))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('orders', cmd_orders))
    app.add_handler(CommandHandler('stats', cmd_stats))
    app.add_handler(CommandHandler('skip', cmd_skip))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern='^main_menu$'))
    app.add_handler(CallbackQueryHandler(cb_browse,    pattern='^browse$'))
    app.add_handler(CallbackQueryHandler(cb_country,   pattern='^c_'))
    app.add_handler(CallbackQueryHandler(cb_payment,   pattern='^pay_'))
    app.add_handler(CallbackQueryHandler(cb_my_orders, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(cb_approve,   pattern='^approve_'))
    app.add_handler(CallbackQueryHandler(cb_reject,    pattern='^reject_'))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        admin_text_handler
    ))

    logger.info("🤖 Bot running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
