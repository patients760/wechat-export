#!/usr/bin/env python3
"""
Export ALL WeChat chats (private + group) as HTML.
Reads from decrypted message_0/1/2.db, contact.db, and session.db.

Prerequisite: Run decrypt_all.py first, then:
  1. Find your wxid: check contact.db → your own row (remark/nick matches your name)
  2. Fill in USER_WXID and USER_NAME below
  3. Run this script
"""
import sqlite3, os, hashlib, datetime, html, re

# === CONFIGURATION ============================================================
DECRYPTED_DIR = os.path.expanduser("~/Desktop/Claude/wechat_decrypted")
OUTPUT_HTML = os.path.expanduser("~/Desktop/Claude/wechat_all_chats.html")
USER_WXID = "YOUR_WXID_HERE"   # Your own wxid (find in contact.db)
USER_NAME = "YOUR_NAME_HERE"   # Your display name
# ==============================================================================

def load_contacts():
    """Return {wxid: {remark, nick_name, alias, display}}"""
    contacts = {}
    db = os.path.join(DECRYPTED_DIR, "contact", "contact.db")
    if os.path.exists(db):
        con = sqlite3.connect(db)
        for row in con.execute(
            "SELECT username, alias, remark, nick_name FROM contact"
        ):
            wxid, alias, remark, nick = row
            display = remark or nick or alias or wxid
            contacts[wxid] = {
                "alias": alias or "",
                "remark": remark or "",
                "nick_name": nick or "",
                "display": display,
            }
        con.close()
    return contacts

def get_display_name(wxid, contacts):
    if wxid == USER_WXID:
        return USER_NAME
    if wxid == "system":
        return "[系统消息]"
    if wxid == "unknown":
        return "[未知]"
    c = contacts.get(wxid)
    if c:
        return c["remark"] or c["nick_name"] or c["alias"] or wxid
    return wxid

def ts_to_str(ts):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)

def decode_content(content):
    """Convert bytes/memoryview to str."""
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

def load_sessions():
    """Return [{username, type, summary, last_timestamp, last_msg_sender, last_sender_display_name}]"""
    sessions = []
    db = os.path.join(DECRYPTED_DIR, "session", "session.db")
    if os.path.exists(db):
        con = sqlite3.connect(db)
        for row in con.execute(
            "SELECT username, type, summary, last_timestamp, last_msg_sender, last_sender_display_name, last_msg_locald_id FROM SessionTable ORDER BY sort_timestamp DESC"
        ):
            sessions.append({
                "username": row[0],
                "type": row[1],
                "summary": row[2] or "",
                "last_timestamp": row[3] or 0,
                "last_msg_sender": row[4] or "",
                "last_sender_display": row[5] or "",
                "last_msg_local_id": row[6] or 0,
            })
        con.close()
    return sessions

def parse_group_content(content_str):
    """Parse group message content. Returns (sender_wxid, text).
    Group messages have format: wxid_xxx:\\ntext"""
    if not content_str:
        return "unknown", ""
    # Check for wxid_ prefix
    match = re.match(r'(wxid_[a-z0-9]+):\n?(.*)', content_str, re.DOTALL)
    if match:
        return match.group(1), match.group(2)
    # Check for other prefix formats (old wxid, qq, etc.)
    match = re.match(r'([a-zA-Z][a-zA-Z0-9_]{5,40}):\n?(.*)', content_str, re.DOTALL)
    if match:
        return match.group(1), match.group(2)
    return "unknown", content_str

def find_user_internal_ids(con, user_wxid):
    """Find the real_sender_id values that correspond to the user in this database.
    Look at the self-chat where all messages are from the user."""
    tbl = f'Msg_{hashlib.md5(user_wxid.encode()).hexdigest()}'
    exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
    ).fetchone()
    if not exists:
        return set()
    rows = con.execute(f'SELECT DISTINCT real_sender_id FROM "{tbl}"').fetchall()
    return {r[0] for r in rows}


