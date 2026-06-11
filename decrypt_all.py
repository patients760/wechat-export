#!/usr/bin/env python3
"""Decrypt all WeChat databases with known keys to output directory."""
import hashlib, hmac as hmac_mod, struct, os, glob, subprocess, shutil

DB_DIR = os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files")
OUT_DIR = os.path.expanduser("~/Desktop/Claude/wechat_decrypted")
PAGE_SZ, SALT_SZ, KEY_SZ = 4096, 16, 32

KEYS = {
    "contact/contact.db": "25c5110adc7e0df9ed2f15cd6c86373a8a611902771538efac15da2ef561b216",
    "contact/contact_fts.db": "ad905e5c99e6c5d435e600bf082c05a4de567f3f82c5287fb9f69763d87817b4",
    "favorite/favorite.db": "4ff97d6edf6b100209e071222668ecf258d81786b4c7cd495e764d8d1aafccb7",
    "favorite/favorite_fts.db": "2d025898887bb6cf9e0797631150b76a536c0011e16ba73a1e893763e6f0c4ff",
    "general/general.db": "9f08a32fa8531b755bf0d42639f9cdb56ad36f2c711c417f9065a50ecb663712",
    "hardlink/hardlink.db": "800c339c277be0af22f0272ae0e3f510652f89b839d223793814b663b3690c9e",
    "head_image/head_image.db": "a3954f59d3d9dcffe099c28e7becc9acfbc1c3139d59187375c4fc2e937af55a",
    "message/message_0.db": "53da41d344ec8161e581d169cda85c7cc1c99c596bb58b2a3500687a2d9b1448",
    "message/message_1.db": "3961743b679ab85875bca1ecf128c9a2b9e171c50ef50c44de73b1c244c2e457",
    "message/message_2.db": "d40d27b267088293a2245b5d5cce1ddadef46c76c06cd08b28e6878913a8e332",
    "message/message_fts.db": "182c5d1a083303a3af04c4fe4a50cd454e3c9e397e7d8fc4b06324dd5b39d472",
    "message/message_resource.db": "0b383f99b51c7954272b10f2d674507b5a750ebb5a0dd6f5848f75b1091b02ed",
    "session/session.db": "c556ea275925124dacba96b37c04e5ddca9221107004f27ca84dd1e47e73bde0",
    "sns/sns.db": "9002a975998153212380f630a667f981eb3b05dbc14d7a8f230e536ac69ed028",
}

def find_db_dir():
    pattern = os.path.join(DB_DIR, "*", "db_storage")
    candidates = glob.glob(pattern)
    return candidates[0] if candidates else None

db_dir = find_db_dir()
if not db_dir:
    print("[-] db_storage not found"); exit(1)

os.makedirs(OUT_DIR, exist_ok=True)

for rel, key_hex in KEYS.items():
    src = os.path.join(db_dir, rel)
    dst = os.path.join(OUT_DIR, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    if not os.path.exists(src):
        print(f"  SKIP {rel} (not found)")
        continue

    size_mb = os.path.getsize(src) / (1024*1024)

    result = subprocess.run([
        "sqlcipher", src,
    ], input=f"""PRAGMA key = "x'{key_hex}'";
PRAGMA cipher_compatibility = 4;
PRAGMA kdf_iter = 2;
ATTACH DATABASE '{dst}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
""", capture_output=True, text=True, timeout=120)

    if result.returncode == 0:
        dst_size = os.path.getsize(dst) / (1024*1024)
        print(f"  OK  {rel} ({size_mb:.1f}M -> {dst_size:.1f}M)")
    else:
        print(f"  FAIL {rel}: {result.stderr[:100]}")

print(f"\nDone. Decrypted DBs: {OUT_DIR}")
