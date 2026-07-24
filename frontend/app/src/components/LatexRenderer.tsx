"use client";
import React, { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

interface LatexRendererProps {
  content: string;
  className?: string;
  inline?: boolean;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderInlineLatex(latex: string): string | null {
  try {
    return katex.renderToString(latex, {
      displayMode: false,
      throwOnError: true,
      output: "html",
    });
  } catch {
    return null;
  }
}

function renderBareLatexFragments(text: string): string {
  // 上游回答偶尔会截断在公式中间。此时仅渲染可独立解析的公式片段，
  // 其余内容按普通文本保留，避免一个损坏的片段让整段答案退化为红色原文。
  const latexFragment = /\\text\{[^{}]*\}|\\[A-Za-z]+(?:\^(?:\\[A-Za-z]+|\{[^{}]*\}|[A-Za-z0-9])|_(?:\\[A-Za-z]+|\{[^{}]*\}|[A-Za-z0-9]))*|[A-Za-z](?:\^(?:\\[A-Za-z]+|\{[^{}]*\}|[A-Za-z0-9])|_(?:\\[A-Za-z]+|\{[^{}]*\}|[A-Za-z0-9]))+/g;
  let result = "";
  let cursor = 0;

  for (const match of text.matchAll(latexFragment)) {
    const index = match.index ?? 0;
    result += escapeHtml(text.slice(cursor, index));
    const fragment = match[0];
    result += renderInlineLatex(fragment) ?? escapeHtml(fragment);
    cursor = index + fragment.length;
  }

  return result + escapeHtml(text.slice(cursor));
}

function renderLatex(text: string): string {
  if (!text) return "";

  // 预处理：模型回答常直接以 LaTeX 命令开头而没有 $ 包裹，例如 \text{...} \Gamma^\sigma。
  // 这类内容按块级公式渲染；已经使用 $、\(...\) 或 \[...\] 包裹的内容仍走下方原有分支。
  const trimmed = text.trim();
  const hasExplicitDelimiter = trimmed.includes("$") || trimmed.includes("\\[") || trimmed.includes("\\(");
  const isBareLaTeX = /^[+-]?\s*\\[A-Za-z]+/.test(trimmed) && !hasExplicitDelimiter;
  if (isBareLaTeX) return renderBareLatexFragments(trimmed);
  const processedText = text;

  let result = "";
  let i = 0;

  while (i < processedText.length) {
    // 检查 $$ 块级公式
    if (processedText.slice(i, i + 2) === "$$") {
      const end = processedText.indexOf("$$", i + 2);
      if (end !== -1) {
        const latex = processedText.slice(i + 2, end);
        try {
          const rendered = katex.renderToString(latex, {
            displayMode: true,
            throwOnError: false,
            output: "html",
          });
          result += `<div class="katex-block">${rendered}</div>`;
        } catch {
          result += `<span style="color:red">$$${latex}$$</span>`;
        }
        i = end + 2;
        continue;
      }
    }

    // 检查 $ 行内公式
    if (processedText[i] === "$") {
      const end = processedText.indexOf("$", i + 1);
      if (end !== -1) {
        const latex = processedText.slice(i + 1, end);
        try {
          const rendered = katex.renderToString(latex, {
            displayMode: false,
            throwOnError: false,
            output: "html",
          });
          result += rendered;
        } catch {
          result += `<span style="color:red">$${latex}$</span>`;
        }
        i = end + 1;
        continue;
      }
    }

    // 检查 \[ \] 块级公式
    if (processedText.slice(i, i + 2) === "\\[") {
      const end = processedText.indexOf("\\]", i + 2);
      if (end !== -1) {
        const latex = processedText.slice(i + 2, end);
        try {
          const rendered = katex.renderToString(latex, {
            displayMode: true,
            throwOnError: false,
            output: "html",
          });
          result += `<div class="katex-block">${rendered}</div>`;
        } catch {
          result += `<span style="color:red">\\[${latex}\\]</span>`;
        }
        i = end + 2;
        continue;
      }
    }

    // 检查 \( \) 行内公式
    if (processedText.slice(i, i + 2) === "\\(") {
      const end = processedText.indexOf("\\)", i + 2);
      if (end !== -1) {
        const latex = processedText.slice(i + 2, end);
        try {
          const rendered = katex.renderToString(latex, {
            displayMode: false,
            throwOnError: false,
            output: "html",
          });
          result += rendered;
        } catch {
          result += `<span style="color:red">\\(${latex}\\)</span>`;
        }
        i = end + 2;
        continue;
      }
    }

    // 普通字符 - 转义 HTML
    const char = processedText[i];
    if (char === "&") result += "&amp;";
    else if (char === "<") result += "&lt;";
    else if (char === ">") result += "&gt;";
    else if (char === "\n") result += "<br/>";
    else result += char;
    i++;
  }

  return result;
}

export default function LatexRenderer({ content, className, inline }: LatexRendererProps) {
  const html = useMemo(() => renderLatex(content), [content]);

  if (inline) {
    return (
      <span
        className={className}
        dangerouslySetInnerHTML={{ __html: html }}
        style={{ display: "inline" }}
      />
    );
  }

  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: html }}
      style={{ lineHeight: 1.8 }}
    />
  );
}
