/**
 * M3 会话迁移测试（方案 §8.2 修订版：迁移保留本地副本，删除是单独确认）。
 *
 * 覆盖：待迁移检测（已迁移标记跳过）、单会话迁移（创建→导入→校验→标记
 * 且本地正文保留）、校验失败不标记（可重试）、request_id 配对校验、
 * 删除本地副本为独立动作（幂等、标记更新）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MIGRATION_MARKERS_KEY,
  _setStorageForTests,
  confirmDeleteLocalSession,
  loadMigrationMarkers,
  migrateSession,
  migratedSessionsWithLocalCopy,
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

/** 服务端消息回放（request_id = local_turn_id:q / :a 成对） */
function serverMessages(turns: Array<{ local_turn_id: string }>, factor = 2) {
  const messages: Array<{ message_id: string; sequence_no: number; role: string; content: string; created_at: string; request_id?: string }> = [];
  turns.forEach((turn, index) => {
    messages.push({ message_id: `q${index}`, sequence_no: index * 2 + 1, role: "user", content: "", created_at: "", request_id: `${turn.local_turn_id}:q` });
    messages.push({ message_id: `a${index}`, sequence_no: index * 2 + 2, role: "assistant", content: "", created_at: "", request_id: `${turn.local_turn_id}:a` });
  });
  return factor === 0 ? [] : messages;
}

describe("会话迁移（§8.2 修订：保留本地副本）", () => {
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
      JSON.stringify({ done: { conversationId: "conv-1", migratedAt: "2026-09-03T00:00:00Z", localCopyRetained: true } }),
    );
    const pending = sessionsPendingMigration([makeSession("done"), makeSession("empty", 0), makeSession("todo")]);
    expect(pending.map((item) => item.id)).toEqual(["todo"]);
  });

  it("迁移成功：记标记且本地正文保留（删除是单独动作）", async () => {
    const session = makeSession("s1", 2);
    store.setItem("mindgraph.chat.sessions", JSON.stringify([{ id: "s1", title: "会话 s1" }]));
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-9", title: "会话 s1", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 2, skipped_existing: 0, mapping: [], total_messages: 4 });
    vi.mocked(api.getConversationMessages).mockResolvedValue(serverMessages([{ local_turn_id: "s1-t0" }, { local_turn_id: "s1-t1" }]) as never);

    const result = await migrateSession(session);

    expect(result.verified).toBe(true);
    expect(result.imported).toBe(2);
    // 本地正文保留（修订版核心语义：不自动清空）
    const stored = JSON.parse(store.getItem("mindgraph.chat.sessions") ?? "[]") as Array<{ id: string }>;
    expect(stored).toHaveLength(1);
    // 标记记录 localCopyRetained
    const markers = loadMigrationMarkers();
    expect(markers.s1.conversationId).toBe("conv-9");
    expect(markers.s1.localCopyRetained).toBe(true);
    // 已迁移 → 不再出现在待迁移；出现在"本地副本仍在"列表
    expect(sessionsPendingMigration([session])).toHaveLength(0);
    expect(migratedSessionsWithLocalCopy([session])).toHaveLength(1);
  });

  it("删除本地副本：单独动作、幂等、标记更新", () => {
    store.setItem("mindgraph.chat.sessions", JSON.stringify([{ id: "s1" }, { id: "s2" }]));
    store.setItem(
      MIGRATION_MARKERS_KEY,
      JSON.stringify({ s1: { conversationId: "c1", migratedAt: "t", localCopyRetained: true } }),
    );
    confirmDeleteLocalSession("s1");
    confirmDeleteLocalSession("s1"); // 幂等
    const stored = JSON.parse(store.getItem("mindgraph.chat.sessions") ?? "[]") as Array<{ id: string }>;
    expect(stored.map((item) => item.id)).toEqual(["s2"]);
    expect(loadMigrationMarkers().s1.localCopyRetained).toBe(false);
    expect(migratedSessionsWithLocalCopy([{ id: "s1", title: "x", turns: [] }])).toHaveLength(0);
  });

  it("校验失败（轮次数不符）：不标记、正文保留、可重试", async () => {
    const session = makeSession("s2", 3);
    store.setItem("mindgraph.chat.sessions", JSON.stringify([{ id: "s2", title: "会话 s2" }]));
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-x", title: "", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 3, skipped_existing: 0, mapping: [], total_messages: 6 });
    vi.mocked(api.getConversationMessages).mockResolvedValue([]); // 消息数不符

    const result = await migrateSession(session);

    expect(result.verified).toBe(false);
    const stored = JSON.parse(store.getItem("mindgraph.chat.sessions") ?? "[]") as Array<{ id: string }>;
    expect(stored).toHaveLength(1);
    expect(loadMigrationMarkers().s2).toBeUndefined();
    expect(sessionsPendingMigration([session])).toHaveLength(1);
  });

  it("request_id 配对校验：缺 :a 的轮次判定失败", async () => {
    const session = makeSession("s3", 1);
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-3", title: "", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 1, skipped_existing: 0, mapping: [], total_messages: 2 });
    // 数量对但 request_id 不成对（只有 :q 没有 :a）
    vi.mocked(api.getConversationMessages).mockResolvedValue([
      { message_id: "m1", sequence_no: 1, role: "user", content: "", created_at: "", request_id: "s3-t0:q" },
      { message_id: "m2", sequence_no: 2, role: "assistant", content: "", created_at: "", request_id: "wrong-id" },
    ] as never);

    const result = await migrateSession(session);
    expect(result.verified).toBe(false); // 配对失败不标记
  });

  it("导入调用携带 local_turn_id（服务端幂等键）", async () => {
    const session = makeSession("s4", 1);
    vi.mocked(api.createConversation).mockResolvedValue({ conversation_id: "conv-4", title: "", status: "active", created_at: "", updated_at: "" });
    vi.mocked(api.importConversationTurns).mockResolvedValue({ imported: 1, skipped_existing: 0, mapping: [], total_messages: 2 });
    vi.mocked(api.getConversationMessages).mockResolvedValue(serverMessages([{ local_turn_id: "s4-t0" }]) as never);
    await migrateSession(session);
    const [conversationId, turns] = vi.mocked(api.importConversationTurns).mock.calls[0];
    expect(conversationId).toBe("conv-4");
    expect((turns as Array<{ local_turn_id: string }>)[0].local_turn_id).toBe("s4-t0");
  });
});
