/* markdown-inline — bộ dựng nội dung văn bản DÙNG CHUNG giữa `MessageBubble`
 * và `TraceFlow` (C1): mọi chữ agent nói, dù nằm trong bong bóng đã chốt hay
 * đang stream trong dòng thời gian phẳng, phải đọc ra CÙNG một kiểu.
 *
 * Tách khỏi `MessageBubble` — trước đây `TraceFlow`/`AgentTrace` không cần
 * nó (chỉ render nhãn tool ngắn), nhưng giờ khối text nằm NGAY TRONG dòng
 * thời gian nên cần cùng bộ dựng.
 *
 * Một bộ dựng inline CỐ Ý HẸP: chỉ **đậm**, *nghiêng*, `code`. Mọi thứ khác
 * giữ nguyên văn — chữ agent nói không bao giờ được chèn markup tuỳ ý. */

import { Fragment, type ReactNode } from "react";

export function renderInline(text: string): ReactNode {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  return text.split(pattern).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="font-mono text-[13.5px] bg-ink-900 px-1 py-0.5 rounded">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function renderContent(content: string): ReactNode {
  return content.split("\n").map((line, i) => (
    <span key={i} className="block">
      {renderInline(line)}
    </span>
  ));
}