def infer_user_ids_for_db(target_db_name, db_conns, db_tables, known_db, known_user_ids):
    """Infer user's internal IDs in target_db by comparing sender_ids
    from chats that exist in both target_db and a known_db.
    Other people's IDs are consistent across DBs; the user's ID differs."""
    target_con = db_conns.get(target_db_name)
    known_con = db_conns.get(known_db)
    if not target_con or not known_con:
        return set()

    target_tables = db_tables.get(target_db_name, set())
    known_tables = db_tables.get(known_db, set())
    common_tables = target_tables & known_tables

    candidate_user_ids = set()
    for tbl in common_tables:
        target_sids = {r[0] for r in target_con.execute(f'SELECT DISTINCT real_sender_id FROM "{tbl}"').fetchall()}
        known_sids = {r[0] for r in known_con.execute(f'SELECT DISTINCT real_sender_id FROM "{tbl}"').fetchall()}

        if len(target_sids) < 2 or len(known_sids) < 2:
            continue

        # Known user IDs in the known DB for this chat
        known_user_in_chat = known_sids & known_user_ids
        known_other_in_chat = known_sids - known_user_ids

        # The other person's ID is consistent across DBs
        for other_id in known_other_in_chat:
            if other_id in target_sids:
                # The remaining IDs in target are the user
                for sid in target_sids:
                    if sid != other_id:
                        candidate_user_ids.add(sid)

    return candidate_user_ids

TYPE_NAMES = {
    1: "text", 3: "image", 34: "voice", 43: "video",
    47: "emoji", 48: "location", 49: "appmsg",
    10000: "system", 10002: "system",
}

def extract_all_messages():
    contacts = load_contacts()
    print(f"[*] Loaded {len(contacts)} contacts")

    sessions = load_sessions()
    print(f"[*] Loaded {len(sessions)} sessions")

    # Build username -> Msg_ table mapping
    username_to_tbl = {}
    for s in sessions:
        uname = s["username"]
        username_to_tbl[uname] = f'Msg_{hashlib.md5(uname.encode()).hexdigest()}'

    # Open all message databases and index their tables
    db_conns = {}
    db_tables = {}
    db_user_ids = {}
    for db_name in ["message_0", "message_1", "message_2"]:
        db_path = os.path.join(DECRYPTED_DIR, "message", f"{db_name}.db")
        if os.path.exists(db_path):
            con = sqlite3.connect(db_path)
            db_conns[db_name] = con
            db_tables[db_name] = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            }
            db_user_ids[db_name] = find_user_internal_ids(con, USER_WXID)
            print(f"[*] {db_name}: user internal ids = {db_user_ids[db_name]}, {len(db_tables[db_name])} tables")

    # Infer user IDs for DBs without self-chat data
    for db_name in ["message_0", "message_1", "message_2"]:
        if db_user_ids.get(db_name):
            continue
        # Try to infer from another DB that has known user IDs
        for known_db in ["message_0", "message_1", "message_2"]:
            if db_user_ids.get(known_db):
                inferred = infer_user_ids_for_db(db_name, db_conns, db_tables, known_db, db_user_ids[known_db])
                if inferred:
                    db_user_ids[db_name] = inferred
                    print(f"[*] {db_name}: inferred user ids = {inferred} (from {known_db})")
                    break

    all_chats = []

    for session in sessions:
        uname = session["username"]
        tbl = username_to_tbl[uname]

        is_group = uname.endswith("@chatroom") or uname.endswith("@im.chatroom")

        # Collect messages from ALL databases that have this table
        # Track source DB per row since real_sender_id mapping differs per DB
        all_rows = []
        source_dbs = []
        for db_name, con in db_conns.items():
            if tbl in db_tables[db_name]:
                try:
                    rows = con.execute(
                        f'SELECT local_id, create_time, local_type, real_sender_id, message_content FROM "{tbl}" ORDER BY create_time ASC'
                    ).fetchall()
                    for r in rows:
                        all_rows.append((db_name,) + r)
                    if rows:
                        source_dbs.append(db_name)
                except Exception as e:
                    print(f"  Skip {tbl} in {db_name}: {e}")

        if not all_rows:
            continue

        # Sort combined rows by time
        all_rows.sort(key=lambda r: r[2])  # create_time

        # For group chats: build sender_id -> wxid mapping from text messages first
        sender_id_to_wxid = {}
        if is_group:
            for row in all_rows:
                db_name, local_id, create_time, local_type, real_sender_id, content = row
                content_str = decode_content(content)
                if not content_str:
                    continue
                sender_wxid, text = parse_group_content(content_str)
                if sender_wxid != "unknown" and sender_wxid != "system":
                    sender_id_to_wxid[real_sender_id] = sender_wxid

        messages = []
        for row in all_rows:
            db_name, local_id, create_time, local_type, real_sender_id, content = row
            user_ids = db_user_ids.get(db_name, set())
            content_str = decode_content(content)

            if is_group:
                if local_type in (10000, 10002):
                    sender_wxid = "system"
                    text = content_str if content_str else "[系统消息]"
                else:
                    sender_wxid, text = parse_group_content(content_str)
                    if sender_wxid == "unknown" and real_sender_id in sender_id_to_wxid:
                        sender_wxid = sender_id_to_wxid[real_sender_id]
                    if sender_wxid == "unknown" and content_str:
                        sender_wxid = "unknown"
            else:
                if not content_str:
                    continue
                text = content_str
                if real_sender_id in user_ids:
                    sender_wxid = USER_WXID
                else:
                    sender_wxid = uname

            if not content_str and not is_group:
                continue
            if is_group and not content_str:
                text = "[非文本消息]"

            messages.append({
                "id": local_id,
                "time": create_time,
                "time_str": ts_to_str(create_time),
                "sender": sender_wxid,
                "sender_display": get_display_name(sender_wxid, contacts),
                "content": text,
                "type": local_type,
                "type_name": TYPE_NAMES.get(local_type, f"type_{local_type}"),
            })

        if not messages:
            continue

        if is_group:
            # Try to get group name from contacts
            cr_name = get_display_name(uname, contacts)
            if cr_name and cr_name != uname:
                title = f"群聊: {cr_name}"
            else:
                title = f"群聊: {uname}"
        else:
            title = get_display_name(uname, contacts)

        all_chats.append({
            "title": title,
            "db": "+".join(source_dbs),
            "table": tbl,
            "is_group": is_group,
            "message_count": len(messages),
            "messages": messages,
        })

    for con in db_conns.values():
        con.close()

    # Sort by most recent message
    for chat in all_chats:
        chat["last_time"] = chat["messages"][-1]["time"] if chat["messages"] else 0
    all_chats.sort(key=lambda c: c["last_time"], reverse=True)

    return all_chats, contacts


