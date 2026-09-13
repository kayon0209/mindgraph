/**
 * 上传链路的纯函数部分：不碰 DOM、不碰 fetch，可以直接被 vitest 钉住。
 *
 * 为什么把这些单独拿出来：这条链路上有两处"只在真机会撞到"的坑——
 * ① 文件名要变成后端能接受的文件系统段（中文名直接传会被 422 挡回）；
 * ② 索引重建会撞上切分口径门禁（409），必须有人确认后才带 force 重试。
 * 这两件事都藏在交互里，写成纯函数才能测，而不是靠"点一遍看看"。
 */

/** 与后端 POST /knowledge/versions 的白名单保持一致（改一边必须改另一边）。 */
export const ACCEPTED_UPLOAD_EXTENSIONS = [".md", ".txt", ".pdf", ".docx", ".xlsx"] as const;

/** <input accept> 用的一整串，避免各处手写扩展名导致漂移。 */
export const UPLOAD_ACCEPT_ATTR = ACCEPTED_UPLOAD_EXTENSIONS.join(",");

/** 后端 IndexConsistencyError 的专属 code（P2 新增，用于把门禁 409 与其它冲突区分开）。 */
export const ACTIVATION_GATE_CODE = "index_consistency_blocked";

/** 类型不合规时返回给用户的话；合规返回 null（调用方据此决定是否继续）。 */
export function uploadFileTypeError(fileName: string): string | null {
  const lower = (fileName || "").toLowerCase();
  if (ACCEPTED_UPLOAD_EXTENSIONS.some((extension) => lower.endsWith(extension))) return null;
  return `仅支持 ${ACCEPTED_UPLOAD_EXTENSIONS.join(" / ")} 文件（与后端白名单一致）。`;
}

/** djb2 变体：把无法进文件名段的字符（中文等）压成 8 位十六进制。 */
function nameDigest(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash * 33) ^ value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * 由文件名派生 `logical_document_id`。
 *
 * 后端 `_SAFE_SEGMENT = ^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`，而这个值会被直接
 * 拼进存储路径 —— 所以中文文件名（"差旅费管理办法.md"）传原名必然 422。
 *
 * 两个约束必须同时满足，否则会造出比 422 更糟的问题：
 * ① 必须 ASCII 且字母数字开头；
 * ② **不同文件名不能塌成同一个 id** —— 若把中文名统一降级成 "doc"，两份不同的
 *    制度会共享一个逻辑文档，后上传的会把先上传的顶成 replaced（版本语义被污染，
 *    排查时看到"我的文件被替换了"却找不到原因）。因此当 ASCII 化会丢信息时，
 *    用文件名摘要兜底，而不是图好看留一个空壳。
 */
export function slugifyLogicalId(fileName: string): string {
  const stem = (fileName || "").replace(/\.[^.]*$/, "");
  const ascii = stem
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 60);
  // 判据是"ASCII 化到底有没有真的丢信息"，而不是"名字好不好看"：
  // 空格/下划线这类 ASCII 符号归一化不丢信息；非 ASCII 字符（中日韩…）则会被
  // 整段替换掉，只剩一个空壳——那时必须换成摘要。
  const hasNonAscii = /[^\x20-\x7E]/.test(stem);
  const lostInformation = hasNonAscii || ascii.replace(/[^A-Za-z0-9]/g, "").length < 3;
  if (!lostInformation) return ascii;
  return `doc-${nameDigest(stem.toLowerCase())}`;
}

/**
 * 内容派生版本号：同一文件名再次上传（内容变了）时用它避开
 * "Document version already exists" 的死路。
 *
 * 用 lastModified 而不是内容哈希：这里只需要"同一份文件稳定、不同份大概率不同"，
 * 不值得为此把整份文件读进内存做摘要；而且它必须是同步纯函数才好测。
 */
export function contentVersion(lastModified: number, size: number): string {
  const stamp = new Date(Number.isFinite(lastModified) ? lastModified : 0);
  const pad = (value: number) => String(value).padStart(2, "0");
  const date = `${stamp.getUTCFullYear()}${pad(stamp.getUTCMonth() + 1)}${pad(stamp.getUTCDate())}`;
  const time = `${pad(stamp.getUTCHours())}${pad(stamp.getUTCMinutes())}${pad(stamp.getUTCSeconds())}`;
  return `u${date}T${time}s${Math.max(0, Math.trunc(size)).toString(36)}`;
}

/**
 * 这个错误是不是"索引门禁拦住了口径切换"。
 *
 * 判据用专属 code，并保留"409 + 重试指引文案"这条兼容路径：前端与服务端版本
 * 不同步时，宁可多认一次门禁（多问一句"要不要强制"），也不要把门禁错误当成
 * 普通失败报掉——那样用户会以为文件没传进去，反复重试同一个动作。
 */
export function isActivationGateError(error: unknown): boolean {
  const candidate = error as { status?: number; code?: string; message?: string } | undefined;
  if (!candidate || candidate.status !== 409) return false;
  return candidate.code === ACTIVATION_GATE_CODE || Boolean(candidate.message?.includes("force=true"));
}
