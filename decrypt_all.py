#!/usr/bin/env python3
"""
Decrypt all WeChat databases using captured keys.

Prerequisite: Run hook_keys.js with Frida first, verify keys, then fill in KEYS dict below.
Each WeChat installation has DIFFERENT keys — the placeholders below are examples only.
"""
import os, glob, subprocess

# === CONFIGURATION ============================================================

# WeChat db_storage directory (auto-detected)
DB_DIR = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)

# Output directory for decrypted databases
OUT_DIR = os.path.expanduser("~/Desktop/Claude/wechat_decrypted")

# Map: relative path under db_storage → AES-256 key (64 hex chars)
# Keys are UNIQUE per WeChat installation. Replace with your own captured keys.
KEYS = {
    # "contact/contact.db":     "REPLACE_WITH_YOUR_KEY",
    # "message/message_0.db":   "REPLACE_WITH_YOUR_KEY",
    # "message/message_1.db":   "REPLACE_WITH_YOUR_KEY",
    # "message/message_2.db":   "REPLACE_WITH_YOUR_KEY",
    # "session/session.db":     "REPLACE_WITH_YOUR_KEY",
    # "favorite/favorite.db":   "REPLACE_WITH_YOUR_KEY",
    # "hardlink/hardlink.db":   "REPLACE_WITH_YOUR_KEY",
    # "head_image/head_image.db": "REPLACE_WITH_YOUR_KEY",
    # "sns/sns.db":             "REPLACE_WITH_YOUR_KEY",
}

# ==============================================================================


def find_db_dir():
    """Auto-detect the db_storage directory for the current WeChat account."""
    pattern = os.path.join(DB_DIR, "*", "db_storage")
    candidates = glob.glob(pattern)
    return candidates[0] if candidates else None


def main():
    if not any("REPLACE" not in v for v in KEYS.values() if v):
        print("[-] No keys configured.")
        print("    Run hook_keys.js with Frida to capture your keys first.")
        print("    Then fill in the KEYS dict at the top of this script.")
        return

    db_dir = find_db_dir()
    if not db_dir:
        print(f"[-] db_storage not found under {DB_DIR}")
        return

    print(f"[*] Source: {db_dir}")
    print(f"[*] Output: {OUT_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)

    for rel, key_hex in KEYS.items():
        src = os.path.join(db_dir, rel)
        dst = os.path.join(OUT_DIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        if not os.path.exists(src):
            print(f"  SKIP  {rel} (file not found)")
            continue

        size_mb = os.path.getsize(src) / (1024 * 1024)

        result = subprocess.run(
            ["sqlcipher", src],
            input=f"""PRAGMA key = "x'{key_hex}'";
PRAGMA cipher_compatibility = 4;
PRAGMA kdf_iter = 2;
ATTACH DATABASE '{dst}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
""",
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            dst_size = os.path.getsize(dst) / (1024 * 1024)
            print(f"  OK    {rel} ({size_mb:.1f}M → {dst_size:.1f}M)")
        else:
            err = result.stderr[:120].replace("\n", " ")
            print(f"  FAIL  {rel}: {err}")

    print(f"\n[*] Done. Decrypted databases: {OUT_DIR}")


if __name__ == "__main__":
    main()
