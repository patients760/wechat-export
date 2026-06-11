#!/usr/bin/env python3
"""
Export a single contact's WeChat chat as HTML.
Modify TARGET_NAME / TARGET_REMARK below to specify the contact.
"""
import sqlite3, os, hashlib, datetime, html

DECRYPTED_DIR = os.path.expanduser("~/Desktop/Claude/wechat_decrypted")
OUTPUT_DIR = os.path.expanduser("~/Desktop/Claude")
USER_WXID = "wxid_mds82h5hdwm122"
USER_NAME = "周彬逊"

# ====== 修改这里：要导出的联系人 ======
TARGET_REMARK = "杨思敏"   # 备注名（优先匹配）
TARGET_NICK = ""           # 或按昵称匹配
TARGET_WXID = ""           # 或直接指定 wxid
# ====================================

def decode_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8", errors="replace")
        except:
            return content.hex()
    try:
        return bytes(content).decode("utf-8", errors="replace")
    except:
        return str(content)[:200]

def ts_to_str(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)

def find_target_wxid():
    """Find target contact's wxid from contact.db"""
    contact_db = os.path.join(DECRYPTED_DIR, "contact", "contact.db")
    con = sqlite3.connect(contact_db)
    rows = con.execute(
        "SELECT username, alias, remark, nick_name FROM contact"
    ).fetchall()
    con.close()

    for wxid, alias, remark, nick in rows:
        display = remark or nick or alias or wxid
        if TARGET_WXID and wxid == TARGET_WXID:
            return wxid, display
        if TARGET_REMARK and remark == TARGET_REMARK:
            return wxid, display
        if TARGET_NICK and nick == TARGET_NICK:
            return wxid, display

    # Fuzzy match
    for wxid, alias, remark, nick in rows:
        display = remark or nick or alias or wxid
        if TARGET_REMARK and TARGET_REMARK in (remark or ""):
            return wxid, display
        if TARGET_NICK and TARGET_NICK in (nick or ""):
            return wxid, display
    return None, None

def find_user_ids_by_db():
    """Find user's internal real_sender_id in each database"""
    tbl = f'Msg_{hashlib.md5(USER_WXID.encode()).hexdigest()}'
    user_ids = {}
    for db_name in ["message_0", "message_1", "message_2"]:
        db_path = os.path.join(DECRYPTED_DIR, "message", f"{db_name}.db")
        if os.path.exists(db_path):
            con = sqlite3.connect(db_path)
            exists = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if exists:
                user_ids[db_name] = {r[0] for r in con.execute(f'SELECT DISTINCT real_sender_id FROM "{tbl}"').fetchall()}
            else:
                user_ids[db_name] = set()
            con.close()
    return user_ids

def main():
    target_wxid, target_name = find_target_wxid()
    if not target_wxid:
        print(f"[-] Contact not found: remark={TARGET_REMARK} nick={TARGET_NICK} wxid={TARGET_WXID}")
        return

    print(f"[*] Target: {target_name} ({target_wxid})")

    user_ids_by_db = find_user_ids_by_db()
    print(f"[*] User internal IDs: {user_ids_by_db}")

    target_tbl = f'Msg_{hashlib.md5(target_wxid.encode()).hexdigest()}'
    print(f"[*] Table: {target_tbl}")

    # Collect messages from all databases
    all_rows = []
    for db_name in ["message_0", "message_1", "message_2"]:
        db_path = os.path.join(DECRYPTED_DIR, "message", f"{db_name}.db")
        if not os.path.exists(db_path):
            continue
        con = sqlite3.connect(db_path)
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (target_tbl,)
        ).fetchone()
        if exists:
            rows = con.execute(
                f'SELECT local_id, create_time, local_type, real_sender_id, message_content FROM "{target_tbl}" ORDER BY create_time ASC'
            ).fetchall()
            for r in rows:
                all_rows.append((db_name,) + r)
            print(f"  {db_name}: {len(rows)} messages")
        con.close()

    all_rows.sort(key=lambda r: r[2])

    TYPE_NAMES = {1: "text", 3: "image", 34: "voice", 43: "video",
                  47: "emoji", 48: "location", 49: "appmsg",
                  10000: "system", 10002: "system"}

    messages = []
    for row in all_rows:
        db_name, local_id, create_time, local_type, real_sender_id, content = row
        content_str = decode_content(content)
        user_ids = user_ids_by_db.get(db_name, set())

        if real_sender_id in user_ids:
            sender = USER_NAME
        else:
            sender = target_name

        type_name = TYPE_NAMES.get(local_type, f"type_{local_type}")
        if type_name != "text":
            content_str = f"[{type_name}]" if not content_str else f"[{type_name}] {content_str[:200]}"

        if not content_str and type_name != "system":
            content_str = f"[{type_name}]"

        messages.append({
            "time_str": ts_to_str(create_time),
            "sender": sender,
            "content": content_str,
            "type_name": type_name,
        })

    print(f"[*] Total: {len(messages)} messages")

    # Generate HTML
    html_parts = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(target_name)} - 聊天记录</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #ededed; }}
.header {{ background: #07c160; color: white; padding: 16px; position: sticky; top: 0; z-index: 100; text-align: center; }}
.header h1 {{ font-size: 18px; }}
.header p {{ font-size: 12px; opacity: 0.85; margin-top: 2px; }}
.msg-list {{ max-width: 800px; margin: 0 auto; padding: 10px; }}
.msg {{ padding: 8px 14px; margin: 2px 0; display: flex; align-items: flex-start; }}
.msg .sender {{ font-weight: 600; min-width: 70px; font-size: 13px; color: #07c160; flex-shrink: 0; }}
.msg .sender.other {{ color: #333; }}
.msg .time {{ color: #aaa; font-size: 10px; margin: 0 8px; min-width: 125px; flex-shrink: 0; }}
.msg .text {{ font-size: 14px; word-break: break-all; flex: 1; }}
.msg.system {{ color: #999; font-size: 12px; text-align: center; }}
.msg.image .text, .msg.voice .text, .msg.emoji .text {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
<div class="header">
<h1>{html.escape(target_name)}</h1>
<p>{len(messages)} 条消息</p>
</div>
<div class="msg-list">
"""]

    for msg in messages:
        type_name = msg["type_name"]
        msg_class = f" {type_name}" if type_name in ("image", "voice", "emoji", "video") else ""
        if type_name == "system":
            msg_class = " system"
        sender_class = " other" if msg["sender"] == target_name else ""
        content_html = html.escape(str(msg["content"]))

        html_parts.append(f"""<div class="msg{msg_class}">
<span class="sender{sender_class}">{html.escape(msg["sender"])}</span>
<span class="time">{msg["time_str"]}</span>
<span class="text">{content_html}</span>
</div>""")

    html_parts.append("</div></body></html>")

    output_path = os.path.join(OUTPUT_DIR, f"{target_name}_聊天记录.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    size_kb = os.path.getsize(output_path) / 1024
    print(f"[*] Exported: {output_path} ({size_kb:.0f}KB)")

if __name__ == "__main__":
    main()
