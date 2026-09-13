import { describe, expect, it } from "vitest";

import {
  ACCEPTED_UPLOAD_EXTENSIONS,
  UPLOAD_ACCEPT_ATTR,
  contentVersion,
  isActivationGateError,
  slugifyLogicalId,
  uploadFileTypeError,
} from "./document-upload";

describe("上传文件类型白名单", () => {
  it("与后端 POST /knowledge/versions 的白名单一致（5 种）", () => {
    expect([...ACCEPTED_UPLOAD_EXTENSIONS]).toEqual([".md", ".txt", ".pdf", ".docx", ".xlsx"]);
    expect(UPLOAD_ACCEPT_ATTR).toBe(".md,.txt,.pdf,.docx,.xlsx");
  });

  it("大小写不敏感，且给出可读的拒绝理由", () => {
    expect(uploadFileTypeError("政策.PDF")).toBeNull();
    expect(uploadFileTypeError("notes.docx")).toBeNull();
    const message = uploadFileTypeError("payload.exe");
    expect(message).toContain(".md");
    expect(message).toContain(".xlsx");
  });
});

describe("logical_document_id 派生", () => {
  it("ASCII 文件名保留可读 slug（后端用它拼存储路径）", () => {
    expect(slugifyLogicalId("Travel Policy 2026.md")).toBe("Travel-Policy-2026");
  });

  it("满足后端 _SAFE_SEGMENT：ASCII、字母数字开头、不超过 80 字符", () => {
    for (const name of ["差旅费管理办法.md", "2026 版-报销制度（试行）.pdf", "___weird___.txt", "政策.xlsx"]) {
      const slug = slugifyLogicalId(name);
      expect(slug).toMatch(/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/);
      expect(slug).not.toContain("..");
    }
  });

  it("中文文件名不能塌成同一个 id（否则两份制度会共享版本线）", () => {
    const first = slugifyLogicalId("差旅费管理办法.md");
    const second = slugifyLogicalId("招待费管理办法.md");
    expect(first).not.toBe(second);
  });

  it("同一文件名稳定：重复上传应落回同一个逻辑文档", () => {
    expect(slugifyLogicalId("差旅费管理办法.md")).toBe(slugifyLogicalId("差旅费管理办法.md"));
  });
});

describe("内容派生版本号", () => {
  it("同一份文件稳定（重传同一文件不会无限造版本）", () => {
    const stamp = Date.UTC(2026, 8, 12, 10, 30, 0);
    expect(contentVersion(stamp, 2048)).toBe(contentVersion(stamp, 2048));
  });

  it("内容变了则版本号跟着变（这就是它存在的理由）", () => {
    const first = contentVersion(Date.UTC(2026, 8, 12, 10, 30, 0), 2048);
    const later = contentVersion(Date.UTC(2026, 8, 12, 11, 5, 0), 3072);
    expect(first).not.toBe(later);
    expect(first).toMatch(/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/);
  });

  it("非法时间戳也不产出畸形版本号", () => {
    expect(contentVersion(Number.NaN, 10)).toMatch(/^u\d{8}T\d{6}s/);
  });
});

describe("索引门禁错误的识别", () => {
  it("专属 code 命中", () => {
    expect(isActivationGateError({ status: 409, code: "index_consistency_blocked", message: "x" })).toBe(true);
  });

  it("旧后端只有 409 + 重试指引文案时也命中（版本不同步不能把门禁当普通失败）", () => {
    expect(
      isActivationGateError({ status: 409, code: "invalid_state_transition", message: "…请带 force=true 重试" }),
    ).toBe(true);
  });

  it("普通 409 与网络错误不命中（否则会误导用户点强制切换）", () => {
    expect(isActivationGateError({ status: 409, code: "duplicate_document", message: "Document already exists" })).toBe(false);
    expect(isActivationGateError(new Error("offline"))).toBe(false);
    expect(isActivationGateError(undefined)).toBe(false);
  });
});
