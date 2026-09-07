/**
 * 引导面板纯函数测试（UI onboarding 升级）。
 *
 * 5 任务可用性脚本（docs/ui/AGENT-UI-DESIGN-SPEC.md §7）→ 空态引导的
 * 映射关系在这里锁定：每个任务都必须有一条"一键可走"的引导路径
 * （快捷问题或引导文案指向的具体操作），不依赖用户自己发现。
 */

import { describe, expect, it } from "vitest";

import {
  buildGuidedTasks,
  type GuidedTask,
} from "./onboarding-tasks";

describe("空态引导 → 5 任务可用性脚本覆盖", () => {
  it("每个脚本任务都有引导项与一键问题/操作", () => {
    const tasks = buildGuidedTasks();
    expect(tasks).toHaveLength(5);
    for (const task of tasks) {
      expect(task.starterQuestion || task.actionHint).toBeTruthy();
      expect(task.description.length).toBeGreaterThan(6);
    }
  });

  it("五个脚本任务全部被映射（不重不漏）", () => {
    const tasks = buildGuidedTasks();
    const covered = new Set(tasks.map((t: GuidedTask) => t.scriptId));
    expect(covered).toEqual(
      new Set(["find-source", "judge-version", "understand-refusal", "clarify-resume", "export-evidence"]),
    );
  });

  it("示例问题具备可提交形态（非空、非超长）", () => {
    for (const task of buildGuidedTasks()) {
      if (task.starterQuestion) {
        expect(task.starterQuestion.trim().length).toBeGreaterThan(4);
        expect(task.starterQuestion.length).toBeLessThan(2000);
      }
    }
  });
});
