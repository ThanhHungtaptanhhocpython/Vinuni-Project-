import re
from typing import Any

_POLITICAL_PATTERN = re.compile(
    r"\b(politics|elections?|parties|politicians?|ideology|geopolitical|voting|political)\b|"
    r"(chính trị|bầu cử|đảng phái|chính trị gia|hệ tư tưởng|địa chính trị|bỏ phiếu)",
    re.IGNORECASE
)

def is_political_topic(text: str) -> bool:
    """Check if the text is about political topics."""
    return bool(_POLITICAL_PATTERN.search(text))

POLITICAL_DEFLECTION_MESSAGE = "Tôi chỉ hỗ trợ vận hành và điều khiển robot. Vui lòng đưa ra yêu cầu liên quan đến robot."

_ENGLISH_STOPWORDS_PATTERN = re.compile(
    r"\b(the|is|are|you|your|we|our|please|sure|okay|ok|yes|no|will|going to|let's|let me)\b",
    re.IGNORECASE
)

def has_mixed_language(text: str) -> bool:
    """Heuristic check for conversational English mixed in the final text."""
    # Simple check for common conversational English words
    return bool(_ENGLISH_STOPWORDS_PATTERN.search(text))

async def rewrite_mixed_language(text: str, ctx: Any) -> str:
    """Rewrite text to natural Vietnamese, keeping technical terms."""
    from langchain_core.messages import HumanMessage

    from .llm import invoke_llm

    prompt = (
        "Dịch hoặc viết lại văn bản sau sang tiếng Việt tự nhiên hoàn toàn. "
        "Giữ nguyên các từ khóa kỹ thuật (tên công cụ, mã lỗi, tên biến, v.v.).\n\n"
        f"Văn bản: {text}"
    )

    try:
        response = await invoke_llm(ctx, [HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                b["text"] if isinstance(b, dict) and "text" in b else str(b)
                for b in content
            )
        return str(content)
    except Exception:
        # Fallback to original text if rewrite fails
        return str(text) if not isinstance(text, str) else text
