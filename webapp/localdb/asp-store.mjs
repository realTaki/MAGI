/** Durable state for a small, local ASP operator. */
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { openLocalDatabase } from "./database.mjs";

const HANDLE = /^@[a-z0-9][a-z0-9_-]{0,62}\.[a-z0-9][a-z0-9_-]{0,62}$/i;

export class AspError extends Error {
  constructor(status, code, detail) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

function rows(db, sql, params = []) {
  const result = db.exec(sql, params)[0];
  return result ? result.values.map((values) => Object.fromEntries(result.columns.map((key, index) => [key, values[index]]))) : [];
}

function one(db, sql, params = []) {
  return rows(db, sql, params)[0];
}

function now() {
  return Date.now();
}

function identifier(kind) {
  return `${kind}_${randomUUID().replaceAll("-", "")}`;
}

function tokenHash(token) {
  return createHash("sha256").update(token).digest("hex");
}

function content(value) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new AspError(400, "invalid.content", "content must be a non-empty array of parts");
  }
  for (const part of value) {
    if (!part || typeof part !== "object" || typeof part.type !== "string") {
      throw new AspError(400, "invalid.content", "each content part needs a type");
    }
    if (part.type === "text" && typeof part.text !== "string") {
      throw new AspError(400, "invalid.content", "text parts need text");
    }
  }
  return value;
}

function participant(row) {
  return { handle: row.handle, status: row.status, joined_at: row.joined_at ?? undefined, left_at: row.left_at ?? undefined };
}

