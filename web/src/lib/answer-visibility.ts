import type { AssistIntegrity } from "../types";

type AnswerVisibilityState = {
  citationIntegrity?: AssistIntegrity | null;
  resultState?: string | null;
};

/** Fail closed: evidence-only and unresolved-conflict turns never render model prose. */
export function shouldSuppressAnswer(turn: AnswerVisibilityState): boolean {
  return (
    (turn.citationIntegrity?.applicable === true && turn.citationIntegrity.passed === false)
    || turn.resultState === "conflicting_evidence"
  );
}
