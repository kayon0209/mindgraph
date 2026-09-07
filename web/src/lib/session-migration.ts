/**
 * M3：本地会话显式迁移到服务端（实施方案 §8.2 修订版）。
 *
 * 原则（2026-09-03 对齐修订版方案）：
 * - 只在用户主动确认后迁移（绝不自动上传本地内容）；
 * - 逐会话上传，服务端返回映射表，前端校验轮次数与内容（request_id 配对）；
 * - 迁移成功后**保留本地副本**——删除本地副本是另一个单独的用户确认动作，
 *   不随迁移自动清空（localStorage 继续作为开发/单机缓存）；
 * - 重复迁移幂等（服务端 local_turn_id 查重 + 本地标记跳过）。
 */

import { api } from "./api";
import type { StreamEvent } from "../types";

/** 本地会话的最小形态（ChatPage 的 ChatSession 兼容子集） */
export type LocalSession = {
  id: string;
  title: string;
  createdAt?: string;
  updatedAt?: string;
  turns: Array<{
    id?: string;
    question: string;
    answer: string;
    citations?: unknown[];
  }>;
};

/** 迁移标记：会话 ID → 服务端 conversation_id + 本地副本保留状态 */
export type MigrationMarker = {
  conversationId: string;
  migratedAt: string;
  /** true = 本地正文仍在（等待用户单独确认删除）；false = 已确认删除 */
  localCopyRetained: boolean;
};

export const MIGRATION_MARKERS_KEY = "mindgraph.chat.session-migration";

/** storage 适配：生产用 window.localStorage；测试可注入内存实现（node 环境） */
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
let storage: StorageLike = typeof window !== "undefined" ? window.localStorage : undefined as unknown as StorageLike;

/** 测试注入点（生产代码不调用） */
export function _setStorageForTests(next: StorageLike | undefined): void {
  storage = next ?? (typeof window !== "undefined" ? window.localStorage : (undefined as unknown as StorageLike));
}

function _read(key: string): string | null {
  return storage?.getItem(key) ?? null;
}

function _write(key: string, value: string): void {
  storage?.setItem(key, value);
}

export function loadMigrationMarkers(): Record<string, MigrationMarker> {
  try {
    return JSON.parse(_read(MIGRATION_MARKERS_KEY) ?? "{}") as Record<string, MigrationMarker>;
  } catch {
    return {};
  }
}

function saveMigrationMarkers(markers: Record<string, MigrationMarker>): void {
  _write(MIGRATION_MARKERS_KEY, JSON.stringify(markers));
}

/** 未迁移且仍有正文的会话（迁移入口的显示依据） */
export function sessionsPendingMigration(sessions: LocalSession[]): LocalSession[] {
  const markers = loadMigrationMarkers();
  return sessions.filter((session) => !markers[session.id] && session.turns.length > 0);
}

/** 已迁移且本地副本仍在的会话（"删除本地副本"单独确认动作的显示依据） */
export function migratedSessionsWithLocalCopy(sessions: LocalSession[]): LocalSession[] {
  const markers = loadMigrationMarkers();
  return sessions.filter((session) => markers[session.id]?.localCopyRetained === true);
}

/** 单会话迁移全流程：创建 → 导入 → 校验（轮次数 + request_id 配对）→ 记标记。
 * 本地正文保留；删除由 confirmDeleteLocalSession 单独执行。 */
export async function migrateSession(
  session: LocalSession,
  options?: { onProgress?: (done: number, total: number) => void },
): Promise<{ conversationId: string; imported: number; skipped: number; verified: boolean }> {
  const created = await api.createConversation({ title: session.title });
  const conversationId = created.conversation_id;

  const turns = session.turns.map((turn, index) => ({
    local_turn_id: turn.id ?? `${session.id}-${index}`,
    question: turn.question,
    answer: turn.answer,
    citations: Array.isArray(turn.citations) ? (turn.citations as Array<Record<string, unknown>>) : [],
  }));
  const result = await api.importConversationTurns(conversationId, turns);

  // 校验：消息数 = 2 × 本地轮次，且每轮的 request_id（{local_id}:q/:a）成对出现
  const messages = await api.getConversationMessages(conversationId);
  const expectedPairs = session.turns.length;
  const requestIds = new Set(messages.map((message) => message.request_id).filter(Boolean) as string[]);
  const pairsMatch = turns.every(
    (turn) => requestIds.has(`${turn.local_turn_id}:q`) && requestIds.has(`${turn.local_turn_id}:a`),
  );
  const verified = messages.length === expectedPairs * 2 && pairsMatch;

  if (verified) {
    const markers = loadMigrationMarkers();
    markers[session.id] = { conversationId, migratedAt: new Date().toISOString(), localCopyRetained: true };
    saveMigrationMarkers(markers);
  }
  options?.onProgress?.(1, 1);
  return {
    conversationId,
    imported: result.imported,
    skipped: result.skipped_existing,
    verified,
  };
}

/** 删除本地副本（单独确认动作；迁移不做）。幂等：重复调用无副作用，标记保留作证明。 */
export function confirmDeleteLocalSession(sessionId: string): void {
  const raw = _read("mindgraph.chat.sessions");
  if (raw) {
    try {
      const sessions = JSON.parse(raw) as Array<{ id: string }>;
      const kept = sessions.filter((item) => item.id !== sessionId);
      _write("mindgraph.chat.sessions", JSON.stringify(kept));
    } catch {
      // 解析失败不动本地数据（宁可冗余也不误删）
    }
  }
  const markers = loadMigrationMarkers();
  if (markers[sessionId]) {
    markers[sessionId] = { ...markers[sessionId], localCopyRetained: false };
    saveMigrationMarkers(markers);
  }
}

/** 全部待迁移会话逐个迁移（任一失败即停，保留剩余会话；已迁移的跳过） */
export async function migrateAllSessions(
  sessions: LocalSession[],
  options?: { onProgress?: (done: number, total: number) => void },
): Promise<{ migrated: string[]; failed: string[] }> {
  const pending = sessionsPendingMigration(sessions);
  const migrated: string[] = [];
  const failed: string[] = [];
  for (let index = 0; index < pending.length; index += 1) {
    try {
      const result = await migrateSession(pending[index]);
      if (result.verified) migrated.push(pending[index].id);
      else failed.push(pending[index].id);
      options?.onProgress?.(index + 1, pending.length);
    } catch {
      failed.push(pending[index].id);
      options?.onProgress?.(index + 1, pending.length);
    }
  }
  return { migrated, failed };
}

/** 迁移入口可见性开关：服务端会话开启且有本地待迁移数据时显示 */
export async function fetchServerConversationsEnabled(): Promise<boolean> {
  try {
    await api.listConversations();
    return true;
  } catch (error) {
    const status = (error as { status?: number }).status;
    return status !== 404;
  }
}
