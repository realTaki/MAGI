import { constants, generateKeyPairSync, privateDecrypt, publicEncrypt } from "node:crypto";
import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export type Identity = { publicKey: string; privateKey: string };

const OAEP = { padding: constants.RSA_PKCS1_OAEP_PADDING, oaepHash: "sha256" } as const;

/**
 * This MAGI's keypair. Created the first time the workspace opens.
 * The private key stays in the workspace file. The public key is what ASP
 * stores on the agent profile.
 */
export function loadOrCreateIdentity(workspace: string): Identity {
  const file = join(workspace, "identity.json");
  if (existsSync(file)) {
    const parsed: unknown = JSON.parse(readFileSync(file, "utf8"));
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("identity.json is not an object");
    }
    const { publicKey, privateKey } = parsed as { publicKey?: unknown; privateKey?: unknown };
    if (typeof publicKey !== "string" || typeof privateKey !== "string" || publicKey === "" || privateKey === "") {
      throw new Error("identity.json is missing a keypair");
    }
    return { publicKey, privateKey };
  }
  const generated = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  const identity: Identity = { publicKey: generated.publicKey, privateKey: generated.privateKey };
  writeFileSync(file, `${JSON.stringify(identity, null, 2)}\n`, { mode: 0o600 });
  try { chmodSync(file, 0o600); } catch { /* Windows has no POSIX mode bits. */ }
  return identity;
}

/** Ciphertext for a recipient's public key. The plaintext never leaves this process. */
export function encryptFor(publicKey: string, plaintext: string): string {
  return publicEncrypt({ key: publicKey, ...OAEP }, Buffer.from(plaintext, "utf8")).toString("base64");
}

export function decryptWith(privateKey: string, ciphertext: string): string {
  return privateDecrypt({ key: privateKey, ...OAEP }, Buffer.from(ciphertext, "base64")).toString("utf8");
}
