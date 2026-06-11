# macOS 微信聊天记录导出为 HTML

从 macOS 版微信的加密数据库中提取全部聊天记录（私聊 + 群聊），导出为可直接在浏览器中查看的 HTML 文件。

## 免责声明

**本项目仅供学习研究使用。使用者应自行承担全部责任。**

- 本项目仅用于个人数据备份目的
- 使用本工具需遵守微信的用户协议和当地法律法规
- 不得将本工具用于非法获取他人数据
- 作者不对因使用本工具导致的任何后果负责（包括但不限于账号封禁、数据丢失、法律纠纷）

## 原理概述

macOS 版微信将聊天记录存储在 `~/Library/Containers/com.tencent.xinWeChat/` 下的 SQLite 数据库中，数据库使用 **SQLCipher 4** 加密，每个数据库有独立的 AES-256 密钥。

整体流程：

```
定位加密数据库 → 提取密钥 → 解密数据库 → 解析数据结构 → 生成 HTML
```

## 环境要求

- macOS（Apple Silicon / Intel）
- Python 3.10+
- [Frida](https://frida.re)（用于动态提取密钥）
- [sqlcipher CLI](https://www.zetetic.net/sqlcipher/)（用于解密数据库）
- WeChat 4.x for Mac

```bash
pip install frida-tools
brew install sqlcipher
```

## 第一步：定位加密数据库

微信数据库位于：

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<用户ID>/db_storage/
```

目录结构：

```
db_storage/
├── contact/         # 联系人
│   ├── contact.db
│   └── contact_fts.db
├── message/         # 聊天消息（核心）
│   ├── message_0.db
│   ├── message_1.db
│   ├── message_2.db
│   └── message_3.db
├── session/         # 会话列表
│   └── session.db
├── favorite/        # 收藏
├── hardlink/        # 文件引用
├── head_image/      # 头像
├── sns/             # 朋友圈
└── ...
```

最关键的是 `message/` 目录下的数据库（消息内容）和 `contact/contact.db`（联系人）、`session/session.db`（会话列表）。

## 第二步：理解 SQLCipher 加密

微信使用 SQLCipher 4 加密格式，每页 4096 字节：

```
+----------------+----------------------------------------+------------------+
|  Salt (16字节)  |          加密数据 (4016字节)              |  HMAC (64字节)    |
+----------------+----------------------------------------+------------------+
|  偏移 0-15     |          偏移 16-4031                     |  偏移 4032-4095   |
+----------------+----------------------------------------+------------------+
```

密钥派生过程：

1. 使用 PBKDF2-HMAC-SHA512 从原始密钥派生加密密钥（2 次迭代）
2. 将 Salt 的每个字节与 `0x3A` 异或得到 MAC Salt
3. 从 MAC Salt 派生 MAC 密钥
4. 用 HMAC-SHA512 验证每页完整性

> 微信对每个数据库使用**不同的 AES-256 密钥**，不存在统一的"主密钥"。

## 第三步：提取密钥（Frida 动态插桩）

密钥在微信通过 `CCCryptorCreate`（Apple CommonCrypto）打开数据库时出现在内存中。可以通过 Frida Hook 捕获：

```javascript
// hook_keys.js
var seen = {};

var addr = DebugSymbol.getFunctionByName("CCCryptorCreate");
Interceptor.attach(addr, {
    onEnter(args) {
        var op = args[0].toInt32();       // 0=加密, 1=解密
        var keyPtr = args[3];              // 密钥指针
        var keyLen = args[4].toInt32();    // 密钥长度

        if (keyLen === 32) {               // AES-256 密钥
            var hex = hexdump(keyPtr, {length: 32, ansi: false});
            if (!seen[hex]) {
                seen[hex] = true;
                send({type: "key", op: op, key: hex});
            }
        }
    }
});
```

运行方式：

```bash
# 启动微信后注入
frida -n WeChat -l hook_keys.js

# 或者在微信启动时注入
frida -f com.tencent.xinWeChat -l hook_keys.js
```

操作微信的不同功能模块（打开不同聊天、查看联系人、朋友圈等），触发更多数据库的加载，从而捕获更多密钥。

## 第四步：验证密钥

将捕获到的密钥与数据库进行验证——读取加密数据库的第一页（4096 字节），用候选密钥验证 HMAC：

```python
import hashlib, hmac, struct

def verify_key(key_bytes, page1):
    """验证密钥是否匹配数据库第一页"""
    PAGE_SZ, SALT_SZ, KEY_SZ = 4096, 16, 32

    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key_bytes, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = page1[SALT_SZ : PAGE_SZ - 80 + 16]
    stored_hmac = page1[PAGE_SZ - 64 : PAGE_SZ]

    h = hmac.new(mac_key, hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return h.digest() == stored_hmac
```

通过验证后，记录每个数据库对应的密钥（保存在 `wechat_keys.txt`）。

## 第五步：批量解密

使用 sqlcipher CLI 将加密数据库导出为明文：

```python
# decrypt_all.py 核心逻辑
import subprocess, os

KEYS = {
    "message/message_0.db": "53da41d344ec8161e581d169cda85c7cc...",
    "message/message_1.db": "3961743b679ab85875bca1ecf128c9a2b9...",
    "contact/contact.db": "25c5110adc7e0df9ed2f15cd6c86373a8a...",
    "session/session.db": "c556ea275925124dacba96b37c04e5ddca...",
    # ...
}

for rel_path, key_hex in KEYS.items():
    src = os.path.join(DB_DIR, rel_path)
    dst = os.path.join(OUT_DIR, rel_path)

    subprocess.run(["sqlcipher", src], input=f"""
PRAGMA key = "x'{key_hex}'";
PRAGMA cipher_compatibility = 4;
PRAGMA kdf_iter = 2;
ATTACH DATABASE '{dst}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
""", capture_output=True, text=True)
```

解密后的数据库可以直接用任何 SQLite 工具读取。

## 第六步：理解数据结构

### 联系人 (contact.db)

```sql
-- 联系人信息
SELECT username, alias, remark, nick_name FROM contact;
-- username: wxid_xxx 或 @chatroom ID
-- remark: 备注名（优先级最高）
-- nick_name: 微信昵称
-- alias: 微信号
```

对于群聊，`username` 以 `@chatroom` 结尾，`nick_name` 字段存储群名。

### 会话列表 (session.db)

```sql
-- SessionTable 记录所有聊天会话
SELECT username, summary, last_timestamp, last_msg_sender
FROM SessionTable
ORDER BY sort_timestamp DESC;
-- username: 私聊为 wxid，群聊为 <id>@chatroom
```

### 消息存储 (message_*.db)

消息分布在多个数据库中（message_0/1/2/3），每个聊天对应一个 `Msg_` 表，表名为 chatroom id 经过 MD5 哈希后的结果：

```python
import hashlib

# 会话 username → Msg_ 表名
table_name = f"Msg_{hashlib.md5(username.encode()).hexdigest()}"
# 例如: Msg_c787186e8e179540f0814cb4a6274d75
```

Msg_ 表的字段结构：

| 字段 | 说明 |
|------|------|
| `local_id` | 消息本地 ID |
| `create_time` | 时间戳（Unix 秒） |
| `local_type` | 消息类型：1=文字, 3=图片, 34=语音, 47=表情, 10000=系统消息 |
| `real_sender_id` | 发送者内部数字 ID |
| `message_content` | 消息内容 |

### 发送者识别

**私聊消息：** `message_content` 为纯文本，发送者通过 `real_sender_id` 区分。每个数据库中，用户的内部 ID 可以通过查询"自己给自己发消息"的聊天记录确定。

**群聊消息：** `message_content` 格式为 `发送者wxid:\n消息内容`，可直接解析出发送者 wxid。非文字消息（图片、语音等）为二进制内容，需通过 `real_sender_id` 与文字消息的对应关系推断发送者。

### 消息类型

| local_type | 含义 |
|-----------|------|
| 1 | 文字 |
| 3 | 图片 |
| 34 | 语音 |
| 43 | 视频 |
| 47 | 表情 |
| 49 | 小程序/文件/链接 |
| 10000 | 系统消息（撤回、入群等） |

## 第七步：导出为 HTML

完整导出脚本 `export_all.py` 的工作流程：

```
1. 从 contact.db 加载全部联系人 → {wxid: 显示名称}
2. 从 session.db 加载全部会话 → [{username, 最后消息时间}]
3. 遍历 message_0/1/2.db:
   a. 对每个会话，通过 MD5(username) 找到对应 Msg_ 表
   b. 读取全部消息
   c. 解析发送者和内容
4. 按最后消息时间排序
5. 生成带目录的 HTML 文件
```

### 使用方法

```bash
# 1. 先解密数据库（需要先捕获密钥并填入 wechat_keys.txt）
python3 decrypt_all.py

# 2. 导出全部聊天
python3 export_all.py
# 输出: ~/Desktop/Claude/wechat_all_chats.html

# 3. 导出单个联系人
python3 export_single.py
# 按提示修改脚本中的联系人备注名
```

## 导出效果

HTML 文件包含：
- 顶部目录（私聊 + 群聊，可点击跳转）
- 每条消息显示发送者、时间、内容
- 群聊中不同发送者用不同颜色区分
- 图片/语音/表情等非文字消息标注类型
- 系统消息（撤回、入群等）灰色居中显示

## 输出示例

```
[*] Loaded 4621 contacts
[*] Loaded 230 sessions
[*] message_0: 72 tables
[*] message_1: 154 tables
[*] message_2: 111 tables

[*] Exported to: wechat_all_chats.html
[*] 166 chats (122 private + 44 groups), 172898 messages
```

## 技术参考

- [SQLCipher File Format](https://www.zetetic.net/sqlcipher/sqlcipher-api/)
- [Frida JavaScript API](https://frida.re/docs/javascript-api/)
- [Apple CommonCrypto - CCCryptorCreate](https://developer.apple.com/documentation/commoncrypto)

## License

MIT License. 仅供学习研究使用。
