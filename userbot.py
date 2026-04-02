import os
import io
import base64
import time
import uuid
import platform
import sys
import speedtest
import asyncio
import urllib.request
import json
import re
import pyrogram
from pyrogram import Client, filters
from pyrogram.types import Message
from openai import AsyncOpenAI
from datetime import timezone, timedelta
from pyrogram import __version__ as pyro_version
from pyrogram.enums import MessageEntityType

# ================== 配置区 ==================
api_id = "YOUR_API_ID"                   
api_hash = "YOUR_API_HASH"  
VPS_IP = "v0s.shiomisha.top"              
WEB_PORT = 8880
TIMEZONE_OFFSET = 8
EXPORT_DIR = "/home/tguser/tg_exports"
# ============================================

os.makedirs(EXPORT_DIR, exist_ok=True)

app = Client("my_userbot", api_id=api_id, api_hash=api_hash)
BOT_START_TIME = time.time()
LOCAL_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET))

def format_duration(seconds):
    if not seconds: return "0:00"
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"

# ====================== 实体解析 ======================
def parse_entities(text, entities):
    if not text: return ""
    if not entities:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    
    b = text.encode('utf-16-le')
    insertions = {}
    for ent in entities:
        start = ent.offset * 2
        end = (ent.offset + ent.length) * 2
        o, c = "", ""
        if ent.type == MessageEntityType.BOLD: o, c = "<strong>", "</strong>"
        elif ent.type == MessageEntityType.ITALIC: o, c = "<em>", "</em>"
        elif ent.type == MessageEntityType.STRIKETHROUGH: o, c = "<s>", "</s>"
        elif ent.type == MessageEntityType.CODE: o, c = "<code>", "</code>"
        elif ent.type == MessageEntityType.PRE: o, c = "<pre>", "</pre>"
        elif ent.type == MessageEntityType.BLOCKQUOTE: o, c = "<blockquote>", "</blockquote>"
        elif ent.type == MessageEntityType.SPOILER: o, c = "<span class='spoiler'>", "</span>"
        elif ent.type == MessageEntityType.TEXT_LINK:
            safe_url = ent.url.replace("&", "&amp;").replace("'", "&#39;")
            o, c = f"<a href='{safe_url}' target='_blank'>", "</a>"
        elif ent.type == MessageEntityType.URL:
            url = b[start:end].decode('utf-16-le')
            o, c = f"<a href='{url}' target='_blank'>", "</a>"
        if o:
            insertions.setdefault(start, []).append(o)
            insertions.setdefault(end, []).insert(0, c)

    res_bytes = bytearray()
    for i in range(0, len(b), 2):
        if i in insertions:
            for tag in insertions[i]: res_bytes.extend(tag.encode('utf-16-le'))
        char_bytes = b[i:i+2]
        if char_bytes == b'<\x00': res_bytes.extend('&lt;'.encode('utf-16-le'))
        elif char_bytes == b'>\x00': res_bytes.extend('&gt;'.encode('utf-16-le'))
        elif char_bytes == b'&\x00': res_bytes.extend('&amp;'.encode('utf-16-le'))
        elif char_bytes == b'\n\x00': res_bytes.extend('<br>'.encode('utf-16-le'))
        else: res_bytes.extend(char_bytes)
    if len(b) in insertions:
        for tag in insertions[len(b)]: res_bytes.extend(tag.encode('utf-16-le'))
    return res_bytes.decode('utf-16-le').replace("<blockquote><br>", "<blockquote>").replace("<br></blockquote>", "</blockquote>")

def get_local_time(dt_obj):
    if not dt_obj: return None
    if dt_obj.tzinfo is None: dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    return dt_obj.astimezone(LOCAL_TZ)

# ====================== 授权管理 ======================
# .auth add [reply/@username/uid] — 授权用户
# .auth del [reply/@username/uid] — 撤销授权
# .auth list — 查看授权列表
# .auth clear — 清空所有授权

AUTH_FILE = "/home/tguser/auth_users.json"

