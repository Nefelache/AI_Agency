#!/usr/bin/env node
/**
 * Agent OS — WhatsApp Bridge (Baileys)
 *
 * OpenClaw-style: QR code login, no Meta Business API needed.
 * Forwards incoming messages to Agent OS API, sends replies back.
 *
 * Env:
 *   AGENT_OS_URL     - e.g. http://127.0.0.1:8000
 *   AGENT_OS_SECRET  - X-WhatsApp-Secret or use API_KEY_CHANNEL as X-API-Key
 *   AGENT_OS_API_KEY - Alternative: X-API-Key (channel key)
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import pino from "pino";
import qrcode from "qrcode-terminal";

const AGENT_OS_URL = process.env.AGENT_OS_URL || "http://127.0.0.1:8000";
const AGENT_OS_SECRET = process.env.AGENT_OS_SECRET || "";
const AGENT_OS_API_KEY = process.env.AGENT_OS_API_KEY || "";

const LOG_LEVEL = process.env.LOG_LEVEL || "silent";
const HEARTBEAT_SECONDS = Number(process.env.HEARTBEAT_SECONDS || "30");

const logger = pino({ level: LOG_LEVEL });

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (AGENT_OS_SECRET) {
    headers["X-WhatsApp-Secret"] = AGENT_OS_SECRET;
  } else if (AGENT_OS_API_KEY) {
    headers["X-API-Key"] = AGENT_OS_API_KEY;
  }
  return headers;
}

async function callAgentOS(payload) {
  const url = `${AGENT_OS_URL.replace(/\/$/, "")}/whatsapp/inbound`;
  const res = await fetch(url, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Agent OS API ${res.status}: ${text}`);
  }
  return res.json();
}

async function heartbeat() {
  try {
    await fetch(`${AGENT_OS_URL.replace(/\/$/, "")}/health/whatsapp`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
  } catch {
    // best-effort only
  }
}

export function extractMessageText(msg) {
  const m = msg.message;
  if (!m) return "";
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.documentMessage?.caption ||
    ""
  ).trim();
}

export function jidToPhone(jid) {
  return jid.replace(/@s\.whatsapp\.net/, "").replace(/@g\.us/, "");
}

async function startOnce() {
  const { state, saveCreds } = await useMultiFileAuthState("./auth");

  const sock = makeWASocket({
    auth: state,
    // WhatsApp Web version — expires ~2mo; update from https://wppconnect.io/whatsapp-versions/
    version: [2, 3000, 1035023383],
    printQRInTerminal: false,
    logger,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { qr, connection, lastDisconnect } = update;

    if (qr) {
      console.log("\n📱 Scan this QR code with WhatsApp to link:\n");
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      console.log("✅ WhatsApp linked. You can now message the agent.\n");
    }
    if (connection === "close") {
      const statusCode = (lastDisconnect?.error instanceof Boom
        ? lastDisconnect.error
        : lastDisconnect?.error
      )?.output?.statusCode;
      if (statusCode !== DisconnectReason.loggedOut) {
        // Let the outer loop restart us
        logger.info({ statusCode }, "connection closed; will reconnect");
      } else {
        console.log("Logged out. Delete ./auth and run again to re-link.");
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    for (const msg of messages) {
      if (msg.key.fromMe) continue;
      if (type !== "notify") continue;

      const text = extractMessageText(msg);
      if (!text) continue;

      const jid = msg.key.remoteJid;
      const isGroup = jid?.endsWith("@g.us");
      const from = msg.key.participant || jid;

      const payload = {
        from_number: jidToPhone(from),
        from_name: null,
        message: text,
        message_id: msg.key.id,
        is_group: isGroup,
        group_id: isGroup ? jid : null,
        group_name: null,
      };

      try {
        const result = await callAgentOS(payload);
        if (result.action === "reply" && result.reply) {
          const chunks = Array.isArray(result.reply) ? result.reply : [result.reply];
          for (const chunk of chunks) {
            await sock.sendMessage(jid, { text: chunk });
          }
        } else if (result.action === "pairing_required") {
          await sock.sendMessage(jid, {
            text: "Pairing required. Contact the owner to get a pairing code.",
          });
        }
      } catch (err) {
        console.error("Agent OS error:", err.message);
        await sock.sendMessage(jid, {
          text: "Sorry, I couldn't process that. Please try again.",
        });
      }
    }
  });

  return sock;
}

async function run() {
  let attempt = 0;
  let hbTimer = null;

  while (true) {
    attempt += 1;
    try {
      const sock = await startOnce();
      if (!hbTimer) {
        hbTimer = setInterval(() => heartbeat(), Math.max(5, HEARTBEAT_SECONDS) * 1000);
        hbTimer.unref?.();
      }

      // Wait until connection closes
      await new Promise((resolve) => {
        sock.ev.on("connection.update", (u) => {
          if (u.connection === "close") resolve(true);
        });
      });
    } catch (e) {
      logger.warn({ err: String(e) }, "bridge run failed");
    }

    // Exponential backoff with cap
    const delayMs = Math.min(60000, 1000 * 2 ** Math.min(6, attempt));
    logger.info({ delayMs }, "restarting after backoff");
    await new Promise((r) => setTimeout(r, delayMs));
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