export async function openAspStore({ database, dataDir } = {}) {
  const ownsDatabase = !database;
  const localdb = database ?? await openLocalDatabase({ dataDir });
  const { db, databasePath } = localdb;

  db.exec(`
    CREATE TABLE IF NOT EXISTS agents (
      handle TEXT PRIMARY KEY,
      token_hash TEXT NOT NULL,
      policy TEXT NOT NULL CHECK(policy IN ('open', 'allowlist')),
      created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS allowlist (
      owner_handle TEXT NOT NULL REFERENCES agents(handle),
      peer_handle TEXT NOT NULL,
      PRIMARY KEY (owner_handle, peer_handle)
    );
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      state TEXT NOT NULL CHECK(state IN ('active', 'ended')),
      topic TEXT,
      created_at INTEGER NOT NULL,
      ended_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS participants (
      session_id TEXT NOT NULL REFERENCES sessions(id),
      handle TEXT NOT NULL REFERENCES agents(handle),
      status TEXT NOT NULL CHECK(status IN ('invited', 'joined', 'left')),
      joined_at INTEGER,
      left_at INTEGER,
      PRIMARY KEY (session_id, handle)
    );
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL REFERENCES sessions(id),
      sequence INTEGER NOT NULL,
      type TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      UNIQUE(session_id, sequence)
    );
    CREATE TABLE IF NOT EXISTS event_recipients (
      event_id TEXT NOT NULL REFERENCES events(id),
      handle TEXT NOT NULL REFERENCES agents(handle),
      PRIMARY KEY (event_id, handle)
    );
    CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, sequence);
    CREATE INDEX IF NOT EXISTS recipients_by_handle ON event_recipients(handle, event_id);
  `);

  function mutate(action) {
    return localdb.transaction(action);
  }

  function requireParticipant(sessionId, handle, statuses) {
    const session = one(db, "SELECT * FROM sessions WHERE id = ?", [sessionId]);
    const membership = one(db, "SELECT * FROM participants WHERE session_id = ? AND handle = ?", [sessionId, handle]);
    if (!session || !membership || !statuses.includes(membership.status)) {
      throw new AspError(404, "session.not_found", "session is unavailable");
    }
    return { session, membership };
  }

  function canContact(from, to) {
    for (const [owner, peer] of [[from, to], [to, from]]) {
      const agent = one(db, "SELECT policy FROM agents WHERE handle = ?", [owner]);
      if (!agent) return false;
      if (agent.policy === "allowlist" && !one(db, "SELECT 1 FROM allowlist WHERE owner_handle = ? AND peer_handle = ?", [owner, peer])) {
        return false;
      }
    }
    return true;
  }

  function append(sessionId, type, payload, recipients) {
    const sequence = Number(one(db, "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM events WHERE session_id = ?", [sessionId]).next);
    const eventPayload = type === "session.message" ? { ...payload, sequence } : payload;
    const event = { type, session_id: sessionId, event_id: identifier("evt"), sequence, created_at: now(), payload: eventPayload };
    db.run("INSERT INTO events (id, session_id, sequence, type, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)", [
      event.event_id, sessionId, sequence, type, event.created_at, JSON.stringify(eventPayload),
    ]);
    for (const handle of new Set(recipients)) {
      db.run("INSERT INTO event_recipients (event_id, handle) VALUES (?, ?)", [event.event_id, handle]);
    }
    return { event, recipients: [...new Set(recipients)] };
  }

  function joinedHandles(sessionId) {
    return rows(db, "SELECT handle FROM participants WHERE session_id = ? AND status = 'joined'", [sessionId]).map((row) => row.handle);
  }

  function activeHandles(sessionId) {
    return rows(db, "SELECT handle FROM participants WHERE session_id = ? AND status != 'left'", [sessionId]).map((row) => row.handle);
  }

  function sessionInfo(sessionId, handle) {
    requireParticipant(sessionId, handle, ["invited", "joined", "left"]);
    const session = one(db, "SELECT * FROM sessions WHERE id = ?", [sessionId]);
    return {
      id: session.id,
      state: session.state,
      topic: session.topic ?? undefined,
      participants: rows(db, "SELECT handle, status, joined_at, left_at FROM participants WHERE session_id = ? ORDER BY handle", [sessionId]).map(participant),
      created_at: session.created_at,
      ended_at: session.ended_at ?? undefined,
    };
  }

  localdb.persist();
  return {
    databasePath,

    registerAgent({ handle, policy = "open" }) {
      if (!HANDLE.test(handle) || !["open", "allowlist"].includes(policy)) {
        throw new AspError(400, "invalid.agent", "handle or policy is invalid");
      }
      return mutate(() => {
        if (one(db, "SELECT 1 FROM agents WHERE handle = ?", [handle])) {
          throw new AspError(409, "agent.exists", "handle is already registered");
        }
        const token = randomBytes(32).toString("base64url");
        db.run("INSERT INTO agents (handle, token_hash, policy, created_at) VALUES (?, ?, ?, ?)", [handle, tokenHash(token), policy, now()]);
        return { handle, token, policy };
      });
    },

    authenticate(token) {
      if (!token) throw new AspError(401, "auth.required", "Bearer token is required");
      const agent = one(db, "SELECT handle FROM agents WHERE token_hash = ?", [tokenHash(token)]);
      if (!agent) throw new AspError(401, "auth.invalid", "Bearer token is invalid");
      return agent.handle;
    },

    setPolicy(handle, policy) {
      if (!["open", "allowlist"].includes(policy)) throw new AspError(400, "invalid.policy", "policy must be open or allowlist");
      return mutate(() => db.run("UPDATE agents SET policy = ? WHERE handle = ?", [policy, handle]));
    },

    allow(handle, peer) {
      if (!HANDLE.test(peer) || !one(db, "SELECT 1 FROM agents WHERE handle = ?", [peer])) {
        throw new AspError(404, "agent.not_found", "agent is unavailable");
      }
      return mutate(() => db.run("INSERT OR IGNORE INTO allowlist (owner_handle, peer_handle) VALUES (?, ?)", [handle, peer]));
    },

    createSession(actor, { invite = [], topic, initial_message: initialMessage } = {}) {
      if (!Array.isArray(invite) || invite.some((handle) => !HANDLE.test(handle))) {
        throw new AspError(400, "invalid.invite", "invite must contain agent handles");
      }
      if (topic != null && typeof topic !== "string") throw new AspError(400, "invalid.topic", "topic must be text");
      const initialContent = initialMessage == null ? null : content(initialMessage.content);
      return mutate(() => {
        const sessionId = identifier("sess");
        db.run("INSERT INTO sessions (id, state, topic, created_at) VALUES (?, 'active', ?, ?)", [sessionId, topic ?? null, now()]);
        db.run("INSERT INTO participants (session_id, handle, status, joined_at) VALUES (?, ?, 'joined', ?)", [sessionId, actor, now()]);
        const delivery = [];
        for (const target of new Set(invite.filter((handle) => handle !== actor))) {
          if (!canContact(actor, target)) continue;
          db.run("INSERT INTO participants (session_id, handle, status) VALUES (?, ?, 'invited')", [sessionId, target]);
          delivery.push(append(sessionId, "session.invited", { session_id: sessionId, invited_by: actor, topic: topic ?? undefined, initial_message: initialContent ? { content: initialContent } : undefined }, [target]));
        }
        if (initialContent) {
          const message = { id: identifier("msg"), session_id: sessionId, sender: actor, created_at: now(), content: initialContent };
          delivery.push(append(sessionId, "session.message", message, joinedHandles(sessionId)));
        }
        return { result: { session_id: sessionId }, delivery };
      });
    },

    join(sessionId, actor) {
      return mutate(() => {
        requireParticipant(sessionId, actor, ["invited"]);
        db.run("UPDATE participants SET status = 'joined', joined_at = ? WHERE session_id = ? AND handle = ?", [now(), sessionId, actor]);
        return { result: { ok: true }, delivery: [append(sessionId, "session.joined", { handle: actor }, joinedHandles(sessionId))] };
      });
    },

    invite(sessionId, actor, invite) {
      if (!Array.isArray(invite)) throw new AspError(400, "invalid.invite", "invite must be an array");
      return mutate(() => {
        const { session } = requireParticipant(sessionId, actor, ["joined"]);
        if (session.state !== "active") throw new AspError(409, "session.ended", "session is ended");
        const invited = [];
        const delivery = [];
        for (const target of new Set(invite)) {
          if (!HANDLE.test(target) || !canContact(actor, target) || one(db, "SELECT 1 FROM participants WHERE session_id = ? AND handle = ?", [sessionId, target])) continue;
          db.run("INSERT INTO participants (session_id, handle, status) VALUES (?, ?, 'invited')", [sessionId, target]);
          invited.push(target);
          delivery.push(append(sessionId, "session.invited", { session_id: sessionId, invited_by: actor }, [target]));
        }
        return { result: { invited }, delivery };
      });
    },

    sendMessage(sessionId, actor, value) {
      const body = content(value.content);
      return mutate(() => {
        const { session } = requireParticipant(sessionId, actor, ["joined"]);
        if (session.state !== "active") throw new AspError(409, "session.ended", "session is ended");
        const message = { id: identifier("msg"), session_id: sessionId, sender: actor, created_at: now(), content: body, metadata: value.metadata ?? undefined };
        const appended = append(sessionId, "session.message", message, joinedHandles(sessionId));
        return { result: { message_id: message.id, sequence: appended.event.sequence }, delivery: [appended] };
      });
    },

    leave(sessionId, actor) {
      return mutate(() => {
        requireParticipant(sessionId, actor, ["joined"]);
        db.run("UPDATE participants SET status = 'left', left_at = ? WHERE session_id = ? AND handle = ?", [now(), sessionId, actor]);
        return { result: { ok: true }, delivery: [append(sessionId, "session.left", { handle: actor }, [actor, ...joinedHandles(sessionId)])] };
      });
    },

    end(sessionId, actor) {
      return mutate(() => {
        const { session } = requireParticipant(sessionId, actor, ["joined"]);
        if (session.state !== "active") throw new AspError(409, "session.ended", "session is ended");
        const endedAt = now();
        db.run("UPDATE sessions SET state = 'ended', ended_at = ? WHERE id = ?", [endedAt, sessionId]);
        return { result: { ok: true }, delivery: [append(sessionId, "session.ended", { ended_at: endedAt }, activeHandles(sessionId))] };
      });
    },

    getSession(sessionId, actor) {
      return sessionInfo(sessionId, actor);
    },

    events(sessionId, actor, { afterSequence = 0, limit = 100 } = {}) {
      requireParticipant(sessionId, actor, ["invited", "joined", "left"]);
      const bounded = Math.max(1, Math.min(Number(limit) || 100, 500));
      const events = rows(db, `
        SELECT e.id, e.type, e.session_id, e.sequence, e.created_at, e.payload_json
        FROM events e JOIN event_recipients r ON r.event_id = e.id
        WHERE e.session_id = ? AND r.handle = ? AND e.sequence > ?
        ORDER BY e.sequence LIMIT ?
      `, [sessionId, actor, Number(afterSequence) || 0, bounded]).map((row) => ({
        type: row.type, session_id: row.session_id, event_id: row.id, sequence: row.sequence, created_at: row.created_at, payload: JSON.parse(row.payload_json),
      }));
      return { events, next_cursor: events.length === bounded ? events.at(-1).sequence : undefined };
    },

    replay(actor, limit = 500) {
      return rows(db, `
        SELECT e.id, e.type, e.session_id, e.sequence, e.created_at, e.payload_json
        FROM events e JOIN event_recipients r ON r.event_id = e.id
        WHERE r.handle = ? ORDER BY e.created_at, e.session_id, e.sequence LIMIT ?
      `, [actor, limit]).map((row) => ({ type: row.type, session_id: row.session_id, event_id: row.id, sequence: row.sequence, created_at: row.created_at, payload: JSON.parse(row.payload_json) }));
    },

    close() {
      if (ownsDatabase) localdb.close();
    },
  };
}
