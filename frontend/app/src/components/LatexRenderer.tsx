"use client";
import React, { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

interface LatexRendererProps {
  content: string;
  className?: string;
  inline?: boolean;
}

function renderLatex(text: string): string {
  if (!text) return "";

  // 预处理：如果整个内容是裸 LaTeX（以 \boxed、\frac、\sqrt 等命令开头，且没有 $ 包裹），自动套 $...$
  const trimmed = text.trim();
  const isBareLaTeX = /^[+-]?\s*\\(boxed|d?frac|sqrt|sum|int|prod|lim|begin|left|right|vec|hat|bar|dot|cdot|times|div|pm|mp|leq|geq|neq|approx|infty|forall|exists|partial|nabla|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|omega|pi)\b/.test(trimmed)
    && !trimmed.includes("$") && !trimmed.includes("\\[");
  const processedText = isBareLaTeX ? `$$${trimmed}$$` : text;

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
