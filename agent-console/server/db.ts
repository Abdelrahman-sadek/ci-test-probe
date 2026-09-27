import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { AgentEvent, AgentState, EventSink } from "@agent-farmework/core";
import { SqliteRunStateStore, openSqlite, type SqliteDatabase } from "@agent-farmework/production";
import type { AuditSink, ToolAuditRecord } from "@agent-farmework/tools";

/** One SQLite file holds run state (framework store), events and the tool audit log. */
export async function openDatabase(path: string) {
  if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
  const db = await openSqlite(path);
  const runs = new SqliteRunStateStore(db);
  db.exec(`
    CREATE TABLE IF NOT EXISTS events (
      event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence INTEGER NOT NULL, type TEXT NOT NULL, event TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_run_idx ON events (run_id, sequence);
    CREATE TABLE IF NOT EXISTS audit (
      audit_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, recorded_at TEXT NOT NULL, record TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS audit_run_idx ON audit (run_id, recorded_at);
  `);
  return { db, runs, events: new EventLog(db), audit: new SqliteAuditSink(db), queries: new RunQueries(db) };
}

type Listener = (event: AgentEvent) => void;

/** Persists every runtime event and fans it out to live subscribers (SSE). */
export class EventLog implements EventSink {
  private readonly listeners = new Map<string, Set<Listener>>();
  constructor(private readonly db: SqliteDatabase) {}

  emit(event: AgentEvent): void {
    this.db
      .prepare("INSERT OR IGNORE INTO events (event_id, run_id, sequence, type, event) VALUES (?, ?, ?, ?, ?)")
      .run(event.eventId, event.runId, event.sequence, event.type, JSON.stringify(event));
    for (const listener of this.listeners.get(event.runId) ?? []) listener(event);
  }

  forRun(runId: string, afterSequence = 0): AgentEvent[] {
    const rows = this.db.prepare("SELECT event FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence").all(runId, afterSequence) as { event: string }[];
    return rows.map((r) => JSON.parse(r.event) as AgentEvent);
  }

  subscribe(runId: string, listener: Listener): () => void {
    const set = this.listeners.get(runId) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(runId, set);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.listeners.delete(runId);
    };
  }
}

export class SqliteAuditSink implements AuditSink {
  constructor(private readonly db: SqliteDatabase) {}
  record(entry: ToolAuditRecord): void {
    this.db.prepare("INSERT OR IGNORE INTO audit (audit_id, run_id, recorded_at, record) VALUES (?, ?, ?, ?)").run(entry.auditId, entry.runId, entry.recordedAt, JSON.stringify(entry));
  }
  forRun(runId: string): ToolAuditRecord[] {
    const rows = this.db.prepare("SELECT record FROM audit WHERE run_id = ? ORDER BY recorded_at").all(runId) as { record: string }[];
    return rows.map((r) => JSON.parse(r.record) as ToolAuditRecord);
  }
}

/** Read models over the framework's `agent_runs` table. */
export class RunQueries {
  constructor(private readonly db: SqliteDatabase) {}
  list(limit = 100): AgentState[] {
    const rows = this.db.prepare("SELECT state FROM agent_runs ORDER BY created_at DESC LIMIT ?").all(limit) as { state: string }[];
    return rows.map((r) => JSON.parse(r.state) as AgentState);
  }
  waiting(): AgentState[] {
    const rows = this.db.prepare("SELECT state FROM agent_runs WHERE status = 'WAITING_FOR_APPROVAL' ORDER BY updated_at").all() as { state: string }[];
    return rows.map((r) => JSON.parse(r.state) as AgentState);
  }
}

export type Database = Awaited<ReturnType<typeof openDatabase>>;
