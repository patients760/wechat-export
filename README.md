# macOS 微信聊天记录导出为 HTML

<p align="center">
  <img src="https://img.shields.io/badge/platform-macOS%20Apple%20Silicon%20%7C%20Intel-lightgrey" alt="platform">
  <img src="https://img.shields.io/badge/WeChat-4.x-brightgreen" alt="wechat">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="license">
  <img src="https://img.shields.io/badge/for%20educational%20purposes%20only-red" alt="disclaimer">
</p>

从 macOS 版微信的加密 SQLite 数据库中，提取全部聊天记录（私聊 + 群聊），导出为浏览器可直接打开的 HTML 文件。

---

## ⚠️ 免责声明

> **本项目仅供个人学习、研究、数据备份使用。使用者应自行承担全部责任。**
>
> - 本项目仅用于**个人数据备份**目的，不得用于非法获取他人聊天记录
> - 使用本工具需遵守微信的用户协议和当地法律法规
> - 作者不对因使用本工具导致的任何后果负责，包括但不限于：账号封禁、数据丢失、法律纠纷
> - 本项目与腾讯公司无关，"微信"、"WeChat" 为腾讯公司商标

---

## 📋 目录

- [原理概览](#原理概览)
- [环境准备](#环境准备)
- [Step 1 — 定位加密数据库](#step-1--定位加密数据库)
- [Step 2 — 理解 SQLCipher 加密机制](#step-2--理解-sqlcipher-加密机制)
- [Step 3 — Frida 动态提取密钥](#step-3--frida-动态提取密钥)
- [Step 4 — 验证密钥匹配](#step-4--验证密钥匹配)
- [Step 5 — 批量解密数据库](#step-5--批量解密数据库)
- [Step 6 — 解析数据结构](#step-6--解析数据结构)
- [Step 7 — 生成 HTML 导出](#step-7--生成-html-导出)
- [脚本说明](#脚本说明)
- [技术参考](#技术参考)

---

## 原理概览

macOS 版微信将聊天记录存储在沙盒目录下的加密 SQLite 数据库中：

```
微信沙盒
  └── 加密 SQLite 数据库（SQLCipher 4，每库独立 AES-256 密钥）
        ├── contact.db      → 联系人信息
        ├── session.db      → 会话列表
        └── message_*.db    → 消息内容（核心，分库存储）
```

每条消息以二进制/文本形式存储在 `Msg_<MD5>` 命名的表中，消息格式因私聊/群聊而异。提取流程：

```
Frida Hook 捕获密钥 → 验证密钥 → sqlcipher 解密 → Python 解析数据结构 → 生成 HTML
```

---

## 环境准备

| 组件 | 用途 | 安装 |
|------|------|------|
| **Python 3.10+** | 解密验证 + 数据解析 + HTML 生成 | 系统自带或 `brew install python` |
| **Frida** | 动态注入微信进程，Hook 密钥派生函数 | `pip install frida-tools` |
| **sqlcipher CLI** | 命令行解密加密的 SQLite 数据库 | `brew install sqlcipher` |
| **macOS 13+** | 微信 4.x 运行环境 | — |

```bash
# 一键安装依赖
pip install frida-tools
brew install sqlcipher
```

> **注意：** Frida 需要关闭 SIP（System Integrity Protection）或对其进行签名授权。具体步骤见 [Frida 官方文档](https://frida.re/docs/macos/)。Apple Silicon Mac 还需确认 Frida 支持 arm64 进程注入。

---

## Step 1 — 定位加密数据库

微信的数据目录：

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
```

每个微信账号有一个独立的子目录（如 `wxid_xxx`），其下的 `db_storage/` 存放所有数据库：

```
db_storage/
├── contact/              # 联系人数据库
│   ├── contact.db        ★ 联系人表（wxid、备注、昵称）
│   └── contact_fts.db       （全文搜索索引）
├── message/              # 消息数据库 ★ 核心
│   ├── message_0.db      ★ 近期消息（活跃会话）
│   ├── message_1.db      ★ 历史消息（数据量最大）
│   ├── message_2.db      ★ 更早的历史消息
│   └── message_3.db         （旧格式/归档，部分版本无此文件）
├── session/              # 会话数据库
│   └── session.db        ★ 会话列表（聊天列表顺序、最后消息摘要）
├── favorite/             # 收藏
├── hardlink/             # 文件/图片引用
├── head_image/           # 头像缓存
├── sns/                  # 朋友圈
├── general/              # 通用设置
└── biz/                  # 企业微信相关
```

> **核心文件：** `contact.db`（联系人） + `session.db`（会话列表） + `message_0/1/2.db`（消息内容）。

---

## Step 2 — 理解 SQLCipher 加密机制

微信使用 **SQLCipher 4** 格式，关键参数：

| 参数 | 值 |
|------|-----|
| 页大小 (page size) | 4096 字节 |
| KDF 算法 | PBKDF2-HMAC-SHA512 |
| KDF 迭代次数 | 2 |
| 加密算法 | AES-256-CBC |
| 完整性校验 | HMAC-SHA512 |
| 密钥数量 | 每个 `.db` 文件独立密钥（无主密钥） |

### 页结构

```
偏移 0         16                                       4032      4096
├──────────────┼─────────────────────────────────────────┼──────────┤
│  Salt        │          加密数据 (密文)                  │  HMAC    │
│  16 bytes    │          4016 bytes                     │  64 bytes │
├──────────────┼─────────────────────────────────────────┼──────────┤
│  明文（未加密）│   用 AES-256-CBC 加密                    │ 完整性校验 │
└──────────────┴─────────────────────────────────────────┴──────────┘
```

### 密钥派生流程

```
原始 AES-256 密钥 (32 bytes)
        │
        ├──→ PBKDF2-HMAC-SHA512(salt, iterations=2)
        │         └──→ 页面加密密钥 (32 bytes)：解密数据区
        │
        └──→ XOR salt with 0x3A → MAC salt
                  └──→ PBKDF2-HMAC-SHA512(MAC salt, iterations=2)
                            └──→ MAC 密钥 (32 bytes)：验证 HMAC
```

### 密钥验证算法 (Python 实现)

```python
import hashlib, hmac, struct

def verify_key(key_bytes: bytes, page1: bytes) -> bool:
    """
    验证 32 字节 AES 密钥是否匹配数据库的第一页。
    读取加密 .db 的前 4096 字节作为 page1 传入。
    """
    PAGE_SZ, SALT_SZ, KEY_SZ = 4096, 16, 32

    # 1. 提取 salt
    salt = page1[:SALT_SZ]

    # 2. 派生 MAC 密钥：salt 每字节 XOR 0x3A
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key_bytes, mac_salt, 2, dklen=KEY_SZ)

    # 3. 计算 HMAC 的数据范围：salt 之后、预留区之前
    hmac_data = page1[SALT_SZ : PAGE_SZ - 80 + 16]
    stored_hmac = page1[PAGE_SZ - 64 : PAGE_SZ]

    # 4. 验证 HMAC-SHA512(page_number=1)
    h = hmac.new(mac_key, hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == stored_hmac
```

> **为什么每个数据库有独立密钥？** 如果使用统一主密钥，任意一个数据库被破解则全部泄漏。独立密钥限制了单一数据库泄漏的影响范围——这也是为什么需要捕获 14+ 个密钥才能解密全部数据库。

---

## Step 3 — Frida 动态提取密钥

### 原理

微信在运行时通过 Apple **CommonCrypto** 框架的 `CCCryptorCreate()` 函数打开加密数据库。该函数的第 4 个参数是 AES 密钥指针，第 5 个参数是密钥长度。在函数入口处 Hook，即可拦截所有数据库打开操作及其密钥。

```
微信进程
  └── wechat.dylib (307MB, 静态链接 WCDB 等)
        └── sqlite3_key() → CCCryptorCreate(op, alg, options, *key_ptr, key_len)
                              │                       │           │
                              └─ 0=加密 1=解密          └─ AES key  └─ 32
```

### Frida 注入脚本 (`hook_keys.js`)

```javascript
// hook_keys.js — Hook CCCryptorCreate 捕获所有 AES-256 密钥
const seen = new Set();

const addr = DebugSymbol.getFunctionByName("CCCryptorCreate");
if (!addr) {
    console.log("[-] CCCryptorCreate not found — check Frida/SIP setup");
} else {
    console.log("[+] Hooked CCCryptorCreate at " + addr);

    Interceptor.attach(addr, {
        onEnter(args) {
            const op = args[0].toInt32();        // 0 = 加密, 1 = 解密
            const keyLen = args[4].toInt32();    // 密钥字节数

            if (keyLen === 32) {                 // 只关注 AES-256
                const keyBytes = args[3].readByteArray(32);
                const hex = Array.from(new Uint8Array(keyBytes))
                    .map(b => b.toString(16).padStart(2, "0"))
                    .join("");

                if (!seen.has(hex)) {
                    seen.add(hex);
                    console.log(`\n=== NEW AES-256 KEY (op=${op}) ===`);
                    console.log(hex);
                }
            }
        }
    });
}
```

### 操作步骤

```bash
# 1. 确保微信已启动并登录

# 2. 注入 Frida（启动后注入）
frida -n WeChat -l hook_keys.js

# 或者跟随微信启动（从启动开始捕获，密钥更全）
frida -f com.tencent.xinWeChat -l hook_keys.js
```

### 触发更多密钥

Frida 注入后，密钥不会一次性全部出现。需要在微信中**主动操作**各个功能触发数据库加载：

| 操作 | 可能触发的数据库 |
|------|-----------------|
| 滚动聊天列表，点击不同会话 | `message_0/1/2.db` 中的不同 Msg_ 表 |
| 搜索聊天记录 | `message_fts.db` |
| 打开联系人列表 | `contact.db` |
| 查看朋友圈 | `sns.db` |
| 查看收藏 | `favorite.db` |
| 切换账号 / 重新登录 | 所有数据库重新加载 |

> **技巧：** 在微信中快速滚动聊天列表、逐个点开不同群聊和私聊，每次操作都可能触发新的 Msg_ 表加载，从而捕获对应的密钥。

---

## Step 4 — 验证密钥匹配

捕获到一批密钥后，需要将每个密钥与每个数据库逐一匹配，确定"哪个密钥解锁哪个库"。

### 验证脚本 (`verify_keys.py`)

```python
# 对每个数据库文件，用每个候选密钥尝试验证第一页
db_files = []  # [(相对路径, 第一页数据), ...]
key_pool = []  # [(标签, 密钥bytes), ...]

for rel_path, page1 in db_files:
    for label, key_bytes in key_pool:
        if verify_key(key_bytes, page1):
            print(f"  ✅ {rel_path} → Key[{label}]")
            break
```

匹配结果示例：

```
Database                                    Key
──────────────────────────────────────────────────
contact/contact.db                          Key_A ✅
message/message_0.db                        Key_B ✅
message/message_1.db                        Key_C ✅
message/message_2.db                        Key_D ✅
session/session.db                          Key_E ✅
...
```

将已验证的密钥映射保存为 `wechat_keys.txt`：

```
contact/contact.db = 25c5110adc7e0df9ed2f15cd6c86373a8a611902771538efac15da2ef561b216
message/message_0.db = 53da41d344ec8161e581d169cda85c7cc1c99c596bb58b2a3500687a2d9b1448
...
```

---

## Step 5 — 批量解密数据库

使用 sqlcipher CLI 的 `sqlcipher_export` 功能，将加密库导出为无密码的明文 SQLite。

### 解密脚本 (`decrypt_all.py`)

```python
#!/usr/bin/env python3
"""用 sqlcipher CLI 批量解密所有已知密钥的微信数据库"""

KEYS = {
    "contact/contact.db":     "25c5110adc...",
    "message/message_0.db":   "53da41d344ec...",
    "message/message_1.db":   "3961743b679a...",
    "message/message_2.db":   "d40d27b26708...",
    "session/session.db":     "c556ea275925...",
    # ... 更多数据库
}

for rel_path, key_hex in KEYS.items():
    src = f"{DB_STORAGE}/{rel_path}"
    dst = f"{OUTPUT_DIR}/{rel_path}"

    subprocess.run(["sqlcipher", src], input=f"""
PRAGMA key = "x'{key_hex}'";
PRAGMA cipher_compatibility = 4;
PRAGMA kdf_iter = 2;
ATTACH DATABASE '{dst}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
""", capture_output=True, text=True)
```

关键 pragma 说明：

| Pragma | 说明 |
|--------|------|
| `key = "x'...'"` | 以十六进制格式指定 AES-256 密钥 |
| `cipher_compatibility = 4` | 使用 SQLCipher 4 兼容模式 |
| `kdf_iter = 2` | PBKDF2 迭代 2 次（微信的配置） |
| `sqlcipher_export('plaintext')` | 将解密后的数据库导出到明文附加库 |

> **注意：** 解密后的数据库不含密码保护，请妥善保管，用后即删。

---

## Step 6 — 解析数据结构

解密后的数据库为标准 SQLite，可直接用 Python `sqlite3` 模块读取。

### 6.1 联系人 (`contact.db`)

```sql
-- contact 表核心字段
SELECT
    username,     -- 'wxid_xxx' (私聊) 或 '12345678@chatroom' (群聊)
    alias,        -- 微信号
    remark,       -- 备注名（自己给对方设置的，优先级最高）
    nick_name     -- 微信昵称（对方设置的）
FROM contact;
```

显示名称优先级：**备注名 > 昵称 > 微信号 > wxid**

### 6.2 会话列表 (`session.db`)

```sql
-- SessionTable：聊天列表的全部会话
SELECT
    username,                     -- 会话标识（wxid 或 @chatroom）
    summary,                      -- 最后一条消息摘要
    last_timestamp,               -- 最后消息时间（Unix 秒）
    last_msg_sender,              -- 最后一条消息的发送者 wxid
    last_sender_display_name      -- 最后发送者的显示名称
FROM SessionTable
ORDER BY sort_timestamp DESC;
```

> `SessionTable` 中的 `username` 是连接消息表的关键——私聊为对方 wxid，群聊为 `<数字>@chatroom`。

### 6.3 消息存储 (`message_*.db`) — 核心

消息按聊天会话分表存储。**表名 = 会话 username 的 MD5 哈希**：

```python
import hashlib

username = "wxid_lwgndi41v9y022"           # 某联系人的 wxid
table_name = f"Msg_{hashlib.md5(username.encode()).hexdigest()}"
# → Msg_c787186e8e179540f0814cb4a6274d75
```

Msg_ 表字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `local_id` | INTEGER | 消息本地序号 |
| `create_time` | INTEGER | 发送时间（Unix 秒） |
| `local_type` | INTEGER | 消息类型编码 |
| `real_sender_id` | INTEGER | 发送者内部数字 ID（**每库不同**） |
| `message_content` | TEXT/BLOB | 消息内容（格式因私聊/群聊而异） |
| `source` | TEXT/BLOB | 消息元数据（XML 或二进制 protobuf） |

**消息类型编码：**

| local_type | 含义 | content 格式 |
|-----------|------|-------------|
| 1 | 文字 | 文本 |
| 3 | 图片 | 二进制（路径/缩略图） |
| 34 | 语音 | 二进制 |
| 43 | 视频 | 二进制 |
| 47 | 表情/emoji | 二进制 |
| 49 | 小程序/文件/链接 | XML + 二进制 |
| 10000 | 系统消息 | 文本（撤回/入群/置顶等） |
| 10002 | 系统通知 | 文本 |

### 6.4 发送者识别（关键难点）

#### 私聊消息

私聊中 `message_content` 为**纯文本**，不含发送者信息。发送者通过 `real_sender_id` 判断——需要先确定"哪个内部 ID 是用户自己"。

**方法：** 查找用户自己的"文件传输助手"或"给自己发消息"的聊天（session username = 用户自己的 wxid），该会话中出现的所有 `real_sender_id` 即为用户在该数据库中的内部 ID。

```
同一数据库内：
  real_sender_id ∈ {2, 14}  →  用户自己（周彬逊）
  real_sender_id 为其他值   →  聊天对象（session.username 对应的联系人）
```

#### 群聊消息

群聊中 `message_content` 格式为 `发送者wxid:\n消息内容`：

```
wxid_p6i2esctw47s21:\n汉中平原的龙灯，龙头小一些
wxid_75xt3djwzbwy41:\n始发站吧
lcxliurly:\n你是不是想当高战              ← 非 wxid 格式的用户名
```

对非文字消息（图片/语音等），content 为二进制，通过 `real_sender_id` 映射来识别发送者——优先用已解析的文字消息建立 `real_sender_id → wxid` 映射表。

> **跨库陷阱：** 用户自己的 `real_sender_id` 在不同数据库中**值不同**（如 message_0 中为 2，message_1 中为 6），但联系人的内部 ID 在跨库时保持一致。可利用此规律推断缺失数据库中的用户 ID。

---

## Step 7 — 生成 HTML 导出

### 导出流程 (`export_all.py`)

```
               contact.db ──→ {wxid → 显示名称}
               session.db ──→ [{username, 最后时间, ...}]
                                   │
                     ┌─────────────┘
                     ▼
              MD5(username) → Msg_表名
                     │
              message_0.db ─┐
              message_1.db ─┼──→ 合并同表消息（跨库去重+排序）
              message_2.db ─┘
                     │
              ┌──────┴──────┐
              ▼             ▼
         私聊消息          群聊消息
    real_sender_id     wxid:\n前缀解析
    → 用户/对方        → 发送者识别
              │             │
              └──────┬──────┘
                     ▼
              按最后消息时间排序
                     │
                     ▼
              生成 HTML（sticky top 目录 + 消息列表）
```

### 使用方法

```bash
# ① 确保已完成解密（decrypt_all.py 成功运行）
ls ~/Desktop/Claude/wechat_decrypted/
# 应包含 contact/ session/ message/ 等目录

# ② 导出全部聊天记录
python3 export_all.py
# → ~/Desktop/Claude/wechat_all_chats.html (约 100MB)

# ③ 导出单个联系人的聊天记录
# 先编辑 export_single.py 中的 TARGET_REMARK，然后：
python3 export_single.py
# → ~/Desktop/Claude/<联系人名>_聊天记录.html (约 500KB-2MB)
```

### 导出效果

生成的 HTML 包含：

- **顶部导航栏** — 微信绿色风格，显示导出时间和统计数据
- **可点击目录** — 私聊和群聊分组，标注消息条数，点击跳转
- **消息气泡** — 发送者 + 时间 + 内容，群聊中不同人用不同颜色
- **消息类型标注** — 图片/语音/表情等非文字消息用斜体 `[image]` 标注
- **系统消息** — 撤回、入群等灰色居中显示

### 运行输出示例

```
[*] Loaded 4621 contacts
[*] Loaded 230 sessions
[*] message_0: user internal ids = {2, 14}, 72 tables
[*] message_1: user internal ids = {6}, 154 tables
[*] message_2: user internal ids = set(), 111 tables
[*] message_2: inferred user ids = {10} (from message_1)

[*] Exported to: /Users/zhou/Desktop/Claude/wechat_all_chats.html
[*] 166 chats (122 private + 44 groups), 172898 messages

  群聊: 三个命苦的牛马         | 12057 msgs | GROUP
  文件传输助手                  |  1138 msgs | private
  杨思敏                       |  2412 msgs | private
  群聊: 君子丨执剑              | 85248 msgs | GROUP
  王佳鑫                       |  4293 msgs | private
  ...
```

---

## 脚本说明

| 文件 | 作用 | 依赖 |
|------|------|------|
| `hook_keys.js` | Frida 脚本，Hook `CCCryptorCreate` 捕获 AES-256 密钥 | Frida, macOS SIP 关闭 |
| `decrypt_all.py` | 用 sqlcipher CLI 批量解密数据库 → 明文 SQLite | sqlcipher CLI, 已捕获的密钥 |
| `export_all.py` | 导出全部私聊+群聊 → 单个大 HTML | Python 3.10+, 已解密的数据库 |
| `export_single.py` | 导出指定联系人的聊天 → 单个小 HTML | 同上，修改脚本中的备注名 |

### `hook_keys.js`

- **原理：** Apple CommonCrypto 的 `CCCryptorCreate` 是 SQLCipher 在 macOS 上打开加密数据库的必经之路
- **过滤条件：** `op = 0`（加密操作）且 `keyLen = 32`（AES-256），去重后输出十六进制密钥
- **最佳实践：** 微信冷启动时通过 `-f` 注入，覆盖最多的数据库加载时机

### `decrypt_all.py`

- **依赖：** 预先捕获密钥，填入脚本的 `KEYS` 字典
- **核心指令：** `sqlcipher_export('plaintext')` — sqlcipher 内置的数据库复制函数，在附加的明文库上生成完整解密副本
- **超时处理：** 大数据库（如 message_1.db 90MB+）解密需要较长时间，脚本设置了 120 秒超时
- **输出目录：** `~/Desktop/Claude/wechat_decrypted/`

### `export_all.py`

- **数据源：** 解密后的 `contact.db` + `session.db` + `message_0/1/2.db`
- **核心逻辑：**
  1. 从 SessionTable 加载全部会话，计算 MD5 得到对应的 Msg_ 表名
  2. 同一聊天可能跨多个 message 数据库，合并后按时间排序
  3. 私聊通过 `real_sender_id` 判断发送者；群聊从 `message_content` 前缀解析发送者 wxid
  4. 联系人备注名优先作为显示名称
- **输出：** 单个 HTML 文件，包含可跳转目录和全部消息

### `export_single.py`

- **与 export_all.py 的区别：** 仅导出指定联系人的聊天，HTML 更轻量（几百 KB）
- **配置方式：** 修改脚本头部的 `TARGET_REMARK`（备注名）、`TARGET_NICK`（昵称）或 `TARGET_WXID`
- **发送者显示：** 绿色 = 用户自己，黑色 = 对方

---

## 技术参考

- [SQLCipher 4 File Format](https://www.zetetic.net/sqlcipher/sqlcipher-api/) — 页结构、KDF、HMAC 验证
- [Frida JavaScript API](https://frida.re/docs/javascript-api/) — Interceptor.attach, NativePointer, DebugSymbol
- [Apple CommonCrypto — CCCryptorCreate](https://developer.apple.com/documentation/commoncrypto) — 密钥操作类型和参数
- [PBKDF2-HMAC-SHA512 (RFC 2898)](https://www.ietf.org/rfc/rfc2898.txt) — 密钥派生标准
- [WCDB (Tencent WeChat Database)](https://github.com/Tencent/wcdb) — 微信使用的数据库框架（macOS 版静态链接到 wechat.dylib）

---

## License

MIT License. © 2025

**仅供个人学习、研究、数据备份使用。与腾讯公司无关。**
