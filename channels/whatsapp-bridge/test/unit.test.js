/**
 * Unit tests for WhatsApp Bridge
 * Run: npm test
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { createRequire } from "node:module";
import { extractMessageText, jidToPhone } from "../index.js";

const require = createRequire(import.meta.url);
const pkg = require("@whiskeysockets/baileys/package.json");

describe("Baileys version", () => {
  it("must be 6.7.21 to avoid 405 Connection Failure", () => {
    assert.strictEqual(
      pkg.version,
      "6.7.21",
      `Expected Baileys 6.7.21, got ${pkg.version}. 405 errors occur with 6.7.8 and other versions.`
    );
  });
});

describe("extractMessageText", () => {
  it("extracts conversation text", () => {
    assert.strictEqual(
      extractMessageText({ message: { conversation: "hello" } }),
      "hello"
    );
  });

  it("extracts extendedTextMessage", () => {
    assert.strictEqual(
      extractMessageText({
        message: { extendedTextMessage: { text: "long message" } },
      }),
      "long message"
    );
  });

  it("extracts image caption", () => {
    assert.strictEqual(
      extractMessageText({
        message: { imageMessage: { caption: "photo caption" } },
      }),
      "photo caption"
    );
  });

  it("returns empty for missing message", () => {
    assert.strictEqual(extractMessageText({}), "");
    assert.strictEqual(extractMessageText({ message: null }), "");
  });

  it("trims whitespace", () => {
    assert.strictEqual(
      extractMessageText({ message: { conversation: "  hi  " } }),
      "hi"
    );
  });
});

describe("jidToPhone", () => {
  it("strips @s.whatsapp.net", () => {
    assert.strictEqual(
      jidToPhone("8613800138000@s.whatsapp.net"),
      "8613800138000"
    );
  });

  it("handles group jid", () => {
    assert.strictEqual(
      jidToPhone("120363xxx@g.us"),
      "120363xxx"
    );
  });

  it("handles participant jid", () => {
    assert.strictEqual(
      jidToPhone("8613800138000@s.whatsapp.net"),
      "8613800138000"
    );
  });
});
