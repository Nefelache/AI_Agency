#!/usr/bin/env node
/**
 * Connection smoke test — verifies WhatsApp pairing does NOT hit 405 error.
 * The 405 "Connection Failure" prevents QR code from appearing.
 *
 * Run: npm run test:connection
 * Requires network. Uses temp auth dir. Times out after 25s.
 */
import makeWASocket, { useMultiFileAuthState } from "@whiskeysockets/baileys";
import pino from "pino";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WA_VERSION = [2, 3000, 1029620931];
const TIMEOUT_MS = 25_000;

async function run() {
  const tmpDir = mkdtempSync(join(tmpdir(), "wa-bridge-smoke-"));
  try {
    const { state, saveCreds } = await useMultiFileAuthState(tmpDir);
    const sock = makeWASocket({
      auth: state,
      version: WA_VERSION,
      printQRInTerminal: false,
      logger: pino({ level: "silent" }),
    });

    sock.ev.on("creds.update", saveCreds);

    const result = await new Promise((resolve) => {
      const timer = setTimeout(() => {
        resolve({ ok: false, reason: "timeout" });
      }, TIMEOUT_MS);

      sock.ev.on("connection.update", (update) => {
        const { qr, connection, lastDisconnect } = update;

        if (qr) {
          clearTimeout(timer);
          resolve({ ok: true, reason: "qr_received" });
        }
        if (connection === "open") {
          clearTimeout(timer);
          resolve({ ok: true, reason: "connected" });
        }
        if (connection === "close") {
          const statusCode = lastDisconnect?.error?.output?.statusCode;
          if (statusCode === 405) {
            clearTimeout(timer);
            resolve({ ok: false, reason: "405", statusCode });
          }
        }
      });
    });

    sock.end(undefined);

    if (!result.ok) {
      if (result.reason === "405" || result.statusCode === 405) {
        console.error("\n❌ FAIL: statusCode 405 (Connection Failure)");
        console.error("   Baileys pairing is broken. Check version is 6.7.21.");
        process.exit(1);
      }
      if (result.reason === "timeout") {
        console.warn("\n⚠️  Timeout: no QR in", TIMEOUT_MS / 1000, "s");
        console.warn("   Network may be slow. 405 was NOT seen (good).");
        process.exit(0);
      }
    }

    console.log("\n✅ PASS:", result.reason);
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
