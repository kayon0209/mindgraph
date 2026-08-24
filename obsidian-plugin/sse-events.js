function buildApiHeaders(apiKey) {
  const headers = { "Content-Type": "application/json" };
  const normalized = typeof apiKey === "string" ? apiKey.trim() : "";
  if (normalized) headers["X-API-Key"] = normalized;
  return headers;
}

function reduceMindGraphEvent(state, envelope) {
  const type = envelope && (envelope.event || envelope.type);
  const data = (envelope && envelope.data) || {};
  const next = {
    answer: state.answer,
    citations: state.citations,
    graphLinks: state.graphLinks,
  };

  if (type === "answer_delta" && typeof data.text === "string") {
    next.answer += data.text;
  } else if (type === "citations" || type === "completed") {
    if (Array.isArray(data.citations)) next.citations = data.citations;
    const graphLinks =
      data.graph_links || (data.retrieval_trace && data.retrieval_trace.graph_links);
    if (Array.isArray(graphLinks)) next.graphLinks = graphLinks;
    if (type === "completed" && !next.answer && typeof data.answer === "string") {
      next.answer = data.answer;
    }
  } else if (type === "error") {
    throw new Error(data.message || "stream error");
  }

  return next;
}

module.exports = { buildApiHeaders, reduceMindGraphEvent };