def _load_auth() -> set:
    try:
        with open(AUTH_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_auth(auth_set: set):
    with open(AUTH_FILE, "w") as f:
        json.dump(list(auth_set), f)

AUTHORIZED_USERS: set = _load_auth()

def _is_authorized(_, __, message):
    if message.from_user is None:
        return False
    return message.from_user.id in AUTHORIZED_USERS

authorized_filter = filters.create(_is_authorized)

async def smart_reply(message, text, **kwargs):
    """自动判断：自己的消息用 edit_text，授权用户的消息用 reply"""
    if message.outgoing:
        return await message.edit_text(text, **kwargs)
    else:
        return await message.reply(text, **kwargs)



@app.on_message(filters.me & filters.command("auth", prefixes="."))
async def auth_manager(client, message):
    """管理授权用户白名单 (.auth add/del/list/clear)"""
    global AUTHORIZED_USERS
    args = message.text.split()

    if len(args) < 2:
        return await smart_reply(message, "`用法: .auth add/del/list/clear`")

    action = args[1].lower()

    if action == "list":
        if not AUTHORIZED_USERS:
            return await smart_reply(message, "`授权列表为空`")
        lines = "\n".join([f"• `{uid}`" for uid in AUTHORIZED_USERS])
        return await smart_reply(message, f"**已授权用户：**\n{lines}")

    if action == "clear":
        AUTHORIZED_USERS.clear()
        _save_auth(AUTHORIZED_USERS)
        return await smart_reply(message, "`✅ 已清空所有授权`")

    if action in ("add", "del"):
        target_id = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
        elif len(args) >= 3:
            arg = args[2].lstrip("@")
            try:
                target_id = int(arg)
            except ValueError:
                try:
                    user = await client.get_users(arg)
                    target_id = user.id
                except Exception:
                    return await smart_reply(message, f"`❌ 找不到用户: {arg}`")

        if not target_id:
            return await smart_reply(message, "`❌ 请指定目标用户`")

        if action == "add":
            AUTHORIZED_USERS.add(target_id)
            _save_auth(AUTHORIZED_USERS)
            await smart_reply(message, f"`✅ 已授权 {target_id}`")
        else:
            AUTHORIZED_USERS.discard(target_id)
            _save_auth(AUTHORIZED_USERS)
            await smart_reply(message, f"`✅ 已撤销 {target_id}`")


# ====================== 纯文本 TXT 导出 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("export", prefixes="."))
async def export_history_txt(client, message):
    """纯文本 TXT 导出聊天记录"""
    try:
        args = message.text.split()
        if len(args) == 2 and args[1].isdigit():
            target, limit_count = message.chat.id, int(args[1])
        elif len(args) == 3 and args[2].isdigit():
            target, limit_count = args[1], int(args[2])
        else:
            return await smart_reply(message, "❌ 格式: .export [数量]")

        await smart_reply(message, "⏳ 正在采集...")
        filename = f"export_{message.id}.txt"
        count, last_update = 0, time.time()
        
        with open(filename, "w", encoding="utf-8") as f:
            async for msg in client.get_chat_history(target, limit=limit_count):
                sender = msg.from_user.first_name if msg.from_user else "System"
                parts = []
                if msg.forward_date: parts.append("[转发]")
                if msg.reply_to_message: parts.append("[回复]")
                if msg.photo: parts.append("[图片]")
                elif msg.sticker: parts.append("[贴纸]")
                elif msg.video: parts.append("[视频]")
                elif msg.voice: parts.append("[语音]")
                elif msg.document: parts.append(f"[{msg.document.file_name or '文件'}]")
                text = msg.text or msg.caption or ""
                if text: parts.append(text)
                final_text = " ".join(parts) if parts else "[未知消息]"
                local_dt = get_local_time(msg.date)
                f.write(f"[{local_dt.strftime('%Y-%m-%d %H:%M:%S')}] {sender}: {final_text}\n")
                
                count += 1
                if time.time() - last_update > 0.8:
                    await smart_reply(message, f"⏳ 已抓取: {count}")
                    last_update = time.time()

        await client.send_document(chat_id=message.chat.id, document=filename, caption=f"✅ 纯文本导出完成")
        await message.delete()
        os.remove(filename)
    except Exception as e:
        await smart_reply(message, f"❌ 失败: {str(e)[:150]}")

# ====================== 100% 官方 DOM 结构 HTML 导出 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("exhtml", prefixes="."))
async def export_history_html(client, message):
    """官方 DOM 结构 HTML 导出 (支持预览)"""
    try:
        args = message.text.split()
        if len(args) == 2 and args[1].isdigit():
            target, limit_count = message.chat.id, int(args[1])
        elif len(args) == 3 and args[2].isdigit():
            target = args[1] if args[1].startswith('@') or args[1].replace('-','').isdigit() else message.chat.id
            limit_count = int(args[2])
        else:
            return await smart_reply(message, "❌ 格式: .exhtml [数量] 或 .exhtml @username [数量]")

        await smart_reply(message, "⏳ 正在按官方 DOM 节点生成文件...")
        messages_data = []
        async for msg in client.get_chat_history(target, limit=limit_count):
            messages_data.append(msg)
        messages_data.reverse()
        total_msgs = len(messages_data)

        html_head = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Exported Data</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { box-sizing: border-box; }
body { font-family: "Lucida Grande", "Lucida Sans Unicode", Arial, Helvetica, Verdana, sans-serif; margin: 0; padding: 0; background: #e6ebee; color: #000; overflow-x: hidden; width: 100%; }
.page_wrap { background: #fff; width: 100%; max-width: 600px; margin: 0 auto; padding-bottom: 30px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); min-height: 100vh; overflow-x: hidden; }
.history { padding-top: 20px; }
.clearfix:after { content: " "; visibility: hidden; display: block; height: 0; clear: both; }
.pull_left { float: left; }
.pull_right { float: right; }
.message { margin: 0; padding: 10px 15px; position: relative; width: 100%; }
.message.joined { margin-top: -10px; }
.message:hover { background: #f5f6f7; }
.message + .message:not(.joined) { border-top: 1px solid rgba(125, 125, 125, 0.15); padding-top: 16px; }
.userpic_wrap { margin-right: 15px; flex-shrink: 0; }
.userpic { width: 42px; height: 42px; border-radius: 50%; overflow: hidden; display: block; background: #549bce; color: #fff; text-align: center; line-height: 42px; font-weight: bold; font-size: 16px; }
.userpic img { width: 100%; height: 100%; object-fit: cover; }
.body { margin-left: 57px; overflow: hidden; }
.from_name { color: #3870a0; font-weight: bold; font-size: 14px; margin-bottom: 5px; }
.date { color: #999; font-size: 13px; font-weight: 300; }
.date.details { margin-top: 2px; float: right; margin-left: 10px; }
.text { font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; color: #000; }
.text a { color: #3870a0; text-decoration: none; word-break: break-all; }
.text a:hover { text-decoration: underline; }
.reply_to { color: #3870a0; font-size: 13px; margin-bottom: 4px; cursor: pointer; }
.reply_to a { color: #3870a0; text-decoration: none; }
.forwarded { color: #3870a0; font-size: 13px; margin-bottom: 4px; }
.media_wrap { margin-top: 8px; margin-bottom: 8px; width: 100%; }
.photo_wrap img { max-width: 100%; width: auto; max-height: 350px; border-radius: 8px; display: block; border: 1px solid #e1e1e1; object-fit: contain; }
.video_file_wrap video { max-width: 100%; width: auto; max-height: 350px; border-radius: 8px; display: block; background: #000; object-fit: contain; }
.video_note_wrap video { width: min(100vw - 90px, 200px); height: min(100vw - 90px, 200px); border-radius: 50%; display: block; object-fit: cover; }
.voice_message { display: flex; align-items: center; background: #f0f2f5; padding: 6px 12px; border-radius: 6px; width: fit-content; max-width: 100%; }
.document_wrap { background: #f0f2f5; padding: 8px 12px; border-radius: 6px; display: inline-block; font-size: 14px; color: #3870a0; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { font-size: 12px; color: #999; margin-left: 10px; flex-shrink: 0; }
body.dark { background: #0f0f0f; }
body.dark .page_wrap { background: #1e1e1e; color: #fff; }
body.dark .message:hover { background: #2a2a2a; }
body.dark .text { color: #fff; }
body.dark .from_name, body.dark .reply_to { color: #8774e1; }
body.dark .document_wrap, body.dark .voice_message { background: #2b2b2b; }
body.dark .message + .message:not(.joined) { border-top: 1px solid rgba(255, 255, 255, 0.08); }
</style>
</head>
<body>
<div class="page_wrap">
  <div class="history">
"""

        messages_list, avatar_cache, last_update = [], {}, time.time()

        for i, msg in enumerate(messages_data):
            uid = msg.from_user.id if msg.from_user else 0
            name = msg.from_user.first_name if msg.from_user else "System"
            text_caption = msg.text or msg.caption or ""
            
            is_joined = False
            if i > 0:
                prev_msg = messages_data[i-1]
                prev_uid = prev_msg.from_user.id if prev_msg.from_user else 0
                time_diff = (msg.date - prev_msg.date).total_seconds()
                if prev_uid == uid and time_diff < 900:
                    is_joined = True

            div_classes = "message default clearfix"
            if is_joined: div_classes += " joined"

            msg_html = f'<div class="{div_classes}" id="message{msg.id}">\n'

            if not is_joined:
                if uid not in avatar_cache:
                    if msg.from_user and msg.from_user.photo:
                        try:
                            av_file = await client.download_media(msg.from_user.photo.small_file_id, in_memory=True)
                            if av_file: 
                                avatar_cache[uid] = f"data:image/jpeg;base64,{base64.b64encode(av_file.getvalue()).decode()}"
                            else: avatar_cache[uid] = ""
                        except: avatar_cache[uid] = ""
                    else: avatar_cache[uid] = ""
                
                src = avatar_cache.get(uid, "")
                avatar_dom = f'<img src="{src}" alt="">' if src else (name[0].upper() if name else '?')
                msg_html += f'''  <div class="pull_left userpic_wrap">
    <div class="userpic userpic{str(uid)[-1]}">
      {avatar_dom}
    </div>
  </div>\n'''

            msg_html += '  <div class="body">\n'
            local_dt = get_local_time(msg.date)
            time_str = local_dt.strftime('%H:%M') if local_dt else ""
            full_time = local_dt.strftime('%d.%m.%Y %H:%M:%S') if local_dt else ""
            msg_html += f'    <div class="pull_right date details" title="{full_time}">{time_str}</div>\n'
            if not is_joined: msg_html += f'    <div class="from_name">{name}</div>\n'

            if msg.forward_date:
                fwd_name = msg.forward_from.first_name if msg.forward_from else (msg.forward_from_chat.title if msg.forward_from_chat else "Unknown")
                msg_html += f'    <div class="forwarded details">Forwarded from {fwd_name}</div>\n'

            if msg.reply_to_message:
                r_id = msg.reply_to_message.id
                msg_html += f'    <div class="reply_to details">In reply to <a href="#message{r_id}">this message</a></div>\n'

            if msg.photo or msg.sticker or msg.video or msg.voice or msg.document or msg.video_note:
                msg_html += '    <div class="media_wrap clearfix">\n'
                if msg.sticker:
                    try:
                        if msg.sticker.is_video:
                            f_mem = await client.download_media(msg, in_memory=True)
                            if f_mem:
                                b64_data = base64.b64encode(f_mem.getvalue()).decode()
                                msg_html += f'      <div class="photo_wrap"><video autoplay loop muted playsinline style="max-width:200px;"><source src="data:video/webm;base64,{b64_data}"></video></div>\n'
                        elif msg.sticker.is_animated:
                            if msg.sticker.thumbs:
                                f_mem = await client.download_media(msg.sticker.thumbs[0].file_id, in_memory=True)
                                if f_mem:
                                    b64_data = base64.b64encode(f_mem.getvalue()).decode()
                                    msg_html += f'      <div class="photo_wrap"><img src="data:image/jpeg;base64,{b64_data}" style="max-width:200px; border:none;"></div>\n'
                        else:
                            f_mem = await client.download_media(msg, in_memory=True)
                            if f_mem:
                                b64_data = base64.b64encode(f_mem.getvalue()).decode()
                                msg_html += f'      <div class="photo_wrap"><img src="data:image/webp;base64,{b64_data}" style="max-width:200px; border:none;"></div>\n'
                    except: msg_html += '      <div class="photo_wrap">[贴纸]</div>'
                elif msg.photo:
                    try:
                        p_mem = await client.download_media(msg, in_memory=True)
                        if p_mem: 
                            b64_p = base64.b64encode(p_mem.getvalue()).decode()
                            msg_html += f'      <a class="photo_wrap" href="#"><img src="data:image/jpeg;base64,{b64_p}"></a>\n'
                    except: pass
                elif msg.video_note:
                    try:
                        vn_mem = await client.download_media(msg, in_memory=True)
                        if vn_mem:
                            b64_vn = base64.b64encode(vn_mem.getvalue()).decode()
                            msg_html += f'      <div class="video_note_wrap"><video autoplay loop muted playsinline><source src="data:video/mp4;base64,{b64_vn}"></video></div>\n'
                    except: pass
                elif msg.video:
                    msg_html += f'      <div class="document_wrap">Video File <span class="status">{format_duration(msg.video.duration)}</span></div>\n'
                elif msg.voice:
                    msg_html += f'      <div class="voice_message">Voice message <span class="status">{format_duration(msg.voice.duration)}</span></div>\n'
                elif msg.document:
                    file_name = msg.document.file_name or "File"
                    file_size = f"{msg.document.file_size / 1024:.1f} KB" if msg.document.file_size else ""
                    msg_html += f'      <div class="document_wrap">{file_name} <span class="status">{file_size}</span></div>\n'
                msg_html += '    </div>\n'

            parsed_text = parse_entities(text_caption, msg.entities or msg.caption_entities)
            if parsed_text: msg_html += f'    <div class="text">\n      {parsed_text}\n    </div>\n'
            msg_html += '  </div>\n</div>\n'
            messages_list.append(msg_html)

            if time.time() - last_update > 0.8:
                await smart_reply(message, f"⏳ DOM 构建进度: {i+1}/{total_msgs}")
                last_update = time.time()

        html_foot = "\n  </div>\n</div>\n</body>\n</html>"
        secret_id = uuid.uuid4().hex[:12]
        filename = f"export_{secret_id}.html"
        file_path = os.path.join(EXPORT_DIR, filename)
        with open(file_path, "w", encoding="utf-8") as f: f.write(html_head + "".join(messages_list) + html_foot)
        preview_url = f"http://{VPS_IP}:{WEB_PORT}/{filename}"
        await client.send_document(chat_id=message.chat.id, document=file_path, caption=f"✅ 官方 DOM 结构导出完成！\n\n🌐 在线预览:\n{preview_url}")
        await message.delete()
    except Exception as e:
        await smart_reply(message, f"❌ 失败: {str(e)[:200]}")
        
   
# ====================== 附加指令：动态心跳网络探针 (最终定型版) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("ping", prefixes="."))
async def advanced_ping(client, message):
    """实时跳动更新的动态 Ping 测试"""
    import time
    import platform
    import sys
    import asyncio
    from pyrogram import __version__ as pyro_version
    from pyrogram.raw.functions import Ping
    from pyrogram.errors import FloodWait

    # 1. 发送占位符
    reply = await smart_reply(message, "`[ INITIATING_MTPROTO_PING... ]`")

    # 2. 抓取静态底层信息
    sys_info = f"{platform.system()} {platform.release()}"
    py_ver = sys.version.split(' ')[0]
    
    pings = []
    
    # 3. 开启 10 轮实时心跳测试循环
    for i in range(10):
        try:
            start_time = time.time()
            await client.invoke(Ping(ping_id=int(start_time)))
            current_ping = round((time.time() - start_time) * 1000)
            
            pings.append(current_ping)
            avg_ping = round(sum(pings) / len(pings))
            
            # 动态进度条控制：前9次显示，第10次隐身
            progress_str = f" `[{i+1}/10]`" if i < 9 else ""
            
            # 严格按照你的换行、加粗与空格对齐，并挂载隐藏超链接
            text = (
                f"🏓 **[SHIO.NEXUS](http://{VPS_IP}:{WEB_PORT})** ONLINE\n\n"
                f"⚡ **当前延迟** :  **{current_ping} ms**\n\n"
                f"⏱**平均延迟**:  **{avg_ping} ms**{progress_str}\n\n"
                f"🖥 **宿主** `{sys_info}`\n"
                f"⚙️ **引擎** `Pyrogram v{pyro_version} | Python v{py_ver}`\n"
                f"🌐 **IP** `{VPS_IP}`"
            )
            
            # 关闭网页预览，防止链接刷出大卡片
            await reply.edit_text(text, disable_web_page_preview=True)
            
            # 停顿 0.8 秒形成跳动感
            if i < 9:
                await asyncio.sleep(0.8)
                
        except FloodWait as e:
            # 遇到防刷屏风控主动休眠
            await asyncio.sleep(e.value)
        except Exception:
            pass

# ====================== 附加指令：IP 嗅探与多源纯净度检测 (四核威胁情报版) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("ip", prefixes="."))
async def check_ip(client, message):
    """多源交叉验证 IP 风险、欺诈评分与归属地"""
    try:
        target_ip = ""

        # 优先从回复消息中提取 IP
        if message.reply_to_message:
            reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
            ip_match = re.search(
                r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
                r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b', reply_text
            )
            if ip_match:
                target_ip = ip_match.group(0)

        if not target_ip:
            args = message.text.split()
            if len(args) > 1:
                target_ip = args[1]

        if not target_ip:
            return await smart_reply(message, "❌ 请直接跟上 IP 地址，或回复一条含 IP 的消息")

        reply = await smart_reply(message, f"`[System] 正在启动 4 核威胁情报矩阵探测 {target_ip} ...`")

        # ── 国旗 emoji ────────────────────────────────────────────────────────
        def get_flag(code):
            if not code or len(code) != 2: return "🏳️"
            return "".join(chr(ord(c) + 127397) for c in code.upper())

        # ── 并发请求四个接口 ──────────────────────────────────────────────────
        def multi_ip_check(ip_addr):
            apis = {
                "ip-api":     f"http://ip-api.com/json/{ip_addr}?fields=status,query,country,countryCode,regionName,city,isp,org,as,mobile,proxy,hosting",
                "ipwho.is":   f"https://ipwho.is/{ip_addr}",
                "freeipapi":  f"https://freeipapi.com/api/json/{ip_addr}",
                "proxycheck": f"http://proxycheck.io/v2/{ip_addr}?vpn=1&asn=1&risk=1",
            }
            res = {}
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            for name, url in apis.items():
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            if name == "ipwho.is" and not data.get("success", True):
                                res[name] = {"error": data.get("message", "未知")}
                            elif name == "proxycheck":
                                res[name] = data.get(ip_addr, data) if data.get("status") == "ok" else data
                            else:
                                res[name] = data
                        else:
                            res[name] = {"error": f"HTTP {resp.status}"}
                except Exception as e:
                    res[name] = {"error": str(e)}
            return res

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, multi_ip_check, target_ip)

        if all("error" in r for r in results.values()):
            return await reply.edit_text(f"❌ 所有接口均请求失败\n`{results}`")

        r_ipapi = results.get("ip-api", {})
        r_whois = results.get("ipwho.is", {})
        r_free  = results.get("freeipapi", {})
        r_pc    = results.get("proxycheck", {})

        # ── 多源投票取最可信值 ────────────────────────────────────────────────
        def majority(lst):
            lst = [x for x in lst if x]
            return max(set(lst), key=lst.count) if lst else "未知"

        query_ip    = r_ipapi.get("query") or r_whois.get("ip") or r_free.get("ipAddress") or target_ip
        final_cc    = majority([r_ipapi.get("countryCode"), r_whois.get("country_code"), r_free.get("countryCode")])
        final_country = majority([r_ipapi.get("country"), r_whois.get("country"), r_free.get("countryName")])
        final_city  = majority([r_ipapi.get("city"), r_whois.get("city"), r_free.get("cityName")])
        final_isp   = majority([
            r_ipapi.get("isp") or r_ipapi.get("org"),
            r_whois.get("connection", {}).get("isp") or r_whois.get("connection", {}).get("org"),
            r_free.get("asnOrganization"),
            r_pc.get("provider"),
        ])

        flag = get_flag(final_cc)

        # ── 风险判定 ──────────────────────────────────────────────────────────
        risk_score = int(r_pc.get("risk") or 0)
        pc_type    = r_pc.get("type", "")

        is_proxy = any([
            r_ipapi.get("proxy") is True,
            r_whois.get("security", {}).get("proxy") is True,
            r_free.get("isProxy") is True,
            r_pc.get("proxy") == "yes",
        ])
        is_hosting = any([
            r_ipapi.get("hosting") is True,
            r_whois.get("security", {}).get("hosting") is True,
        ])
        is_mobile = r_ipapi.get("mobile") is True
        conn_type  = str(r_whois.get("connection", {}).get("type", "")).lower()

        # 网络类型
        if is_mobile:
            net_type = "📱 蜂窝移动 (Cellular)"
        elif "business" in conn_type or "corporate" in conn_type:
            net_type = "🏢 企业专线 (Corporate)"
        elif "education" in conn_type:
            net_type = "🏫 教育网 (Education)"
        elif is_hosting or "hosting" in conn_type:
            net_type = "🖥️ 数据中心 (Hosting)"
        else:
            net_type = "🏠 住宅宽带 (Residential)"

        # 安全评级
        if risk_score > 66 or is_proxy:
            sec_level = "🔴 高风险 (Dirty / Proxy)"
            scene     = f"⚠️ 匿名节点 ({pc_type or 'VPN/Proxy'})"
        elif risk_score > 33 or is_hosting:
            sec_level = "🟡 中度嫌疑 (Suspicious)"
            scene     = "☁️ 机房网络 (Datacenter)"
        else:
            sec_level = "🟢 纯净 (Clean)"
            scene     = "🌐 常规终端 (End-User)"

        # ── 各源摘要 ──────────────────────────────────────────────────────────
        def src_line(r, keys, fallback="获取失败"):
            if r.get("error"): return fallback
            parts = [str(r.get(k, "")) for k in keys if r.get(k)]
            return " | ".join(parts) if parts else fallback

        ipapi_disp = src_line(r_ipapi, ["country", "isp", "org"])
        whois_disp = src_line(r_whois, ["country"]) + " | " + src_line(r_whois.get("connection", {}), ["isp", "org"]) if not r_whois.get("error") else "获取失败"
        free_disp  = src_line(r_free,  ["countryName", "asnOrganization"])
        pc_disp    = f"风险值: {risk_score} | 类型: {pc_type or 'N/A'}" if not r_pc.get("error") else "获取失败"

        # ── 组装输出 ──────────────────────────────────────────────────────────
        text = (
            f"**🛡 IP 威胁雷达 — {query_ip}**\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"安全评级：{sec_level}\n"
            f"地理位置：{flag} {final_country} · {final_city}\n"
            f"网络服务：`{final_isp}`\n"
            f"网络类型：{net_type}\n"
            f"使用场景：{scene}\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"**>** **[四源指纹矩阵]**\n"
            f"**>** `proxycheck` : {pc_disp}\n"
            f"**>** `ip-api`     : {ipapi_disp}\n"
            f"**>** `ipwho.is`   : {whois_disp}\n"
            f"**>** `freeipapi`  : {free_disp}"
        )

        await reply.edit_text(text)

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        await smart_reply(message, f"❌ **核心交叉验证失败**:\n`{str(e)}`\n\n`{error_details[-300:]}`")


# ====================== 附加指令：批量删除自己的消息 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("d", prefixes="."))
async def delete_my_messages(client, message):
    """批量删除自己发出的消息"""
    _MAX = 100  # 硬上限，超出自动截断

    try:
        args = message.text.split()

        # 回复模式：删除指令本身 + 被回复的消息（如果是自己发的）
        if message.reply_to_message and len(args) == 1:
            ids = [message.id]
            if message.reply_to_message.outgoing:
                ids.append(message.reply_to_message.id)
            await client.delete_messages(message.chat.id, ids)
            return

        # 解析数量，超出硬上限直接截断
        count = 1
        if len(args) > 1 and args[1].isdigit():
            count = min(int(args[1]), _MAX)

        # 扫描历史：只扫 count*3 条（保证能找到足够的 outgoing）
        # 最多不超过 _MAX*3=300，避免大数字时卡住
        scan_limit = min(count * 3, _MAX * 3)
        message_ids = []

        async for msg in client.get_chat_history(message.chat.id, limit=scan_limit):
            if msg.outgoing:
                message_ids.append(msg.id)
            if len(message_ids) >= count + 1:  # +1 包含指令消息本身
                break

        if message_ids:
            # 每批最多100条，分批删除
            for i in range(0, len(message_ids), 100):
                await client.delete_messages(message.chat.id, message_ids[i:i+100])

    except Exception as e:
        try:
            await smart_reply(message, f"❌ 删除失败: `{str(e)}`")
        except:
            pass

# ====================== 附加指令：语录贴纸生成 (QuotLy API 正式版) ======================
# 严格按官方文档：photo 传 file_id，sticker 传 file_id，API 服务器自行下载
# 无需托管文件，无需 URL，直接传 Telegram file_id

async def _yl_call_api(payload: dict) -> bytes | None:
    from curl_cffi.requests import AsyncSession
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            resp = await session.post("https://bot.lyo.su/quote/generate", json=payload, timeout=20)
        if resp.status_code != 200: return None
        data = resp.json()
        if not data.get("ok") or not data.get("result", {}).get("image"): return None
        return base64.b64decode(data["result"]["image"])
    except Exception:
        return None


@app.on_message((filters.me | authorized_filter) & filters.command("yl", prefixes="."))
async def generate_quote_sticker(client, message):
    """QuotLy API 语录贴纸：直接传 file_id，API 服务器自行下载头像和贴纸"""
    if not message.reply_to_message:
        return await smart_reply(message, "❌ 请回复消息")

    reply = await smart_reply(message, "⏳ `[YL] 正在生成语录贴纸...`")
    src = message.reply_to_message

    try:
        # ── 发送者 ────────────────────────────────────────────────────────────
        if src.from_user:
            u = src.from_user
            uid = u.id
            name = ((u.first_name or "") + (" " + u.last_name if u.last_name else "")).strip() or "Unknown"
        elif src.sender_chat:
            uid = src.sender_chat.id
            name = src.sender_chat.title or "Unknown"
        else:
            uid, name = 0, "Unknown"

        # ── 头像：直接传 file_id，官方文档格式 ───────────────────────────────
        # ── 头像：下载后托管到 VPS web 目录，传公网 URL 给 API ──────────────
        # QuotLy API 的 bot token 无法下载其他用户的 file_id，必须用真实 URL
        av_tmp = None
        from_obj = {"id": uid, "name": name}
        if src.from_user:
            if src.from_user.first_name: from_obj["first_name"] = src.from_user.first_name
            if src.from_user.last_name:  from_obj["last_name"]  = src.from_user.last_name
            if src.from_user.username:   from_obj["username"]   = src.from_user.username

        try:
            mem = None
            photo_src = None
            if src.from_user and src.from_user.photo:
                photo_src = src.from_user.photo.small_file_id
            elif src.sender_chat and src.sender_chat.photo:
                photo_src = src.sender_chat.photo.small_file_id

            if not photo_src:
                # 兜底 get_profile_photos
                sid = src.from_user.id if src.from_user else (src.sender_chat.id if src.sender_chat else None)
                if sid:
                    photos = await client.get_profile_photos(sid, limit=1)
                    if photos and photos.total_count > 0:
                        photo_src = photos[0][-1].file_id

            if photo_src:
                mem = await client.download_media(photo_src, in_memory=True)

            if mem:
                sid_str = str(uid)
                av_fname = f"yl_av_{sid_str}_{int(time.time())}.jpg"
                av_tmp   = os.path.join(EXPORT_DIR, av_fname)
                with open(av_tmp, "wb") as f:
                    f.write(mem.getvalue())
                av_url = f"http://{VPS_IP}:{WEB_PORT}/{av_fname}"
                from_obj["photo"] = {"url": av_url}
        except Exception:
            pass

        # ── 消息文本 ──────────────────────────────────────────────────────────
        text_content = src.text or src.caption or ""
        is_sticker = bool(src.sticker)
        is_photo   = bool(src.photo)
        if not text_content and not is_sticker and not is_photo:
            text_content = {"video": "🎬 视频", "voice": "🎤 语音", "audio": "🎵 音频",
                            "document": "📄 文件", "video_note": "📹 视频消息"}.get(
                next((k for k in ["video","voice","audio","document","video_note"]
                      if getattr(src, k, None)), None), " ")
        elif not text_content:
            text_content = " "

        # ── entities ─────────────────────────────────────────────────────────
        entities = []
        for ent in (src.entities or src.caption_entities or []):
            e = {"type": str(ent.type).replace("MessageEntityType.", "").lower(),
                 "offset": ent.offset, "length": ent.length}
            if hasattr(ent, "url") and ent.url: e["url"] = ent.url
            entities.append(e)

        # ── 引用消息 ──────────────────────────────────────────────────────────
        reply_msg_obj = {}
        if src.reply_to_message:
            r = src.reply_to_message
            rname = (r.from_user.first_name or "") if r.from_user else (r.sender_chat.title if r.sender_chat else "")
            reply_msg_obj = {"name": rname, "text": r.text or r.caption or "",
                             "entities": [], "chatId": r.chat.id if r.chat else 0}

        # ── 消息对象 ──────────────────────────────────────────────────────────
        msg_obj = {
            "entities": entities,
            "avatar": True,
            "from": from_obj,
            "text": text_content,
            "replyMessage": reply_msg_obj,
            "chatId": src.chat.id if src.chat else 0,
        }

        # ── 贴纸/图片：下载后托管，传 URL 给 API ────────────────────────────
        media_tmp = None
        if is_sticker or is_photo:
            try:
                if is_sticker:
                    st = src.sticker
                    if st.is_animated or st.is_video:
                        dl_target = st.thumbs[0].file_id if st.thumbs else None
                    else:
                        dl_target = src
                    w, h = st.width or 512, st.height or 512
                    mtype = "sticker"
                    ext   = "jpg" if (st.is_animated or st.is_video) else "webp"
                else:
                    dl_target = src
                    w = h = 0
                    mtype = "image"
                    ext   = "jpg"

                if dl_target:
                    mem = await client.download_media(dl_target, in_memory=True)
                    if mem:
                        m_fname = f"yl_media_{uid}_{int(time.time())}.{ext}"
                        media_tmp = os.path.join(EXPORT_DIR, m_fname)
                        with open(media_tmp, "wb") as f:
                            f.write(mem.getvalue())
                        m_url = f"http://{VPS_IP}:{WEB_PORT}/{m_fname}"
                        entry = {"url": m_url}
                        if w and h:
                            entry["width"]  = w
                            entry["height"] = h
                        msg_obj["media"]     = [entry]
                        msg_obj["mediaType"] = mtype
            except Exception:
                pass

        # ── 调用 API ──────────────────────────────────────────────────────────
        img_bytes = await _yl_call_api({
            "type": "quote", "format": "webp", "backgroundColor": "#1b1429", "width": 512,
            "messages": [msg_obj]
        })

        if not img_bytes:
            return await reply.edit_text("❌ QuotLy API 无响应，稍后重试")

        buf = io.BytesIO(img_bytes); buf.name = "quote.webp"
        await client.send_sticker(chat_id=message.chat.id, sticker=buf, reply_to_message_id=src.id)
        await message.delete()

    except Exception as e:
        await reply.edit_text(f"❌ 失败: `{str(e)[:200]}`")
    finally:
        for tmp in [av_tmp if "av_tmp" in dir() else None,
                    media_tmp if "media_tmp" in dir() else None]:
            try:
                if tmp and os.path.exists(tmp): os.remove(tmp)
            except Exception: pass


# ====================== 附加指令：VPS 硬件探针面板 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("vps", prefixes="."))
async def vps_status(client, message):
    """监控服务器 CPU/内存/硬盘/网络/进程状态"""
    try:
        import psutil
        from datetime import datetime, timedelta

        reply = await smart_reply(message, "`[System] 正在读取底层硬件信息...`")

        # ── CPU ──────────────────────────────────────────────────────────────
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count_logical  = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)
        cpu_freq = psutil.cpu_freq()
        freq_str = f"{cpu_freq.current:.0f} MHz" if cpu_freq else "N/A"
        load1, load5, load15 = psutil.getloadavg()

        # ── 内存 ─────────────────────────────────────────────────────────────
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # ── 硬盘 ─────────────────────────────────────────────────────────────
        disk = psutil.disk_usage("/")
        try:
            dio = psutil.disk_io_counters()
            dio_str = f"读 {dio.read_bytes/1024**3:.1f}G / 写 {dio.write_bytes/1024**3:.1f}G"
        except Exception:
            dio_str = "N/A"

        # ── 网络 ─────────────────────────────────────────────────────────────
        net = psutil.net_io_counters()
        rx_gb = net.bytes_recv / 1024**3
        tx_gb = net.bytes_sent / 1024**3

        # ── 进程 & 运行时间 ───────────────────────────────────────────────────
        proc_count = len(psutil.pids())
        boot_time  = psutil.boot_time()
        uptime_sec = time.time() - boot_time
        uptime_str = str(timedelta(seconds=int(uptime_sec)))

        # ── 进度条辅助 ────────────────────────────────────────────────────────
        def bar(percent, width=10):
            filled = int(width * percent / 100)
            return "█" * filled + "░" * (width - filled)

        text = (
            f"🖥 **VPS 实时状态面板**\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"**⚙️ CPU**\n"
            f"`{bar(cpu_percent)}` `{cpu_percent:.1f}%`\n"
            f"核心: `{cpu_count_physical}C{cpu_count_logical}T` | 频率: `{freq_str}`\n"
            f"负载: `{load1:.2f}` `{load5:.2f}` `{load15:.2f}` (1/5/15m)\n\n"
            f"**💾 内存**\n"
            f"`{bar(mem.percent)}` `{mem.percent:.1f}%`\n"
            f"已用: `{mem.used/1024**3:.1f}G / {mem.total/1024**3:.1f}G`\n"
            f"Swap: `{swap.used/1024**3:.1f}G / {swap.total/1024**3:.1f}G` (`{swap.percent}%`)\n\n"
            f"**💽 硬盘**\n"
            f"`{bar(disk.percent)}` `{disk.percent:.1f}%`\n"
            f"已用: `{disk.used/1024**3:.1f}G / {disk.total/1024**3:.1f}G`\n"
            f"I/O 累计: `{dio_str}`\n\n"
            f"**🌐 网络** (累计)\n"
            f"↓ 接收: `{rx_gb:.2f} GB` | ↑ 发送: `{tx_gb:.2f} GB`\n\n"
            f"**📊 系统**\n"
            f"进程数: `{proc_count}` | 运行时长: `{uptime_str}`"
        )

        await reply.edit_text(text)

    except Exception as e:
        await smart_reply(message, f"❌ 获取失败: `{str(e)}`")


# ====================== 修复版：内联跨语种翻译 ======================
# 用法:
#   .fy [文本]            — 自动检测源语言，翻译为中文
#   .fy en [文本]         — 翻译为英文
#   .fy ja                — 回复消息翻译为日文
#   .fy                   — 回复消息，自动翻译为中文
#
# 语言代码: zh-CN / en / ja / ko / ru / fr / de / es / th / ...

# 常用语言快捷别名
_LANG_ALIASES = {
    "中": "zh-CN", "中文": "zh-CN", "cn": "zh-CN",
    "英": "en",    "英文": "en",
    "日": "ja",    "日文": "ja",   "日语": "ja",
    "韩": "ko",    "韩文": "ko",
    "俄": "ru",    "俄文": "ru",
    "法": "fr",    "德": "de",
    "西": "es",    "泰": "th",
}

@app.on_message((filters.me | authorized_filter) & filters.command(["fy", "tr"], prefixes="."))
async def translate_text(client, message):
    """多语种内联翻译，支持回复消息、语言别名"""
    import urllib.parse
    from curl_cffi.requests import AsyncSession

    args = message.text.split(maxsplit=2)
    target_lang = "zh-CN"
    text_to_translate = ""

    if len(args) >= 2:
        maybe_lang = args[1].lower().strip()
        # 判断第二个参数是语言代码还是文本内容
        resolved = _LANG_ALIASES.get(maybe_lang, maybe_lang)
        # 语言代码：纯字母+连字符，且较短（en / zh-CN / ja 等）
        is_lang = bool(re.match(r'^[a-zA-Z]{2,5}(-[a-zA-Z]{2,4})?$', resolved)) and len(resolved) <= 8
        if is_lang:
            target_lang = resolved
            text_to_translate = args[2] if len(args) >= 3 else ""
        else:
            # 第二个参数就是要翻译的文本
            text_to_translate = message.text.split(maxsplit=1)[1]

    # 没有文本就从回复消息取
    if not text_to_translate and message.reply_to_message:
        text_to_translate = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not text_to_translate:
        return await smart_reply(message,
            "**🌍 翻译引擎**\n\n"
            "`.fy [文本]` — 翻译为中文\n"
            "`.fy en [文本]` — 翻译为英文\n"
            "`.fy ja` — 回复消息翻译为日文\n"
            "`.fy` — 回复消息自动翻译为中文\n\n"
            "支持别名: `中/英/日/韩/俄/法/德` 等"
        )

    reply = await smart_reply(message, "`🌍 正在请求翻译节点...`")

    # 截断超长文本，Google Translate 有 URL 长度限制
    if len(text_to_translate) > 2000:
        text_to_translate = text_to_translate[:2000]

    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={urllib.parse.quote(target_lang)}"
            f"&dt=t&q={urllib.parse.quote(text_to_translate)}"
        )
        async with AsyncSession(impersonate="chrome120") as session:
            res = await session.get(url, timeout=10)

        if res.status_code == 200:
            data = res.json()
            translated = "".join(seg[0] for seg in data[0] if seg[0])
            source_lang = data[2] or "auto"
            await reply.edit_text(
                f"**[译文]** `{source_lang}` ➔ `{target_lang}`\n"
                f"{translated}"
            )
        else:
            await reply.edit_text(f"❌ API 拦截: HTTP {res.status_code}")

    except Exception as e:
        await reply.edit_text(f"❌ 翻译失败: `{str(e)[:150]}`")


# ====================== 附加指令：节点/文本一键转二维码 ======================
# .qr [文本/链接]          — 直接生成二维码
# .qr                      — 回复消息，提取文本/图片Caption/文件名生成二维码
# .qr logo                 — 回复图片，将该图片作为 Logo 嵌入二维码中心
#                            用法: .qr logo [文本]  或  回复图片 + .qr logo [文本]
# 图片/文件大小限制: 5MB

@app.on_message((filters.me | authorized_filter) & filters.command("qr", prefixes="."))
async def generate_qr(client, message):
    """文本或代理链接一键转二维码，支持嵌入图片 Logo"""
    import qrcode
    import io

    MAX_SIZE = 5 * 1024 * 1024  # 5MB
    args = message.text.split(maxsplit=2)
    replied = message.reply_to_message

    # ── 解析模式 ──────────────────────────────────────────────────────────────
    logo_mode = len(args) >= 2 and args[1].lower() == "logo"
    logo_bytes = None

    if logo_mode:
        # logo 模式：从回复的图片提取 logo
        qr_text = args[2] if len(args) >= 3 else ""
        if not qr_text and replied:
            qr_text = replied.caption or replied.text or ""
        if replied and replied.photo:
            reply = await smart_reply(message, "`[QR] 正在提取 Logo 图片...`")
            try:
                if replied.photo.file_size and replied.photo.file_size > MAX_SIZE:
                    return await reply.edit_text(f"❌ 图片超过 5MB 限制")
                logo_mem = await client.download_media(replied, in_memory=True)
                logo_bytes = logo_mem.getvalue()
            except Exception as e:
                return await reply.edit_text(f"❌ Logo 提取失败: `{str(e)[:100]}`")
        else:
            reply = await smart_reply(message, "`[QR] 正在生成...`")
    else:
        reply = await smart_reply(message, "`[QR] 正在矩阵化渲染...`")
        qr_text = args[1] if len(args) >= 2 else ""

        # 从回复消息提取内容
        if not qr_text and replied:
            if replied.text:
                qr_text = replied.text
            elif replied.caption:
                qr_text = replied.caption
            elif replied.document:
                qr_text = replied.document.file_name or ""
            elif replied.photo:
                qr_text = replied.caption or ""

    if not qr_text:
        return await reply.edit_text(
            "**二维码生成器**\n\n"
            "`.qr [文本/链接]` — 生成二维码\n"
            "`.qr` — 回复消息自动提取内容\n"
            "`.qr logo [文本]` — 回复图片作为中心 Logo\n\n"
            "图片大小限制: 5MB"
        )

    # QR 内容长度检查（QR 码容量上限约 2953 字节）
    if len(qr_text.encode("utf-8")) > 2900:
        qr_text = qr_text[:900]  # 截断防止生成失败

    try:
        await reply.edit_text("`[QR] 正在渲染...`")

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_H if logo_bytes else qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_text)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

        # ── 嵌入 Logo ─────────────────────────────────────────────────────────
        if logo_bytes:
            try:
                from PIL import Image
                logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")

                # Logo 占 QR 图宽度的 20%
                qr_w, qr_h = qr_img.size
                logo_max = int(qr_w * 0.20)
                logo.thumbnail((logo_max, logo_max), Image.LANCZOS)

                # 圆角遮罩
                logo_w, logo_h = logo.size
                mask = Image.new("L", (logo_w, logo_h), 0)
                from PIL import ImageDraw
                draw = ImageDraw.Draw(mask)
                radius = logo_w // 5
                draw.rounded_rectangle([0, 0, logo_w, logo_h], radius=radius, fill=255)
                logo.putalpha(mask)

                # 白色背景衬底
                pad = 6
                bg = Image.new("RGBA", (logo_w + pad*2, logo_h + pad*2), (255, 255, 255, 255))
                bg.paste(logo, (pad, pad), logo)

                # 居中粘贴
                pos = ((qr_w - bg.width) // 2, (qr_h - bg.height) // 2)
                qr_img.paste(bg, pos, bg)
            except Exception:
                pass  # Logo 嵌入失败不影响基础 QR 输出

        # ── 输出 ──────────────────────────────────────────────────────────────
        out = io.BytesIO()
        qr_img.save(out, format="PNG")
        out.name = "qr.png"
        out.seek(0)

        preview = qr_text[:40] + ("..." if len(qr_text) > 40 else "")
        caption = f"✅ **二维码**{'（含 Logo）' if logo_bytes else ''}\n`{preview}`"

        await client.send_photo(
            chat_id=message.chat.id,
            photo=out,
            reply_to_message_id=replied.id if replied else None,
            caption=caption
        )
        await message.delete()

    except Exception as e:
        await reply.edit_text(f"❌ 渲染失败: `{str(e)[:150]}`")


# ====================== 附加指令：随机数生成 ======================
@app.on_message((filters.me | authorized_filter) & filters.command(["rand", "roll"], prefixes="."))
async def random_generator(client, message):
    """生成基于系统熵源的真随机数"""
    import secrets
    args = message.text.split()

    try:
        if len(args) == 1:
            start, end = 1, 100
        elif len(args) == 2:
            start, end = 1, int(args[1])
        elif len(args) >= 3:
            start, end = int(args[1]), int(args[2])

        if start > end:
            start, end = end, start

        # secrets.randbelow 基于 os.urandom()，走内核 /dev/urandom
        # 对 KVM 虚拟化环境，底层由 virtio-rng 或 host 的 RDRAND 喂熵
        result = start + secrets.randbelow(end - start + 1)

        await smart_reply(message,
            f"🎲 **Roll ({start} - {end}):**\n"
            f"`{result}`"
        )

    except ValueError:
        await smart_reply(message, "❌ 参数错误，请输入整数。例如: `.roll 10 50`")


# ====================== 附加指令：文本/文件哈希计算器 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("hash", prefixes="."))
async def hash_calculator(client, message):
    """计算文本或文件的 MD5/SHA1/SHA256/SHA3-256 哈希值"""
    import hashlib

    MAX_SIZE = 500 * 1024 * 1024  # 500MB 硬上限
    CHUNK    = 4 * 1024 * 1024    # 4MB 分块

    args       = message.text.split(maxsplit=1)
    text       = args[1] if len(args) > 1 else ""
    target_msg = message.reply_to_message

    # ── 初始化四个哈希器 ──────────────────────────────────────────────────────
    def new_hashers():
        return {
            "MD5":      hashlib.md5(),
            "SHA1":     hashlib.sha1(),
            "SHA256":   hashlib.sha256(),
            "SHA3-256": hashlib.sha3_256(),
        }

    def digest(hashers):
        return {k: v.hexdigest() for k, v in hashers.items()}

    def feed(hashers, chunk):
        for h in hashers.values():
            h.update(chunk)

    # ── 纯文本模式 ────────────────────────────────────────────────────────────
    if text:
        data = text.encode("utf-8")
        hashers = new_hashers()
        feed(hashers, data)
        results = digest(hashers)
        preview = text[:40] + ("..." if len(text) > 40 else "")
        display_info = f"**源文本:** `{preview}`\n**字节数:** `{len(data)} B`"
        reply_msg = message

    # ── 媒体/文件模式 ─────────────────────────────────────────────────────────
    elif target_msg and target_msg.media:
        # 获取文件大小
        file_size = 0
        file_name = "未知文件"
        if target_msg.document:
            file_size = target_msg.document.file_size or 0
            file_name = target_msg.document.file_name or "Document"
        elif target_msg.photo:
            file_size = target_msg.photo.file_size or 0
            file_name = "Photo.jpg"
        elif target_msg.video:
            file_size = target_msg.video.file_size or 0
            file_name = target_msg.video.file_name or "Video.mp4"
        elif target_msg.sticker:
            file_name = "Sticker.webp"
        elif target_msg.voice:
            file_name = "Voice.ogg"
        elif target_msg.audio:
            file_size = target_msg.audio.file_size or 0
            file_name = target_msg.audio.file_name or "Audio"

        if file_size > MAX_SIZE:
            return await smart_reply(message,
                f"❌ 文件超过 500MB 上限 (`{file_size/1024**2:.1f} MB`)")

        reply_msg = await smart_reply(message,
            f"`[Hash] 正在流式读取 {file_name}...`")

        try:
            # 流式下载到临时文件，分块喂给哈希器，不全量进内存
            tmp_path = f"/tmp/hash_{message.id}.tmp"
            await client.download_media(target_msg, file_name=tmp_path)

            actual_size = os.path.getsize(tmp_path)
            hashers = new_hashers()

            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    feed(hashers, chunk)

            os.remove(tmp_path)
            results = digest(hashers)
            size_str = (f"{actual_size/1024**2:.2f} MB"
                        if actual_size >= 1024**2
                        else f"{actual_size/1024:.1f} KB")
            display_info = f"**源文件:** `{file_name}`\n**体积:** `{size_str}`"

        except Exception as e:
            try: os.remove(tmp_path)
            except: pass
            return await reply_msg.edit_text(f"❌ 读取失败: `{str(e)[:150]}`")

    # ── 回复文本模式 ──────────────────────────────────────────────────────────
    elif target_msg:
        text = target_msg.text or target_msg.caption or ""
        if not text:
            return await smart_reply(message, "❌ 回复的消息中没有文本或文件。")
        data = text.encode("utf-8")
        hashers = new_hashers()
        feed(hashers, data)
        results = digest(hashers)
        preview = text[:40] + ("..." if len(text) > 40 else "")
        display_info = f"**源文本:** `{preview}`"
        reply_msg = message

    else:
        return await smart_reply(message,
            "❌ 请在指令后附带文本，或回复一段文字/图片/文件。")

    # ── 输出 ──────────────────────────────────────────────────────────────────
    out = (
        f"**[Hash 计算报告]**\n\n"
        f"{display_info}\n\n"
        + "\n".join(f"**{k}:**\n`{v}`" for k, v in results.items())
    )

    if reply_msg is message:
        await smart_reply(message, out)
    else:
        await reply_msg.edit_text(out)


# ====================== 附加指令：Edge TTS 神经网络语音合成 ======================
# 引擎: Microsoft Edge Neural TTS (免费，无限制，无需 API Key)
# .tts [文本]              — 使用默认声线 (晓晓 zh-CN)
# .tts -v [声线] [文本]    — 指定声线
# .tts -list               — 列出所有中文声线
#
# 常用声线:
#   zh-CN-XiaoxiaoNeural   晓晓 (默认，温柔女声)
#   zh-CN-YunxiNeural      云希 (男声)
#   zh-CN-XiaoyiNeural     晓伊 (活泼女声)
#   zh-TW-HsiaoChenNeural  台湾女声
#   ja-JP-NanamiNeural     日语女声
#   en-US-JennyNeural      英语女声

_TTS_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

@app.on_message((filters.me | authorized_filter) & filters.command("tts", prefixes="."))
async def text_to_speech(client, message):
    """Edge TTS 神经网络语音合成 (免费无限制)"""
    try:
        import edge_tts
    except ImportError:
        return await smart_reply(message,
            "❌ 缺少依赖，请执行:\n"
            "`pip install edge-tts`"
        )

    args = message.text.split(maxsplit=1)
    param = args[1].strip() if len(args) > 1 else ""

    # ── 列出声线 ──────────────────────────────────────────────────────────────
    if param == "-list":
        try:
            voices = await edge_tts.list_voices()
            zh_voices = [v for v in voices if v["Locale"].startswith("zh")]
            lines = [f"`{v['ShortName']}` — {v['FriendlyName']}" for v in zh_voices]
            return await smart_reply(message,
                f"**🎙 中文声线列表 ({len(zh_voices)} 个)**\n\n" + "\n".join(lines)
            )
        except Exception as e:
            return await smart_reply(message, f"❌ 获取声线失败: `{str(e)}`")

    # ── 解析声线和文本 ────────────────────────────────────────────────────────
    voice = _TTS_DEFAULT_VOICE
    text  = ""

    if param.startswith("-v "):
        rest = param[3:].strip()
        parts = rest.split(maxsplit=1)
        if parts:
            voice = parts[0]
            text  = parts[1] if len(parts) > 1 else ""
    else:
        text = param

    # 回复消息补充文本
    if not text and message.reply_to_message:
        text = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not text:
        return await smart_reply(message,
            "**🎙 Edge TTS**\n\n"
            "`.tts [文本]` — 合成语音\n"
            "`.tts -v zh-CN-YunxiNeural 你好` — 指定声线\n"
            "`.tts -list` — 查看全部中文声线\n\n"
            f"当前默认声线: `{_TTS_DEFAULT_VOICE}`"
        )

    # 文本长度限制（Edge TTS 单次上限约 5000 字）
    if len(text) > 3000:
        text = text[:3000]

    reply = await smart_reply(message, f"`[TTS] 正在合成语音 ({voice})...`")
    output_file = f"/tmp/tts_{message.id}.mp3"

    try:
        communicate = edge_tts.Communicate(text=text, voice=voice)
        await communicate.save(output_file)

        await client.send_voice(
            chat_id=message.chat.id,
            voice=output_file,
            reply_to_message_id=message.reply_to_message.id if message.reply_to_message else None,
            caption=f"🎙 `{voice}`"
        )
        await message.delete()

    except Exception as e:
        await reply.edit_text(f"❌ 合成失败: `{str(e)[:150]}`")
    finally:
        if os.path.exists(output_file):
            os.remove(output_file)

# ====================== 附加指令：聊天状态伪装 ======================
# 用法: .wz [状态] [秒数(可选，默认5s)]
#
# 状态关键词 (中英文均可):
#   输入 / type          — 正在输入
#   录音 / voice         — 正在录音
#   视频 / video         — 正在录制视频
#   游戏 / game          — 正在玩游戏
#   上传 / upload        — 正在上传文件
#   选择 / choose        — 正在选择贴纸
#
# 示例:
#   .wz 录音             — 录音状态持续 5 秒
#   .wz 输入 30          — 输入状态持续 30 秒
#   .wz voice 10         — 英文也可以

_ACT_MAX = 300  # 最长 5 分钟

@app.on_message((filters.me | authorized_filter) & filters.command(["wz", "act"], prefixes="."))
async def fake_action(client, message):
    """伪装聊天状态 (输入/录音/视频/游戏等)，支持中英文关键词"""
    from pyrogram.enums import ChatAction

    action_map = {
        # 中文
        "输入": ChatAction.TYPING,
        "录音": ChatAction.RECORD_AUDIO,
        "视频": ChatAction.RECORD_VIDEO,
        "游戏": ChatAction.PLAYING,
        "上传": ChatAction.UPLOAD_DOCUMENT,
        "选择": ChatAction.CHOOSE_STICKER,
        # 英文
        "type":   ChatAction.TYPING,
        "voice":  ChatAction.RECORD_AUDIO,
        "video":  ChatAction.RECORD_VIDEO,
        "game":   ChatAction.PLAYING,
        "upload": ChatAction.UPLOAD_DOCUMENT,
        "choose": ChatAction.CHOOSE_STICKER,
    }

    args = message.text.split()

    if len(args) < 2:
        keys_cn = "输入 / 录音 / 视频 / 游戏 / 上传 / 选择"
        return await smart_reply(message,
            f"**🎭 状态伪装**\n\n"
            f"`.wz [状态] [秒数]`\n\n"
            f"**可用状态:** {keys_cn}\n"
            f"**示例:** `.wz 录音 30`"
        )

    action_key = args[1].lower()
    action = action_map.get(args[1]) or action_map.get(action_key)
    if not action:
        return await smart_reply(message,
            f"❌ 未知状态 `{args[1]}`\n"
            f"可用: 输入 / 录音 / 视频 / 游戏 / 上传 / 选择"
        )

    duration = 5
    if len(args) >= 3:
        try:
            duration = max(1, min(int(args[2]), _ACT_MAX))
        except ValueError:
            return await smart_reply(message, "❌ 秒数必须是整数")

    await message.delete()

    try:
        # Telegram 每次 send_chat_action 最多维持 5 秒，循环续期
        elapsed = 0
        while elapsed < duration:
            await client.send_chat_action(message.chat.id, action)
            step = min(5, duration - elapsed)
            await asyncio.sleep(step)
            elapsed += step
    except Exception:
        pass
    finally:
        try:
            await client.send_chat_action(message.chat.id, ChatAction.CANCEL)
        except Exception:
            pass


# ====================== 附加指令：VPS 闪电代下 (多链接/优先级解析) ======================
# .wget <链接>          — 直接下载
# .wget <GitHub Release 页>  — 自动解析 release 资产列表，选择下载
# 支持: 直链 / GitHub Release 页 / 回复消息中的链接
# 单文件上限: 2GB (Telegram Bot 限制)
# 最多同时处理 5 个文件

_WGET_EXTS = {
    ".apk", ".zip", ".rar", ".7z", ".gz", ".tar",
    ".exe", ".msi", ".dmg", ".deb", ".rpm",
    ".mp4", ".mkv", ".mp3", ".pdf", ".iso",
    ".bin", ".img", ".xz", ".zst",
}
_WGET_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

@app.on_message((filters.me | authorized_filter) & filters.command("wget", prefixes="."))
async def vps_downloader(client, message):
    """VPS 闪电代下，支持直链与 GitHub Release 页面解析"""
    from urllib.parse import urlparse, urljoin

    args = message.text.split(maxsplit=1)
    text_source = args[1].strip() if len(args) > 1 else ""
    if not text_source and message.reply_to_message:
        text_source = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not text_source:
        return await smart_reply(message, "❌ 请附带链接，或回复包含链接的消息。")

    raw_urls = re.findall(r"https?://\S+", text_source)
    raw_urls = list(dict.fromkeys(raw_urls))  # 去重保序
    if not raw_urls:
        return await smart_reply(message, "❌ 未检测到有效链接。")

    reply = await smart_reply(message, "`[wget] 正在解析链接...`")

    # ── GitHub Release 页面解析 ───────────────────────────────────────────────
    def _gh_api(api_url):
        """调用 GitHub API 返回 JSON"""
        req = urllib.request.Request(api_url, headers={
            "User-Agent": _WGET_UA,
            "Accept": "application/vnd.github+json"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def parse_github_url(url: str):
        """
        解析各类 GitHub 链接，返回 [(dl_url, name, size), ...]
        支持:
          /releases/tag/<tag>         — 指定版本所有 assets
          /releases/latest            — 最新版所有 assets
          /releases                   — 最新版所有 assets
          /releases/download/.../file — 直链，直接返回
          raw.githubusercontent.com/… — 直链，直接返回
          /blob/<branch>/<path>       — 转换为 raw 直链
          /archive/refs/heads/<br>.zip — 仓库打包直链
        """
        import re as _re
        u = url.rstrip("/")

        # ① releases/download 直链
        if _re.search(r"/releases/download/", u):
            fname = os.path.basename(urlparse(u).path)
            return [(u, fname, 0)]

        # ② raw.githubusercontent.com 直链
        if "raw.githubusercontent.com" in u:
            fname = os.path.basename(urlparse(u).path)
            return [(u, fname, 0)]

        # ③ /blob/<branch>/<path> → 转 raw
        m = _re.match(r"https://github\.com/([^/]+/[^/]+)/blob/([^/]+)/(.+)", u)
        if m:
            repo, branch, path = m.group(1), m.group(2), m.group(3)
            raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
            fname = os.path.basename(path)
            return [(raw_url, fname, 0)]

        # ④ /archive/refs/heads/<branch>.zip 或 .tar.gz
        if "/archive/" in u:
            fname = os.path.basename(urlparse(u).path)
            return [(u, fname, 0)]

        # ⑤ releases/tag/<tag>
        m = _re.match(r"https://github\.com/([^/]+/[^/]+)/releases/tag/(.+)", u)
        if m:
            repo, tag = m.group(1), m.group(2)
            try:
                data = _gh_api(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
                assets = data.get("assets", [])
                return [(a["browser_download_url"], a["name"], a["size"]) for a in assets]
            except Exception:
                return []

        # ⑥ releases/latest 或 /releases（取最新版）
        m = _re.match(r"https://github\.com/([^/]+/[^/]+)/releases", u)
        if m:
            repo = m.group(1)
            try:
                data = _gh_api(f"https://api.github.com/repos/{repo}/releases/latest")
                assets = data.get("assets", [])
                tag = data.get("tag_name", "latest")
                return [(a["browser_download_url"], a["name"], a["size"]) for a in assets], tag
            except Exception:
                return []

        return []

    def is_direct_link(url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in _WGET_EXTS)

    def is_github_url(url: str) -> bool:
        return "github.com" in url or "raw.githubusercontent.com" in url

    # ── 收集最终下载任务 ──────────────────────────────────────────────────────
    download_tasks = []  # list of (url, filename, size_bytes)

    for url in raw_urls[:3]:
        if is_github_url(url):
            await reply.edit_text("`[wget] 正在解析 GitHub 链接...`")
            result = parse_github_url(url)
            # latest 返回 (list, tag_name) 元组
            tag_name = ""
            if isinstance(result, tuple):
                result, tag_name = result
            if result:
                preview_lines = "\n".join(
                    f"• `{n}` ({s/1024**2:.1f} MB)" if s else f"• `{n}`"
                    for _, n, s in result[:5]
                )
                tag_str = f" `{tag_name}`" if tag_name else ""
                await reply.edit_text(
                    f"**📦 GitHub 解析完成{tag_str}**\n"
                    f"共 {len(result)} 个文件，即将下载前 {min(len(result),5)} 个：\n"
                    f"{preview_lines}"
                )
                await asyncio.sleep(1.5)
                for dl_url, name, size in result[:5]:
                    download_tasks.append((dl_url, name, size))
            else:
                await reply.edit_text("❌ GitHub 链接解析失败，无可下载资产。")
                return
        elif is_direct_link(url):
            fname = os.path.basename(urlparse(url).path)
            download_tasks.append((url, fname, 0))
        else:
            # 非 GitHub 页面：HEAD 探测是否是文件
            try:
                req = urllib.request.Request(url, method="HEAD",
                    headers={"User-Agent": _WGET_UA})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    cl = int(resp.headers.get("Content-Length") or 0)
                    if "text/html" not in ct:
                        fname = os.path.basename(urlparse(url).path) or f"file_{int(time.time())}"
                        download_tasks.append((url, fname, cl))
                    else:
                        await reply.edit_text(f"⚠️ `{url[:60]}` 是网页，已跳过。")
            except Exception:
                fname = os.path.basename(urlparse(url).path) or f"file_{int(time.time())}"
                download_tasks.append((url, fname, 0))

    if not download_tasks:
        return await reply.edit_text("❌ 没有可下载的文件。")

    download_tasks = download_tasks[:5]

    # ── 执行下载 ──────────────────────────────────────────────────────────────
    def _download(dl_url, save_path):
        req = urllib.request.Request(dl_url, headers={"User-Agent": _WGET_UA})
        with urllib.request.urlopen(req, timeout=120) as resp, open(save_path, "wb") as f:
            while True:
                chunk = resp.read(4 * 1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return os.path.getsize(save_path)

    total = len(download_tasks)
    for i, (dl_url, fname, _) in enumerate(download_tasks, 1):
        # 文件名清洗
        fname = re.sub(r'[\/:*?"<>|]', "_", fname) or f"file_{i}"
        save_path = f"/tmp/wget_{message.id}_{i}_{fname}"

        await reply.edit_text(
            f"⏳ **[{i}/{total}] 拉取中**\n"
            f"`{fname}`\n"
            f"`{dl_url[:60]}{'...' if len(dl_url)>60 else ''}`"
        )

        try:
            loop = asyncio.get_running_loop()
            file_size = await loop.run_in_executor(None, _download, dl_url, save_path)
            size_str = f"{file_size/1024**2:.1f} MB"

            await reply.edit_text(
                f"🚀 **[{i}/{total}] 推流中 ({size_str})**\n`{fname}`")

            await client.send_document(
                chat_id=message.chat.id,
                document=save_path,
                file_name=fname,
                caption=f"⬇️ `{fname}` ({size_str})"
            )
        except Exception as e:
            await client.send_message(
                chat_id=message.chat.id,
                text=f"❌ **[{i}/{total}] 失败:** `{fname}`\n`{str(e)[:150]}`"
            )
        finally:
            if os.path.exists(save_path):
                os.remove(save_path)

    await message.delete()


# ====================== 附加指令：打字机与光标闪烁特效 ======================
# .dazi [文字]          — 默认速度打字机
# .dazi fast [文字]     — 快速模式 (每字 0.05s)
# .dazi slow [文字]     — 慢速模式 (每字 0.3s)

@app.on_message((filters.me | authorized_filter) & filters.command("dazi", prefixes="."))
async def typewriter_effect(client, message):
    """打字机动态字符输出特效"""
    from pyrogram.errors import FloodWait, MessageNotModified

    speed_map = {"fast": 0.05, "slow": 0.3}
    args = message.text.split(maxsplit=2)

    if len(args) < 2:
        return await smart_reply(message, "❌ 格式: `.dazi [文字]` 或 `.dazi fast/slow [文字]`")

    # 解析速度参数
    if args[1].lower() in speed_map and len(args) >= 3:
        interval = speed_map[args[1].lower()]
        text = args[2]
    else:
        interval = 0.12
        text = message.text.split(maxsplit=1)[1]

    if not text:
        return await smart_reply(message, "❌ 文字不能为空")

    # 先占位，后续只 edit 这条消息，不重复发
    reply_msg = await smart_reply(message, "▌")
    current = ""
    cursors = ["▌", ""]  # 光标闪烁帧
    last_edit = time.time()

    try:
        for i, char in enumerate(text):
            current += char
            cursor = cursors[i % 2]

            # 节流：距上次 edit 不足 interval 则等待
            elapsed = time.time() - last_edit
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)

            try:
                await reply_msg.edit_text(current + cursor)
                last_edit = time.time()
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await reply_msg.edit_text(current + cursor)
                last_edit = time.time()
            except MessageNotModified:
                pass

        # 最终去掉光标
        await reply_msg.edit_text(current)

    except Exception as e:
        try:
            await reply_msg.edit_text(current or f"❌ 渲染异常: `{str(e)}`")
        except Exception:
            pass

# ====================== 附加指令：异步实时 Shell (白名单+资源熔断重构版) ======================
#
# 安全架构：
#   1. 白名单优先 — 只有明确允许的基础命令可直接执行
#   2. 黑名单兜底 — 针对白名单通过后仍存在的高危模式二次拦截
#   3. timeout 硬限制 — 防止死循环/资源耗尽
#   4. ulimit 资源限制 — 限制进程可用 CPU/内存/子进程数
#   5. 降权执行 (可选) — 若配置了 SHELL_RUNNER_USER 则 su 切换
#   6. 熔断仍保留 os._exit(0) — 匹配原始结构
#
# 联动说明：
#   - 与其他指令共享 app 实例，无全局副作用
#   - SHELL_TIMEOUT / SHELL_MAX_OUTPUT / SHELL_RUNNER_USER 可在主配置统一管理
#   - edit_text 节流逻辑与原版保持一致 (1s间隔 + FloodWait 自适应)
# ===============================================================================

import re
import asyncio
import os
import time
import shlex
import resource
from pyrogram.errors import FloodWait, MessageNotModified

# ── 可在主配置中覆盖的全局常量 ──────────────────────────────────────────────────
SHELL_TIMEOUT: int = 60          # 单条命令最长执行时间 (秒)
SHELL_MAX_OUTPUT: int = 3500     # 消息中显示的最大字符数
SHELL_UPDATE_INTERVAL: float = 1.0  # Telegram 消息更新节流间隔 (秒)
SHELL_RUNNER_USER: str = ""      # 降权用户名，空字符串 = 不降权 (e.g. "nobody")

# ── 白名单：允许作为起始命令的可执行文件名 ──────────────────────────────────────
# 原则：读操作 / 网络诊断 / 常用运维工具，不含任何写入或进程控制权限
_WHITELIST: set[str] = {
    # 文件/目录查看
    "ls", "ll", "la", "cat", "head", "tail", "less", "more",
    "find", "locate", "stat", "file", "wc", "du", "df",
    "tree", "pwd", "realpath",
    # 文本处理
    "grep", "egrep", "fgrep", "rg",
    "awk", "sed", "cut", "sort", "uniq", "tr", "diff", "patch",
    "jq", "yq", "column",
    # 系统信息
    "ps", "top", "htop", "free", "uptime", "uname", "whoami",
    "id", "env", "printenv", "date", "timedatectl", "hostname",
    "lscpu", "lsmem", "lsblk", "lsof", "lspci", "lsusb",
    "iostat", "vmstat", "sar", "dstat",
    # 网络诊断 (只读)
    "ping", "traceroute", "tracepath", "mtr",
    "curl", "wget", "dig", "nslookup", "host",
    "ss", "netstat", "ip", "ifconfig", "nmap",
    "whois",
    # 进程/日志
    "journalctl", "dmesg", "last", "lastlog", "who", "w",
    # 压缩/解压 (不含写系统目录)
    "tar", "gzip", "gunzip", "zip", "unzip", "7z", "xz",
    # 包/版本查询
    "dpkg", "apt", "rpm", "pip", "pip3", "python3",
    "node", "npm", "cargo", "go",
    # 代理/服务状态查询
    "xray", "v2ray", "sing-box", "nginx", "systemctl",
    # 其他常用
    "echo", "printf", "which", "type", "hash",
    "md5sum", "sha256sum", "base64",
    "screen", "tmux",
}

# ── 黑名单：白名单通过后的二次高危模式拦截 ─────────────────────────────────────
# 主要防御：白名单命令被利用于间接执行 (如 find -exec、curl|sh 等)
_BLACKLIST_PATTERNS: list[tuple[str, str]] = [
    # 命令替换/间接执行
    (r"`[^`]+`|(\$\([^)]+\))",
     "命令替换 (反引号/$()) 可能导致间接执行"),
    # 管道接解释器
    (r"\|\s*(sudo\s+)?(sh|bash|zsh|ksh|dash|python3?|py|node|perl|php|ruby|lua)\b",
     "管道接解释器"),
    # find/xargs 利用链
    (r"(find|xargs).+(-exec|-execdir|--exec)\s+.*(sh|bash|rm|chmod|chown|mv|cp)",
     "find/xargs 利用链"),
    # curl/wget 执行链
    (r"(curl|wget)\s+.+\|\s*(sh|bash|python|node)",
     "下载并执行"),
    # base64 解码执行
    (r"base64\s+(-d|--decode).+\|\s*(sh|bash|python)",
     "base64解码执行链"),
    # 重定向到关键路径
    (r"(>{1,2})\s*/\s*(etc|bin|sbin|usr|boot|root|sys|proc)",
     "重定向写入系统目录"),
    # systemctl 危险子命令 (即使在白名单内也拦截)
    (r"systemctl\s+(reboot|poweroff|halt|isolate|kexec|switch-root)",
     "systemctl 停机/重启指令"),
    # 环境变量注入
    (r"\b(LD_PRELOAD|LD_LIBRARY_PATH|PATH\s*=)\s*[^\s]",
     "环境变量劫持"),
]

# ── 资源限制 preexec_fn ────────────────────────────────────────────────────────
def _apply_resource_limits():
    """在子进程 fork 后、exec 前设置资源上限"""
    # 最多 128MB 虚拟内存 (可按需调整)
    # resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
    # 最多 64 个子进程
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    # 最多写入 32MB 文件
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    # CPU 时间与 SHELL_TIMEOUT 对齐 (内核级强制)
    resource.setrlimit(resource.RLIMIT_CPU, (SHELL_TIMEOUT, SHELL_TIMEOUT + 5))


# ── 安全检查入口 ───────────────────────────────────────────────────────────────
def _security_check(cmd: str) -> tuple[bool, str]:
    """
    返回 (is_blocked, reason)
    白名单检查 → 黑名单二次检查
    """
    # 提取第一个 token 作为主命令
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return True, "命令解析失败 (引号不匹配)"

    if not tokens:
        return True, "空命令"

    base_cmd = os.path.basename(tokens[0])  # 支持全路径如 /usr/bin/cat

    if base_cmd not in _WHITELIST:
        return True, f"命令 `{base_cmd}` 不在白名单中"

    # 黑名单二次扫描 (全命令字符串)
    for pattern, reason in _BLACKLIST_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE | re.MULTILINE):
            return True, reason

    return False, ""


# ── 构造实际执行命令 (含 timeout + 可选降权) ────────────────────────────────────
def _build_exec_cmd(cmd: str) -> str:
    safe_cmd = cmd  # shlex.quote 不在此处包裹，保留用户参数语义
    if SHELL_RUNNER_USER:
        safe_cmd = f"su -s /bin/sh -c {shlex.quote(cmd)} {SHELL_RUNNER_USER}"
    return f"timeout --kill-after=5 {SHELL_TIMEOUT} {safe_cmd}"


# ── 主处理函数 ─────────────────────────────────────────────────────────────────
@app.on_message(filters.me & filters.command("sh", prefixes="."))
async def bash_terminal(client, message):
    """异步执行 VPS 系统 Shell 命令 (实时输出 + 白名单 + 资源熔断)"""

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await smart_reply(message, 
            "❌ **格式:** `.sh [命令]`\n"
            "💡 **提示:** 仅白名单命令可执行，使用 `.sh --list` 查看"
        )

    # 查看白名单列表
    if args[1].strip() == "--list":
        wl_str = "  ".join(sorted(_WHITELIST))
        return await smart_reply(message, 
            f"📋 **Shell 白名单命令 ({len(_WHITELIST)} 条)**\n\n"
            f"```\n{wl_str}\n```"
        )

    cmd = args[1].strip()

    # ── 安全检查 ──────────────────────────────────────────────────────────────
    blocked, reason = _security_check(cmd)
    if blocked:
        await smart_reply(message, 
            f"🚨 **Security Gate — 指令拦截**\n\n"
            f"**指令：** `{cmd}`\n"
            f"**原因：** {reason}\n\n"
            f"_系统将在 3s 后清理现场并终止进程_"
        )
        await asyncio.sleep(3)
        await message.delete()
        os._exit(0)

    # ── 正常执行逻辑 ──────────────────────────────────────────────────────────
    exec_cmd = _build_exec_cmd(cmd)
    reply = await smart_reply(message, f"🏃 **正在启动进程:**\n`{cmd}`")

    try:
        process = await asyncio.create_subprocess_shell(
            exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=_apply_resource_limits,
        )

        output_log = ""
        last_update_time = time.time()
        last_text = ""

        while True:
            try:
                chunk = await asyncio.wait_for(
                    process.stdout.read(4096), timeout=0.5
                )
            except asyncio.TimeoutError:
                chunk = b""

            if chunk:
                output_log += chunk.decode(errors="replace")

            if not chunk and process.stdout.at_eof():
                break

            current_time = time.time()
            if current_time - last_update_time >= SHELL_UPDATE_INTERVAL:
                display_log = (
                    "...\n" + output_log[-SHELL_MAX_OUTPUT:]
                    if len(output_log) > SHELL_MAX_OUTPUT
                    else output_log
                )
                display_text = (
                    f"🏃 **执行中:** `{cmd}`\n\n"
                    f"```bash\n{display_log if display_log.strip() else '[等待回显...]'}\n```"
                )

                if display_text != last_text:
                    try:
                        await reply.edit_text(display_text)
                        last_text = display_text
                        last_update_time = current_time
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except (MessageNotModified, Exception):
                        pass

        await process.wait()

        # timeout 命令返回码语义
        rc = process.returncode
        if rc == 124:
            rc_label = f"{rc} ⏱ TIMEOUT ({SHELL_TIMEOUT}s)"
        elif rc == 137:
            rc_label = f"{rc} 💀 KILLED (资源超限或手动终止)"
        else:
            rc_label = str(rc)

        final_log = (
            "...\n" + output_log[-SHELL_MAX_OUTPUT:]
            if len(output_log) > SHELL_MAX_OUTPUT
            else output_log
        )
        final_text = (
            f"💻 **执行完毕 (Exit: {rc_label}):** `{cmd}`\n\n"
            f"```bash\n{final_log if final_log.strip() else '(无输出)'}\n```"
        )

        try:
            await reply.edit_text(final_text)
        except MessageNotModified:
            pass

    except Exception as e:
        await reply.edit_text(f"❌ **运行时异常:**\n`{str(e)}`")


# ====================== 修复版：系统底层 Shell 扩展 (.wt / .tr / .art) ======================
# .wt [城市名]     支持中文/英文/拼音，如 .wt 成都 / .wt tokyo / .wt 东京

@app.on_message((filters.me | authorized_filter) & filters.command("wt", prefixes="."))
async def get_weather(client, message):
    """查询全球实时天气预报，支持中文城市名，含空气质量"""
    import urllib.parse
    from curl_cffi.requests import AsyncSession

    args = message.text.split(maxsplit=1)
    city = args[1].strip() if len(args) > 1 else "成都"

    reply = await smart_reply(message, f"`[Weather] 正在检索 {city} ...`")

    try:
        async with AsyncSession(impersonate="chrome120") as session:

            # ── 第一步：地理编码（支持中文，language=zh 返回中文地名）──────────
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search"
                f"?name={urllib.parse.quote(city)}&count=1&language=zh&format=json"
            )
            geo_res = await session.get(geo_url, timeout=10)
            geo_data = geo_res.json()

            if not geo_data.get("results"):
                return await reply.edit_text(f"❌ 无法识别城市: `{city}`\n💡 尝试英文或拼音")

            loc      = geo_data["results"][0]
            lat, lon = loc["latitude"], loc["longitude"]
            city_name = loc.get("name", city)
            country   = loc.get("country", "")
            admin     = loc.get("admin1", "")  # 省/州

            # ── 第二步：天气 + 空气质量并发请求 ──────────────────────────────
            weather_url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                f"weather_code,wind_speed_10m,wind_direction_10m,"
                f"precipitation,surface_pressure,visibility,uv_index"
                f"&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_sum"
                f"&timezone=auto&forecast_days=1"
            )
            air_url = (
                f"https://air-quality-api.open-meteo.com/v1/air-quality"
                f"?latitude={lat}&longitude={lon}"
                f"&current=pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,ozone,european_aqi"
                f"&timezone=auto"
            )

            weather_res, air_res = await asyncio.gather(
                session.get(weather_url, timeout=10),
                session.get(air_url, timeout=10),
                return_exceptions=True
            )

        cur   = weather_res.json()["current"]
        daily = weather_res.json().get("daily", {})

        # 空气质量（接口可能失败，容错）
        aqi_data = {}
        if not isinstance(air_res, Exception):
            try:
                aqi_data = air_res.json().get("current", {})
            except Exception:
                pass

        # ── 天气描述映射 ──────────────────────────────────────────────────────
        _WC = {
            0: ("☀️", "晴朗"),   1: ("🌤", "晴间多云"), 2: ("⛅", "多云"),    3: ("☁️", "阴天"),
            45: ("🌫", "雾"),    48: ("🌫", "浓雾"),    51: ("🌦", "轻毛毛雨"), 53: ("🌧", "毛毛雨"),
            55: ("🌧", "重毛毛雨"), 61: ("🌧", "小雨"),  63: ("🌧", "中雨"),    65: ("🌧", "大雨"),
            71: ("🌨", "小雪"),  73: ("❄️", "中雪"),   75: ("❄️", "大雪"),    77: ("🌨", "冰粒"),
            80: ("🌦", "阵雨"),  81: ("🌧", "大阵雨"),  82: ("⛈", "暴阵雨"),
            85: ("🌨", "阵雪"),  86: ("❄️", "大阵雪"),
            95: ("⛈", "雷阵雨"), 96: ("⛈", "雷暴冰雹"), 99: ("⛈", "强雷暴"),
        }
        emoji, desc = _WC.get(cur.get("weather_code", 0), ("🌡", "未知"))

        # ── 风向转汉字 ────────────────────────────────────────────────────────
        def wind_dir(deg):
            dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
            return dirs[int((deg + 22.5) / 45) % 8]

        # ── AQI 等级 ──────────────────────────────────────────────────────────
        def aqi_level(aqi):
            if aqi is None: return "N/A"
            if aqi <= 20:   return f"{aqi} 🟢 优"
            if aqi <= 40:   return f"{aqi} 🟡 良"
            if aqi <= 60:   return f"{aqi} 🟠 中等"
            if aqi <= 80:   return f"{aqi} 🔴 差"
            return              f"{aqi} 🟣 极差"

        # ── UV 指数 ───────────────────────────────────────────────────────────
        def uv_level(uv):
            if uv is None: return "N/A"
            if uv < 3:  return f"{uv:.0f} 低"
            if uv < 6:  return f"{uv:.0f} 中"
            if uv < 8:  return f"{uv:.0f} 高"
            if uv < 11: return f"{uv:.0f} 很高"
            return          f"{uv:.0f} 极高"

        # ── 今日最高最低 ──────────────────────────────────────────────────────
        t_max = daily.get("temperature_2m_max", [None])[0]
        t_min = daily.get("temperature_2m_min", [None])[0]
        sunrise  = (daily.get("sunrise",  [""])[0] or "")[-5:]
        sunset   = (daily.get("sunset",   [""])[0] or "")[-5:]
        precip   = daily.get("precipitation_sum", [0])[0] or 0

        loc_str = " · ".join(filter(None, [country, admin, city_name]))
        wind_str = f"{cur.get('wind_speed_10m', 0)} km/h {wind_dir(cur.get('wind_direction_10m', 0))}"
        range_str = f"{t_min}°C ~ {t_max}°C" if t_min is not None else "N/A"

        aqi   = aqi_data.get("european_aqi")
        pm25  = aqi_data.get("pm2_5")
        pm10  = aqi_data.get("pm10")
        no2   = aqi_data.get("nitrogen_dioxide")
        o3    = aqi_data.get("ozone")

        text = (
            f"{emoji} **{city_name}** · {country}\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"**天气状态** {desc}\n"
            f"**实时气温** `{cur['temperature_2m']}°C` (体感 `{cur['apparent_temperature']}°C`)\n"
            f"**今日温差** `{range_str}`\n"
            f"**湿度/气压** `{cur['relative_humidity_2m']}%` / `{cur.get('surface_pressure', 'N/A')} hPa`\n"
            f"**风速/风向** `{wind_str}`\n"
            f"**降水量** `{precip} mm`\n"
            f"**能见度** `{cur.get('visibility', 'N/A')} m`\n"
            f"**UV 指数** `{uv_level(cur.get('uv_index'))}`\n"
            f"**日出/日落** `{sunrise}` / `{sunset}`\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        )

        if aqi is not None:
            text += (
                f"**🌿 空气质量 (EU AQI)**\n"
                f"综合指数 `{aqi_level(aqi)}`\n"
                f"PM2.5 `{pm25:.1f}` | PM10 `{pm10:.1f}`\n"
                f"NO₂ `{no2:.1f}` | O₃ `{o3:.1f}`\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            )

        text += f"**数据来源** `Open-Meteo` | 坐标 `{lat:.2f},{lon:.2f}`"

        await reply.edit_text(text)

    except Exception as e:
        await reply.edit_text(f"❌ 气象调度失败: `{str(e)[:150]}`")


@app.on_message((filters.me | authorized_filter) & filters.command("news", prefixes="."))
async def get_news(client, message):
    """采集今日 60 秒新闻简报，多源自动切换"""
    from curl_cffi.requests import AsyncSession

    reply = await smart_reply(message, "`[News] 正在采集新闻简报...`")

    # 多个备用接口，按顺序尝试，任一成功即停
    _SOURCES = [
        {
            "url": "https://api.03li.com/api/60s",
            "parse": lambda d: d.get("data", []),
        },
        {
            "url": "https://60s-api.viki.moe/v2/60s?encoding=json",
            "parse": lambda d: d.get("data", {}).get("news", []),
        },
        {
            "url": "https://api.iyk0.com/60s",
            "parse": lambda d: d.get("news", d.get("data", [])),
        },
        {
            "url": "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=15",
            "parse": lambda d: [
                f"{i+1}. {item['target']['title']}"
                for i, item in enumerate(d.get("data", [])[:15])
                if item.get("target", {}).get("title")
            ],
            "label": "知乎热榜",
        },
    ]

    news_list = []
    source_label = "60s简报"
    used_url = ""

    async with AsyncSession(impersonate="chrome120") as session:
        for src in _SOURCES:
            try:
                res = await session.get(src["url"], timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    items = src["parse"](data)
                    if items and len(items) >= 3:
                        news_list = items
                        source_label = src.get("label", "60s简报")
                        used_url = src["url"]
                        break
            except Exception:
                continue

    if not news_list:
        return await reply.edit_text(
            "❌ 所有新闻接口均不可用\n"
            "可能原因：VPS IP 被限流 / 接口故障\n"
            "💡 稍后重试或更换节点"
        )

    # 限制条数，折叠显示
    items = news_list[:15]
    lines = "\n".join(f"• {n}" for n in items)

    from datetime import datetime
    date_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    header = f"📅 **{source_label} · {date_str}** ({len(items)} 条)\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
    body   = f"<blockquote expandable>\n{lines}\n</blockquote>"

    await reply.edit_text(header + body)

@app.on_message((filters.me | authorized_filter) & filters.command("coin", prefixes="."))
async def get_crypto(client, message):
    """调取加密货币实时行情，不指定默认输出主流前5"""
    from curl_cffi.requests import AsyncSession

    args = message.text.split()
    # 默认主流5种，也可以指定单个或多个，如 .coin btc eth
    _DEFAULT = ["BTC", "ETH", "BNB", "SOL", "XRP"]
    targets = [a.upper() for a in args[1:]] if len(args) > 1 else _DEFAULT
    targets = targets[:8]  # 最多8个

    reply = await smart_reply(message, f"`[Crypto] 正在拉取行情数据...`")

    # 用 CoinGecko 免费公共 API，无需 key，稳定
    _ID_MAP = {
        "BTC": "bitcoin",    "ETH": "ethereum",   "BNB": "binancecoin",
        "SOL": "solana",     "XRP": "ripple",      "DOGE": "dogecoin",
        "ADA": "cardano",    "TRX": "tron",        "TON": "the-open-network",
        "AVAX": "avalanche-2", "DOT": "polkadot",  "MATIC": "matic-network",
        "LTC": "litecoin",   "BCH": "bitcoin-cash", "LINK": "chainlink",
        "UNI": "uniswap",    "ATOM": "cosmos",     "XLM": "stellar",
        "FIL": "filecoin",   "APT": "aptos",       "ARB": "arbitrum",
        "OP": "optimism",    "SUI": "sui",          "PEPE": "pepe",
    }

    # 识别 coin id
    ids = []
    unknown = []
    for sym in targets:
        cg_id = _ID_MAP.get(sym)
        if cg_id:
            ids.append((sym, cg_id))
        else:
            unknown.append(sym)

    if not ids:
        return await reply.edit_text(
            f"❌ 无法识别货币: `{'、'.join(unknown)}`\n"
            f"支持: {', '.join(_ID_MAP.keys())}"
        )

    try:
        async with AsyncSession(impersonate="chrome120") as session:
            id_str = ",".join(cg_id for _, cg_id in ids)
            url = (
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={id_str}&vs_currencies=usd"
                f"&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true"
            )
            res = await session.get(url, timeout=12)
            if res.status_code != 200:
                return await reply.edit_text(f"❌ API 返回 HTTP {res.status_code}")
            data = res.json()

        lines = []
        for sym, cg_id in ids:
            d = data.get(cg_id, {})
            if not d:
                lines.append(f"• **{sym}** — 数据缺失")
                continue
            price   = d.get("usd", 0)
            change  = d.get("usd_24h_change") or 0
            vol     = d.get("usd_24h_vol") or 0
            mcap    = d.get("usd_market_cap") or 0

            # 价格格式化
            if price >= 1000:
                p_str = f"{price:,.2f}"
            elif price >= 1:
                p_str = f"{price:.4f}"
            else:
                p_str = f"{price:.8f}"

            # 涨跌箭头
            arrow = "🟢 +" if change >= 0 else "🔴 "
            chg_str = f"{arrow}{change:.2f}%"

            # 成交量/市值简写
            def fmt_big(n):
                if n >= 1e9: return f"{n/1e9:.2f}B"
                if n >= 1e6: return f"{n/1e6:.2f}M"
                return f"{n:.0f}"

            lines.append(
                f"**{sym}** `${p_str}` {chg_str}\n"
                f"  Vol `${fmt_big(vol)}` | MCap `${fmt_big(mcap)}`"
            )

        from datetime import datetime
        ts = datetime.now(LOCAL_TZ).strftime("%H:%M:%S")
        header = f"📊 **Crypto Market** · `{ts}`\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        footer = "\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n> 来源: `CoinGecko`"
        if unknown:
            footer += f"\n> 未识别: `{'、'.join(unknown)}`"

        await reply.edit_text(header + "\n".join(lines) + footer)

    except Exception as e:
        await reply.edit_text(f"❌ 行情获取失败: `{str(e)[:150]}`")

@app.on_message((filters.me | authorized_filter) & filters.command("sys", prefixes="."))
async def sys_monitor(client, message):
    """深度读取 VPS 系统底层运行数据"""
    try:
        await smart_reply(message, "`[Shell] 正在读取系统状态计数器...`")
        cmd = "uptime && echo --- && free -h && echo --- && df -h / | grep /"
        process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        res = stdout.decode().strip()
        await smart_reply(message, f"📊 **VPS System Status**\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n<code>{res}</code>\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n> 💡 负载/内存/磁盘占用")
    except Exception as e:
        await smart_reply(message, f"❌ 获取失败: {str(e)}")


# ====================== 修复版：ASCII 字符画 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("art", prefixes="."))
async def ascii_art(client, message):
    """将文本转换为 ASCII 字符画"""
    try:
        args = message.text.split(None, 1)
        text = args[1] if len(args) > 1 else "SHIO"
        await smart_reply(message, "`[Shell] 正在请求字符画引擎...`")
        
        import urllib.parse
        encoded_text = urllib.parse.quote(text)
        
        cmd = f"curl -sL -m 10 'https://asciified.thelicato.io/api/v2/ascii?text={encoded_text}'"
        process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        res = stdout.decode().strip()
        
        if not res or "<html" in res.lower():
             return await smart_reply(message, "❌ API 返回异常数据。")
             
        await smart_reply(message, f"🎨 **ASCII ART**\n<pre>{res}</pre>")
    except Exception as e:
        await smart_reply(message, "❌ 引擎响应超时。")

@app.on_message((filters.me | authorized_filter) & filters.command("ito", prefixes="."))
async def get_hitokoto(client, message):
    """随机获取一句灵魂语录 (一言)"""
    try:
        cmd = "curl -sL -m 5 'https://v1.hitokoto.cn/?encode=json&charset=utf-8'"
        process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        if stdout:
            data = json.loads(stdout.decode())
            text = (
                f"💬 **一言 (Hitokoto)**\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"「 {data.get('hitokoto')} 」\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"**>** **出处**：{data.get('from')}\n"
                f"**>** **作者**：{data.get('from_who') or '佚名'}"
            )
            await smart_reply(message, text)
    except Exception as e:
        await smart_reply(message, "❌ 无法触达感性世界。")


# ====================== 自动化指令：动态帮助中心 (终极物理防劫持分类折叠版) ======================

# 扫描结果缓存，.kill 重载后自动失效
_HELP_CACHE: dict = {}

def _build_cmd_registry() -> dict:
    """扫描源码构建指令注册表，结果缓存"""
    if _HELP_CACHE:
        return _HELP_CACHE
    from collections import defaultdict

    _STRIP = [
        "附加指令：", "自动化指令：", "修复版：", "升级版：",
        "极致视觉渲染：", "极致", "重构版：", "优化版：",
    ]
    registry = defaultdict(list)
    script_path = os.path.abspath(__file__)
    with open(script_path, "r", encoding="utf-8") as f:
        src_lines = f.readlines()

    current_category = "核心基础模块"
    for i, line in enumerate(src_lines):
        cat_match = re.search(r"# ={5,}\s*(.*?)\s*={5,}", line)
        if cat_match:
            cat = cat_match.group(1).strip()
            for prefix in _STRIP:
                cat = cat.replace(prefix, "")
            cat = cat.strip()
            if cat and not re.match(r"^[\-=\s]+$", cat):
                current_category = cat

        if "filters.command" in line and "@app.on_message" in line:
            cmd_arg_match = re.search(r"command\((.*?)\)", line)
            if not cmd_arg_match:
                continue
            cmds = re.findall(r"[\"\']([a-zA-Z0-9_]+)[\"\']", cmd_arg_match.group(1))
            if not cmds:
                continue
            desc = "暂无描述"
            for j in range(1, 4):
                if i + j < len(src_lines):
                    doc_match = re.search(r'"""(.*?)"""', src_lines[i + j])
                    if doc_match:
                        desc = doc_match.group(1).strip()
                        break
            for c in cmds:
                if c not in ("help",):
                    registry[current_category].append((c, desc))

    _HELP_CACHE.update(registry)
    return registry


@app.on_message((filters.me | authorized_filter) & filters.command("help", prefixes="."))
async def help_center(client, message):
    """自动扫描并展示所有已注册指令 (按区块折叠)"""
    try:
        cmd_registry = _build_cmd_registry()

        if not cmd_registry:
            return await smart_reply(message, "⚠️ 源代码扫描完毕，未探测到规范定义的指令模块。")

        query = message.text.split(maxsplit=1)[1].strip().lower() if len(message.text.split()) > 1 else ""

        # ── 精准查询模式 ──────────────────────────────────────────────────────
        if query:
            matched_cat = None
            matched_cmds = []
            for cat, entries in cmd_registry.items():
                if any(c.lower() == query or query in c.lower() for c, _ in entries):
                    matched_cat = cat
                    matched_cmds = entries
                    break

            if not matched_cat:
                return await smart_reply(message,
                    f"❌ 未找到指令 <code>.{query}</code>\n\n"
                    f"💡 输入 <code>.help</code> 查看全部指令列表",
                    disable_web_page_preview=True
                )

            lines_out = []
            for name, desc in matched_cmds:
                marker = "▶️" if name.lower() == query else "•"
                lines_out.append(f"{marker} <code>.{name}</code>\n   {desc}")

            body = (
                f"<b>🔍 指令查询：<code>.{query}</code></b>\n"
                f"<b>所属模块：</b>{matched_cat}\n"
                f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                + "\n".join(lines_out)
            )
            return await smart_reply(message, body, disable_web_page_preview=True)

        # ── 全览模式 ──────────────────────────────────────────────────────────
        total_cmds = sum(len(v) for v in cmd_registry.values())

        header  = f"<b>🌌 <a href='http://{VPS_IP}:{WEB_PORT}'>SHIO.NEXUS</a> | 动态指令矩阵</b>\n"
        header += f"就绪模块：<code>{total_cmds}</code> 个\n"
        header += "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

        body = "<blockquote expandable>\n"
        for cat, cmds in cmd_registry.items():
            if not cmds:
                continue
            body += f"<b>[ {cat} ]</b>\n"
            for name, desc in cmds:
                body += f"• <code>.{name}</code> — {desc}\n"
            body += "\n"
        body = body.strip() + "\n</blockquote>"

        footer  = "\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        footer += "<b>> 自动同步</b>：重启后生效 | <b>分类引擎</b>：物理正则 | <b>内核</b>：Pyrofork v2.3.69"

        await smart_reply(message, header + body + footer, disable_web_page_preview=True)

    except Exception as e:
        await smart_reply(message, f"❌ 帮助系统内部错误: <code>{str(e)}</code>")


        
# ==========================================
# 附加指令：AI 认知扩展与摘要模块
# ==========================================
from openai import AsyncOpenAI

OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"

MODEL_CONFIG_FILE  = "/home/tguser/current_model.txt"
PROMPT_CONFIG_FILE = "/home/tguser/system_prompt.txt"

AI_MEMORY: dict = {}

# 加载持久化配置
if os.path.exists(MODEL_CONFIG_FILE):
    with open(MODEL_CONFIG_FILE, "r", encoding="utf-8") as _f:
        CURRENT_MODEL = _f.read().strip() or "qwen/qwen3-6b-plus:free"
else:
    CURRENT_MODEL = "qwen/qwen3-6b-plus:free"

GLOBAL_SYSTEM_PROMPT = ""
if os.path.exists(PROMPT_CONFIG_FILE):
    with open(PROMPT_CONFIG_FILE, "r", encoding="utf-8") as _f:
        GLOBAL_SYSTEM_PROMPT = _f.read().strip()

MODEL_ALIASES = {
    "gemini":       "google/gemini-2.5-flash",
    "gemini-pro":   "google/gemini-2.5-pro",
    "gemini-flash": "google/gemini-2.5-flash",
    "claude":       "anthropic/claude-sonnet-4-5",
    "claude-opus":  "anthropic/claude-opus-4",
    "gpt4o":        "openai/gpt-4o",
    "gpt4o-mini":   "openai/gpt-4o-mini",
    "gpt4.1":       "openai/gpt-4.1",
    "o3":           "openai/o3",
    "o4-mini":      "openai/o4-mini",
    "llama3":       "meta-llama/llama-3.3-70b-instruct",
    "deepseek":     "deepseek/deepseek-chat-v3-0324",
    "deepseek-r1":  "deepseek/deepseek-r1",
    "grok":         "x-ai/grok-3-beta",
    "qwen":         "qwen/qwen3-235b-a22b",
}

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
) if OPENROUTER_API_KEY else None


@app.on_message((filters.me | authorized_filter) & filters.command("model", prefixes="."))
async def model_handler(client, message):
    """动态切换云端大语言模型"""
    global CURRENT_MODEL
    if len(message.command) == 1:
        alias_list = "\n".join(f"  `{k}` → `{v}`" for k, v in MODEL_ALIASES.items())
        return await smart_reply(message,
            f"**⚙️ 当前模型：** `{CURRENT_MODEL}`\n\n"
            f"**快捷别名：**\n{alias_list}\n\n"
            f"**用法：** `.model deepseek`"
        )
    target = message.text.split(maxsplit=1)[1].strip().lower()
    CURRENT_MODEL = MODEL_ALIASES.get(target, target)
    try:
        with open(MODEL_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(CURRENT_MODEL)
        await smart_reply(message, f"✅ 已切换至 `{CURRENT_MODEL}`")
    except Exception as e:
        await smart_reply(message, f"⚠️ 切换成功但持久化失败：`{str(e)}`")


@app.on_message((filters.me | authorized_filter) & filters.command("prompt", prefixes="."))
async def prompt_handler(client, message):
    """设置、查看或清除 AI 全局系统提示词"""
    global GLOBAL_SYSTEM_PROMPT
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        if GLOBAL_SYSTEM_PROMPT:
            return await smart_reply(message,
                f"**🧠 当前 System Prompt：**\n\n`{GLOBAL_SYSTEM_PROMPT}`\n\n"
                f"> 使用 `.prompt clear` 清除"
            )
        return await smart_reply(message,
            "**🧠 当前未设置 System Prompt**\n\n"
            "> 用法：`.prompt 你是一个极客助理，必须用中文回答`"
        )
    target = args[1].strip()
    if target.lower() in ("clear", "rm", "none", "清除", "清空"):
        GLOBAL_SYSTEM_PROMPT = ""
        if os.path.exists(PROMPT_CONFIG_FILE):
            os.remove(PROMPT_CONFIG_FILE)
        return await smart_reply(message, "✅ System Prompt 已清除，恢复默认状态。")
    GLOBAL_SYSTEM_PROMPT = target
    try:
        with open(PROMPT_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(GLOBAL_SYSTEM_PROMPT)
        await smart_reply(message,
            f"✅ **System Prompt 已设置：**\n`{GLOBAL_SYSTEM_PROMPT}`"
        )
    except Exception as e:
        await smart_reply(message, f"⚠️ 已生效但持久化失败：`{str(e)}`")


@app.on_message((filters.me | authorized_filter) & filters.command("aiqc", prefixes="."))
async def clear_ai_memory(client, message):
    """清空当前聊天节点的 AI 记忆"""
    chat_id = message.chat.id
    if chat_id in AI_MEMORY:
        del AI_MEMORY[chat_id]
        await smart_reply(message, "🧹 `当前会话记忆已清空`")
    else:
        await smart_reply(message, "🧹 `当前节点无记忆`")


@app.on_message((filters.me | authorized_filter) & filters.command("ai", prefixes="."))
async def ai_handler(client, message):
    """调用云端大模型进行交互，支持多模态图片/贴纸识别与上下文记忆"""
    from pyrogram.errors import MessageNotModified

    if not ai_client:
        return await smart_reply(message, "❌ 未配置 `OPENROUTER_API_KEY`")

    chat_id = message.chat.id
    AI_MEMORY.setdefault(chat_id, [])

    prompt    = message.text.split(maxsplit=1)[1] if len(message.command) > 1 else ""
    b64_image = None
    mime_type = "image/jpeg"

    # ── 处理回复引用 ──────────────────────────────────────────────────────────
    reply_to = message.reply_to_message
    if reply_to:
        if reply_to.photo or reply_to.sticker:
            loading = await smart_reply(message, "⏳ `[Vision] 正在编码图片...`")
            try:
                target_media = reply_to
                if reply_to.sticker:
                    if reply_to.sticker.is_animated or reply_to.sticker.is_video:
                        if reply_to.sticker.thumbs:
                            target_media = reply_to.sticker.thumbs[0].file_id
                        else:
                            raise ValueError("动态贴纸无缩略图")
                    else:
                        mime_type = "image/webp"
                file_mem = await client.download_media(target_media, in_memory=True)
                if file_mem:
                    b64_image = base64.b64encode(file_mem.getvalue()).decode()
                # 复用 loading 消息作为 reply_msg
                message = loading
            except Exception as e:
                return await loading.edit_text(f"❌ 图片提取失败：`{str(e)}`")

        ctx = reply_to.text or reply_to.caption or ""
        if ctx:
            prompt = f"引用内容：\n{ctx}\n\n请求：\n{prompt}" if prompt else ctx

    if not prompt and not b64_image:
        return await smart_reply(message, "❌ 请提供文本或回复包含内容的消息")
    if not prompt:
        prompt = "请详细描述并分析这张图片的内容与情绪。"

    # ── 构建消息 ──────────────────────────────────────────────────────────────
    if b64_image:
        user_msg = {"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}}
        ]}
    else:
        user_msg = {"role": "user", "content": prompt}

    AI_MEMORY[chat_id].append(user_msg)
    AI_MEMORY[chat_id] = AI_MEMORY[chat_id][-10:]  # 保留最近10轮

    api_messages = []
    if GLOBAL_SYSTEM_PROMPT:
        api_messages.append({"role": "system", "content": GLOBAL_SYSTEM_PROMPT})
    api_messages.extend(AI_MEMORY[chat_id])

    # ── 发占位消息，后续只 edit 这一条 ────────────────────────────────────────
    # 强制使用 reply 创建新消息，避免编辑原消息导致的 MessageIdInvalid 问题
    reply_msg = await message.reply(f"⏳ `{CURRENT_MODEL} 思考中...`")
    try:
        await message.delete()
    except Exception:
        pass

    try:
        stream = await ai_client.chat.completions.create(
            model=CURRENT_MODEL,
            messages=api_messages,
            stream=True,
            extra_body={"reasoning": {"effort": "high"}}
        )

        full_text      = ""
        last_edit_time = time.time()
        UPDATE_INTERVAL = 0.8

        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if not delta:
                continue
            full_text += delta

            if time.time() - last_edit_time >= UPDATE_INTERVAL:
                preview = "\n".join(
                    f"> {l}" if l.strip() else ">" for l in full_text.split("\n")
                )
                display = (preview[:4080] + "\n> █") if len(preview) > 4080 else (preview + " █")
                try:
                    await reply_msg.edit_text(f"**💡 AI 回复：**\n{display}")
                    last_edit_time = time.time()
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except (MessageNotModified, Exception):
                    pass

        # ── 最终输出 ──────────────────────────────────────────────────────────
        AI_MEMORY[chat_id].append({"role": "assistant", "content": full_text})
        final = "\n".join(
            f"**> {l}" if l.strip() else "**>" for l in full_text.split("\n")
        )
        final = (final[:4090] + "...") if len(final) > 4090 else final
        await reply_msg.edit_text(f"**💡 AI 回复：**\n{final}")

    except Exception as e:
        if AI_MEMORY[chat_id] and AI_MEMORY[chat_id][-1]["role"] == "user":
            AI_MEMORY[chat_id].pop()
        await reply_msg.edit_text(f"❌ **请求失败：**\n`{str(e)}`")


@app.on_message((filters.me | authorized_filter) & filters.command("sum", prefixes="."))
async def sum_handler(client, message):
    """生成近期聊天记录的 AI 摘要"""
    if not ai_client:
        return await smart_reply(message, "❌ 未配置 `OPENROUTER_API_KEY`")

    try:
        limit = min(int(message.command[1]), 1000) if len(message.command) > 1 else 50
    except ValueError:
        limit = 50

    reply_msg = await smart_reply(message, f"⏳ `正在抓取最近 {limit} 条记录...`")

    try:
        msgs = [m async for m in client.get_chat_history(message.chat.id, limit=limit)]
        msgs.reverse()
        history = "\n".join(
            f"{(m.from_user.first_name if m.from_user else '未知')}: {m.text or m.caption or '[媒体]'}"
            for m in msgs
        )
    except Exception as e:
        return await reply_msg.edit_text(f"❌ 记录读取失败：`{str(e)}`")

    if not history.strip():
        return await reply_msg.edit_text("❌ 未找到可解析的文本记录。")

    await reply_msg.edit_text("⏳ `AI 分析中...`")

    api_messages = []
    if GLOBAL_SYSTEM_PROMPT:
        api_messages.append({"role": "system", "content": GLOBAL_SYSTEM_PROMPT})
    api_messages.append({"role": "user", "content": (
        f"请阅读以下聊天记录，生成一份简洁的 TL;DR 摘要，提取核心话题和结论：\n\n{history}"
    )})

    try:
        resp = await ai_client.chat.completions.create(
            model=CURRENT_MODEL,
            messages=api_messages,
            extra_body={"reasoning": {"effort": "high"}}
        )
        res_text = resp.choices[0].message.content
        final = "\n".join(
            f"**> {l}" if l.strip() else "**>" for l in res_text.split("\n")
        )
        final = (final[:4090] + "...") if len(final) > 4090 else final
        await reply_msg.edit_text(f"**📑 摘要（近 {limit} 条）：**\n{final}")
    except Exception as e:
        await reply_msg.edit_text(f"❌ 摘要失败：`{str(e)}`")



# ==========================================
# 附加指令：流媒体深度解析引擎 (yt-dlp)
# ==========================================
# .ytdl <链接>          — 下载最高画质视频
# .ytdl <链接> -a       — 仅提取音频 (mp3)
# .ytdl <链接> -info    — 仅查看视频信息，不下载
# 支持: YouTube/B站/Twitter/TikTok/Instagram 等 1000+ 平台

# yt-dlp 可执行文件路径（自动探测，优先 venv pip 安装路径）
def _find_ytdlp() -> str:
    import shutil, sys
    candidates = [
        # pip install yt-dlp 在 venv 里的位置
        os.path.join(os.path.dirname(sys.executable), "yt-dlp"),
        "/home/tguser/tgbot_venv/bin/yt-dlp",
        "/root/.local/bin/yt-dlp",
        "/usr/local/bin/yt-dlp",
        "/usr/bin/yt-dlp",
        shutil.which("yt-dlp") or "",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""

_YTDLP_BIN = _find_ytdlp()

# 通用反检测参数
_YTDLP_BASE_ARGS = [
    "--no-warnings",
    "--no-playlist",
    "--socket-timeout", "30",
    "--add-header", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "--add-header", "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8",
    "--extractor-retries", "3",
    "--fragment-retries", "5",
    "--retry-sleep", "3",
]

@app.on_message((filters.me | authorized_filter) & filters.command("ytdl", prefixes="."))
async def media_downloader(client, message):
    """提取各大平台音视频，支持 -a 音频模式和 -info 信息查询"""
    args_raw = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""

    # 从回复消息取链接
    if not args_raw and message.reply_to_message:
        src = message.reply_to_message.text or message.reply_to_message.caption or ""
        m = re.search(r"https?://\S+", src)
        if m: args_raw = m.group(0)

    if not args_raw:
        return await smart_reply(message,
            "**🎬 流媒体解析引擎**\n\n"
            "`.ytdl <链接>` — 下载最高画质\n"
            "`.ytdl <链接> -a` — 仅提取音频\n"
            "`.ytdl <链接> -info` — 查看视频信息\n\n"
            "支持 YouTube / B站 / Twitter / TikTok 等 1000+ 平台"
        )

    # 解析模式标志
    audio_only = "-a" in args_raw
    info_only  = "-info" in args_raw
    url = re.sub(r"\s*-(a|info)\s*", " ", args_raw).strip()

    reply = await smart_reply(message, f"`[ytdl] 正在解析目标链接...`")

    # ── 信息查询模式 ──────────────────────────────────────────────────────────
    if not _YTDLP_BIN and (info_only or True):
        # 在 info_only 分支前统一检查，下面下载模式也有检查
        pass

    if info_only:
        if not _YTDLP_BIN:
            return await reply.edit_text(
                "❌ **未找到 yt-dlp**\n"
                "`/home/tguser/tgbot_venv/bin/pip install yt-dlp`"
            )
        cmd = [_YTDLP_BIN] + _YTDLP_BASE_ARGS + [
            "--dump-json", "--no-download", url
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode(errors="ignore")[-300:]
            return await reply.edit_text(f"❌ 解析失败\n`{err}`")
        try:
            import json as _json
            info = _json.loads(stdout.decode())
            duration = f"{info.get('duration', 0) // 60}:{info.get('duration', 0) % 60:02d}"
            text = (
                f"**🎬 视频信息**\n"
                f"**标题：** {info.get('title','未知')[:60]}\n"
                f"**来源：** {info.get('extractor_key','未知')}\n"
                f"**时长：** {duration}\n"
                f"**上传：** {info.get('uploader','未知')}\n"
                f"**画质：** {info.get('resolution','未知')}\n"
                f"**大小：** {(info.get('filesize') or info.get('filesize_approx') or 0) / 1024**2:.1f} MB\n"
                f"**链接：** `{url[:60]}`"
            )
            return await reply.edit_text(text)
        except Exception as e:
            return await reply.edit_text(f"❌ 信息解析异常：`{str(e)}`")

    # ── 下载模式 ──────────────────────────────────────────────────────────────
    # ── 检查 yt-dlp 是否存在 ─────────────────────────────────────────────────
    if not _YTDLP_BIN:
        return await reply.edit_text(
            "❌ **未找到 yt-dlp**\n\n"
            "请在 VPS 执行安装：\n"
            "`/home/tguser/tgbot_venv/bin/pip install yt-dlp`\n\n"
            "安装后重启 userbot 即可自动识别路径"
        )

    file_id   = f"ytdl_{message.id}_{int(time.time())}"
    out_tmpl  = f"/tmp/{file_id}.%(ext)s"
    downloaded_file = None

    if audio_only:
        fmt_args = [
            "-x", "--audio-format", "mp3", "--audio-quality", "0",
        ]
        await reply.edit_text("`[ytdl] 正在提取音频流...`")
    else:
        fmt_args = [
            "-f", "bestvideo[ext=mp4][filesize<1900M]+bestaudio[ext=m4a]/bestvideo[filesize<1900M]+bestaudio/best[filesize<1900M]/best",
            "--merge-output-format", "mp4",
        ]
        await reply.edit_text("`[ytdl] 正在拉取视频流，可能需要数分钟...`")

    cmd = [_YTDLP_BIN] + _YTDLP_BASE_ARGS + fmt_args + ["-o", out_tmpl, url]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 实时读取 stderr 进度，每 5 秒刷一次 UI
        last_ui = time.time()
        stderr_buf = []

        async def _read_stderr():
            async for line in proc.stderr:
                decoded = line.decode(errors="ignore").strip()
                stderr_buf.append(decoded)

        reader_task = asyncio.create_task(_read_stderr())

        # 等待进程完成，带超时
        try:
            await asyncio.wait_for(proc.wait(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            return await reply.edit_text("❌ 下载超时（10分钟），请检查链接或稍后重试")

        await reader_task

        # 找下载文件
        for f in os.listdir("/tmp"):
            if f.startswith(file_id):
                downloaded_file = os.path.join("/tmp", f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            err_tail = "\n".join(stderr_buf[-8:]) if stderr_buf else "文件未生成"
            # 分析常见错误
            err_str = err_tail.lower()
            if "403" in err_str or "forbidden" in err_str:
                hint = "\n💡 403 被拦截，可能需要 cookies 或平台不支持"
            elif "not found" in err_str or "no such" in err_str:
                hint = f"\n💡 yt-dlp 路径：`{_YTDLP_BIN}`，请确认已安装"
            elif "private" in err_str or "unavailable" in err_str:
                hint = "\n💡 视频不可用或需要登录"
            elif "copyright" in err_str:
                hint = "\n💡 版权限制，该地区无法下载"
            else:
                hint = ""
            return await reply.edit_text(
                f"❌ **下载失败**{hint}\n\n"
                f"<blockquote expandable>\n{err_tail}\n</blockquote>"
            )

        size_mb = os.path.getsize(downloaded_file) / 1024**2
        await reply.edit_text(f"🚀 `下载完成 ({size_mb:.1f} MB)，正在上传...`")

        # 进度回调（25% 步进防 FloodWait）
        _last_pct = [0]
        async def progress(current, total):
            if not total: return
            pct = int(current * 100 / total)
            if pct - _last_pct[0] >= 25:
                _last_pct[0] = pct
                try:
                    await reply.edit_text(f"🚀 `上传中... {pct}%`")
                except Exception:
                    pass

        ext = os.path.splitext(downloaded_file)[1].lower()
        if ext in (".mp3", ".m4a", ".ogg", ".flac", ".wav"):
            await client.send_audio(
                chat_id=message.chat.id,
                audio=downloaded_file,
                caption=f"🎵 `{url[:60]}`",
                progress=progress
            )
        elif ext in (".mp4", ".webm", ".mkv"):
            await client.send_video(
                chat_id=message.chat.id,
                video=downloaded_file,
                caption=f"🎥 `{url[:60]}`",
                progress=progress,
                supports_streaming=True
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=downloaded_file,
                caption=f"📦 `{url[:60]}`",
                progress=progress
            )

        await reply.delete()

    except Exception as e:
        await reply.edit_text(f"❌ 系统异常：`{str(e)}`")
    finally:
        if downloaded_file and os.path.exists(downloaded_file):
            try: os.remove(downloaded_file)
            except: pass
            
            
# ====================== 附加指令：网易云音乐全自动解析 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("wyjx", prefixes="."))
async def netease_parser(client, message):
    """网易云音乐自动解析与下载"""
    import os
    import urllib.parse
    from curl_cffi.requests import AsyncSession
    
    args = message.text.split(maxsplit=1)
    query = args[1] if len(args) > 1 else ""
    
    # 兼容回复搜歌机制
    if not query and message.reply_to_message:
        query = message.reply_to_message.text or message.reply_to_message.caption or ""
        
    if not query:
        return await smart_reply(message, "❌ **参数缺失**：请在指令后附带歌曲名/歌手，或回复包含歌名的消息。")

    reply = await smart_reply(message, f"⏳ `[Netease API] 正在检索关键词: {query}...`")
    
    # 伪装正常浏览器访问
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "http://music.163.com/"
    }
    
    try:
        # 复用已有的 curl_cffi 高级会话绕过特征检测
        async with AsyncSession(impersonate="chrome120") as session:
            # 1. 调用官方无加密搜索接口检索 Top 1 结果
            search_url = "http://music.163.com/api/search/get/web"
            data = {"s": query, "type": 1, "offset": 0, "total": "true", "limit": 1}
            search_res = await session.post(search_url, data=data, headers=headers, timeout=10)
            search_json = search_res.json()
            
            songs = search_json.get("result", {}).get("songs", [])
            if not songs:
                return await reply.edit_text(f"❌ **无匹配结果**：网易云音乐曲库中未找到 `{query}`。")
                
            song = songs[0]
            song_id = song["id"]
            
            # 2. 穿透获取歌曲详情 (为了拿到最高清的封面和标准歌手名称)
            detail_url = f"http://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
            detail_res = await session.get(detail_url, headers=headers, timeout=10)
            detail_json = detail_res.json()
            
            song_detail = detail_json.get("songs", [{}])[0]
            song_name = song_detail.get("name") or song.get("name", "Unknown Song")
            
            # 兼容网易云不同版本 API 的封面字段
            cover_url = ""
            if "al" in song_detail and "picUrl" in song_detail["al"]:
                cover_url = song_detail["al"]["picUrl"]
            elif "album" in song_detail and "picUrl" in song_detail["album"]:
                cover_url = song_detail["album"]["picUrl"]
                
            artists = song_detail.get("ar") or song.get("artists", [])
            artist_name = "/".join([ar.get("name", "Unknown") for ar in artists])
            
            await reply.edit_text(f"⏳ `[Netease API] 锁定目标: {song_name} - {artist_name}`\n`正在尝试剥离最高音质直链...`")
            
            # 3. 通过官方外链接口提取最高音频流
            dl_url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
            audio_res = await session.get(dl_url, headers=headers, allow_redirects=True, timeout=30)
            
            # 核心拦截逻辑：网易云对 VIP/无版权歌曲会返回极小体积的 404 伪装页或空文件
            if audio_res.status_code != 200 or len(audio_res.content) < 200000: # 小于 200KB 绝对不可能是完整音频
                return await reply.edit_text(
                    f"❌ **防盗链/VIP 墙阻断**：\n"
                    f"`{song_name} - {artist_name}` 属于 VIP 专享或存在版权限制。\n"
                    f"当前引擎无大会员授权，已被目标服务器拒绝拉取数据。"
                )
                
            # 落盘音频文件
            audio_file = f"/tmp/{song_id}.mp3"
            with open(audio_file, "wb") as f:
                f.write(audio_res.content)
                
            # 落盘封面文件 (使用网易云 CDN 原生图片裁剪参数，强制转为 320x320 适配 TG 缩略图规范)
            cover_file = None
            if cover_url:
                thumb_url = f"{cover_url}?param=320y320"
                cover_res = await session.get(thumb_url, headers=headers, timeout=10)
                if cover_res.status_code == 200:
                    cover_file = f"/tmp/{song_id}.jpg"
                    with open(cover_file, "wb") as f:
                        f.write(cover_res.content)
            
            await reply.edit_text(f"🚀 `[I/O] 音轨与封面剥离完成，正在切入 Telegram 传输管道...`")
            
            # 4. 推送到 Telegram
            await client.send_audio(
                chat_id=message.chat.id,
                audio=audio_file,
                caption=f"🎧 **{song_name}**\n👤 {artist_name}\n\n> `Parsed by SHIO.NEXUS`",
                performer=artist_name,
                title=song_name,
                thumb=cover_file
            )
            
            await reply.delete()
            
    except Exception as e:
        await reply.edit_text(f"❌ **API 交互崩溃**:\n`{str(e)[:200]}`")
    finally:
        # 释放 VPS 缓存，防止磁盘阻塞
        try:
            if 'audio_file' in locals() and os.path.exists(audio_file): os.remove(audio_file)
            if 'cover_file' in locals() and cover_file and os.path.exists(cover_file): os.remove(cover_file)
        except:
            pass
            
            
# ====================== 升级版：极客 UI 节点测速 ======================
def _run_speedtest() -> dict:
    """同步执行测速，兼容 speedtest-cli 新旧版本"""
    import speedtest
    st = speedtest.Speedtest(secure=True)
    st.get_best_server()
    st.download(threads=4)
    st.upload(threads=4)
    r = st.results

    # 兼容新旧版本：新版用属性，旧版用 .dict()
    try:
        res = r.dict()
    except AttributeError:
        res = {
            "download":       r.download,
            "upload":         r.upload,
            "ping":           r.ping,
            "bytes_received": getattr(r, "bytes_received", 0),
            "bytes_sent":     getattr(r, "bytes_sent", 0),
            "client": {
                "isp": getattr(r, "client", {}).get("isp", "未知") if isinstance(getattr(r, "client", None), dict) else "未知",
                "ip":  getattr(r, "client", {}).get("ip",  "未知") if isinstance(getattr(r, "client", None), dict) else "未知",
            },
            "server": {
                "id":      str(st.best.get("id", "")),
                "name":    st.best.get("name", ""),
                "sponsor": st.best.get("sponsor", ""),
                "cc":      st.best.get("cc", ""),
            },
        }

    # share URL（可能失败，容错）
    try:
        res["_share_url"] = r.share()
    except Exception:
        res["_share_url"] = None

    return res


@app.on_message((filters.me | authorized_filter) & filters.command("speed", prefixes="."))
async def vps_speedtest(client, message):
    """运行 Ookla 节点速度测试 (极客排版版)"""
    import psutil
    from datetime import datetime

    reply = await smart_reply(message, "`[Speed] 正在连接 Ookla 测速节点...`")

    # 测速阶段进度提示（异步任务）
    async def _progress_ticker():
        stages = [
            (5,  "`[Speed] 正在测量网络延迟 (Ping)...`"),
            (12, "`[Speed] ↓ 下行测速中...`"),
            (22, "`[Speed] ↑ 上行测速中...`"),
            (32, "`[Speed] 正在汇总结果...`"),
        ]
        for delay, msg in stages:
            await asyncio.sleep(delay)
            try:
                await reply.edit_text(msg)
            except Exception:
                pass

    ticker = asyncio.create_task(_progress_ticker())

    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, _run_speedtest)
    except Exception as e:
        ticker.cancel()
        return await reply.edit_text(
            f"❌ 测速失败：`{str(e)}`\n\n"
            f"💡 确认依赖已安装：`pip install speedtest-cli psutil`"
        )

    ticker.cancel()

    dl_speed   = round(res["download"] / 1_000_000, 2)
    ul_speed   = round(res["upload"]   / 1_000_000, 2)
    ping       = round(res["ping"], 2)
    isp        = res["client"].get("isp", "未知")
    client_ip  = res["client"].get("ip",  "未知")
    server_id  = res["server"].get("id",  "")
    server_name= res["server"].get("name","")
    sponsor    = res["server"].get("sponsor","")
    cc         = res["server"].get("cc",  "")
    bytes_recv = round(res.get("bytes_received", 0) / 1024**2, 2)
    bytes_sent = round(res.get("bytes_sent",     0) / 1024**2, 2)
    share_url  = res.get("_share_url")

    net_io = psutil.net_io_counters()
    rx_gb  = round(net_io.bytes_recv / 1024**3, 2)
    tx_gb  = round(net_io.bytes_sent / 1024**3, 2)

    current_time = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    flag = "".join(chr(ord(c) + 127397) for c in cc.upper()) if cc and len(cc) == 2 else "🏳️"

    # 排版保持原样
    text = (
        f"> ⚡️**SPEEDTEST by OOKLA** @{cc.upper()}{flag}\n"
        f"**名称** `{isp}`\n"
        f"**节点** `{server_id} - {sponsor} - {server_name}`\n"
        f"**网络** `{client_ip}`\n"
        f"**延迟** `⇔{ping} ms`\n"
        f"**速率** `↓{dl_speed} Mbps  ↑{ul_speed} Mbps`\n"
        f"**消耗** `↓{bytes_recv} MB  ↑{bytes_sent} MB`\n"
        f"**统计** `RX {rx_gb} GB  TX {tx_gb} GB`\n"
        f"**时间** `{current_time}`"
    )

    if share_url:
        await client.send_photo(chat_id=message.chat.id, photo=share_url, caption=text)
        await message.delete()
    else:
        # share URL 生成失败时直接发文字（不影响数据展示）
        await reply.edit_text(text)
        
        
# ====================== 附加指令：刷屏与转发复读机 ======================
# .fd [次数] [文本]      — 重复发送文本
# .fd [次数]             — 回复消息，重复转发
# .fd stop               — 中止正在进行的刷屏任务
# 次数上限 100，间隔自适应防 FloodWait

_FD_RUNNING: dict = {}  # chat_id -> True，用于中止控制

@app.on_message((filters.me | authorized_filter) & filters.command("fd", prefixes="."))
async def flood_message(client, message):
    """重复发送文本或转发消息，支持 .fd stop 中止"""
    from pyrogram.errors import FloodWait

    chat_id    = message.chat.id
    target_msg = message.reply_to_message
    args       = message.text.split()

    # ── stop 指令 ─────────────────────────────────────────────────────────────
    if len(args) >= 2 and args[1].lower() == "stop":
        if _FD_RUNNING.get(chat_id):
            _FD_RUNNING[chat_id] = False
            return await smart_reply(message, "`[fd] 已发送中止信号，等待当前批次完成...`")
        return await smart_reply(message, "`[fd] 当前没有正在运行的任务`")

    # ── 解析参数 ──────────────────────────────────────────────────────────────
    # 格式: .fd [次数] [-i 间隔秒] [文本]
    # 例: .fd 10 -i 2 你好    → 每 2 秒发一次，共 10 次
    #     .fd 5 -i 0.5        → 回复消息，每 0.5 秒转发一次
    count        = 1
    text_to_spam = ""
    interval     = 0.5  # 默认间隔

    # 先从参数里提取 -i 间隔
    raw_text = message.text
    import re as _re
    i_match = _re.search(r"-i\s+([\d.]+)", raw_text)
    if i_match:
        try:
            interval = float(i_match.group(1))
            interval = max(0.1, min(interval, 60.0))  # 限制 0.1s ~ 60s
        except ValueError:
            pass
        # 从文本里移除 -i 参数，避免被当成内容
        raw_text = _re.sub(r"\s*-i\s+[\d.]+", "", raw_text).strip()

    # 重新解析清洗后的参数
    clean_args = raw_text.split()
    if len(clean_args) >= 2:
        try:
            count = int(clean_args[1])
            text_to_spam = " ".join(clean_args[2:]) if len(clean_args) > 2 else ""
        except ValueError:
            text_to_spam = " ".join(clean_args[1:])
            count = 1

    count = max(1, min(count, 100))

    if not text_to_spam and not target_msg:
        return await smart_reply(message,
            "**📢 复读机**\n\n"
            "`.fd [次数] [文本]` — 重复发送\n"
            "`.fd [次数]` — 回复消息重复转发\n"
            "`.fd [次数] -i [秒]` — 自定义间隔\n"
            "`.fd stop` — 中止任务\n\n"
            "示例: `.fd 10 -i 2 你好` 每 2 秒发一次\n"
            "间隔范围: 0.1s ~ 60s，上限 100 次"
        )

    # ── 防并发：同一会话只允许一个任务 ───────────────────────────────────────
    if _FD_RUNNING.get(chat_id):
        return await smart_reply(message, "❌ 该会话已有任务在运行，请先 `.fd stop`")

    try:
        await message.delete()
    except Exception:
        pass

    _FD_RUNNING[chat_id] = True
    sent = 0

    try:
        for i in range(count):
            if not _FD_RUNNING.get(chat_id):
                break  # 收到 stop 信号

            try:
                if text_to_spam:
                    await client.send_message(chat_id=chat_id, text=text_to_spam)
                else:
                    await target_msg.forward(chat_id=chat_id)
                sent += 1
                await asyncio.sleep(interval)

            except FloodWait as e:
                # 触发限流：休眠 + 增大间隔
                wait = e.value + 1
                interval = min(interval * 1.5, 5.0)
                await asyncio.sleep(wait)

            except Exception as e:
                break

    finally:
        _FD_RUNNING.pop(chat_id, None)


# ====================== 附加指令：进程级强杀与内核重载 ======================
@app.on_message(filters.me & filters.command("kill", prefixes="."))
async def kill_and_restart(client, message):
    """强制停止当前所有程序进程，并自动重启"""
    import os
    import sys
    import asyncio

    await smart_reply(message, "🔄 `[System] 收到高优先级终止指令，正在强杀当前进程树并重载 Userbot 内核...`")
    
    # 短暂休眠确保 edit_text 请求已完全发送到 TG 服务器
    await asyncio.sleep(0.5)
    
    # 调用系统底层接口替换当前进程，实现无缝重启，不残留僵尸进程
    os.execl(sys.executable, sys.executable, *sys.argv)
    
# ====================== 附加指令：Telegram 数据中心测速 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("dc", prefixes="."))
async def tg_dc_ping(client, message):
    """测试 Telegram 官方数据中心延迟，支持自定义次数"""
    # .dc        — 默认 5 次
    # .dc 20     — 自定义 1~100 次
    args = message.text.split()
    count = 5
    if len(args) >= 2:
        try:
            count = max(1, min(int(args[1]), 100))
        except ValueError:
            pass

    reply = await smart_reply(message, f"`[DC] 正在并发探测 TG 五大数据中心 (×{count})...`")

    dcs = [
        ("DC1", "US · Miami",     "149.154.175.50"),
        ("DC2", "NL · Amsterdam", "149.154.167.51"),
        ("DC3", "US · Miami",     "149.154.175.100"),
        ("DC4", "NL · Amsterdam", "149.154.167.91"),
        ("DC5", "SG · Singapore", "91.108.56.110"),
    ]

    def _emoji(ms: float) -> str:
        if ms < 50:  return "🟢"
        if ms < 120: return "🟡"
        if ms < 250: return "🟠"
        return "🔴"

    async def ping_dc(dc_id, location, ip):
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", str(count), "-q", "-W", "2", ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            timeout = count * 2.5 + 5
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode()

            loss_m = re.search(r"(\d+)%\s+packet loss", out)
            loss   = int(loss_m.group(1)) if loss_m else 100

            rtt_m = re.search(
                r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out
            )
            if rtt_m and loss < 100:
                avg    = float(rtt_m.group(2))
                jitter = float(rtt_m.group(4))
                loss_str = f" ⚠️ {loss}% loss" if loss > 0 else ""
                return dc_id, location, avg, (
                    f"{_emoji(avg)} **{dc_id}** `{location}`  "
                    f"`{avg:.1f} ms`  ±`{jitter:.1f}`{loss_str}"
                )
            else:
                return dc_id, location, 9999, (
                    f"🔴 **{dc_id}** `{location}`  `超时/全部丢包`"
                )
        except asyncio.TimeoutError:
            return dc_id, location, 9999, f"🔴 **{dc_id}** `{location}`  `探测超时`"
        except Exception as e:
            return dc_id, location, 9999, f"🔴 **{dc_id}** `{location}`  `{str(e)[:40]}`"

    tasks = [ping_dc(*dc) for dc in dcs]
    raw   = await asyncio.gather(*tasks)

    fastest = min(raw, key=lambda x: x[2])[0] if any(x[2] < 9999 for x in raw) else None

    from datetime import datetime
    ts    = datetime.now(LOCAL_TZ).strftime("%H:%M:%S")
    lines = []
    for dc_id, location, avg_ms, line in raw:
        suffix = "  ← 最快" if dc_id == fastest else ""
        lines.append(line + suffix)

    await asyncio.sleep(1.6)
    await reply.edit_text(
        f"📡 **TG DC Latency** · `{ts}`  ×{count}\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        + "\n".join(lines)
    )
    
    
# ====================== 附加指令：多形态动态文本动画 ======================
@app.on_message((filters.me | authorized_filter) & filters.command(["animate", "anim"], prefixes="."))
async def dynamic_animation(client, message):
    """生成多形态动态文本动画 (支持: type/scroll/heart/load)"""
    import asyncio
    from pyrogram.errors import FloodWait

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        text = (
            "❌ **参数缺失**\n"
            "用法: `.animate [模式] [文本]`\n\n"
            "**可用模式:**\n"
            "`type` - 打字机效果 (默认)\n"
            "`scroll` - 跑马灯滚动\n"
            "`heart` - 心跳变色框\n"
            "`load` - 极客进度条"
        )
        return await smart_reply(message, text)

    mode = "type"
    target_text = ""

    # 解析参数层
    if args[1].lower() in ["type", "scroll", "heart", "load"]:
        mode = args[1].lower()
        if len(args) > 2:
            target_text = args[2]
        else:
            return await smart_reply(message, f"❌ 请在模式 `{mode}` 后附带要渲染的文本。")
    else:
        # 缺省模式，直接读取全部文本
        target_text = message.text.split(maxsplit=1)[1]

    frames = []

    # 构造帧序列引擎
    if mode == "type":
        # 打字机帧
        for i in range(1, len(target_text) + 1):
            cursor = "█" if i % 2 != 0 else "_"
            frames.append(target_text[:i] + cursor)
        frames.append(target_text)

    elif mode == "scroll":
        # 跑马灯帧
        pad = " ｜ "
        scroll_text = target_text + pad
        length = len(scroll_text)
        for _ in range(2): # 滚动两圈
            for i in range(length):
                frames.append(f" `{scroll_text[i:]}{scroll_text[:i]}` ")
        frames.append(f" `{target_text}` ")

    elif mode == "heart":
        # 心跳特效帧
        hearts = ["❤️", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍"]
        for _ in range(3): # 循环闪烁3轮
            for h in hearts:
                frames.append(f"{h} {target_text} {h}")
        frames.append(f"✨ {target_text} ✨")

    elif mode == "load":
        # 终端加载条帧
        bar_len = 12
        for i in range(bar_len + 1):
            filled = "█" * i
            empty = "▒" * (bar_len - i)
            percent = int((i / bar_len) * 100)
            frames.append(f"**{target_text}**\n`[{filled}{empty}] {percent}%`")
        frames.append(f"**{target_text}**\n`[████████████] 100%`\n✅ **PROCESS COMPLETE**")

    # 帧间延迟配置 (规避 FloodWait，越复杂的帧延迟越高)
    delay_map = {"type": 0.15, "scroll": 0.25, "heart": 0.3, "load": 0.35}
    delay = delay_map.get(mode, 0.2)

    # 渲染层
    for frame in frames:
        try:
            await smart_reply(message, frame)
            await asyncio.sleep(delay)
        except FloodWait as e:
            # 触发流控熔断
            await asyncio.sleep(e.value)
        except Exception:
            pass
            
# ====================== 附加指令：极致涩气萌化 (R18 猫娘) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("mm", prefixes="."))
async def erotic_catgirl_moeify(client, message):
    """将文本转换为极度撩人/涩气的猫娘风格 (支持 .mm r18)"""
    import random
    import re

    args = message.command
    is_r18 = len(args) > 1 and args[1].lower() == "r18"
    
    # 提取目标文本：如果是 .mm r18 [文本]，跳过 r18 取后面的
    if is_r18:
        target_text = message.text.split(maxsplit=2)[2] if len(args) > 2 else ""
    else:
        target_text = message.text.split(maxsplit=1)[1] if len(args) > 1 else ""

    # 如果指令后没词，尝试读取回复内容
    if not target_text and message.reply_to_message:
        target_text = message.reply_to_message.text or message.reply_to_message.caption or ""

    if not target_text:
        return await smart_reply(message, "❌ 参数缺失：请使用 `.mm r18 [文本]` 或回复消息。")

    if is_r18:
        # 1. R18 核心词库替换 (高强度色化)
        replacements = {
            "我": "人家", "你": "主人", "的": "哒", "了": "啦～💦", "吧": "叭～♡",
            "饿": "下面饿饿", "渴": "想要主人的精液", "疼": "被干得好疼",
            "快点": "快点干进来", "舒服": "高潮了", "不行": "要被操坏了",
            "好": "超色色", "看": "舔", "走": "去床上做色色的事",
            "不要": "不要停止用力", "怎么": "怎喵", "什么": "什喵"
        }
        
        # 2. 随机插入娇喘词
        gasps = ["…啊哈…", "…唔嗯…", "…呀啊…", "…呼…哈…", "…嗯啊…"]
        words = list(target_text)
        for _ in range(len(words) // 4):
            insert_pos = random.randint(0, len(words))
            words.insert(insert_pos, random.choice(gasps))
        target_text = "".join(words)

        for k, v in replacements.items():
            target_text = target_text.replace(k, v)

        # 3. 涩气前缀与后缀
        prefixes = ["呜呜…主人…人家…", "啊…哈…主人…", "人家的小穴已经…", "本喵好想要…"]
        suffixes = ["～想被大肉棒填满喵💦", "～求主人现在就干进来🔞", "～要被操到喷水了呜呜♡", "～(灬ºωº灬)♡💦"]
        kaomojis = ["(灬ºωº灬)♡💦", "(>﹏<)🔞", "(つд⊂)💦", "✧(>ω<)✧🔥", "(づ￣ ³￣)づ🔞"]
        
        final_text = f"{random.choice(prefixes)}{target_text}{random.choice(suffixes)} {random.choice(kaomojis)}"
    else:
        # 常规萌化逻辑 (保留原有逻辑)
        replacements = {"的": "哒", "了": "啦", "吧": "叭", "吗": "嘛", "呢": "捏", "我": "人家", "你": "尼"}
        for k, v in replacements.items():
            target_text = target_text.replace(k, v)
        final_text = f"喵呜～{target_text}喵～ (灬ºωº灬)♡"

    try:
        await smart_reply(message, final_text)
    except Exception as e:
        await smart_reply(message, f"❌ 欲望引擎溢出: `{str(e)}`")
# ====================== 附加指令：零宽字符隐写术 (.hide / .seek) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("hide", prefixes="."))
async def hide_secret_text(client, message):
    """将机密文本隐写入普通文本中
    用法: .hide [伪装文本] || [机密文本]
    """
    import base64
    
    # 定义零宽字典
    ZW_0 = '\u200b'
    ZW_1 = '\u200c'
    ZW_M = '\u200d'

    args = message.text.split(" || ", 1)
    if len(args) < 2:
        return await smart_reply(message, "❌ 格式错误。用法: `.hide 表面伪装的文字 || 需要隐藏的机密文字`")
    
    # 提取伪装层与机密层
    cover_text = args[0].split(maxsplit=1)[1] if len(args[0].split(maxsplit=1)) > 1 else ""
    secret_text = args[1]

    if not cover_text or not secret_text:
        return await smart_reply(message, "❌ 伪装文本和机密文本均不能为空。")

    try:
        # 1. 将机密文本转为 UTF-8 字节，再转为二进制字符串
        secret_bytes = secret_text.encode('utf-8')
        binary_str = ''.join(format(byte, '08b') for byte in secret_bytes)
        
        # 2. 将 0 和 1 映射为不可见的零宽字符
        zw_str = binary_str.replace('0', ZW_0).replace('1', ZW_1)
        
        # 3. 使用界定符包裹隐写矩阵，并将其插入到伪装文本的第一个字符之后
        # 避免放在句首或句尾导致在某些 UI 渲染引擎中被 Trim 掉
        payload = ZW_M + zw_str + ZW_M
        
        if len(cover_text) > 1:
            final_text = cover_text[0] + payload + cover_text[1:]
        else:
            final_text = cover_text + payload

        await smart_reply(message, final_text)

    except Exception as e:
        await smart_reply(message, f"❌ 隐写编码失败: `{str(e)}`")


@app.on_message((filters.me | authorized_filter) & filters.command("seek", prefixes="."))
async def seek_secret_text(client, message):
    """提取消息中的零宽字符隐写内容"""
    import re

    # 定义零宽字典
    ZW_0 = '\u200b'
    ZW_1 = '\u200c'
    ZW_M = '\u200d'

    target_msg = message.reply_to_message
    if not target_msg:
        return await smart_reply(message, "❌ 请回复一条包含隐写数据的消息。")

    text_to_scan = target_msg.text or target_msg.caption or ""
    if not text_to_scan:
        return await smart_reply(message, "❌ 目标消息不包含可读取的文本载体。")

    try:
        # 正则匹配被 ZW_M 包裹的 ZW_0 和 ZW_1 序列
        pattern = f"{ZW_M}([{ZW_0}{ZW_1}]+){ZW_M}"
        match = re.search(pattern, text_to_scan)
        
        if not match:
            return await smart_reply(message, "⚠️ 未在目标文本中探测到零宽隐写矩阵。")
            
        zw_payload = match.group(1)
        
        # 1. 将零宽字符还原为二进制字符串
        binary_str = zw_payload.replace(ZW_0, '0').replace(ZW_1, '1')
        
        # 2. 校验二进制流长度是否为 8 的倍数
        if len(binary_str) % 8 != 0:
            return await smart_reply(message, "❌ 数据损毁：隐写矩阵存在丢包或被非标准客户端截断。")
            
        # 3. 按字节重组并解码为 UTF-8
        bytes_list = [int(binary_str[i:i+8], 2) for i in range(0, len(binary_str), 8)]
        secret_text = bytes(bytes_list).decode('utf-8')
        
        out_text = (
            f"**[隐写解密完成]**\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"**隐写载荷体积:** `{len(bytes_list)} Bytes`\n"
            f"**提取内容:**\n`{secret_text}`"
        )
        
        await smart_reply(message, out_text)

    except UnicodeDecodeError:
        await smart_reply(message, "❌ 解密失败：提取的二进制流无法通过 UTF-8 校验，可能混入了非协议噪声。")
    except Exception as e:
        await smart_reply(message, f"❌ 解密引擎异常: `{str(e)}`")
# ====================== 附加指令：媒体数字取证 (.exif) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("exif", prefixes="."))
async def forensic_exif(client, message):
    """数字取证: 解析原图/视频文件元数据"""
    import os
    import json
    import asyncio
    import io
    
    # 动态导入依赖，防止启动失败
    try:
        import exifread
    except ImportError:
        return await smart_reply(message, "❌ 缺少依赖：请在 VPS 执行 `pip install exifread`")

    target_msg = message.reply_to_message
    
    # 验证目标：必须是文件(Document)
    if not target_msg or not target_msg.document:
        return await smart_reply(message, "❌ 请回复一个以**“文件(Document)”**格式发送的照片或视频。")

    mime = target_msg.document.mime_type or ""
    if not (mime.startswith("image/") or mime.startswith("video/")):
        return await smart_reply(message, f"❌ 不支持的文件类型: `{mime}`。仅支持 JPEG/TIFF 图片及部分 MP4/MOV 视频。")

    reply = await smart_reply(message, "⏳ `[Forensic] 正在将文件载入内存进行头部流解构...`")
    
    try:
        # 1. 载入内存
        file_mem = await client.download_media(target_msg, in_memory=True)
        file_mem.seek(0)
        
        # 2. 调用 exifread 解析二进制流
        # details=False 不解析 MakerNote 详细数据以加快速度
        tags = exifread.process_file(file_mem, details=False)
        
        if not tags:
            return await reply.edit_text("⚠️ 该文件头部未探测到标准 EXIF 矩阵。可能已被抹除或格式不支持。")

        # 辅助函数：安全获取 Tag 值
        def g(key):
            tag = tags.get(key)
            if not tag: return "N/A"
            # 格式化 Rational 类型 (例如将 "1/125" 保持原样，"18/10" 转为 "1.8")
            return str(tag)

        # 3. 核心字段提取
        # [设备与软件]
        make = g('Image Make')
        model = g('Image Model')
        software = g('Image Software')
        modify_date = g('Image DateTime') # 软件最后修改时间
        
        # [拍摄参数]
        lens_model = g('EXIF LensModel')
        f_number = g('EXIF FNumber')
        iso = g('EXIF ISOSpeedRatings')
        focal_length = g('EXIF FocalLength')
        
        # 曝光时间特殊处理：将 "0.008" 转为 "1/125"
        exposure_time_tag = tags.get('EXIF ExposureTime')
        if exposure_time_tag:
            val = exposure_time_tag.values[0]
            if val < 1 and val > 0:
                exposure_time = f"1/{int(1/val)}"
            else:
                exposure_time = str(val)
        else:
            exposure_time = "N/A"
            
        original_date = g('EXIF DateTimeOriginal') # 原始拍摄时间

        # 4. GPS 深度解析与反查
        gps_lat = tags.get('GPS GPSLatitude')
        gps_lat_ref = tags.get('GPS GPSLatitudeRef')
        gps_lon = tags.get('GPS GPSLongitude')
        gps_lon_ref = tags.get('GPS GPSLongitudeRef')
        
        location_str = "N/A"
        address_str = "N/A"

        if gps_lat and gps_lat_ref and gps_lon and gps_lon_ref:
            try:
                # 坐标转换辅助函数：Rational D/M/S -> Decimal
                def convert_to_deciamal(curr_tags, ref):
                    d = float(curr_tags.values[0].num) / float(curr_tags.values[0].den)
                    m = float(curr_tags.values[1].num) / float(curr_tags.values[1].den)
                    s = float(curr_tags.values[2].num) / float(curr_tags.values[2].den)
                    deciamal = d + (m / 60.0) + (s / 3600.0)
                    if ref.values in ['S', 'W']: deciamal = -deciamal
                    return deciamal

                lat = convert_to_deciamal(gps_lat, gps_lat_ref)
                lon = convert_to_deciamal(gps_lon, gps_lon_ref)
                location_str = f"`{lat:.6f}, {lon:.6f}`"
                
                await reply.edit_text(f"⏳ `[Geocoder] 探测到坐标 {location_str}，正在反查物理街道...`")
                
                # 调用 Nominatim 接口进行逆地理编码
                from curl_cffi.requests import AsyncSession
                # Nominatim 要求必须有明确的 User-Agent
                headers = {"User-Agent": "SHIO.NEXUS.Forensics/1.0 (TG_Userbot)"}
                geo_url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=zh-CN&zoom=18"
                
                async with AsyncSession() as session:
                    res = await session.get(geo_url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        geo_data = res.json()
                        address_str = geo_data.get('display_name', '未找到具体街道')
                    else:
                        address_str = f"API 请求失败 (HTTP {res.status_code})"
            except Exception as e:
                address_str = f"坐标解析/反查异常: `{str(e)}`"

        # 5. 格式化输出 (极客控制台风格)
        text = (
            f"🛠 **Data Forensics Report**\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"**>** **[设备矩阵]**\n"
            f"制 造 商： `{make}`\n"
            f"设备型号： `{model}`\n"
            f"镜头型号： `{lens_model}`\n\n"
            
            f"**>** **[拍摄参数]**\n"
            f"光 圈 值： `f/{f_number}`\n"
            f"快 门： `{exposure_time}`\n"
            f"感 光 度： `ISO {iso}`\n"
            f"焦 距： `{focal_length} mm`\n"
            f"拍摄时间： `{original_date}`\n\n"
            
            f"**>** **[地理位置]**\n"
            f"精确坐标： {location_str}\n"
            f"物理街道： `{address_str}`\n\n"
            
            f"**>** **[数字水印]**\n"
            f"处理软件： `{software}`\n"
            f"修改时间： `{modify_date}`\n"
            f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"> `Mode: Deep Packet Inspection`"
        )
        
        await reply.edit_text(text)

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        await reply.edit_text(f"❌ **取证核心崩溃**:\n`{str(e)}`\n\n`{error_details[-200:]}`")
# ====================== 附加指令：阅后即焚自毁协议 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("bomb", prefixes="."))
async def self_destruct_message(client, message):
    """发送带有进度条的倒数自毁消息，归零后双向删除"""
    import asyncio
    from pyrogram.errors import FloodWait

    args = message.command
    if len(args) < 3:
        return await smart_reply(message, "格式: `.bomb [秒数] [文本]`")

    try:
        total_time = int(args[1])
        if total_time <= 0 or total_time > 300:
            return await smart_reply(message, "错误：秒数需在 1-300 之间")
    except ValueError:
        return await smart_reply(message, "错误：秒数必须为整数")

    text = message.text.split(maxsplit=2)[2]
    bar_len = 10

    for remaining in range(total_time, 0, -1):
        progress = (total_time - remaining) / total_time
        filled = int(bar_len * progress)
        bar = "=" * filled + "-" * (bar_len - filled)
        
        display_text = f"{text}\n\n`[{bar}] {remaining}s`"
        
        try:
            await smart_reply(message, display_text)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass
            
        await asyncio.sleep(1)

    try:
        await message.delete()
    except Exception:
        pass
# ====================== 附加指令：原生闪照 (私聊阅后即焚) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("flash", prefixes="."))
async def native_flash_media(client, message):
    """发送原生闪照 (仅限私聊)
    格式: .flash [秒数] (回复图片或视频)
    """
    args = message.text.split()
    if len(args) < 2:
        return await smart_reply(message, "❌ 格式: `.flash [秒数]` (需回复图片或视频)")
        
    try:
        ttl = int(args[1])
    except ValueError:
        return await smart_reply(message, "❌ 秒数必须为整数")

    target = message.reply_to_message
    if not target or not (target.photo or target.video):
        return await smart_reply(message, "❌ 未找到可提取的图片或视频节点")

    try:
        await message.delete()
        if target.photo:
            await client.send_photo(message.chat.id, target.photo.file_id, ttl_seconds=ttl)
        elif target.video:
            await client.send_video(message.chat.id, target.video.file_id, ttl_seconds=ttl)
    except Exception as e:
        # TG 限制：ttl_seconds 参数在 Group/Channel 环境下会导致 API 报错
        await client.send_message(message.chat.id, f"❌ 原生闪照调用失败，请确认当前环境为 1 对 1 私聊。详细日志: `{str(e)}`")


# ====================== 附加指令：VPS 旁路外链销毁 (收藏夹中转版) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("burn", prefixes="."))
async def vps_burn_media_secure(client, message):
    """
    在“收藏夹”发送媒体并在附言处填写:
    .burn [秒数] [目标@用户名/UID]
    """
    import time
    import os
    import asyncio

    if not message.media:
        return await smart_reply(message, "❌ 必须附带媒体文件发送")

    args = message.caption.split() if message.caption else message.text.split()
    if len(args) < 3:
        return await smart_reply(message, "❌ 格式: `.burn [秒数] [目标@用户名/UID]` (在附言中填写)")
        
    try:
        ttl = int(args[1])
        target_chat = args[2]
    except ValueError:
        return await smart_reply(message, "❌ 秒数必须为整数")

    reply = await message.reply_text("⏳ `[Bypass] 正在 VPS 本地构建隔离区...`")

    try:
        # 1. 提取流至 VPS
        file_name_tmp = f"burn_{int(time.time())}.tmp"
        file_path_tmp = os.path.join(EXPORT_DIR, file_name_tmp)

        downloaded_path = await client.download_media(message, file_name=file_path_tmp)
        if not downloaded_path:
            raise ValueError("API 拒绝下发二进制流")

        # 2. 修正扩展名
        ext = ".jpg"
        if message.video: ext = ".mp4"
        elif message.document and message.document.file_name: ext = os.path.splitext(message.document.file_name)[1]
        elif message.voice: ext = ".ogg"
            
        final_path = downloaded_path + ext
        os.rename(downloaded_path, final_path)
        final_file_name = os.path.basename(final_path)

        # 3. 生成 Nginx 外链
        link = f"http://{VPS_IP}:{WEB_PORT}/{final_file_name}"

        # 4. 跨域投递至目标用户
        await client.send_message(
            chat_id=target_chat,
            text=f"**[ 绝密数据传输 ]**\n此链接将在 `{ttl}` 秒后从物理扇区销毁。\n\n🔗 {link}",
            disable_web_page_preview=True
        )

        await reply.edit_text(f"✅ `[Bypass] 数据已投递至 {target_chat}，开始 {ttl}s 销毁倒数...`")

        # 5. 倒数执行物理覆写
        await asyncio.sleep(ttl)
        if os.path.exists(final_path):
            os.remove(final_path)
            
        await reply.edit_text(f"🗑 `[Bypass] 物理扇区已覆写清空。`")

    except Exception as e:
        await reply.edit_text(f"❌ 旁路搭建失败: `{str(e)}`")
        try:
            if 'final_path' in locals() and os.path.exists(final_path): os.remove(final_path)
        except: pass

# ====================== 极致视觉渲染：单消息帧动画 (迷你画面+高频换轨版) ======================
@app.on_message((filters.me | authorized_filter) & filters.command("badapple", prefixes="."))
async def render_ascii_animation(client, message):
    """ASCII 动画 (迷你画面 + 可视化进度条 + 防风控)"""
    import asyncio
    import os
    import time
    from pyrogram.enums import ParseMode
    from pyrogram.errors import FloodWait, MessageNotModified

    try:
        import cv2
    except ImportError:
        return await smart_reply(message, "❌ 缺失依赖: `pip install opencv-python-headless`")

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    VIDEO_FILE = os.path.join(BASE_DIR, "badapple.mp4")
    
    if not os.path.exists(VIDEO_FILE) or os.path.getsize(VIDEO_FILE) < 1024:
        return await smart_reply(message, "❌ 视频文件异常，请重新下载。")

    # [参数调优]
    TARGET_WIDTH = 28       # 画面宽度减半
    CHAR_ASPECT_RATIO = 2.2
    TARGET_FPS = 1.0  
    MAX_EDITS_PER_MSG = 10  # 降低阈值：每 10 次编辑强制换一条新消息
    CHARS = r"$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,\"^`'. "

    cap = cv2.VideoCapture(VIDEO_FILE)
    v_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    v_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    
    if v_width <= 0 or v_height <= 0:
        cap.release()
        return await smart_reply(message, "❌ OpenCV 解码失败。")

    original_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    target_height = int(TARGET_WIDTH * (v_height / v_width) / CHAR_ASPECT_RATIO)
    
    current_msg = await smart_reply(message, "⏳ `[Render] 迷你渲染引擎启动...`")
    await asyncio.sleep(1)

    loop = asyncio.get_running_loop()
    last_edit_time = 0
    frame_interval = 1.0 / TARGET_FPS
    char_len = len(CHARS) - 1
    
    last_sent_text = ""
    edit_count = 0

    def process_frame(frame_img, current_edit_count):
        gray = cv2.cvtColor(frame_img, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (TARGET_WIDTH, target_height))
        # 修复了 NumPy uint8 溢出问题
        ascii_lines = ["".join([CHARS[int(p) * char_len // 255] for p in row]) for row in resized]
        
        # 底部追加可视进度条
        status_bar = f"\n[ 缓冲: {current_edit_count}/{MAX_EDITS_PER_MSG} | FPS: {TARGET_FPS} ]"
        return f"<pre>{chr(10).join(ascii_lines)}{status_bar}</pre>"

    frame_idx = 0
    frame_step = int(original_fps / TARGET_FPS) or 1

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret: break

            frame_idx += 1
            if frame_idx % frame_step != 0: continue

            formatted_ascii = await loop.run_in_executor(None, process_frame, frame, edit_count + 1)
            
            # 去除进度条内容进行对比，防止因进度条数字变化而重复发送相同画面
            pure_ascii = formatted_ascii.rsplit('\n[', 1)[0]
            pure_last = last_sent_text.rsplit('\n[', 1)[0] if last_sent_text else ""
            
            if pure_ascii == pure_last:
                continue

            now = time.time()
            wait = last_edit_time + frame_interval - now
            if wait > 0: await asyncio.sleep(wait)

            try:
                if edit_count >= MAX_EDITS_PER_MSG:
                    new_msg = await current_msg.reply_text(formatted_ascii, parse_mode=ParseMode.HTML)
                    await current_msg.delete() 
                    current_msg = new_msg
                    edit_count = 0
                else:
                    await current_msg.edit_text(formatted_ascii, parse_mode=ParseMode.HTML)
                
                edit_count += 1
                last_sent_text = formatted_ascii
                last_edit_time = time.time()
                
            except FloodWait as e:
                # 若仍被拦截，强制休眠后立刻换轨
                await current_msg.reply_text(f"⚠️ `触发风控，被迫休眠 {e.value}s...`")
                await asyncio.sleep(e.value)
                last_edit_time = time.time()
                edit_count = MAX_EDITS_PER_MSG 
            except MessageNotModified:
                pass
                
    except Exception as e:
        await current_msg.edit_text(f"❌ 渲染终结: `{str(e)}`")
    finally:
        cap.release()
        try:
            await current_msg.edit_text(f"{last_sent_text}\n\n🎬 **[播放结束]**", parse_mode=ParseMode.HTML)
        except:
            pass

# ====================== 附加指令：异步定时发送消息 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("sch", prefixes="."))
async def schedule_message(client, message):
    """异步定时发送消息
    格式: .sch [时间] [文本] (时间单位: s/m/h)
    """
    import asyncio
    import re

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        return await smart_reply(message, "❌ 格式: `.sch [时间] [文本]`\n示例: `.sch 5m 记得喝水`")

    time_str = args[1].lower()
    text = args[2]

    match = re.match(r"^(\d+)([smh])$", time_str)
    if not match:
        return await smart_reply(message, "❌ 时间格式错误。支持的单位：s(秒), m(分), h(时)。示例: `30s`, `10m`")

    amount = int(match.group(1))
    unit = match.group(2)

    if unit == "s":
        delay = amount
    elif unit == "m":
        delay = amount * 60
    elif unit == "h":
        delay = amount * 3600

    if delay <= 0:
        return await smart_reply(message, "❌ 延迟时间必须大于 0。")

    await smart_reply(message, f"⏳ `[Timer] 消息已进入调度队列，将于 {amount}{unit} 后发送。`")

    async def send_later():
        await asyncio.sleep(delay)
        await client.send_message(chat_id=message.chat.id, text=f"⏰ **[定时消息]**\n{text}")

    asyncio.create_task(send_later())
# ====================== 附加指令：发送 Bad Apple 原视频 ======================
@app.on_message((filters.me | authorized_filter) & filters.command("sendapple", prefixes="."))
async def send_badapple_video(client, message):
    """直接上传 VPS 本地的 Bad Apple 原视频"""
    import os

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    VIDEO_FILE = os.path.join(BASE_DIR, "badapple.mp4")

    if not os.path.exists(VIDEO_FILE) or os.path.getsize(VIDEO_FILE) < 1024:
        return await smart_reply(message, "❌ 本地未检测到有效的 badapple.mp4 文件，请先下载。")

    await smart_reply(message, "⏳ `[Upload] 正在读取本地流并向 TG 服务器推流...`")

    try:
        await client.send_video(
            chat_id=message.chat.id,
            video=VIDEO_FILE,
            caption="🎬 **Bad Apple!!**",
            reply_to_message_id=message.reply_to_message_id or message.id
        )
        await message.delete()
    except Exception as e:
        await smart_reply(message, f"❌ 视频推流失败: `{str(e)}`")
# ====================== 附加指令：.honeypot 金丝雀陷阱 ======================
#
# 功能：在文本中注入不可见的零宽字符指纹，追踪信息泄露来源
#
# 用法：
#   .honeypot set <文本>       生成指纹文本 + 存档
#   .honeypot send <ID> <文本> 向指定用户发送专属指纹版本
#   .honeypot check <文本>     解码疑似泄露文本，查出归属人
#   .honeypot list             查看所有已发出的指纹记录
#   .honeypot clear            清空所有存档记录
#
# 联动：复用 .hide/.seek 的零宽编解码逻辑
# 存储：本地 JSON 文件持久化，重启不丢失
# ============================================================================

import json
import os
import time
from datetime import datetime
from pyrogram import filters

# ── 零宽字符映射表 ────────────────────────────────────────────────────────────
_ZW_0 = "\u200B"   # 零宽空格     → bit 0
_ZW_1 = "\u200C"   # 零宽非连接符 → bit 1
_ZW_SEP = "\u200D" # 零宽连接符   → 字节分隔符

# ── 存档路径 ──────────────────────────────────────────────────────────────────
_HONEYPOT_DB = os.path.join(os.path.dirname(__file__), "honeypot_records.json")


# ── 存档读写 ──────────────────────────────────────────────────────────────────
def _load_records() -> dict:
    if not os.path.exists(_HONEYPOT_DB):
        return {}
    try:
        with open(_HONEYPOT_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_records(records: dict):
    with open(_HONEYPOT_DB, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ── 指纹编码 ──────────────────────────────────────────────────────────────────
def _encode_fingerprint(uid: int) -> str:
    """将 user_id 编码为零宽字符串"""
    uid_bytes = str(uid).encode("utf-8")
    bits = []
    for byte in uid_bytes:
        for i in range(7, -1, -1):
            bits.append(_ZW_1 if (byte >> i) & 1 else _ZW_0)
        bits.append(_ZW_SEP)
    return "".join(bits)


def _decode_fingerprint(text: str) -> str | None:
    """从文本中提取零宽指纹，还原为 user_id 字符串"""
    zw_chars = [c for c in text if c in (_ZW_0, _ZW_1, _ZW_SEP)]
    if not zw_chars:
        return None

    result_bytes = []
    current_bits = []
    for c in zw_chars:
        if c == _ZW_SEP:
            if len(current_bits) == 8:
                byte_val = 0
                for bit in current_bits:
                    byte_val = (byte_val << 1) | (1 if bit == _ZW_1 else 0)
                result_bytes.append(byte_val)
            current_bits = []
        else:
            current_bits.append(c)

    try:
        return bytes(result_bytes).decode("utf-8")
    except Exception:
        return None


def _inject_fingerprint(text: str, uid: int) -> str:
    """将指纹注入文本中段（插在第一个换行后，无换行则插在末尾）"""
    fingerprint = _encode_fingerprint(uid)
    newline_pos = text.find("\n")
    if newline_pos != -1:
        return text[:newline_pos + 1] + fingerprint + text[newline_pos + 1:]
    return text + fingerprint


# ── 主处理函数 ─────────────────────────────────────────────────────────────────
@app.on_message((filters.me | authorized_filter) & filters.command("honeypot", prefixes="."))
async def honeypot_handler(client, message):
    """金丝雀陷阱 — 零宽指纹追踪信息泄露来源"""

    args = message.text.split(maxsplit=2)
    sub = args[1].lower() if len(args) >= 2 else ""

    records = _load_records()

    # ── .honeypot set <文本> ──────────────────────────────────────────────────
    if sub == "set":
        if len(args) < 3:
            return await smart_reply(message, "❌ 格式: `.honeypot set <文本>`")

        raw_text = args[2]
        # 用自己的 ID 作为模板基准版本
        me = await client.get_me()
        fingerprinted = _inject_fingerprint(raw_text, me.id)

        record_id = str(int(time.time()))
        records[record_id] = {
            "original": raw_text,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sent_to": {}
        }
        _save_records(records)

        await smart_reply(message, 
            f"✅ **指纹模板已创建**\n\n"
            f"**记录 ID:** `{record_id}`\n"
            f"**原文预览:** `{raw_text[:80]}{'...' if len(raw_text) > 80 else ''}`\n\n"
            f"📤 发送给目标: `.honeypot send <用户ID> <文本>`\n"
            f"🔍 检测泄露: `.honeypot check <疑似文本>`"
        )

    # ── .honeypot send <用户ID> <文本> ───────────────────────────────────────
    elif sub == "send":
        parts = message.text.split(maxsplit=3)
        if len(parts) < 4:
            return await smart_reply(message, "❌ 格式: `.honeypot send <用户ID> <文本>`")

        try:
            target_id = int(parts[2])
        except ValueError:
            return await smart_reply(message, "❌ 用户 ID 必须是数字")

        raw_text = parts[3]
        fingerprinted = _inject_fingerprint(raw_text, target_id)

        try:
            await client.send_message(target_id, fingerprinted)
        except Exception as e:
            return await smart_reply(message, f"❌ 发送失败: `{e}`")

        # 存档这条发送记录（按时间戳归档）
        record_id = str(int(time.time()))
        if record_id not in records:
            records[record_id] = {
                "original": raw_text,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sent_to": {}
            }

        # 尝试获取目标用户名
        try:
            target_user = await client.get_users(target_id)
            target_label = f"@{target_user.username}" if target_user.username else target_user.first_name
        except Exception:
            target_label = str(target_id)

        records[record_id]["sent_to"][str(target_id)] = {
            "label": target_label,
            "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        _save_records(records)

        await smart_reply(message, 
            f"✅ **已发送带指纹版本**\n\n"
            f"**接收方:** {target_label} (`{target_id}`)\n"
            f"**记录 ID:** `{record_id}`\n"
            f"**文本预览:** `{raw_text[:60]}{'...' if len(raw_text) > 60 else ''}`\n\n"
            f"_指纹已静默注入，接收方不可见_"
        )

    # ── .honeypot check <文本> ────────────────────────────────────────────────
    elif sub == "check":
        if len(args) < 3:
            return await smart_reply(message, "❌ 格式: `.honeypot check <疑似泄露文本>`")

        suspect_text = args[2]
        decoded_uid = _decode_fingerprint(suspect_text)

        if not decoded_uid:
            return await smart_reply(message, 
                "🔍 **检测结果：未发现指纹**\n\n"
                "此文本不含零宽字符指纹，可能已被清洗或非本系统生成"
            )

        # 在存档里匹配
        match_label = None
        match_record_id = None
        for record_id, record in records.items():
            if decoded_uid in record.get("sent_to", {}):
                match_label = record["sent_to"][decoded_uid].get("label", decoded_uid)
                match_record_id = record_id
                break

        if match_label:
            sent_at = records[match_record_id]["sent_to"][decoded_uid].get("sent_at", "未知")
            await smart_reply(message, 
                f"🎯 **指纹命中 — 泄露来源已定位**\n\n"
                f"**泄露者:** {match_label} (`{decoded_uid}`)\n"
                f"**发送时间:** {sent_at}\n"
                f"**记录 ID:** `{match_record_id}`"
            )
        else:
            await smart_reply(message, 
                f"🔍 **指纹已解码，但无匹配存档**\n\n"
                f"**解码 ID:** `{decoded_uid}`\n"
                f"_可能存档已被清除，或指纹来自其他设备的记录_"
            )

    # ── .honeypot list ────────────────────────────────────────────────────────
    elif sub == "list":
        if not records:
            return await smart_reply(message, "📋 **暂无指纹记录**")

        lines = ["📋 **金丝雀陷阱 — 发送记录**\n"]
        for record_id, record in sorted(records.items(), reverse=True)[:10]:
            preview = record.get("original", "")[:40]
            created = record.get("created_at", "未知")
            sent_count = len(record.get("sent_to", {}))
            recipients = ", ".join(
                v.get("label", k) for k, v in record.get("sent_to", {}).items()
            ) or "—"
            lines.append(
                f"**ID:** `{record_id}`\n"
                f"  创建: {created}\n"
                f"  已发: {sent_count} 人 → {recipients}\n"
                f"  内容: `{preview}{'...' if len(preview) == 40 else ''}`\n"
            )

        await smart_reply(message, "\n".join(lines))

    # ── .honeypot clear ───────────────────────────────────────────────────────
    elif sub == "clear":
        _save_records({})
        await smart_reply(message, "🗑 **所有指纹记录已清空**")

    # ── 帮助 ──────────────────────────────────────────────────────────────────
    else:
        await smart_reply(message, 
            "🍯 **HONEYPOT — 金丝雀陷阱**\n\n"
            "在文本中注入不可见零宽指纹，精准追踪信息泄露来源\n\n"
            "**指令:**\n"
            "`.honeypot set <文本>` — 创建带指纹的文本模板\n"
            "`.honeypot send <ID> <文本>` — 向指定用户发专属指纹版本\n"
            "`.honeypot check <文本>` — 解码疑似泄露文本查出归属人\n"
            "`.honeypot list` — 查看所有发送记录\n"
            "`.honeypot clear` — 清空所有存档\n\n"
            "**原理:** 每条发送版本含唯一零宽字符水印，肉眼/截图不可见"
        )
# ====================== 附加指令：语言风格克隆 (.echo) ======================
# 联动说明：本模块直接读写 GLOBAL_SYSTEM_PROMPT 与 PROMPT_CONFIG_FILE

@app.on_message((filters.me | authorized_filter) & filters.command("echo", prefixes="."))
async def echo_handler(client, message):
    """抓取目标语气特征并注入 AI 认知 (.echo @用户 / 回复消息)"""
    global GLOBAL_SYSTEM_PROMPT
    from datetime import datetime
    import asyncio

    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    # 1. 功能：关闭克隆
    if sub in ["off", "clear", "重置"]:
        GLOBAL_SYSTEM_PROMPT = ""
        if os.path.exists(PROMPT_CONFIG_FILE):
            os.remove(PROMPT_CONFIG_FILE)
        return await smart_reply(message, "🔇 **Echo 模式已关闭**\n`.ai` 已恢复原始人格。")

    # 2. 功能：查看状态
    if sub == "status":
        status_text = "📭 **Echo 当前未激活**"
        if GLOBAL_SYSTEM_PROMPT:
            status_text = f"🔊 **Echo 激活中**\n\n**当前注入人格：**\n`{GLOBAL_SYSTEM_PROMPT[:300]}...`"
        return await smart_reply(message, status_text)

    # 3. 功能：开启克隆 (解析目标)
    target_user = None
    sample_limit = 50

    # 优先解析回复的消息
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
        if len(args) > 1 and args[1].isdigit():
            sample_limit = int(args[1])
    # 其次解析 @用户名
    elif len(args) > 1:
        try:
            target_user = await client.get_users(args[1])
            if len(args) > 2 and args[2].isdigit():
                sample_limit = int(args[2])
        except Exception:
            return await smart_reply(message, "❌ 无法解析该目标用户。")
    
    if not target_user:
        return await smart_reply(message, "❌ 请回复一条消息或跟上 `@用户名`。\n用法：`.echo @汐 50` 或回复消息后输入 `.echo 50`")

    sample_limit = max(10, min(sample_limit, 200)) # 限制样本范围
    await smart_reply(message, f"🔍 **正在从当前会话提取 {target_user.first_name} 的 {sample_limit} 条语料...**")

    # 抓取逻辑
    samples = []
    async for msg in client.get_chat_history(message.chat.id, limit=sample_limit * 5):
        if len(samples) >= sample_limit:
            break
        if msg.from_user and msg.from_user.id == target_user.id:
            text = msg.text or msg.caption or ""
            # 过滤短文本、指令、及空内容
            if text and not text.startswith(".") and len(text) > 2:
                samples.append(text)

    if len(samples) < 5:
        return await smart_reply(message, f"⚠️ **样本贫瘠**：仅获取到 {len(samples)} 条有效文本，无法提炼。")

    await smart_reply(message, f"🧠 **正在基于 {len(samples)} 条语料构建性格补丁...**")

    # 构造分析模型指令
    corpus = "\n".join([f"- {s}" for s in samples])
    analysis_prompt = (
        f"以下是用户「{target_user.first_name}」的原始通讯记录样本：\n\n{corpus}\n\n"
        f"任务目标：\n"
        f"1. 深度分析该用户的词汇偏好（口头禅、emoji频率、中英混杂习惯）。\n"
        f"2. 提取句式结构（是碎片化短句还是长难句）。\n"
        f"3. 捕捉情绪底色（高冷、暴躁、儒雅、随性、阴阳怪气等）。\n\n"
        f"请基于此生成一段高效的 System Prompt。要求：\n"
        f"1. 以“你现在完全模仿[该用户]说话”开头。\n"
        f"2. 输出内容必须简洁且极具特征，严禁输出分析过程，只输出 Prompt 内容。"
    )

    try:
        # 调用 OpenRouter 接口
        response = await ai_client.chat.completions.create(
            model=CURRENT_MODEL,
            messages=[
                {"role": "system", "content": "你是一个精通自然语言处理与人格侧写的行为学专家。"},
                {"role": "user", "content": analysis_prompt}
            ],
            extra_body={"reasoning": {"effort": "high"}}
        )
        
        injected_style = response.choices[0].message.content.strip()
        
        # 联动持久化逻辑
        GLOBAL_SYSTEM_PROMPT = injected_style
        with open(PROMPT_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(GLOBAL_SYSTEM_PROMPT)

        await smart_reply(message, 
            f"✅ **风格克隆已生效**\n\n"
            f"**目标：** {target_user.first_name}\n"
            f"**样本：** {len(samples)} 条有效数据\n\n"
            f"**[性格补丁预览]**\n"
            f"<blockquote>{injected_style[:500]}...</blockquote>\n"
            f"💡 *现在使用 `.ai` 提问，其语气将与目标保持一致。*"
        )

    except Exception as e:
        await smart_reply(message, f"❌ **人格提炼失败**：\n`{str(e)}`")

# ====================== 附加指令：关键词自动回复引擎 ======================
# 规则分为两种作用域：
#   全局规则  — 所有会话均触发
#   本地规则  — 仅当前会话触发
#
# .ar add <模式> <回复>         — 添加本地规则（仅当前会话）
# .ar addg <模式> <回复>        — 添加全局规则（所有会话）
# .ar addr <模式> <回复>        — 添加本地正则规则
# .ar addrg <模式> <回复>       — 添加全局正则规则
# .ar del <ID>                  — 删除当前会话规则
# .ar delg <ID>                 — 删除全局规则
# .ar list                      — 查看当前会话+全局规则
# .ar listg                     — 仅查看全局规则
# .ar clear                     — 清空当前会话规则
# .ar clearg                    — 清空全局规则
#
# 延时/自动删除 (附加在回复末尾用 || 分隔):
#   delay=3        — 延迟 3 秒后回复
#   ttl=10         — 回复发出后 10 秒自动删除
# ============================================================================

import re as _re
import asyncio as _asyncio

_AR_FILE = "/home/tguser/autoreply_rules.json"

def _ar_load() -> dict:
    """加载规则，结构: {"global": [...], "chat_id": [...], ...}"""
    try:
        with open(_AR_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容旧格式（list）自动迁移为新格式
            if isinstance(data, list):
                return {"global": data}
            return data
    except Exception:
        return {"global": []}

def _ar_save(rules: dict):
    with open(_AR_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)

def _ar_get(rules: dict, scope: str) -> list:
    return rules.setdefault(scope, [])

AR_RULES: dict = _ar_load()

def _ar_parse_options(content: str):
    """解析回复内容中的 delay/ttl/limit 参数"""
    delay, ttl, limit = 0, 0, 0
    if " || " in content:
        text_part, opt_part = content.rsplit(" || ", 1)
        for seg in opt_part.split(","):
            seg = seg.strip()
            if seg.startswith("delay="):
                try: delay = int(seg[6:])
                except: pass
            elif seg.startswith("ttl="):
                try: ttl = int(seg[4:])
                except: pass
            elif seg.startswith("limit="):
                try: limit = max(1, int(seg[6:]))
                except: pass
    else:
        text_part = content
    return text_part.strip(), delay, ttl, limit


@app.on_message(filters.me & filters.command("ar", prefixes="."))
async def autoreply_manager(client, message):
    """管理关键词自动回复规则（支持本地/全局作用域）"""
    global AR_RULES
    chat_key = str(message.chat.id)
    args = message.text.split(maxsplit=1)
    sub  = args[1].strip() if len(args) > 1 else ""

    # ── list ─────────────────────────────────────────────────────────────────
    if not sub or sub in ("list", "listg"):
        show_global = True
        show_local  = sub != "listg"
        lines = []

        def _fmt_rule(r, idx, prefix=""):
            mode_s = "正则" if r["mode"] == "regex" else "完全"
            opt = []
            if r.get("delay"):  opt.append(f"delay={r['delay']}s")
            if r.get("ttl"):    opt.append(f"ttl={r['ttl']}s")
            if r.get("limit"):  opt.append(f"limit={r['limit']}次(已{r.get('count',0)})")
            opt_str = f" [{', '.join(opt)}]" if opt else ""
            return f"  **[{prefix}{idx}]** `{r['pattern']}` ({mode_s}) → `{r['reply']}`{opt_str}"

        if show_global:
            g_rules = _ar_get(AR_RULES, "global")
            if g_rules:
                lines.append("**🌐 全局规则：**")
                for i, r in enumerate(g_rules):
                    lines.append(_fmt_rule(r, i, "g"))
            elif not show_local:
                lines.append("`全局规则列表为空`")

        if show_local:
            l_rules = _ar_get(AR_RULES, chat_key)
            s_rules = _ar_get(AR_RULES, "self_" + chat_key)
            if l_rules:
                lines.append("**💬 本会话规则（别人发触发）：**")
                for i, r in enumerate(l_rules):
                    lines.append(_fmt_rule(r, i))
            if s_rules:
                lines.append("**👤 本会话自触发规则（自己发触发）：**")
                for i, r in enumerate(s_rules):
                    lines.append(_fmt_rule(r, i, "s"))
            if not l_rules and not s_rules:
                lines.append("`本会话暂无规则`")

        # 全局自触发规则
        sg_rules = _ar_get(AR_RULES, "self_global")
        if sg_rules:
            lines.append("**👤🌐 全局自触发规则：**")
            for i, r in enumerate(sg_rules):
                lines.append(_fmt_rule(r, i, "sg"))

        return await message.edit_text("\n".join(lines) if lines else "`规则列表为空`")

    # ── clear ─────────────────────────────────────────────────────────────────
    if sub == "clear":
        AR_RULES.pop(chat_key, None)
        AR_RULES.pop("self_" + chat_key, None)
        _ar_save(AR_RULES)
        return await message.edit_text("`✅ 已清空本会话所有规则（含自触发）`")

    if sub == "clearg":
        AR_RULES["global"] = []
        _ar_save(AR_RULES)
        return await message.edit_text("`✅ 已清空全局规则`")

    if sub == "clearsg":
        AR_RULES["self_global"] = []
        _ar_save(AR_RULES)
        return await message.edit_text("`✅ 已清空全局自触发规则`")

    # ── del ───────────────────────────────────────────────────────────────────
    if any(sub.startswith(p) for p in ("del ", "delg ", "dels ", "delsg ")):
        is_global  = sub.startswith("delg ")
        is_self_d  = sub.startswith("dels ")
        is_self_dg = sub.startswith("delsg ")
        if is_global:      scope = "global"
        elif is_self_dg:   scope = "self_global"
        elif is_self_d:    scope = "self_" + chat_key
        else:              scope = chat_key
        id_str = sub.split(maxsplit=1)[1].strip()
        try:
            idx     = int(id_str)
            bucket  = _ar_get(AR_RULES, scope)
            removed = bucket.pop(idx)
            _ar_save(AR_RULES)
            if is_global:     tag = "全局"
            elif is_self_dg:  tag = "全局自触发"
            elif is_self_d:   tag = "本会话自触发"
            else:             tag = "本会话"
            return await message.edit_text(f"`✅ 已删除{tag}规则 [{idx}]: {removed['pattern']}`")
        except Exception as e:
            return await message.edit_text(f"`❌ 删除失败: {e}`")

    # ── add / addr / addg / addrg / adds / addrs ─────────────────────────────
    # adds  = 匹配自己发送的消息并自动回复（自触发）
    # addrs = 自触发正则版
    is_self        = sub.startswith("adds") and not sub.startswith("addsg")
    is_self_global = sub.startswith("addsg ") or sub.startswith("addrsg ")
    is_global      = (sub.startswith("addg ") or sub.startswith("addrg ")) and not is_self_global
    is_regex       = sub.startswith("addr")
    is_self        = is_self and not is_self_global

    if is_self_global:
        scope = "self_global"
    elif is_self:
        scope = "self_" + chat_key
    elif is_global:
        scope = "global"
    else:
        scope = chat_key
    mode = "regex" if is_regex else "exact"

    # 剥离子命令前缀
    for prefix in ("addrsg ", "addsg ", "addrg ", "addg ", "addrs ", "adds ", "addr ", "add "):
        if sub.startswith(prefix):
            sub = sub[len(prefix):]
            break
    else:
        return await message.edit_text(
            "**用法：**\n"
            "`.ar add <关键词> <回复>` — 本会话（别人发触发）\n"
            "`.ar addg <关键词> <回复>` — 全局（别人发触发）\n"
            "`.ar adds <关键词> <回复>` — 本会话（自己发触发）\n"
            "`.ar addr / addrg / addrs` — 正则版本\n"
            "`.ar del <ID>` — 删本会话  `.ar delg <ID>` — 删全局\n"
            "`.ar list` — 查看  `.ar clear / clearg` — 清空\n\n"
            "**附加参数** (用 `||` 分隔)：\n"
            "`delay=N` 延迟N秒  `ttl=N` N秒后删除  `limit=N` 最多触发N次"
        )

    parts = sub.split(maxsplit=1)
    if len(parts) < 2:
        return await message.edit_text("`❌ 格式: .ar add <关键词> <回复内容>`")

    pattern = parts[0]
    reply_text, delay, ttl, limit = _ar_parse_options(parts[1])

    if mode == "regex":
        try:
            _re.compile(pattern)
        except Exception as e:
            return await message.edit_text(f"`❌ 正则表达式错误: {e}`")

    rule = {
        "pattern": pattern, "mode": mode, "reply": reply_text,
        "delay": delay, "ttl": ttl, "limit": limit, "count": 0,
        "self_trigger": is_self,
    }
    bucket = _ar_get(AR_RULES, scope)
    bucket.append(rule)
    _ar_save(AR_RULES)

    opt_info = []
    if delay: opt_info.append(f"延迟 {delay}s")
    if ttl:   opt_info.append(f"{ttl}s 后删除")
    if limit: opt_info.append(f"限触发 {limit} 次")
    opt_str  = f"，{' + '.join(opt_info)}" if opt_info else ""
    if is_self_global:   tag = "👤🌐 自触发全局"
    elif is_self:        tag = "👤 自触发本会话"
    elif is_global:      tag = "🌐 全局"
    else:                tag = "💬 本会话"
    mode_str = "正则" if mode == "regex" else "完全"
    await message.edit_text(
        f"✅ **{tag}规则已添加 [ID: {len(bucket)-1}]**\n"
        f"模式: `{pattern}` ({mode_str}匹配){opt_str}\n"
        f"回复: `{reply_text}`"
    )


async def _ar_fire(rule: dict, message, rules_dict: dict):
    """执行一条 AR 规则，处理 limit/delay/ttl，并更新计数"""
    # limit 检查
    limit = rule.get("limit", 0)
    if limit:
        if rule.get("count", 0) >= limit:
            return  # 已达上限，跳过
        rule["count"] = rule.get("count", 0) + 1
        _ar_save(rules_dict)

    delay = rule.get("delay", 0)
    ttl   = rule.get("ttl", 0)
    if delay:
        await _asyncio.sleep(delay)
    sent = await message.reply(rule["reply"])
    if ttl and sent:
        async def _del(msg, t):
            await _asyncio.sleep(t)
            try: await msg.delete()
            except: pass
        _asyncio.create_task(_del(sent, ttl))


def _ar_match(rule: dict, text: str) -> bool:
    if rule["mode"] == "regex":
        try:
            return bool(_re.search(rule["pattern"], text))
        except Exception:
            return False
    return text == rule["pattern"]


@app.on_message(filters.incoming & filters.text & ~filters.me)
async def autoreply_trigger(client, message):
    """关键词自动回复触发器（别人发消息，本地优先+全局兜底）"""
    if not AR_RULES:
        return
    text     = (message.text or "").strip()
    chat_key = str(message.chat.id)
    if not text:
        return
    for scope in (chat_key, "global"):
        for rule in _ar_get(AR_RULES, scope):
            if rule.get("self_trigger"):
                continue  # 自触发规则跳过
            if _ar_match(rule, text):
                await _ar_fire(rule, message, AR_RULES)
                return


@app.on_message(filters.me & filters.text)
async def autoreply_self_trigger(client, message):
    """自触发规则：匹配自己发的消息（本会话优先，全局兜底）"""
    if not AR_RULES:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("."):
        return
    chat_key = "self_" + str(message.chat.id)
    # 本会话自触发 > 全局自触发
    for scope in (chat_key, "self_global"):
        for rule in _ar_get(AR_RULES, scope):
            if _ar_match(rule, text):
                await _ar_fire(rule, message, AR_RULES)
                return



# ====================== 附加指令：屏蔽过滤引擎 ======================
# .block add [reply/@username/uid]  — 屏蔽用户 (不再自动回复/响应其指令)
# .block del [reply/@username/uid]  — 解除屏蔽
# .block bot                        — 屏蔽所有 bot 账号
# .block unbot                      — 解除全局 bot 屏蔽
# .block list                       — 查看屏蔽列表
# .block clear                      — 清空屏蔽列表
# ============================================================================

_BLOCK_FILE = "/home/tguser/block_list.json"

def _block_load() -> dict:
    try:
        with open(_BLOCK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": [], "block_bots": False}

def _block_save(data: dict):
    with open(_BLOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

_BLOCK_DATA: dict = _block_load()
BLOCKED_USERS: set = set(_BLOCK_DATA.get("users", []))
BLOCK_ALL_BOTS: bool = _BLOCK_DATA.get("block_bots", False)


def _is_blocked(_, __, message):
    """检查消息发送者是否被屏蔽"""
    if message.from_user is None:
        return False
    if BLOCK_ALL_BOTS and message.from_user.is_bot:
        return True
    return message.from_user.id in BLOCKED_USERS

blocked_filter = filters.create(_is_blocked)


@app.on_message(filters.me & filters.command("block", prefixes="."))
async def block_manager(client, message):
    """管理屏蔽列表"""
    global BLOCKED_USERS, BLOCK_ALL_BOTS, _BLOCK_DATA
    args = message.text.split()
    sub = args[1].lower() if len(args) > 1 else ""

    def _persist():
        _BLOCK_DATA = {"users": list(BLOCKED_USERS), "block_bots": BLOCK_ALL_BOTS}
        _block_save(_BLOCK_DATA)

    if sub == "list":
        bot_status = "🤖 全局 Bot 屏蔽：**开启**" if BLOCK_ALL_BOTS else "🤖 全局 Bot 屏蔽：关闭"
        if not BLOCKED_USERS:
            return await message.edit_text(f"{bot_status}\n\n`屏蔽列表为空`")
        lines = "\n".join([f"• `{uid}`" for uid in BLOCKED_USERS])
        return await message.edit_text(f"{bot_status}\n\n**已屏蔽用户：**\n{lines}")

    if sub == "clear":
        BLOCKED_USERS.clear()
        _persist()
        return await message.edit_text("`✅ 已清空屏蔽列表`")

    if sub == "bot":
        BLOCK_ALL_BOTS = True
        _persist()
        return await message.edit_text("🤖 `✅ 已开启全局 Bot 屏蔽，所有 bot 的消息将不触发自动回复`")

    if sub == "unbot":
        BLOCK_ALL_BOTS = False
        _persist()
        return await message.edit_text("🤖 `✅ 已关闭全局 Bot 屏蔽`")

    if sub in ("add", "del"):
        target_id = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
        elif len(args) >= 3:
            arg = args[2].lstrip("@")
            try:
                target_id = int(arg)
            except ValueError:
                try:
                    user = await client.get_users(arg)
                    target_id = user.id
                except Exception:
                    return await message.edit_text(f"`❌ 找不到用户: {arg}`")

        if not target_id:
            return await message.edit_text("`❌ 请指定目标用户（回复消息或附带 @用户名/UID）`")

        if sub == "add":
            BLOCKED_USERS.add(target_id)
            _persist()
            await message.edit_text(f"`✅ 已屏蔽 {target_id}`")
        else:
            BLOCKED_USERS.discard(target_id)
            _persist()
            await message.edit_text(f"`✅ 已解除屏蔽 {target_id}`")
        return

    await message.edit_text(
        "**🚫 屏蔽引擎用法：**\n"
        "`.block add` — 回复消息或跟 @用户名/UID 屏蔽\n"
        "`.block del` — 解除屏蔽\n"
        "`.block bot` — 屏蔽所有 bot\n"
        "`.block unbot` — 解除 bot 屏蔽\n"
        "`.block list` — 查看列表\n"
        "`.block clear` — 清空列表"
    )


# 在自动回复触发器中注入屏蔽检查 (通过给 autoreply_trigger 加前置守卫)
# 原触发器已注册，这里用更高优先级的 handler 拦截被屏蔽用户
@app.on_message(blocked_filter & filters.incoming, group=-1)
async def block_interceptor(client, message):
    """拦截被屏蔽用户，阻止其触发任何自动回复"""
    raise pyrogram.StopPropagation


# ====================== 附加指令：订阅链接深度解析引擎 (.dyjx) ======================
# .dyjx <订阅链接>         — 解析订阅，输出节点列表摘要
# .dyjx <链接> -v          — 详细模式，输出每个节点完整参数
# .dyjx <链接> -s          — 静默模式，只输出节点数量统计
#
# 支持格式：
#   - Clash / Clash Meta YAML (proxies 字段)
#   - V2Ray/Xray Base64 订阅 (vmess://, vless://, trojan://, ss://, ssr://)
#   - ShadowsocksR Base64 订阅
#   - 纯文本逐行节点链接
#   - SSD 格式
# ============================================================================

@app.on_message((filters.me | authorized_filter) & filters.command("dyjx", prefixes="."))
async def sub_parser(client, message):
    """解析机场订阅链接，支持 Clash/V2Ray/SS/SSR/VLESS/Trojan 等格式"""
    import base64
    import json as _json
    import urllib.request
    import urllib.error

    import re as _re_url
    args = message.text.split()
    verbose = "-v" in args
    silent = "-s" in args
    url = ""

    # 优先从指令参数取 URL
    for a in args[1:]:
        if a.startswith("http://") or a.startswith("https://"):
            url = a
            break

    # 其次从回复消息中提取第一个 URL
    if not url and message.reply_to_message:
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        m = _re_url.search(r"https?://\S+", reply_text)
        if m:
            url = m.group(0).rstrip(")")  # 去掉可能粘连的括号

    if not url:
        return await smart_reply(message,
            "**📡 订阅解析引擎**\n\n"
            "用法: `.dyjx <订阅链接> [-v|-s]`\n"
            "或直接**回复**含链接的消息使用 `.dyjx`\n\n"
            "**参数:**\n"
            "`-v` 详细模式 (输出每节点完整参数)\n"
            "`-s` 静默模式 (仅统计)\n\n"
            "**支持格式:**\n"
            "Clash YAML / V2Ray Base64 / SS / SSR / VLESS / Trojan / 混合文本"
        )

    reply = await smart_reply(message, f"⏳ `[SubParser] 正在拉取订阅源...`\n`{url[:60]}{'...' if len(url) > 60 else ''}`")

    # ── 拉取订阅内容 ──────────────────────────────────────────────────────────
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ClashforWindows/0.20.39",
                "Accept": "*/*",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_bytes = resp.read()
            sub_info_header = resp.headers.get("subscription-userinfo", "")
    except urllib.error.URLError as e:
        return await reply.edit_text(f"❌ **拉取失败**：`{str(e)}`")
    except Exception as e:
        return await reply.edit_text(f"❌ **网络异常**：`{str(e)}`")

    await reply.edit_text("⏳ `[SubParser] 订阅拉取完成，正在识别格式...`")

    # ── 解析流量信息头 ────────────────────────────────────────────────────────
    def _parse_sub_info(header: str) -> str:
        if not header:
            return ""
        info = {}
        for seg in header.split(";"):
            seg = seg.strip()
            if "=" in seg:
                k, v = seg.split("=", 1)
                info[k.strip()] = v.strip()
        try:
            used = (int(info.get("upload", 0)) + int(info.get("download", 0))) / 1024**3
            total = int(info.get("total", 0)) / 1024**3
            expire_ts = int(info.get("expire", 0))
            expire_str = ""
            if expire_ts:
                from datetime import datetime
                expire_str = f" | 到期: {datetime.fromtimestamp(expire_ts).strftime('%Y-%m-%d')}"
            if total > 0:
                return f"📊 流量: `{used:.2f}G / {total:.2f}G`{expire_str}\n"
        except Exception:
            pass
        return ""

    sub_info_str = _parse_sub_info(sub_info_header)

    # ── 格式检测与解析 ────────────────────────────────────────────────────────
    nodes = []
    fmt_name = "未知"

    def _b64decode_safe(data: bytes) -> bytes | None:
        """容错 Base64 解码"""
        try:
            # 补齐 padding
            padded = data + b"=" * (4 - len(data) % 4)
            return base64.b64decode(padded)
        except Exception:
            try:
                return base64.urlsafe_b64decode(padded)
            except Exception:
                return None

    def _parse_vmess(link: str) -> dict | None:
        try:
            b64 = link[8:]
            decoded = _b64decode_safe(b64.encode())
            if not decoded:
                return None
            obj = _json.loads(decoded.decode("utf-8"))
            return {
                "type": "VMess",
                "name": obj.get("ps", obj.get("add", "未命名")),
                "server": obj.get("add", ""),
                "port": obj.get("port", ""),
                "net": obj.get("net", "tcp"),
                "tls": "tls" if obj.get("tls") == "tls" else "",
                "uuid": obj.get("id", "")[:8] + "...",
            }
        except Exception:
            return None

    def _parse_vless(link: str) -> dict | None:
        try:
            # vless://uuid@server:port?params#name
            body = link[8:]
            name = ""
            if "#" in body:
                body, name = body.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            import urllib.parse
            if "@" in body:
                uuid_part, rest = body.split("@", 1)
            else:
                return None
            if "?" in rest:
                addr_port, params_str = rest.split("?", 1)
                params = dict(urllib.parse.parse_qsl(params_str))
            else:
                addr_port = rest
                params = {}
            if ":" in addr_port:
                server, port = addr_port.rsplit(":", 1)
            else:
                server, port = addr_port, ""
            return {
                "type": "VLESS",
                "name": name or server,
                "server": server,
                "port": port,
                "net": params.get("type", "tcp"),
                "tls": params.get("security", ""),
                "sni": params.get("sni", params.get("peer", "")),
            }
        except Exception:
            return None

    def _parse_trojan(link: str) -> dict | None:
        try:
            import urllib.parse
            body = link[9:]
            name = ""
            if "#" in body:
                body, name = body.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            if "@" in body:
                pwd_part, rest = body.split("@", 1)
            else:
                return None
            if "?" in rest:
                addr_port, params_str = rest.split("?", 1)
                params = dict(urllib.parse.parse_qsl(params_str))
            else:
                addr_port = rest
                params = {}
            if ":" in addr_port:
                server, port = addr_port.rsplit(":", 1)
            else:
                server, port = addr_port, ""
            return {
                "type": "Trojan",
                "name": name or server,
                "server": server,
                "port": port,
                "sni": params.get("sni", params.get("peer", "")),
                "tls": "tls",
            }
        except Exception:
            return None

    def _parse_ss(link: str) -> dict | None:
        try:
            import urllib.parse
            body = link[5:]
            name = ""
            if "#" in body:
                body, name = body.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            if "@" in body:
                method_pwd_b64, server_part = body.rsplit("@", 1)
                decoded = _b64decode_safe(method_pwd_b64.encode())
                if decoded:
                    method_pwd = decoded.decode("utf-8")
                else:
                    method_pwd = method_pwd_b64
                if ":" in method_pwd:
                    method, _ = method_pwd.split(":", 1)
                else:
                    method = method_pwd
            else:
                decoded = _b64decode_safe(body.encode())
                if not decoded:
                    return None
                plain = decoded.decode("utf-8")
                if "@" in plain:
                    method_pwd, server_part = plain.rsplit("@", 1)
                    method = method_pwd.split(":")[0] if ":" in method_pwd else method_pwd
                else:
                    return None
            if ":" in server_part:
                server, port = server_part.rsplit(":", 1)
                port = port.split("/")[0]
            else:
                server, port = server_part, ""
            return {
                "type": "SS",
                "name": name or server,
                "server": server,
                "port": port,
                "method": method,
            }
        except Exception:
            return None

    def _parse_ssr(link: str) -> dict | None:
        try:
            body = link[6:]
            decoded = _b64decode_safe(body.encode())
            if not decoded:
                return None
            plain = decoded.decode("utf-8")
            main, _, params_str = plain.partition("/?")
            parts = main.split(":")
            if len(parts) < 6:
                return None
            server, port, protocol, method, obfs, pwd_b64 = parts[:6]
            params = {}
            for seg in params_str.split("&"):
                if "=" in seg:
                    k, v = seg.split("=", 1)
                    params[k] = v
            name_b64 = params.get("remarks", "")
            name = ""
            if name_b64:
                nd = _b64decode_safe(name_b64.encode())
                if nd:
                    name = nd.decode("utf-8")
            return {
                "type": "SSR",
                "name": name or server,
                "server": server,
                "port": port,
                "method": method,
                "protocol": protocol,
                "obfs": obfs,
            }
        except Exception:
            return None

    def _parse_hysteria2(link: str) -> dict | None:
        try:
            import urllib.parse
            # hy2://pwd@server:port?params#name
            body = link.split("://", 1)[1]
            name = ""
            if "#" in body:
                body, name = body.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            if "@" in body:
                _, rest = body.split("@", 1)
            else:
                rest = body
            if "?" in rest:
                addr_port, params_str = rest.split("?", 1)
                params = dict(urllib.parse.parse_qsl(params_str))
            else:
                addr_port = rest
                params = {}
            if ":" in addr_port:
                server, port = addr_port.rsplit(":", 1)
            else:
                server, port = addr_port, ""
            return {
                "type": "Hysteria2",
                "name": name or server,
                "server": server,
                "port": port,
                "sni": params.get("sni", ""),
            }
        except Exception:
            return None

    def _parse_link(link: str) -> dict | None:
        link = link.strip()
        if link.startswith("vmess://"):
            return _parse_vmess(link)
        elif link.startswith("vless://"):
            return _parse_vless(link)
        elif link.startswith("trojan://"):
            return _parse_trojan(link)
        elif link.startswith("ss://"):
            return _parse_ss(link)
        elif link.startswith("ssr://"):
            return _parse_ssr(link)
        elif link.startswith("hysteria2://") or link.startswith("hy2://"):
            return _parse_hysteria2(link)
        return None

    # ── 尝试 Clash YAML ───────────────────────────────────────────────────────
    raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
    if "proxies:" in raw_text or raw_text.startswith("port:") or raw_text.startswith("mixed-port:"):
        fmt_name = "Clash YAML"
        try:
            import yaml  # 可选依赖
            data = yaml.safe_load(raw_text)
            proxies = data.get("proxies", []) or []
            for p in proxies:
                node = {
                    "type": str(p.get("type", "")).upper(),
                    "name": p.get("name", ""),
                    "server": p.get("server", ""),
                    "port": p.get("port", ""),
                }
                if p.get("network"): node["net"] = p["network"]
                if p.get("tls"): node["tls"] = "tls"
                if p.get("cipher") or p.get("method"): node["method"] = p.get("cipher") or p.get("method")
                nodes.append(node)
        except ImportError:
            # pyyaml 未安装，逐行状态机解析
            fmt_name = "Clash YAML (轻量解析)"
            import re as _re2

            def _clash_line_parse(text):
                _nodes = []
                in_proxies = False
                cur = {}

                def _flush(c):
                    if c.get("name") and c.get("server"):
                        _nodes.append({
                            "type": str(c.get("type", "?")).upper(),
                            "name": c.get("name", ""),
                            "server": c.get("server", ""),
                            "port": str(c.get("port", "")),
                            "method": c.get("cipher") or c.get("method") or "",
                            "net": c.get("network", ""),
                            "tls": "tls" if c.get("tls") else "",
                        })

                for raw_line in text.splitlines():
                    line = raw_line.rstrip()
                    # 顶层 key（字母开头）
                    if _re2.match(r"^[a-zA-Z]", line):
                        key = line.split(":")[0].strip()
                        if key == "proxies":
                            in_proxies = True
                        else:
                            if in_proxies:
                                _flush(cur)
                                cur = {}
                            in_proxies = False
                        continue

                    if not in_proxies:
                        continue

                    # 内联格式: - {name: x, server: y, ...}
                    m_inline = _re2.match(r"^- \{(.+)\}", line)
                    if m_inline:
                        _flush(cur)
                        cur = {}
                        for kv in _re2.findall(r"(\w+):\s*[\"\']?([^,}\"\'^]+?)[\"\']?(?=,|\})", m_inline.group(1)):
                            cur[kv[0].strip()] = kv[1].strip()
                        continue

                    # 新节点: - name: xxx
                    m_new = _re2.match(r"^- name:\s*[\"\']?(.+?)[\"\']?\s*$", line)
                    if m_new:
                        _flush(cur)
                        cur = {"name": m_new.group(1).strip()}
                        continue

                    # 新节点: - （其他字段开头）
                    if _re2.match(r"^- ", line):
                        _flush(cur)
                        cur = {}
                        rest = line[2:]
                        m_kv = _re2.match(r"(\w+):\s*[\"\']?(.+?)[\"\']?\s*$", rest)
                        if m_kv:
                            cur[m_kv.group(1)] = m_kv.group(2).strip()
                        continue

                    # 普通字段行
                    m_field = _re2.match(r"^\s+(\w+):\s*[\"\']?(.+?)[\"\']?\s*$", line)
                    if m_field and cur is not None:
                        cur[m_field.group(1)] = m_field.group(2).strip()

                _flush(cur)
                return _nodes

            nodes = _clash_line_parse(raw_text)
        except Exception as e:
            return await reply.edit_text(f"❌ **Clash YAML 解析失败**：`{str(e)}`")

    # ── 尝试 Base64 订阅 ──────────────────────────────────────────────────────
    if not nodes:
        decoded = _b64decode_safe(raw_bytes)
        if decoded:
            lines_decoded = decoded.decode("utf-8", errors="ignore").splitlines()
        else:
            lines_decoded = raw_text.splitlines()

        proto_prefixes = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hysteria2://", "hy2://")
        link_lines = [l.strip() for l in lines_decoded if any(l.strip().startswith(p) for p in proto_prefixes)]

        if link_lines:
            fmt_name = "Base64/文本混合节点链接"
            for link in link_lines:
                node = _parse_link(link)
                if node:
                    nodes.append(node)

    # ── 尝试 SSD 格式 ─────────────────────────────────────────────────────────
    if not nodes and raw_text.startswith("ssd://"):
        fmt_name = "SSD"
        try:
            decoded = _b64decode_safe(raw_text[6:].encode())
            if decoded:
                obj = _json.loads(decoded.decode("utf-8"))
                airport = obj.get("airport", "")
                for s in obj.get("servers", []):
                    nodes.append({
                        "type": "SS",
                        "name": s.get("remarks", s.get("server", "")),
                        "server": s.get("server", ""),
                        "port": s.get("port", obj.get("port", "")),
                        "method": s.get("encryption", obj.get("encryption", "")),
                    })
        except Exception as e:
            return await reply.edit_text(f"❌ **SSD 解析失败**：`{str(e)}`")

    # ── 输出结果 ──────────────────────────────────────────────────────────────
    if not nodes:
        return await reply.edit_text(
            f"❌ **无法解析该订阅**\n\n"
            f"原始内容前 200 字符:\n`{raw_text[:200]}`"
        )

    total = len(nodes)

    # 按协议类型统计
    type_counter = {}
    for n in nodes:
        t = n.get("type", "?")
        type_counter[t] = type_counter.get(t, 0) + 1
    type_summary = "  ".join([f"`{k}×{v}`" for k, v in sorted(type_counter.items())])

    header = (
        f"**📡 订阅解析完成**\n"
        f"格式：`{fmt_name}`\n"
        f"{sub_info_str}"
        f"节点总数：**{total}**\n"
        f"协议分布：{type_summary}\n"
        f"┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
    )

    if silent:
        return await reply.edit_text(header.rstrip("┈\n"))

    # 节点列表
    lines = []
    for i, n in enumerate(nodes):
        ntype = n.get("type", "?")
        nname = n.get("name", "")[:30]
        nserver = n.get("server", "")
        nport = n.get("port", "")

        if verbose:
            extras = []
            for k in ("net", "tls", "sni", "method", "protocol", "obfs", "uuid"):
                if n.get(k):
                    extras.append(f"{k}={n[k]}")
            extra_str = f" | {', '.join(extras)}" if extras else ""
            lines.append(f"`[{i+1:03d}]` **{ntype}** `{nname}` → `{nserver}:{nport}`{extra_str}")
        else:
            lines.append(f"`[{i+1:03d}]` **{ntype}** `{nname}` `{nserver}:{nport}`")

    # 节点列表塞进可折叠引用块，超出 3800 字符则分段，每段独立折叠
    MAX_BLOCK = 3800
    segments = []
    cur_seg = ""
    for line in lines:
        if len(cur_seg) + len(line) + 1 > MAX_BLOCK:
            segments.append(cur_seg)
            cur_seg = ""
        cur_seg += line + "\n"
    if cur_seg:
        segments.append(cur_seg)

    total_segs = len(segments)
    # 第一段：header + 折叠节点列表
    seg_label = f" (1/{total_segs})" if total_segs > 1 else ""
    first_body = (
        header +
        f"<blockquote expandable>\n{segments[0]}</blockquote>"
    ) if segments else header

    await reply.edit_text(first_body, disable_web_page_preview=True)

    # 后续段：单独折叠块回复
    for idx, seg in enumerate(segments[1:], start=2):
        seg_header = f"**📡 节点列表 ({idx}/{total_segs})**\n"
        await message.reply(
            seg_header + f"<blockquote expandable>\n{seg}</blockquote>",
            disable_web_page_preview=True
        )


# ====================== 附加指令：用户信息查询 (.id) ======================
# .id                   — 查询自己
# .id @username/UID     — 查询指定用户
# .id                   — 回复消息查询被回复人

@app.on_message((filters.me | authorized_filter) & filters.command("id", prefixes="."))
async def user_info(client, message):
    """查询用户详细信息 (ID/DC/会员/用户名/注册时间等)"""
    reply = None
    try:
        reply = await smart_reply(message, "`[ID] 正在抓取用户信息...`")
        target = None
        target_chat = None
        args = message.text.split()

        # 优先级：回复 > 参数 > 自己
        if message.reply_to_message:
            if message.reply_to_message.from_user:
                # 普通用户身份发送
                target = message.reply_to_message.from_user
            elif message.reply_to_message.sender_chat:
                # 以群组/频道身份发送
                target_chat = message.reply_to_message.sender_chat
        
        if not target and not target_chat:
            if len(args) >= 2:
                arg = args[1].lstrip("@")
                try:
                    target = await client.get_users(int(arg))
                except ValueError:
                    try:
                        target = await client.get_users(arg)
                    except Exception:
                        target_chat = await client.get_chat(arg)
            else:
                target = await client.get_me()

        # ── 群组/频道模式 ─────────────────────────────────────────────────────
        if target_chat and not target:
            tc = target_chat
            cid       = tc.id
            title     = tc.title or "未知"
            username  = f"@{tc.username}" if tc.username else "无"
            ctype     = str(tc.type).replace("ChatType.", "")
            dc_id     = getattr(tc, "dc_id", None)
            dc_str    = f"DC{dc_id}" if dc_id else "未知"
            members   = getattr(tc, "members_count", None)
            members_str = f"{members:,}" if members else "未知"
            desc      = (getattr(tc, "description", "") or "")[:200]
            is_verify = "✅ 是" if getattr(tc, "is_verified", False) else "否"
            is_scam   = "⚠️ 是" if getattr(tc, "is_scam", False) else "否"
            is_fake   = "⚠️ 是" if getattr(tc, "is_fake", False) else "否"

            if tc.username:
                name_link = f"<a href='https://t.me/{tc.username}'>{title}</a>"
            else:
                name_link = f"<a href='tg://resolve?domain={cid}'>{title}</a>"

            text = (
                f"🏠 <b>群组/频道信息</b>\n"
                f"<b>群组名称：</b>{name_link}\n"
                f"<b>用户账号：</b><code>{username}</code>\n"
                f"<b>群组编号：</b><code>{cid}</code>\n"
                f"<b>群组类型：</b><code>{ctype}</code>\n"
                f"<b>数据中心：</b><code>{dc_str}</code>\n"
                f"<b>成员数量：</b><code>{members_str}</code>\n"
                f"<b>官方认证：</b>{is_verify}\n"
                f"<b>诈骗标记：</b>{is_scam}\n"
                f"<b>虚假账号：</b>{is_fake}\n"
            )
            if desc:
                text += f"<b>群组简介：</b>\n{desc}\n"

            return await reply.edit_text(text, disable_web_page_preview=True)

        if not target:
            return await reply.edit_text("❌ 无法获取用户信息")

        # ── 用户模式 ──────────────────────────────────────────────────────────
        uid       = target.id
        first     = target.first_name or ""
        last      = target.last_name or ""
        full_name = (first + " " + last).strip()
        username  = f"@{target.username}" if target.username else "无"
        phone     = target.phone_number or "不可见"
        is_bot    = "🤖 是" if target.is_bot else "否"
        is_prem   = "💎 是" if getattr(target, "is_premium", False) else "否"
        is_verify = "✅ 是" if getattr(target, "is_verified", False) else "否"
        is_scam   = "⚠️ 是" if getattr(target, "is_scam", False) else "否"
        is_fake   = "⚠️ 是" if getattr(target, "is_fake", False) else "否"
        is_restrict = "🚫 是" if getattr(target, "is_restricted", False) else "否"
        lang      = getattr(target, "language_code", None) or "未知"
        dc_id     = getattr(target, "dc_id", None)
        dc_str    = f"DC{dc_id}" if dc_id else "未知"

        try:
            photos = await client.get_profile_photos(uid, limit=1)
            photo_count = photos.total_count if photos else 0
        except Exception:
            photo_count = "不可见"

        bio = ""
        try:
            full_chat = await client.get_chat(uid)
            bio = getattr(full_chat, "bio", "") or ""
        except Exception:
            pass

        if target.username:
            name_link = f"<a href='https://t.me/{target.username}'>{full_name}</a>"
        else:
            name_link = f"<a href='tg://user?id={uid}'>{full_name}</a>"

        # 全部标签统一4个汉字，冒号对齐值，无需空格填充
        text = (
            f"👤 <b>用户信息</b>\n"
            f"<b>用户名称：</b>{name_link}\n"
            f"<b>用户账号：</b><code>{username}</code>\n"
            f"<b>用户编号：</b><code>{uid}</code>\n"
            f"<b>数据中心：</b><code>{dc_str}</code>\n"
            f"<b>语言设置：</b><code>{lang}</code>\n"
            f"<b>头像数量：</b><code>{photo_count}</code>\n"
            f"<b>会员状态：</b>{is_prem}\n"
            f"<b>机器人号：</b>{is_bot}\n"
            f"<b>官方认证：</b>{is_verify}\n"
            f"<b>诈骗标记：</b>{is_scam}\n"
            f"<b>虚假账号：</b>{is_fake}\n"
            f"<b>受限账号：</b>{is_restrict}\n"
        )

        if bio:
            text += f"<b>个人简介：</b>\n{bio[:200]}\n"



        await reply.edit_text(text, disable_web_page_preview=True)

    except Exception as e:
        err_msg = f"❌ 查询失败: `{str(e)[:150]}`"
        if reply:
            try:
                await reply.edit_text(err_msg)
            except Exception:
                await smart_reply(message, err_msg)
        else:
            await smart_reply(message, err_msg)


if __name__ == "__main__":
    print("Userbot started! 官方 DOM 生成器与多源探针准备就绪")
    app.run()     
