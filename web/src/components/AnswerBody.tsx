import { Fragment, type ReactNode } from "react";

import type { Citation } from "../types";

/**
 * 答案正文渲染（P1-1 Markdown + P1-2 内联引用锚点）：
 * - 轻量 Markdown：标题（#~####）、无序/有序列表、引用块、代码块、
 *   加粗、行内代码、外链。不引入第三方依赖，直接构建 React 元素，
 *   不使用 innerHTML —— 外部内容天然无法注入脚本。
 * - [citation-N] 解析为可点击角标：点击后证据链轨道滚动并高亮对应引用卡。
 *   N 与后端 citation_id = citation-{final_rank} 对应（chat_service.py）。
 * - 流式渲染：每次 delta 后对当前累计文本整体解析；未闭合的 ** / 代码块
 *   会短暂显示原始符号，闭合后自动恢复，符合增量解析的可接受行为。
 */

type AnswerBodyProps = {
  text: string;
  citations: Citation[];
  onCitationClick?: (rank: number) => void;
  streaming?: boolean;
};

const INLINE_PATTERN = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[citation-(\d+)\](?!:)|\[[^\]\n]+\]\((?:https?:)?\/\/[^)\s]+\))/g;

/** 行内标记 → React 节点；其余文本原样成为文本节点（React 自动转义） */
function renderInline(
  text: string,
  keyPrefix: string,
  citationRanks: Set<number>,
  onCitationClick?: (rank: number) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  INLINE_PATTERN.lastIndex = 0;
  while ((match = INLINE_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${index}`;
    index += 1;
    if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(
        <code className="inline-code" key={key}>
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[2] !== undefined) {
      const rank = Number(match[2]);
      if (citationRanks.has(rank) && onCitationClick) {
        nodes.push(
          <button
            aria-label={`查看引用 ${rank} 的原文`}
            className="citation-ref"
            key={key}
            onClick={() => onCitationClick(rank)}
            title="定位到证据链中的引用原文"
            type="button"
          >
            {rank}
          </button>,
        );
      } else {
        // 引用号不在本轮引用列表中（模型偶发越界标注）：保留原文，不做成假锚点
        nodes.push(<span className="citation-ref-unlinked" key={key}>{`[citation-${rank}]`}</span>);
      }
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\(((?:https?:)?\/\/[^)\s]+)\)$/);
      if (linkMatch) {
        const href = linkMatch[2].startsWith("//") ? `https:${linkMatch[2]}` : linkMatch[2];
        nodes.push(
          <a className="answer-external-link" href={href} key={key} rel="noopener noreferrer" target="_blank">
            {linkMatch[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "code"; lines: string[] }
  | { kind: "paragraph"; lines: string[] };

const UL_ITEM = /^[-*•]\s+/;
const OL_ITEM = /^\d+[.、)]\s+/;
const HEADING = /^(#{1,4})\s+/;

function parseBlocks(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;
  let quote: string[] | null = null;
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", lines: paragraph });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };
  const flushQuote = () => {
    if (quote) {
      blocks.push({ kind: "quote", lines: quote });
      quote = null;
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  for (const line of lines) {
    // 代码块优先：围栏内的行原样保留
    if (code !== null) {
      if (line.trimStart().startsWith("```")) {
        blocks.push({ kind: "code", lines: code });
        code = null;
      } else {
        code.push(line);
      }
      continue;
    }
    if (line.trimStart().startsWith("```")) {
      flushAll();
      code = [];
      continue;
    }

    if (!line.trim()) {
      flushAll();
      continue;
    }

    const headingMatch = line.match(HEADING);
    if (headingMatch) {
      flushAll();
      blocks.push({ kind: "heading", level: headingMatch[1].length, text: line.replace(HEADING, "") });
      continue;
    }

    if (UL_ITEM.test(line.trim())) {
      flushParagraph();
      flushQuote();
      if (!list || list.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(line.trim().replace(UL_ITEM, ""));
      continue;
    }

    if (OL_ITEM.test(line.trim())) {
      flushParagraph();
      flushQuote();
      if (!list || list.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(line.trim().replace(OL_ITEM, ""));
      continue;
    }

    if (line.trim().startsWith(">")) {
      flushParagraph();
      flushList();
      quote = quote ?? [];
      quote.push(line.trim().replace(/^>\s?/, ""));
      continue;
    }

    // 普通行：进入当前段落（列表/引用结束）
    flushList();
    flushQuote();
    paragraph.push(line);
  }

  if (code !== null) {
    // 流式中未闭合的代码围栏：按代码块展示已到达内容
    blocks.push({ kind: "code", lines: code });
  }
  flushAll();
  return blocks;
}

export function AnswerBody({ text, citations, onCitationClick, streaming = false }: AnswerBodyProps) {
  const citationRanks = new Set(citations.map((citation) => citation.final_rank));
  const blocks = parseBlocks(text);

  return (
    <div className={`answer-body${streaming ? " streaming" : ""}`}>
      {blocks.map((block, blockIndex) => {
        const keyPrefix = `b${blockIndex}`;
        const inline = (line: string, suffix: string) =>
          renderInline(line, `${keyPrefix}${suffix}`, citationRanks, onCitationClick);
        switch (block.kind) {
          case "heading":
            // 答案内部标题统一用 h4，避免抢占页面 h1/h2 层级
            return (
              <h4 className="answer-heading-md" key={keyPrefix}>
                {inline(block.text, "-h")}
              </h4>
            );
          case "ul":
            return (
              <ul className="answer-list" key={keyPrefix}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{inline(item, `-i${itemIndex}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol className="answer-list" key={keyPrefix}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{inline(item, `-i${itemIndex}`)}</li>
                ))}
              </ol>
            );
          case "quote":
            return (
              <blockquote className="answer-quote" key={keyPrefix}>
                {block.lines.map((line, lineIndex) => (
                  <Fragment key={lineIndex}>
                    {lineIndex > 0 ? <br /> : null}
                    {inline(line, `-q${lineIndex}`)}
                  </Fragment>
                ))}
              </blockquote>
            );
          case "code":
            return (
              <pre className="answer-code" key={keyPrefix}>
                <code>{block.lines.join("\n")}</code>
              </pre>
            );
          case "paragraph":
            return (
              <p key={keyPrefix}>
                {block.lines.map((line, lineIndex) => (
                  <Fragment key={lineIndex}>
                    {lineIndex > 0 ? <br /> : null}
                    {inline(line, `-l${lineIndex}`)}
                  </Fragment>
                ))}
              </p>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
