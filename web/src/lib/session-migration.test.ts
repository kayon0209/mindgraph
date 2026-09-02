/**
 * M3 会话迁移测试：显式迁移流程的纯逻辑（注入内存 storage + api stub）。
 *
 * 覆盖：待迁移检测（已迁移标记跳过）、单会话迁移（创建→导入→校验→清理正文
 * →标记）、校验失败不清理（可重试）、导入调用携带幂等键。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MIGRATION_MARKERS_KEY,
  _setStorageForTests,
  loadMigrationMarkers,
  migrateSession,
  sessionsPendingMigration,
  type LocalSession,
} from "./session-migration";
import { api } from "./api";

vi.mock("./api", () => ({
  api: {
    createConversation: vi.fn(),
    importConversationTurns: vi.fn(),
    getConversationMessages: vi.fn(),
    listConversations: vi.fn(),
  },
}));

function makeSession(id: string, turns = 1): LocalSession {
  return {
    id,
    title: `会话 ${id}`,
    turns: Array.from({ length: turns }, (_, index) => ({
      id: `${id}-t${index}`,
      question: `问题 ${index}`,
      answer: `回答 ${index} [citation-1]`,
      citations: [{ citation_id: "citation-1" }],
    })),
  };
}

/** node 环境下的内存 storage（生产走 window.localStorage） */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
    key: (index: number) => [...data.keys()][index] ?? null,
    length: 0,
  } as Storage;
}

let store: Storage;

describe("会话迁移", () => {
  beforeEach(() => {
    store = memoryStorage();
    _setStorageForTests(store);
    vi.mocked(api.createConversation).mockReset();
    vi.mocked(api.importConversationTurns).mockReset();
    vi.mocked(api.getConversationMessages).mockReset();
  });
  afterEach(() => {
    _setStorageForTests(undefined);
  });

  it("检测待迁移会话：空轮次与已迁移标记的都跳过", () => {
    store.setItem(
      MIGRATION_MARKERS_KEY,
      JSON.stringify({ done: { conversationId: "conv-1", migratedAt: "2026-09-03T00:00:00Z" } }),
    );
    const pending = sessionsPendingMigration([makeSession("done"), makeSession("empty", 0), makeSession("todo")]);
    expect(pending.map((item) => item.id)).toEqual(["todo"]);
  });

  it("单会话迁移：创建→导入→校验→清理正文→保留标记", async () => {
    const session = makeSession("s1", 2);
    store.setItem("mindgraph.chat.sessions", JSON.stringify([{ id: "s1", title: "会话 s1" }]));
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-9", title: "会话 s1", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 2, skipped_existing: 0, mapping: [], total_messages: 4 });
    vi.mocked(api.getConversationMessages).mockResolvedValue(
      Array.from({ length: 4 }, (_, i) => ({ message_id: `m${i}`, sequence_no: i + 1, role: i % 2 === 0 ? "user" : "assistant", content: "", created_at: "" })),
    );

    const result = await migrateSession(session);

    expect(result.verified).toBe(true);
    expect(result.imported).toBe(2);
    // 本地正文清理：SESSIONS_KEY 里该会话被移除
    const stored = JSON.parse(store.getItem("mindgraph.chat.sessions") ?? "[]") as Array<{ id: string }>;
    expect(stored).toHaveLength(0);
    // 迁移标记保留（证明 + 幂等跳过依据）
    const markers = loadMigrationMarkers();
    expect(markers.s1.conversationId).toBe("conv-9");
    // 之后不再被列为待迁移
    expect(sessionsPendingMigration([session])).toHaveLength(0);
  });

  it("校验失败不清理正文（可重试）", async () => {
    const session = makeSession("s2", 3);
    store.setItem("mindgraph.chat.sessions", JSON.stringify([{ id: "s2", title: "会话 s2" }]));
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-x", title: "", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 3, skipped_existing: 0, mapping: [], total_messages: 6 });
    // 校验失败：返回消息数与期望（3×2=6）不符
    vi.mocked(api.getConversationMessages).mockResolvedValue([]);

    const result = await migrateSession(session);

    expect(result.verified).toBe(false);
    const stored = JSON.parse(store.getItem("mindgraph.chat.sessions") ?? "[]") as Array<{ id: string }>;
    expect(stored).toHaveLength(1); // 本地正文保留
    expect(loadMigrationMarkers().s2).toBeUndefined(); // 未标记
    expect(sessionsPendingMigration([session])).toHaveLength(1); // 仍可重试
  });

  it("导入调用携带 local_turn_id（服务端幂等键）", async () => {
    const session = makeSession("s3", 1);
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-3", title: "", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 1, skipped_existing: 0, mapping: [], total_messages: 2 });
    vi.mocked(api.getConversationMessages).mockResolvedValue(
      [{ message_id: "m1", sequence_no: 1, role: "user", content: "", created_at: "" }, { message_id: "m2", sequence_no: 2, role: "assistant", content: "", created_at: "" }],
    );
    await migrateSession(session);
    const [conversationId, turns] = vi.mocked(api.importConversationTurns).mock.calls[0];
    expect(conversationId).toBe("conv-3");
    expect((turns as Array<{ local_turn_id: string }>)[0].local_turn_id).toBe("s3-t0");
  });
});
