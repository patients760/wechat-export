# Reversing WeChat's SQLCipher Encryption on macOS to Export Chat History as HTML — Full Toolchain Open Source

**TL;DR:** WeChat for macOS stores your chat history in SQLCipher-encrypted SQLite databases with per-file AES-256 keys. I reversed the encryption scheme, wrote Frida hooks to capture keys from CommonCrypto, built a Python toolchain to decrypt and parse the data, and generate standalone HTML exports. [GitHub repo](https://github.com/patients760/wechat-export).

---

## Motivation

I wanted to preserve years of chat history with someone important. WeChat for Mac has zero export functionality. Most existing tools are Windows-only, closed-source, or poorly documented. So I spent a weekend reversing it myself.

## What I found

### 1. Database Layout

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<user_id>/db_storage/
├── contact/contact.db         # Contacts (wxid, remark, nickname)
├── session/session.db         # Chat session list
├── message/message_0.db       # Recent messages
├── message/message_1.db       # Bulk of history (mine was 93MB)
└── message/message_2.db       # Older archives
```

### 2. Encryption: SQLCipher 4, Per-Database Keys

Each `.db` file uses an **independent AES-256 key**. There is no master key. The page format is standard SQLCipher 4:

```
Offset  0        16                              4032      4096
       ├─────────┼─────────────────────────────────┼──────────┤
       │ Salt    │  Encrypted payload (CBC)         │ HMAC     │
       │ 16 B    │  4016 B                          │ 64 B     │
       └─────────┴─────────────────────────────────┴──────────┘
```

Key derivation: `PBKDF2-HMAC-SHA512(salt, iterations=2)` for the page key, then `salt XOR 0x3A` → another PBKDF2 round for the HMAC key.

### 3. Key Capture via Frida

WeChat uses Apple's CommonCrypto (`CCCryptorCreate`) to open encrypted databases. The function signature:

```c
CCCryptorCreate(op, alg, options, key_ptr, key_len, iv_ptr, &cryptor);
```

Hook `key_ptr` when `key_len == 32` and you get every AES-256 key WeChat uses. The Frida script is 40 lines of JavaScript.

### 4. Message Storage

Messages are stored in tables named `Msg_<MD5(username)>`. So to find the chat for contact `wxid_lwgndi41v9y022`:

```python
table = f"Msg_{hashlib.md5('wxid_lwgndi41v9y022'.encode()).hexdigest()}"
# → Msg_c787186e8e179540f0814cb4a6274d75
```

**Private chats:** `message_content` is plain text. Sender identified by `real_sender_id` (a per-DB internal numeric ID).

**Group chats:** `message_content` format is `sender_wxid:\nmessage_text`. Sender parsed directly from the prefix.

### 5. The "Cross-DB User ID" Problem

Your own `real_sender_id` differs across databases (e.g., ID=2 in message_0, ID=6 in message_1). But other people's IDs are consistent. This quirk allowed me to infer missing user IDs through cross-DB comparison.

## Results

My personal export: **4,621 contacts · 230 sessions · 172,898 messages · 122 private + 44 group chats** → single 100MB HTML file.

## Repo Contents

| File | Purpose |
|------|---------|
| `hook_keys.js` | Frida script — hook CCCryptorCreate, capture keys |
| `decrypt_all.py` | Batch-decrypt .db files using sqlcipher CLI |
| `export_all.py` | Parse all chats → single HTML with TOC |
| `export_single.py` | Export one contact → lightweight HTML |
| `README.md` | Full 7-step methodology with code |

## Usage

```bash
pip install frida-tools && brew install sqlcipher
frida -n WeChat -l hook_keys.js      # Step 1: capture keys
python3 decrypt_all.py               # Step 2: decrypt databases
python3 export_all.py                # Step 3: generate HTML
```

## Disclaimer

Educational / personal backup use only. Not affiliated with Tencent. Use at your own risk.

---

**GitHub:** [github.com/patients760/wechat-export](https://github.com/patients760/wechat-export)

*Tested on macOS 13.7, WeChat 4.1.10, Apple Silicon*
