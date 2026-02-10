"""Flow – Q&A Accounting (Trợ lý hỏi đáp & diễn giải nghiệp vụ kế toán).

Template-based + DB query answering.
When ``USE_REAL_LLM=true`` the fallback path invokes the shared LLM client
for free-form questions that no keyword handler matches.
Answers are built from real Acct* data — NEVER fabricates numbers.

Fine-tune hooks:
  - ``_build_reasoning_chain()`` → step-by-step reasoning for answer transparency
  - ``_cite_regulation()`` → VN accounting regulation references
  - ``_graph_reasoning_hook()`` → LangGraph multi-step reasoning integration
  - All answers include ``reasoning_chain`` for audit trail

Supported question patterns:
  1. Voucher count queries ("bao nhiêu chứng từ", "tháng X có bao nhiêu")
  2. Journal explanation queries ("vì sao", "hạch toán", "tài khoản")
  3. Anomaly summary queries ("bất thường", "anomaly")
  4. Cashflow queries ("dòng tiền", "cashflow")
  5. Classification summary ("phân loại", "classification")
  6. VN regulation queries ("thông tư", "nghị định", "quy định")
  7. Default: generic helpful answer

Future: integrate LangGraph chain for multi-step reasoning.
"""
from __future__ import annotations

import json as _json
import logging
import os
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclaw_agent.common.models import (
    AcctAnomalyFlag,
    AcctCashflowForecast,
    AcctJournalLine,
    AcctJournalProposal,
    AcctVoucher,
)

log = logging.getLogger("openclaw.flows.qna_accounting")

_USE_LANGGRAPH = os.getenv("USE_LANGGRAPH", "").lower() in ("1", "true", "yes")

# -- Regex patterns for stripping chain-of-thought noise from LLM output ------

