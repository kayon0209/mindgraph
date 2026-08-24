const test = require("node:test");
const assert = require("node:assert/strict");

const { buildApiHeaders, reduceMindGraphEvent } = require("./sse-events");

test("adds an API key header only when the user supplied one", () => {
  assert.deepEqual(buildApiHeaders(""), { "Content-Type": "application/json" });
  assert.deepEqual(buildApiHeaders("  secret-key  "), {
    "Content-Type": "application/json",
    "X-API-Key": "secret-key",
  });
});

test("reduces backend SSE envelopes without reading fields from the envelope root", () => {
  let state = { answer: "", citations: [], graphLinks: [] };
  state = reduceMindGraphEvent(state, {
    event: "answer_delta",
    data: { text: "第一段" },
  });
  state = reduceMindGraphEvent(state, {
    event: "citations",
    data: { citations: [{ document_name: "制度 A" }] },
  });
  state = reduceMindGraphEvent(state, {
    event: "completed",
    data: {
      citations: [{ document_name: "制度 A" }],
      retrieval_trace: {
        graph_links: [{ source_title: "A", target_title: "B" }],
      },
    },
  });

  assert.deepEqual(state, {
    answer: "第一段",
    citations: [{ document_name: "制度 A" }],
    graphLinks: [{ source_title: "A", target_title: "B" }],
  });
});

test("throws the backend error message", () => {
  assert.throws(
    () =>
      reduceMindGraphEvent(
        { answer: "", citations: [], graphLinks: [] },
        { event: "error", data: { message: "provider unavailable" } },
      ),
    /provider unavailable/,
  );
});
