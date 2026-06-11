/**
 * hook_keys.js — Frida script to capture WeChat AES-256 database keys on macOS.
 *
 * Hooks CCCryptorCreate (Apple CommonCrypto) which WeChat calls via SQLCipher/WCDB
 * to open encrypted SQLite databases. Captures the 32-byte AES key from arg #3.
 *
 * Usage:
 *   # Attach to running WeChat:
 *   frida -n WeChat -l hook_keys.js
 *
 *   # Launch WeChat under Frida (captures more keys from cold start):
 *   frida -f com.tencent.xinWeChat -l hook_keys.js
 *
 * After injecting, interact with WeChat to trigger database loads:
 *   - Scroll through chat list, open different private/group chats
 *   - Open Contacts, Favorites, Moments
 *   - Search messages
 *
 * Each captured key is printed once (deduplicated). Collect all unique keys,
 * then verify them against the encrypted .db files with verify_keys.py.
 *
 * macOS prerequisites:
 *   - Disable SIP or sign Frida: https://frida.re/docs/macos/
 *   - pip install frida-tools
 */

var seen = new Set();
var count = 0;

var addr = DebugSymbol.getFunctionByName("CCCryptorCreate");
if (!addr) {
    console.log("[-] CCCryptorCreate not found");
    console.log("    Check: SIP disabled? Frida signed? WeChat running?");
} else {
    console.log("[+] Hooked CCCryptorCreate at " + addr);
    console.log("[*] Waiting for database open events...");
    console.log("[*] Interact with WeChat to trigger key captures.\n");

    Interceptor.attach(addr, {
        onEnter(args) {
            var op = args[0].toInt32();        // 0=encrypt, 1=decrypt (rare in WeChat)
            var keyPtr = args[3];              // pointer to key bytes
            var keyLen = args[4].toInt32();    // key length in bytes

            if (keyLen !== 32) return;         // only AES-256

            var keyBytes = keyPtr.readByteArray(32);
            var hex = "";
            var bytes = new Uint8Array(keyBytes);
            for (var i = 0; i < bytes.length; i++) {
                var b = bytes[i].toString(16);
                hex += (b.length === 1 ? "0" : "") + b;
            }

            if (!seen.has(hex)) {
                seen.add(hex);
                count++;
                console.log("┌─────────────────────────────────────────────");
                console.log("│ KEY #" + count + "  (op=" + op + ")");
                console.log("├─────────────────────────────────────────────");
                console.log("│ " + hex);
                console.log("└─────────────────────────────────────────────");
            }
        }
    });
}