# Broad set of English CoT starters produced by reasoning models.
_COT_PATTERNS = re.compile(
    r"(?i)^\s*(?:"
    # Explicit reasoning markers
    r"step\s*\d|reasoning[:\s]|chain[- ]of[- ]thought|"
    # "Let me …" / "Let's …"
    r"let me\b|let'?s\s|"
    # "I [verb]" patterns
    r"i (?:need|recall|think|remember|know|believe|should|must|will|guess|see|am)\b|"
    # Hesitation / self-correction
    r"actually[,.\s]|wait[,.\s]|hmm\b|not sure|oh[,.\s]|"
    # Subject phrases referring to conversation
    r"the user|they (?:want|ask|need)|we (?:can|must|should|need|know|have)\b|"
    # Tentative / speculative language
    r"alternatively\b|maybe\b|perhaps\b|probably\b|possibly\b|"
    # Account references in English
    r"account\s*\d|"
    # Conclusion / summary markers
    r"so[,.]\s|so the|thus\b|therefore\b|hence\b|in conclusion|to summariz|"
    # Affirmation / negation at line start
    r"no[,.]\s|yes[,.]\s|correct\b|exactly\b|right[,.]\s|"
    # Memory / search actions
    r"recall[:\s]|search\b|confirm\b|verify\b|"
    # Generalisations
    r"in many|in some|in most|in this case|in general|"
    # Demonstrative + be/modal
    r"(?:it|that|this) (?:is|could|might|would|should|was|has|seems)\b|"
    # Other verbosity
    r"provide\b|refer\b|mention\b|note that|"
    r"^\?\s|^answer[:\s]"
    r")",
)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n([\s\S]*?)\n```")

# Vietnamese diacritic characters – presence indicates Vietnamese text.
_VN_DIACRITIC_RE = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợ"
    r"ùúủũụưứừửữựỳýỷỹỵđÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊ"
    r"ÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴĐ]"
)

_FALLBACK_ANSWER = (
    "Xin lỗi, hệ thống chưa thể đưa ra câu trả lời rõ ràng. "
    "Vui lòng thử lại hoặc diễn đạt câu hỏi theo cách khác."
)

# A word that is purely ASCII letters (4+ chars) – likely English.
# Using 4+ to avoid false positives on short Vietnamese words without
# diacritics (e.g. thu, ghi, ban, cho, con …).
_ENG_WORD_RE = re.compile(r"^[a-zA-Z]{4,}$")


def _clean_llm_answer(raw: str) -> str:
    """Post-process LLM output: strip CoT, JSON blobs, and noise.

    Reasoning models (like GPT-oss-120b) put chain-of-thought in
    ``reasoning_content`` which we fall back to.  This function
    aggressively strips English-language reasoning and keeps only
    Vietnamese text suitable for end-user display.

    Strategy (v4):
    1. Handle raw-JSON responses.
    2. Remove fenced code blocks.
    3. Early-exit if >20 % of words are 4+-letter ASCII words
       (English CoT dominates the response).
    4. Line-by-line filtering: CoT patterns, Vietnamese diacritics,
       and per-line English word ratio.
    5. If nothing survives, return a polite fallback.
    """
    text = raw.strip()
    if not text:
        return text

    # 1. If entire response looks like JSON → extract meaningful Vietnamese text
    if text.startswith(("{", "[")):
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                for key in ("answer", "summary", "explanation", "content", "message"):
                    if key in parsed and isinstance(parsed[key], str) and parsed[key].strip():
                        return parsed[key].strip()
                if parsed.get("decision") or parsed.get("tier"):
                    return (
                        "Xin lỗi, tôi cần thêm thông tin ngữ cảnh để trả lời "
                        "câu hỏi này chính xác hơn. Vui lòng cung cấp thêm chi tiết."
                    )
        except (ValueError, _json.JSONDecodeError):
            pass

    # 2. Remove fenced JSON code blocks
    text = _JSON_BLOCK_RE.sub("", text)

    # 3. Early exit – if >20 % of words are 4+-letter ASCII words,
    #    the model produced a full English CoT answer.
    all_words = text.split()
    if len(all_words) >= 8:
        eng_count = sum(1 for w in all_words if _ENG_WORD_RE.match(w))
        if eng_count / len(all_words) > 0.20:
            return _FALLBACK_ANSWER

    # 4. Line-by-line filtering
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned:
                cleaned.append("")
            continue

        # Skip lines that match known CoT patterns
        if _COT_PATTERNS.search(stripped):
            continue

        # Skip lines without Vietnamese diacritics that are long enough
        # (likely pure English reasoning even if containing some VN terms)
        if len(stripped) > 15 and not _VN_DIACRITIC_RE.search(stripped):
            continue

        # Skip lines with high English word ratio (CoT interleaved with VN terms)
        if len(stripped) > 25:
            line_words = stripped.split()
            if len(line_words) >= 5:
                line_eng = sum(1 for w in line_words if _ENG_WORD_RE.match(w))
                if line_eng / len(line_words) > 0.30:
                    continue

        cleaned.append(line)

    # Trim leading/trailing blank lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    result = "\n".join(cleaned).strip()

    # 5. If nothing meaningful survived, return polite fallback
    if not result or len(result) < 10:
        return _FALLBACK_ANSWER

    return result


def _is_real_llm_enabled() -> bool:
    """Read USE_REAL_LLM at call time (not import time)."""
    return os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Reasoning chain builder (fine-tune hook for answer transparency)
# ---------------------------------------------------------------------------

def _build_reasoning_chain(
    question: str,
    steps: list[str],
    regulation_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Build structured reasoning chain for audit trail.

    Fine-tune hook: Each answer should include step-by-step reasoning
    so reviewers can verify the logic path.
    """
    chain: dict[str, Any] = {
        "question": question,
        "steps": steps,
        "step_count": len(steps),
    }
    if regulation_refs:
        chain["regulation_references"] = regulation_refs
    return chain


# ---------------------------------------------------------------------------
# VN Regulation reference database (fine-tune hook)
# ---------------------------------------------------------------------------

_VN_REGULATIONS: dict[str, dict[str, str]] = {
    "tt200": {
        "name": "Thông tư 200/2014/TT-BTC",
        "subject": "Chế độ kế toán doanh nghiệp",
        "scope": "Hệ thống tài khoản kế toán, mẫu sổ kế toán, mẫu báo cáo tài chính",
    },
    "tt133": {
        "name": "Thông tư 133/2016/TT-BTC",
        "subject": "Chế độ kế toán doanh nghiệp nhỏ và vừa",
        "scope": "DN vốn dưới 100 tỷ, doanh thu dưới 300 tỷ",
    },
    "nd123": {
        "name": "Nghị định 123/2020/NĐ-CP",
        "subject": "Quy định về hóa đơn, chứng từ",
        "scope": "Hóa đơn điện tử, quy cách, xử lý sai sót",
    },
    "tt78": {
        "name": "Thông tư 78/2021/TT-BTC",
        "subject": "Hướng dẫn hóa đơn điện tử theo NĐ 123",
        "scope": "Đăng ký, phát hành, quản lý hóa đơn điện tử",
    },
    "lkt": {
        "name": "Luật Kế toán 88/2015/QH13",
        "subject": "Luật Kế toán",
        "scope": "Nguyên tắc, quy định chung về kế toán doanh nghiệp",
    },
}


def _cite_regulation(topic: str) -> list[str]:
    """Return relevant VN regulation references for a topic.

    Fine-tune hook: Expand mappings as more regulations are indexed.
    """
    topic_lower = topic.lower()
    refs: list[str] = []

    mapping = {
        "hạch toán": ["tt200", "tt133"],
        "bút toán": ["tt200", "tt133"],
        "tài khoản": ["tt200", "tt133"],
        "hóa đơn": ["nd123", "tt78"],
        "chứng từ": ["nd123", "lkt"],
        "thuế": ["nd123", "tt78"],
        "kế toán": ["tt200", "lkt"],
        "báo cáo": ["tt200", "lkt"],
    }

    for keyword, reg_keys in mapping.items():
        if keyword in topic_lower:
            for rk in reg_keys:
                reg = _VN_REGULATIONS.get(rk)
                if reg:
                    ref = f"{reg['name']} — {reg['subject']}"
                    if ref not in refs:
                        refs.append(ref)
    return refs


def _graph_reasoning_hook(
    question: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """LangGraph multi-step reasoning integration hook.

    Fine-tune hook: When USE_LANGGRAPH=1, this function will invoke
    the qna_accounting LangGraph for multi-step reasoning.

    Currently returns None (pass-through to template-based handlers).
    """
    if not _USE_LANGGRAPH:
        return None
    try:
        from openclaw_agent.graphs.registry import get_graph
        graph = get_graph("qna_accounting")
        if graph is None:
            return None
        # Future: invoke graph with question + context
        log.info("graph_reasoning_hook: graph available but not yet wired")
        return None
    except Exception as e:
        log.warning("graph_reasoning_hook failed: %s", e)
        return None


def _try_llm_answer(
    question: str,
    context_summary: str = "",
    regulation_refs: list[str] | None = None,
    existing_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attempt an LLM-powered answer (returns ``None`` when disabled / error)."""
    if not _is_real_llm_enabled():
        return None
    try:
        from openclaw_agent.llm.client import get_llm_client
        client = get_llm_client()
        if not client.config.enabled:
            return None
        return client.generate_qna_answer(
            question=question,
            context_summary=context_summary or "Không có dữ liệu ngữ cảnh bổ sung.",
            regulation_refs=regulation_refs,
            existing_reasoning=existing_reasoning,
        )
    except Exception:
        log.exception("_try_llm_answer failed — fallback")
        return None


def _extract_period(question: str) -> str | None:
    """Try to extract YYYY-MM from question text."""
    # "tháng 1/2025" or "tháng 01/2025"
    m = re.search(r"tháng\s*(\d{1,2})\s*/\s*(\d{4})", question, re.I)
    if m:
        month = int(m.group(1))
        year = int(m.group(2))
        return f"{year}-{month:02d}"

    # "2025-01" direct
    m = re.search(r"(\d{4})-(\d{2})", question)
    if m:
        return m.group(0)

    return None


def _extract_voucher_no(question: str) -> str | None:
    """Try to extract a voucher/invoice number."""
    m = re.search(r"(?:chứng từ|hóa đơn|số)\s*(?:số\s*)?([A-Z0-9]+)", question, re.I)
    if m:
        return m.group(1)
    return None


def _answer_voucher_count(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: how many vouchers in a period?"""
    keywords = ["bao nhiêu chứng từ", "số lượng chứng từ", "có bao nhiêu", "tổng chứng từ"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    period = _extract_period(question)
    q = select(func.count(AcctVoucher.id))
    if period:
        q = q.where(AcctVoucher.date.like(f"{period}%"))
    count = session.execute(q).scalar() or 0

    if period:
        # Format for display
        parts = period.split("-")
        display_period = f"tháng {int(parts[1])}/{parts[0]}"
        answer = f"Trong {display_period}, hệ thống ghi nhận {count} chứng từ đã ingest."
    else:
        answer = f"Tổng cộng hệ thống đã ingest {count} chứng từ."

    return {
        "answer": answer,
        "used_models": ["AcctVoucher"],
    }


def _answer_journal_explanation(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: why was a voucher classified / journaled a certain way?"""
    keywords = ["vì sao", "tại sao", "hạch toán", "gợi ý", "tài khoản"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    voucher_no = _extract_voucher_no(question)
    if not voucher_no:
        return {
            "answer": (
                "Để giải thích bút toán, vui lòng cung cấp số chứng từ/hóa đơn cụ thể. "
                "Ví dụ: 'Vì sao chứng từ hóa đơn số 0000123 được gợi ý hạch toán như vậy?'"
            ),
            "used_models": [],
        }

    # Find voucher
    voucher = session.query(AcctVoucher).filter(
        AcctVoucher.voucher_no.like(f"%{voucher_no}%")
    ).first()

    if not voucher:
        return {
            "answer": f"Không tìm thấy chứng từ số {voucher_no} trong hệ thống.",
            "used_models": ["AcctVoucher"],
        }

    # Find journal proposal for this voucher
    proposal = session.query(AcctJournalProposal).filter_by(voucher_id=voucher.id).first()
    if not proposal:
        return {
            "answer": (
                f"Chứng từ số {voucher.voucher_no} (loại: {voucher.voucher_type}, "
                f"số tiền: {voucher.amount:,.0f} {voucher.currency}) đã được ghi nhận "
                f"nhưng chưa có đề xuất bút toán."
            ),
            "used_models": ["AcctVoucher"],
        }

    # Build explanation from journal lines
    lines = session.query(AcctJournalLine).filter_by(proposal_id=proposal.id).all()
    lines_desc = []
    accounts_mentioned = []
    for ln in lines:
        if ln.debit > 0:
            lines_desc.append(f"Nợ TK {ln.account_code} ({ln.account_name}): {ln.debit:,.0f}")
        if ln.credit > 0:
            lines_desc.append(f"Có TK {ln.account_code} ({ln.account_name}): {ln.credit:,.0f}")
        accounts_mentioned.append(ln.account_code)

    lines_str = "; ".join(lines_desc)
    reasoning = proposal.reasoning or ""

    # Build VN explanation
    vtype_map = {
        "sell_invoice": "hóa đơn bán hàng",
        "buy_invoice": "hóa đơn mua hàng",
        "receipt": "phiếu thu",
        "payment": "phiếu chi",
    }
    vtype_label = vtype_map.get(voucher.voucher_type, voucher.voucher_type)

    answer_parts = [
        f"Chứng từ số {voucher.voucher_no} là {vtype_label}, "
        f"số tiền {voucher.amount:,.0f} {voucher.currency}.",
    ]

    if voucher.voucher_type == "sell_invoice":
        answer_parts.append(
            "Do đây là hóa đơn bán hàng nên ghi nhận doanh thu TK 511 "
            "và phải thu khách hàng TK 131."
        )
    elif voucher.voucher_type == "buy_invoice":
        answer_parts.append(
            "Do đây là hóa đơn mua hàng nên ghi nhận chi phí NVL TK 621 "
            "và phải trả người bán TK 331."
        )
    elif voucher.voucher_type == "receipt":
        answer_parts.append(
            "Do đây là phiếu thu nên ghi nhận tiền mặt TK 111 "
            "và giảm phải thu TK 131."
        )
    elif voucher.voucher_type == "payment":
        answer_parts.append(
            "Do đây là phiếu chi nên ghi nhận phải trả TK 331 "
            "và giảm tiền gửi ngân hàng TK 112."
        )

    answer_parts.append(f"Bút toán đề xuất: {lines_str}.")
    answer_parts.append(f"Độ tin cậy: {proposal.confidence:.0%}.")

    if reasoning:
        answer_parts.append(f"Lý do: {reasoning}")

    return {
        "answer": " ".join(answer_parts),
        "used_models": ["AcctVoucher", "AcctJournalProposal", "AcctJournalLine"],
    }


def _answer_anomaly_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: anomaly/risk summary."""
    keywords = ["bất thường", "anomaly", "rủi ro", "risk"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    total = session.execute(select(func.count(AcctAnomalyFlag.id))).scalar() or 0
    open_count = session.execute(
        select(func.count(AcctAnomalyFlag.id)).where(AcctAnomalyFlag.resolution == "open")
    ).scalar() or 0

    if total == 0:
        answer = "Hiện tại chưa phát hiện giao dịch bất thường nào trong hệ thống."
    else:
        answer = (
            f"Hệ thống đã phát hiện tổng cộng {total} cảnh báo bất thường, "
            f"trong đó {open_count} cảnh báo chưa được xử lý."
        )

    return {
        "answer": answer,
        "used_models": ["AcctAnomalyFlag"],
    }


def _answer_cashflow_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: cashflow summary."""
    keywords = ["dòng tiền", "cashflow", "cash flow", "thu chi"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    rows = session.execute(select(AcctCashflowForecast)).scalars().all()
    if not rows:
        return {
            "answer": "Chưa có dữ liệu dự báo dòng tiền. Hãy chạy 'cashflow_forecast' trước.",
            "used_models": ["AcctCashflowForecast"],
        }

    total_in = sum(r.amount for r in rows if r.direction == "inflow")
    total_out = sum(r.amount for r in rows if r.direction == "outflow")
    net = total_in - total_out

    answer = (
        f"Dòng tiền dự báo: Thu {total_in:,.0f} VND, Chi {total_out:,.0f} VND, "
        f"Ròng {net:,.0f} VND ({len(rows)} dòng dự báo)."
    )

    return {
        "answer": answer,
        "used_models": ["AcctCashflowForecast"],
    }


def _answer_classification_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: classification breakdown."""
    keywords = ["phân loại", "classification", "loại chứng từ"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    q = (
        select(AcctVoucher.classification_tag, func.count(AcctVoucher.id))
        .group_by(AcctVoucher.classification_tag)
    )
    rows = session.execute(q).all()

    if not rows:
        return {
            "answer": "Chưa có dữ liệu phân loại chứng từ.",
            "used_models": ["AcctVoucher"],
            "reasoning_chain": _build_reasoning_chain(question, [
                "Truy vấn phân loại chứng từ",
                "Không tìm thấy dữ liệu",
            ]),
        }

    parts = [f"{tag or 'Chưa phân loại'}: {cnt}" for tag, cnt in rows]
    answer = "Phân loại chứng từ hiện tại: " + ", ".join(parts) + "."

    return {
        "answer": answer,
        "used_models": ["AcctVoucher"],
        "reasoning_chain": _build_reasoning_chain(question, [
            "Truy vấn bảng AcctVoucher nhóm theo classification_tag",
            f"Tìm thấy {len(rows)} nhóm phân loại",
            "Tổng hợp và trả kết quả",
        ]),
    }


def _answer_regulation_query(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: VN accounting regulation questions.

    Fine-tune hook: matches regulation-related keywords and provides
    references to specific Vietnamese accounting standards.
    """
    keywords = ["thông tư", "nghị định", "quy định", "luật kế toán",
                 "regulation", "chuẩn mực", "chế độ kế toán"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    refs = _cite_regulation(question)

    if not refs:
        # Generic regulation answer
        all_regs = [f"- {r['name']}: {r['subject']}" for r in _VN_REGULATIONS.values()]
        answer = (
            "Các văn bản pháp quy kế toán VN quan trọng:\n"
            + "\n".join(all_regs)
            + "\n\nBạn muốn tra cứu về nội dung cụ thể nào?"
        )
        refs = [r["name"] for r in _VN_REGULATIONS.values()]
    else:
        answer = "Các văn bản liên quan đến câu hỏi:\n" + "\n".join(f"- {r}" for r in refs)

    return {
        "answer": answer,
        "used_models": [],
        "reasoning_chain": _build_reasoning_chain(
            question,
            ["Phát hiện câu hỏi về quy định pháp lý",
             f"Tìm thấy {len(refs)} văn bản liên quan"],
            regulation_refs=refs,
        ),
    }


def answer_question(session: Session, question: str) -> dict[str, Any]:
    """Main Q&A dispatcher — tries each handler in priority order.

    Returns: {"answer": str, "used_models": list[str], "reasoning_chain": dict}

    Fine-tune hooks:
      - Graph reasoning (LangGraph) is attempted first when USE_LANGGRAPH=1
      - Each handler returns a reasoning_chain for audit transparency
      - Regulation references are automatically cited when relevant
    """
    # Try LangGraph reasoning first (fine-tune hook)
    graph_result = _graph_reasoning_hook(question, {})
    if graph_result is not None:
        return graph_result

    handlers = [
        _answer_voucher_count,
        _answer_journal_explanation,
        _answer_anomaly_summary,
        _answer_cashflow_summary,
        _answer_classification_summary,
        _answer_regulation_query,
    ]

    for handler in handlers:
        result = handler(session, question)
        if result is not None:
            # Ensure reasoning_chain is present
            if "reasoning_chain" not in result:
                result["reasoning_chain"] = _build_reasoning_chain(
                    question,
                    [f"Handler: {handler.__name__}", "Trả kết quả thành công"],
                    regulation_refs=_cite_regulation(question) or None,
                )
            elif _cite_regulation(question):
                # Add regulation refs if not already present
                chain = result["reasoning_chain"]
                if "regulation_references" not in chain:
                    refs = _cite_regulation(question)
                    if refs:
                        chain["regulation_references"] = refs
            return result

    # --- Try LLM for free-form questions when enabled ---
    llm_result = _try_llm_answer(
        question,
        regulation_refs=_cite_regulation(question) or None,
    )
    if llm_result is not None:
        # DEV-04: post-process LLM answer to strip CoT / JSON noise
        llm_result["answer"] = _clean_llm_answer(llm_result.get("answer", ""))
        llm_result.setdefault("used_models", [])
        llm_result["reasoning_chain"] = _build_reasoning_chain(
            question,
            ["Không khớp handler nào — chuyển sang LLM", "LLM trả lời thành công"],
            regulation_refs=_cite_regulation(question) or None,
        )
        return llm_result

    # Default fallback (no handler, no LLM)
    return {
        "answer": (
            "Xin lỗi, tôi chưa hiểu câu hỏi này. Bạn có thể hỏi về:\n"
            "- Số lượng chứng từ (ví dụ: 'Tháng 1/2025 có bao nhiêu chứng từ?')\n"
            "- Giải thích bút toán (ví dụ: 'Vì sao chứng từ số 0000123 được hạch toán vậy?')\n"
            "- Giao dịch bất thường (ví dụ: 'Có bao nhiêu anomaly chưa xử lý?')\n"
            "- Dòng tiền (ví dụ: 'Tóm tắt dòng tiền dự báo')\n"
            "- Phân loại chứng từ (ví dụ: 'Thống kê phân loại chứng từ')\n"
            "- Quy định kế toán (ví dụ: 'Thông tư 200 quy định gì về hạch toán?')"
        ),
        "used_models": [],
        "reasoning_chain": _build_reasoning_chain(
            question,
            ["Không khớp với handler nào", "Trả hướng dẫn sử dụng"],
        ),
    }
