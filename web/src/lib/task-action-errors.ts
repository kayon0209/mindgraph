import { ApiError } from "./api";

export type TaskAction = "submit" | "detail" | "cancel" | "artifact";

export type TaskActionOutcome<T> =
  | { ok: true; value: T }
  | { ok: false; message: string };

const GENERIC_MESSAGES: Record<TaskAction, string> = {
  submit: "提交后台核对任务未完成，请检查网络后重试。",
  detail: "读取任务详情失败，请稍后重试。",
  cancel: "取消任务未完成，请检查网络后重试。",
  artifact: "读取证据包失败，请稍后重试。",
};

export function taskActionErrorMessage(action: TaskAction, error: unknown): string {
  if (error instanceof ApiError && error.status === 409 && action === "submit") {
    return "相同的后台核对任务已在处理中；请稍后刷新任务列表。";
  }
  if (error instanceof ApiError && error.status === 403 && action === "artifact") {
    return "无法查看该证据包：你可能没有该任务的访问权限。";
  }
  return GENERIC_MESSAGES[action];
}

export async function executeTaskAction<T>(
  action: TaskAction,
  operation: () => Promise<T>,
): Promise<TaskActionOutcome<T>> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    return { ok: false, message: taskActionErrorMessage(action, error) };
  }
}