def generate_html(all_chats, contacts):
    total_msgs = sum(c["message_count"] for c in all_chats)
    private = [c for c in all_chats if not c["is_group"]]
    groups = [c for c in all_chats if c["is_group"]]

    html_parts = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>微信聊天记录导出 - {USER_NAME}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f0f0f0; color: #333; }}
.header {{ background: #07c160; color: white; padding: 20px 30px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }}
.header h1 {{ font-size: 20px; font-weight: 600; }}
.header p {{ font-size: 13px; opacity: 0.9; margin-top: 4px; }}
.toc {{ background: white; margin: 20px; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.toc h2 {{ font-size: 16px; margin-bottom: 12px; color: #07c160; }}
.toc a {{ color: #333; text-decoration: none; display: block; padding: 6px 0; font-size: 14px; border-bottom: 1px solid #f5f5f5; }}
.toc a:hover {{ color: #07c160; }}
.toc .badge {{ background: #07c160; color: white; border-radius: 10px; padding: 1px 8px; font-size: 11px; margin-left: 6px; }}
.toc .badge.group {{ background: #1989fa; }}
.chat-section {{ background: white; margin: 20px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.chat-header {{ background: #ededed; padding: 12px 20px; border-bottom: 1px solid #e0e0e0; display: flex; justify-content: space-between; align-items: center; }}
.chat-header h3 {{ font-size: 16px; font-weight: 600; }}
.chat-header .meta {{ font-size: 12px; color: #888; }}
.msg-list {{ padding: 10px 0; }}
.msg {{ padding: 6px 20px; border-bottom: 1px solid #f9f9f9; display: flex; }}
.msg:hover {{ background: #fafafa; }}
.msg .sender {{ font-weight: 600; color: #07c160; min-width: 100px; font-size: 13px; }}
.msg.group-sender {{ color: #1989fa; }}
.msg .time {{ color: #aaa; font-size: 11px; margin-left: 10px; min-width: 130px; }}
.msg .text {{ flex: 1; font-size: 14px; word-break: break-all; }}
.msg.system {{ color: #999; font-size: 12px; }}
.msg.image .text {{ color: #1989fa; font-style: italic; }}
.msg.voice .text {{ color: #ff976a; font-style: italic; }}
.footer {{ text-align: center; padding: 30px; color: #999; font-size: 12px; }}
.empty-text {{ color: #ccc; font-style: italic; }}
</style>
</head>
<body>
<div class="header">
<h1>微信聊天记录</h1>
<p>{USER_NAME} · {len(private)} 个私聊 · {len(groups)} 个群聊 · {total_msgs} 条消息</p>
<p>导出时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
</div>

<div class="toc">
<h2>目录</h2>
<h3 style="margin-top:12px;font-size:14px;color:#888;">私聊 ({len(private)})</h3>
"""]

    for i, chat in enumerate(private):
        anchor = f"chat_{i}"
        html_parts.append(
            f'<a href="#{anchor}">{html.escape(chat["title"])} <span class="badge">{chat["message_count"]}条</span></a>'
        )

    html_parts.append(
        f'<h3 style="margin-top:12px;font-size:14px;color:#888;">群聊 ({len(groups)})</h3>'
    )
    offset = len(private)
    for i, chat in enumerate(groups):
        anchor = f"chat_{offset + i}"
        html_parts.append(
            f'<a href="#{anchor}">{html.escape(chat["title"])} <span class="badge group">{chat["message_count"]}条</span></a>'
        )

    html_parts.append("</div>")

    for i, chat in enumerate(all_chats):
        anchor = f"chat_{i}"
        is_group = chat["is_group"]
        html_parts.append(f"""
<div class="chat-section" id="{anchor}">
<div class="chat-header">
<h3>{html.escape(chat["title"])}</h3>
<span class="meta">{chat["message_count"]} 条消息 · {chat["db"]} · {chat["table"]}</span>
</div>
<div class="msg-list">""")

        for msg in chat["messages"]:
            msg_class = ""
            type_name = msg["type_name"]
            if type_name in ("image", "video"):
                msg_class = " image"
            elif type_name == "voice":
                msg_class = " voice"
            elif type_name == "system":
                msg_class = " system"

            sender_class = " group-sender" if is_group else ""
            sender_display = html.escape(msg["sender_display"])
            raw = msg["content"]
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8", errors="replace")
                except:
                    raw = raw.hex()
            elif not isinstance(raw, str):
                raw = str(raw)
            content_html = html.escape(raw) if raw else '<span class="empty-text">[非文本消息]</span>'

            html_parts.append(f"""
<div class="msg{msg_class}">
<span class="sender{sender_class}">{sender_display}</span>
<span class="time">{msg["time_str"]}</span>
<span class="text">{content_html}</span>
</div>""")

        html_parts.append("</div></div>")

    html_parts.append("""
<div class="footer">由 Claude Code 导出 · 仅供个人备份使用</div>
</body></html>""")

    return "\n".join(html_parts)


def main():
    all_chats, contacts = extract_all_messages()
    html_content = generate_html(all_chats, contacts)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n[*] Exported to: {OUTPUT_HTML}")
    total_msgs = sum(c["message_count"] for c in all_chats)
    private = sum(1 for c in all_chats if not c["is_group"])
    groups = sum(1 for c in all_chats if c["is_group"])
    print(f"[*] {len(all_chats)} chats ({private} private + {groups} groups), {total_msgs} messages")

    for c in all_chats[:20]:
        print(f"  {c['title'][:50]} | {c['message_count']} msgs | {'GROUP' if c['is_group'] else 'private'} | {c['db']}")


if __name__ == "__main__":
    main()
