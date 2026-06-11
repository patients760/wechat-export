/**
 * Frida script to hook CCCryptorCreate and capture AES-256 keys.
 *
 * Usage:
 *   frida -n WeChat -l hook_keys.js
 *   frida -f com.tencent.xinWeChat -l hook_keys.js
 *
 * Captured keys are printed to console. Interact with different WeChat
 * features (open chats, view contacts, check Moments, etc.) to trigger
 * more database loads and capture more keys.
 */

var seen = {};

var addr = DebugSymbol.getFunctionByName("CCCryptorCreate");
if (!addr) {
    console.log("[-] CCCryptorCreate not found");
} else {
    console.log("[+] Hooked CCCryptorCreate at " + addr);

    Interceptor.attach(addr, {
        onEnter(args) {
            var op = args[0].toInt32();
            var alg = args[1].toInt32();
            var options = args[2].toInt32();
            var keyPtr = args[3];
            var keyLen = args[4].toInt32();

            // AES-256 keys are 32 bytes
            if (keyLen === 32) {
                var keyBytes = keyPtr.readByteArray(32);
                var hex = "";
                var bytes = new Uint8Array(keyBytes);
                for (var i = 0; i < bytes.length; i++) {
                    hex += ("0" + bytes[i].toString(16)).slice(-2);
                }

                if (!seen[hex]) {
                    seen[hex] = true;
                    console.log("\n=== AES-256 KEY (op=" + op + ") ===");
                    console.log(hex);
                }
            }
        }
    });
}
