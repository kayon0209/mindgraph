/**
 * 空态引导任务定义：5 任务可用性脚本 → 引导面板的映射（唯一后端无关来源）。
 *
 * 设计对齐 docs/ui/AGENT-UI-DESIGN-SPEC.md §7 的脚本化任务：
 * 1. 找到一条结论对应的原文；
 * 2. 判断引用版本是否现行；
 * 3. 理解为什么系统停止回答（版本冲突 fail-closed）；
 * 4. 完成一次澄清后继续；
 * 5. 导出可复核证据包。
 *
 * 每个任务必须有 starterQuestion（一键提交的示例问题）或 actionHint
 * （指向具体操作的文案）——空态不让用户"自己发现能力"。
 */

export type GuidedTask = {
  /** 脚本任务标识（冻结，测试锁定五个齐全） */
  scriptId: string;
  title: string;
  description: string;
  /** 一键提交的示例问题（点击即走完整流程） */
  starterQuestion?: string;
  /** 无示例问题时的操作指引（如"回答完成后点导出"） */
  actionHint?: string;
};

export function buildGuidedTasks(): GuidedTask[] {
  return [
    {
      scriptId: "find-source",
      title: "看到结论，点它旁边的数字角标",
      description: "每个结论都标注 [引用-N]；点击角标，右侧「回答依据」会定位到制度原文。",
      starterQuestion: "差旅餐补的标准是多少？",
      actionHint: "提问后点答案里的角标试试。",
    },
    {
      scriptId: "judge-version",
      title: "检查引用的版本是不是现行",
      description: "证据卡上有版本徽标（现行/草稿/失效）与生效日期，一眼判断时效。",
      starterQuestion: "2026 年 8 月发生的费用，适用哪一版差旅标准？",
    },
    {
      scriptId: "understand-refusal",
      title: "系统为什么会拒绝回答",
      description: "同一制度存在多个同时生效的版本时，系统会停止回答并列出冲突版本，而不是猜一个。",
      starterQuestion: "报销 v1 和 v2 哪个适用？", // 路由到版本核对/冲突路径
    },
    {
      scriptId: "clarify-resume",
      title: "补充一句，就能继续",
      description: "问题不够明确时会出现补充信息卡；填写提交后系统带着补充继续查，不用重问。",
      starterQuestion: "餐补和招待费可以同时报吗？", // 多义问题触发澄清
    },
    {
      scriptId: "export-evidence",
      title: "把证据打包带走",
      description: "回答完成后点「导出证据」：问题、结论、引用、版本、时间戳进一个 Markdown，可直接交给制度责任人复核。",
      actionHint: "任一回答的下方就有导出按钮。",
    },
  ];
}
