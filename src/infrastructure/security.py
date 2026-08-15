"""输入验证、数据校验与安全工具。

提供:
- 输入清理（防注入/防XSS）
- PII 检测与脱敏
- 文档内容安全扫描
- 文件上传校验
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

# ── 敏感信息检测 ──

# 中国身份证号正则（非捕获组）
CN_ID_PATTERN = re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")

# 手机号正则
CN_PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")

# 邮箱正则
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 银行卡号正则
BANK_CARD_PATTERN = re.compile(r"\b\d{16,19}\b")

# 姓名模式（中文2-4字 + 常见姓氏）
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮下齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯咎管卢莫经房裘缪干解应宗宣丁贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊于惠甄魏家封芮羿储靳汲邴糜松"

# 姓名模式（中文2-4字 + 常见姓氏，不使用 \\b 因为对中文无效）
CN_PERSON_NAME_PATTERN = re.compile(rf"(?:^|[^a-zA-Z0-9\u4e00-\u9fa5])[{SURNAMES}][\u4e00-\u9fa5]{{1,3}}(?:$|[^a-zA-Z0-9\u4e00-\u9fa5])")


def detect_pii(text: str) -> dict[str, list[str]]:
    """检测文本中的 PII 信息。

    Returns:
        dict: {类型: [匹配的文本列表]}
    """
    findings: dict[str, list[str]] = {}
    if matches := CN_ID_PATTERN.findall(text):
        findings["cn_id"] = matches
    if matches := CN_PHONE_PATTERN.findall(text):
        findings["cn_phone"] = matches
    if matches := EMAIL_PATTERN.findall(text):
        findings["email"] = matches
    if matches := BANK_CARD_PATTERN.findall(text):
        findings["bank_card"] = matches
    if matches := CN_PERSON_NAME_PATTERN.findall(text):
        findings["person_name"] = matches[:5]  # 只报告前5个
    return findings


def redact_pii(text: str) -> str:
    """脱敏文本中的 PII。

    Returns:
        str: 脱敏后的文本
    """
    text = CN_ID_PATTERN.sub("[身份证号已隐藏]", text)
    text = CN_PHONE_PATTERN.sub("[手机号已隐藏]", text)
    text = EMAIL_PATTERN.sub("[邮箱已隐藏]", text)
    text = BANK_CARD_PATTERN.sub("[银行卡号已隐藏]", text)
    return text


# ── 输入清理 ──

# SQL 注入危险字符
SQL_INJECTION_PATTERNS = [
    (re.compile(r"(--|#|/\*|\*/)", re.IGNORECASE), "SQL 注释"),
    (re.compile(r"\b(DROP|ALTER|TRUNCATE|DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b", re.IGNORECASE), "SQL DML"),
    (re.compile(r"\b(UNION\s+SELECT|EXEC\s*\(|EXECUTE\s*\()", re.IGNORECASE), "SQL 注入模式"),
]

# XSS 危险标签
XSS_PATTERNS = [
    (re.compile(r"<script[\s>]", re.IGNORECASE), "script 标签"),
    (re.compile(r"javascript\s*:", re.IGNORECASE), "javascript 协议"),
    (re.compile(r"on\w+\s*=", re.IGNORECASE), "事件处理器"),
]


def sanitize_input(text: str) -> str:
    """清理用户输入：去除首尾空白、移除注入风险字符、限制长度。

    Args:
        text: 原始输入

    Returns:
        str: 清理后的文本（最长 2000 字符）
    """
    if not isinstance(text, str):
        return ""
    text = text.strip()
    # 移除危险字符（SQL 注释、XSS 标签等）
    text = re.sub(r"(?:--|#|/\*|\*/|<script[\s>]|javascript\s*:|on\w+\s*=)", "", text, flags=re.IGNORECASE)
    # 限制输入长度
    max_len = 2000
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def has_injection_risk(text: str) -> tuple[bool, list[str]]:
    """检测输入是否包含 SQL 注入或 XSS 风险。

    Returns:
        tuple: (是否有风险, 匹配到的模式名称列表)
    """
    risks: list[str] = []
    for pattern, name in SQL_INJECTION_PATTERNS + XSS_PATTERNS:
        if pattern.search(text):
            risks.append(name)
    return len(risks) > 0, risks


# ── 文件上传校验 ──

ALLOWED_DOCUMENT_MIMETYPES = {
    "text/markdown",
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

ALLOWED_DOCUMENT_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".xlsx"}

MAX_FILENAME_LENGTH = 255


def validate_filename(filename: str) -> str:
    """验证并清理文件名。

    Returns:
        str: 清理后的安全文件名

    Raises:
        ValueError: 文件名无效时
    """
    if not filename or not isinstance(filename, str):
        raise ValueError("Filename is required")

    # 提取纯文件名（去掉路径）
    safe_name = filename.replace("\\", "/").split("/")[-1]

    # 先检查扩展名（防止截断切除扩展名）
    ext = Path(safe_name).suffix.lower()
    if ext and ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_DOCUMENT_EXTENSIONS)}")

    if len(safe_name) > MAX_FILENAME_LENGTH:
        # 保留扩展名：截断主体部分
        safe_name = safe_name[:MAX_FILENAME_LENGTH - len(ext)] + ext

    # 移除危险字符
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name)

    if not safe_name or safe_name in (".", ".."):
        raise ValueError("Invalid filename")

    return safe_name


def validate_content_type(content_type: str | None, filename: str) -> bool:
    """验证 MIME 类型是否在允许列表中。

    Returns:
        bool: 是否通过验证
    """
    if not content_type:
        # 允许无 content-type（某些客户端不发送）
        return True
    if content_type in ALLOWED_DOCUMENT_MIMETYPES:
        return True
    if content_type == "application/octet-stream":
        # 通用二进制流，依赖扩展名二次验证
        return True
    return False


def hash_content(content: bytes) -> str:
    """计算内容的 SHA256 哈希。"""
    return hashlib.sha256(content).hexdigest()
