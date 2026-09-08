import { describe, expect, it } from "vitest";

import { executeTaskAction, taskActionErrorMessage } from "./task-action-errors";
import { ApiError } from "./api";

describe("task action failure guidance", () => {
  it("makes an idempotency conflict actionable instead of exposing a raw API error", () => {
    expect(taskActionErrorMessage("submit", new ApiError("conflict", 409))).toBe(
      "相同的后台核对任务已在处理中；请稍后刷新任务列表。",
    );
  });

  it("keeps artifact authorization failures private and actionable", () => {
    expect(taskActionErrorMessage("artifact", new ApiError("forbidden", 403))).toBe(
      "无法查看该证据包：你可能没有该任务的访问权限。",
    );
  });

  it("gives a retry path for an unavailable task API", () => {
    expect(taskActionErrorMessage("cancel", new Error("network offline"))).toBe(
      "取消任务未完成，请检查网络后重试。",
    );
  });

  it("converts a rejected task API operation into a controlled failure result", async () => {
    await expect(
      executeTaskAction("detail", async () => {
        throw new ApiError("not found", 404);
      }),
    ).resolves.toEqual({ ok: false, message: "读取任务详情失败，请稍后重试。" });
  });
});
