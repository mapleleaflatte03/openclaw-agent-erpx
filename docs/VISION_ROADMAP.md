# VISION ROADMAP — Accounting Agent Layer ERP AI Kế toán

> **Nguyên tắc bất biến**: 7 dòng milestone dưới đây là **Acceptance Criteria cấp hệ thống**.
> Không được sửa wording, không được giảm accuracy, không được bỏ bất kỳ từ khóa nào
> ("swarms", "multi-agent", "read-only 100%", "VAS/IFRS", v.v.).
> Mọi Phase chỉ mô tả **con đường tiệm cận** — target trên giấy **không đổi**.

---

## MILESTONE – 7 TRẦN NĂNG LỰC

```text
[VISION TOUCHPOINT]
- Đọc/OCR chứng từ: "Swarms xử lý hàng loạt đa định dạng với accuracy >98%, tự chuẩn hóa theo quy định VN mới nhất, lưu bản sao + audit trail."
- Gợi ý/tự động hạch toán: "Swarms reasoning ngữ cảnh lịch sử + chính sách DN, gợi ý bút toán tối ưu thuế, giải thích đa tầng, read-only 100%."
- Đối chiếu chứng từ/giao dịch: "So khớp real-time đa nguồn (ngân hàng, thuế điện tử), phát hiện gian lận cơ bản, gợi ý khắc phục tự động."
- Kiểm tra thiếu/sai/rủi ro: "Quét liên tục real-time, dự đoán rủi ro phổ biến, multi-agent đạt accuracy ~98%, theo chuẩn VN."
- Phân tích xu hướng/dự báo: "Dự báo đa kịch bản (hàng nghìn), accuracy >95%, tự điều chỉnh theo dữ liệu mới + sự kiện."
- Hỏi đáp/diễn giải: "Agent hiểu ngữ cảnh toàn ERP + pháp lý VN, diễn giải như chuyên gia cấp cao, tự học từ feedback."
- Lập báo cáo tài chính: "Tạo báo cáo động đa chuẩn (VAS/IFRS), tổng hợp + phân tích sâu tự động, dự phòng kiểm toán."
```

---

## 1. Đọc/OCR chứng từ

**Milestone**: _"Swarms xử lý hàng loạt đa định dạng với accuracy >98%, tự chuẩn hóa theo quy định VN mới nhất, lưu bản sao + audit trail."_

### Tiến độ hiện tại: ~25%

| Đã có | Chưa có |
|---|---|
| Mock OCR pipeline (`voucher_ingest.py`) | Real OCR engine (Tesseract/Vision/PaddleOCR) |
| Placeholder `_ocr_extract()` hook | Multi-format (PDF/image/XML e-invoice) |
| VN diacritic normalizer stub | Swarms parallel OCR via Ray |
| Audit trail (AcctVoucher rows + raw_payload) | Accuracy benchmark >98% |
| READ-ONLY principle enforced | Tự chuẩn hóa theo NĐ123/TT78 mới nhất |
| 3 VN OCR datasets surveyed (MC_OCR, Receipt, Appen) | Training pipeline trên VN data |
| Ray `batch_classify_vouchers` skeleton | Bản sao file lưu MinIO + checksum |

### Phase 1 (P1) — Nền tảng OCR thực

| Task | Module | KPI |
|---|---|---|
| Tích hợp PaddleOCR VN model | `flows/voucher_ingest.py::_ocr_extract()` | accuracy ≥85% trên MC_OCR test set |
| Multi-format adapter (PDF, JPEG, PNG, XML e-invoice) | `flows/voucher_ingest.py::_load_document()` | 4 formats supported |
| Lưu bản sao file → MinIO + checksum SHA256 | `common/storage.py` | 100% files saved + verified |
| Audit trail: ghi log OCR confidence + engine version | `common/models.py::AcctVoucher.ocr_meta` | every ingest has audit |

### Phase 2 (P2) — Chuẩn hóa VN + Ray batch

| Task | Module | KPI |
|---|---|---|
| VN normalizer: NĐ123 e-invoice fields (MST, mã HĐ, số tiền) | `flows/voucher_ingest.py::_normalize_nd123()` | compliance check pass |
| Ray swarm batch OCR: `RaySwarm.batch_map(_ocr_extract, files)` | `kernel/swarm.py`, `kernel/batch.py` | parallelism ≥4x on 2-node |
| Fine-tune PaddleOCR trên MC_OCR + Appen VN | training pipeline | accuracy ≥92% |
| Schema versioning: track chuẩn VN (NĐ123→TT78→updates) | `regulations/vn_invoice_schema.py` | auto-validate against current schema |

### Phase 3 (P3) — Swarms >98%

| Task | Module | KPI |
|---|---|---|
| Multi-agent OCR swarm: consensus voting (3 engines) | `kernel/swarm.py::OcrSwarm` | accuracy >98% (MC_OCR benchmark) |
| Auto-detect format + route to best engine | `flows/voucher_ingest.py::_route_ocr()` | 0 format failures |
| Regression benchmark CI gate | `tests/benchmark/test_ocr_accuracy.py` | CI blocks if accuracy drops |
| Self-update when regulation changes (webhook/polling) | `regulations/vn_update_monitor.py` | <24h lag on new regulation |

---

## 2. Gợi ý/tự động hạch toán

**Milestone**: _"Swarms reasoning ngữ cảnh lịch sử + chính sách DN, gợi ý bút toán tối ưu thuế, giải thích đa tầng, read-only 100%."_

### Tiến độ hiện tại: ~35%

| Đã có | Chưa có |
|---|---|
| Rule-based `_classify_voucher()` (5 types) | LLM reasoning trên ngữ cảnh lịch sử |
| `_ACCOUNT_MAP` TT200 (131/511/621/331/111/112/642) | Tax optimization reasoning |
| `AcctJournalProposal` + `AcctJournalLine` models | Chính sách DN custom engine |
| Confidence scoring (0.55–0.95) | Multi-tầng explanation |
| LLM refine hook (USE_REAL_LLM) | Swarms multi-agent consensus |
| LangGraph `journal_suggestion_graph` | History-aware context window |
| READ-ONLY 100% enforced | Full TT200 account chart (180+ TK) |
| Ray batch classify skeleton | DN policy configuration |

### Phase 1 (P1) — LLM-enhanced + full TT200

| Task | Module | KPI |
|---|---|---|
| Expand `_ACCOUNT_MAP` → full TT200 chart (180+ accounts) | `flows/journal_suggestion.py` | cover 95% voucher types |
| LLM-enhanced classification: history context (last 30 vouchers same type) | `flows/journal_suggestion.py::_classify_with_llm()` | accuracy ≥90% vs expert |
| Multi-level explanation: rule reason + LLM reason + regulation ref | `flows/journal_suggestion.py::_explain()` | 3 explanation tiers |
| Tax hint: suggest optimal tax treatment per voucher | `flows/journal_suggestion.py::_tax_hint()` | present for ≥50% proposals |

### Phase 2 (P2) — DN policy + reasoning graph

| Task | Module | KPI |
|---|---|---|
| DN policy engine: configurable rules per company | `flows/policy_engine.py` | CRUD policies via API |
| LangGraph multi-step reasoning: classify→verify→explain→optimize | `graphs/journal_suggestion_graph.py` | 4-node graph |
| Tax optimization: compare TT200 vs TT133, suggest better structure | `flows/journal_suggestion.py::_optimize_tax()` | saves ≥5% on test cases |
| Confidence calibration: backtest proposals vs expert decisions | `tests/benchmark/test_journal_accuracy.py` | calibration score ≥0.85 |

### Phase 3 (P3) — Swarms reasoning

| Task | Module | KPI |
|---|---|---|
| Multi-agent swarm: 3 agents (classifier/auditor/optimizer) vote | `kernel/swarm.py::JournalSwarm` | consensus accuracy ≥95% |
| Full history reasoning: last 12 months same counterparty/type | LLM context builder | context recall ≥90% |
| Self-learning: log expert overrides → retrain rules | `flows/journal_suggestion.py::_feedback_loop()` | monthly improvement |
| Read-only verification: automated check no ERP write ever occurs | `tests/test_readonly_guarantee.py` | 100% pass |

---

## 3. Đối chiếu chứng từ/giao dịch

**Milestone**: _"So khớp real-time đa nguồn (ngân hàng, thuế điện tử), phát hiện gian lận cơ bản, gợi ý khắc phục tự động."_

### Tiến độ hiện tại: ~30%

| Đã có | Chưa có |
|---|---|
| Rule-based bank reconciliation | Real-time matching |
| ±3 days date / ±1% amount tolerance matcher | E-invoice (thuế điện tử) reconciliation |
| `AcctBankTransaction` + `AcctAnomalyFlag` models | Multi-source (ngân hàng + thuế + nội bộ) |
| LangGraph `bank_reconcile_graph` | Fraud detection rules |
| Anomaly flags: amount_mismatch/date_gap/unmatched_tx | Auto-remediation suggestions |
| VN Feeder engine (3 data sources) | Real bank API integration |

### Phase 1 (P1) — E-invoice + multi-source

| Task | Module | KPI |
|---|---|---|
| E-invoice XML parser (thuế điện tử format) | `flows/bank_reconcile.py::_parse_e_invoice()` | parse 100% valid XML |
| Multi-source matcher: bank + e-invoice + voucher 3-way | `flows/bank_reconcile.py::_match_multi_source()` | 3-way match rate ≥80% |
| Fraud rules v1: duplicate payment, split invoice, round-trip | `flows/bank_reconcile.py::_fraud_rules` | detect 5 patterns |
| Remediation suggestions: "Tạo bút toán bổ sung cho chênh lệch X VND" | `flows/bank_reconcile.py::_suggest_fix()` | suggestion for 100% anomalies |

### Phase 2 (P2) — Near-real-time + advanced fraud

| Task | Module | KPI |
|---|---|---|
| VN Feeder → reconcile pipeline: auto-trigger on new events | `agent_service/vn_feeder_engine.py` | <5 min latency |
| LLM-assisted fuzzy match for unresolved items | `graphs/bank_reconcile_graph.py` | resolve +15% unmatched |
| Fraud scoring model (ML on historical anomalies) | `flows/fraud_model.py` | precision ≥90%, recall ≥80% |
| Alert webhook: notify on high-risk anomaly | `agent_service/main.py::_webhook_alert()` | <1 min notification |

### Phase 3 (P3) — Real-time swarms

| Task | Module | KPI |
|---|---|---|
| Real-time bank API polling (Vietcombank/MB/Techcombank sandbox) | `flows/bank_api_connector.py` | 3 banks supported |
| Multi-agent reconcile swarm: matcher/verifier/fraud-detector | `kernel/swarm.py::ReconcileSwarm` | throughput ≥1000 tx/min |
| Auto-fix execution: create adjustment vouchers (read-only draft) | `flows/bank_reconcile.py::_auto_fix()` | draft-only, no ERP write |
| Continuous reconciliation dashboard (Grafana) | `deploy/observability/` | real-time panels |

---

## 4. Kiểm tra thiếu/sai/rủi ro

**Milestone**: _"Quét liên tục real-time, dự đoán rủi ro phổ biến, multi-agent đạt accuracy ~98%, theo chuẩn VN."_

### Tiến độ hiện tại: ~40%

| Đã có | Chưa có |
|---|---|
| 5 rule-based checks (MISSING_ATTACHMENT, JOURNAL_IMBALANCED, OVERDUE_INVOICE, DUPLICATE_VOUCHER, LARGE_AMOUNT_NO_APPROVAL) | Real-time continuous scan |
| `AcctSoftCheckResult` + `AcctValidationIssue` models | ML risk prediction model |
| LLM explanation for flagged issues | Multi-agent check swarm |
| LangGraph `soft_checks_graph` | Accuracy benchmark ~98% |
| Score aggregation (errors/warnings/info) | VN regulation update tracking |
| Period-based scan via run system | Predictive risk (dự đoán rủi ro) |

### Phase 1 (P1) — Expand rules + benchmark

| Task | Module | KPI |
|---|---|---|
| Add 10 more VN rules: TT200 mandatory fields, VAT mismatch, period cutoff | `flows/soft_checks_acct.py::_RULES` | 15 total rules |
| Accuracy benchmark: golden dataset with known issues | `tests/benchmark/test_soft_check_accuracy.py` | accuracy ≥90% |
| Score calibration: map score → risk level (low/medium/high/critical) | `flows/soft_checks_acct.py` | 4-tier risk |
| VN regulation awareness: flag violations of TT200/TT133/NĐ123 | `regulations/tt133_index.py` | 20+ regulation refs |

### Phase 2 (P2) — Predictive + continuous

| Task | Module | KPI |
|---|---|---|
| ML risk model: predict common issues from voucher patterns | `flows/risk_model.py` | precision ≥85% |
| Continuous scan: trigger soft_checks on every voucher_ingest | `agent_worker/tasks.py` | <30s after ingest |
| Multi-agent check: rule-agent + LLM-agent + ML-agent vote | `kernel/swarm.py::CheckSwarm` | accuracy ≥95% |
| Alert escalation: high-risk → Slack/email notification | `agent_service/main.py` | <5 min alert |

### Phase 3 (P3) — Multi-agent ~98%

| Task | Module | KPI |
|---|---|---|
| Full multi-agent swarm with consensus + self-correction | `kernel/swarm.py::AuditSwarm` | accuracy ~98% |
| Real-time streaming check (process as data arrives) | `agent_service/vn_feeder_engine.py` | real-time, 0 batch delay |
| Regulation auto-update: poll MOF/GDT websites for new circulars | `regulations/vn_update_monitor.py` | <24h detection |
| Audit evidence pack: auto-generate for flagged items | `flows/soft_checks_acct.py::_pack_evidence()` | 100% flagged items have pack |

---

## 5. Phân tích xu hướng/dự báo

**Milestone**: _"Dự báo đa kịch bản (hàng nghìn), accuracy >95%, tự điều chỉnh theo dữ liệu mới + sự kiện."_

### Tiến độ hiện tại: ~20%

| Đã có | Chưa có |
|---|---|
| 30-day cashflow forecast (simple heuristic) | Multi-scenario generation |
| Inflow/outflow from invoices | ML time-series model |
| Recurring pattern detection (counterparty heuristic) | Accuracy benchmark >95% |
| `AcctCashflowForecast` model | Auto-adjust on new data |
| LangGraph `cashflow_forecast_graph` | Event-driven recalculation |

### Phase 1 (P1) — Statistical forecast + scenarios

| Task | Module | KPI |
|---|---|---|
| Statistical model: ARIMA/Prophet on historical cashflow | `flows/cashflow_forecast.py::_statistical_forecast()` | MAE <10% on 30d |
| 3 base scenarios: optimistic/baseline/pessimistic | `flows/cashflow_forecast.py::_scenarios()` | 3 scenarios |
| Backtest framework: accuracy measurement on historical data | `tests/benchmark/test_forecast_accuracy.py` | accuracy ≥80% |
| Auto-recalculate on new voucher_ingest | `agent_worker/tasks.py` | trigger on ingest |

### Phase 2 (P2) — ML + event-driven

| Task | Module | KPI |
|---|---|---|
| ML model: LSTM/Transformer on multi-feature input | `flows/forecast_ml.py` | accuracy ≥90% |
| Event injection: "what-if" scenarios (lose customer, new contract) | `flows/cashflow_forecast.py::_what_if()` | 100+ scenarios |
| Auto-adjust: retrain on every period close | `flows/forecast_ml.py::_retrain()` | monthly cycle |
| Revenue/expense trend analysis + visualization data | `flows/trend_analysis.py` | 6 trend metrics |

### Phase 3 (P3) — Thousands of scenarios >95%

| Task | Module | KPI |
|---|---|---|
| Monte Carlo simulation: 1000+ scenario runs | `flows/cashflow_forecast.py::_monte_carlo()` | 1000+ scenarios per run |
| Multi-agent forecast swarm: domain experts + data scientist | `kernel/swarm.py::ForecastSwarm` | ensemble accuracy >95% |
| Real-time dashboard: live forecast with confidence bands | `ui/app.py` (Streamlit) | real-time update |
| External event connector: VN macro data (CPI, exchange rate) | `flows/external_data.py` | 3+ external sources |

---

## 6. Hỏi đáp/diễn giải

**Milestone**: _"Agent hiểu ngữ cảnh toàn ERP + pháp lý VN, diễn giải như chuyên gia cấp cao, tự học từ feedback."_

### Tiến độ hiện tại: ~45%

| Đã có | Chưa có |
|---|---|
| Q&A dispatcher: keyword → handler → LLM | Full ERP context awareness |
| TT133 index (chart of accounts) | VAS/IFRS regulation index |
| LLM integration (DigitalOcean GPT-oss-120b) | Feedback loop (self-learning) |
| PO benchmark templates (3 hardcoded) | Chuyên gia cấp cao depth |
| Quality guardrail (monologue/English/generic reject) | Multi-turn conversation |
| Regulation query handler (TT200/TT133 keywords) | RAG over full regulation corpus |
| Vietnamese answer enforcement | Citation with article/clause reference |
| 9/9 acceptance test pass | Expert-level nuanced reasoning |

### Phase 1 (P1) — RAG + expanded regulation

| Task | Module | KPI |
|---|---|---|
| RAG pipeline: embed full TT200 + TT133 + VAS text | `regulations/rag_index.py` | 500+ articles indexed |
| Expand PO benchmark: 15+ canonical Q&A pairs | `flows/qna_accounting.py` | 15 templates |
| Citation: every answer includes article/clause reference | `flows/qna_accounting.py::_cite()` | 100% answers cited |
| ERP context injection: inject current period summary into LLM prompt | `flows/qna_accounting.py::_erp_context()` | context for 5 data types |

### Phase 2 (P2) — Expert-level + feedback

| Task | Module | KPI |
|---|---|---|
| Multi-turn conversation: session memory (last 10 Q&A) | `flows/qna_accounting.py::_session` | multi-turn works |
| Feedback loop: thumbs up/down → fine-tune ranking | `flows/qna_accounting.py::_record_feedback()` | feedback stored |
| VAS + IFRS index: dual-standard answers | `regulations/vas_ifrs_index.py` | VAS + IFRS coverage |
| Comparison mode: "So sánh xử lý VAS vs IFRS cho giao dịch này" | `flows/qna_accounting.py` | side-by-side |

### Phase 3 (P3) — Senior expert + self-learning

| Task | Module | KPI |
|---|---|---|
| Expert agent: reason through complex scenarios (M&A, restructure) | multi-agent flow | handle 10+ complex scenarios |
| Self-learning: monthly model update from feedback data | `flows/qna_accounting.py::_self_learn()` | measurable improvement |
| Full ERP context: query any ERP entity during Q&A | `flows/qna_accounting.py::_query_erp()` | all 8 entity types |
| Regulatory update awareness: answer "Có gì thay đổi trong TT mới?" | `regulations/vn_update_monitor.py` | <48h after new regulation |

---

## 7. Lập báo cáo tài chính

**Milestone**: _"Tạo báo cáo động đa chuẩn (VAS/IFRS), tổng hợp + phân tích sâu tự động, dự phòng kiểm toán."_

### Tiến độ hiện tại: ~25%

| Đã có | Chưa có |
|---|---|
| VAT report (vat_list) with versioning | VAS format report |
| Trial balance summary stub | IFRS format report |
| `AcctReportSnapshot` model (type/period/version/payload) | Dynamic drill-down |
| Tax export flow (`tax_report.py`) | Phân tích sâu tự động |
| Period + version management | Dự phòng kiểm toán pack |
| LangGraph `tax_report_graph` | Multi-template engine |

### Phase 1 (P1) — VAS templates + drill-down

| Task | Module | KPI |
|---|---|---|
| VAS Balance Sheet template (Bảng cân đối kế toán B01-DN) | `flows/tax_report.py::_vas_balance_sheet()` | B01-DN format |
| VAS Income Statement (Báo cáo KQKD B02-DN) | `flows/tax_report.py::_vas_income_statement()` | B02-DN format |
| VAS Cashflow Statement (Báo cáo LCTT B03-DN) | `flows/tax_report.py::_vas_cashflow()` | B03-DN format |
| Drill-down: click account → see underlying vouchers | `ui/app.py` | drill-down works |

### Phase 2 (P2) — IFRS + analysis

| Task | Module | KPI |
|---|---|---|
| IFRS conversion: VAS → IFRS mapping for key accounts | `flows/tax_report.py::_ifrs_convert()` | 50+ account mappings |
| Auto-analysis: LLM commentary on each report section | `flows/tax_report.py::_analyze()` | analysis for 5 sections |
| Comparison mode: period-over-period + budget variance | `flows/tax_report.py::_compare()` | YoY + budget |
| PDF/Excel export: downloadable formatted reports | `flows/tax_report.py::_export_file()` | PDF + XLSX |

### Phase 3 (P3) — Audit-ready + dynamic

| Task | Module | KPI |
|---|---|---|
| Audit evidence pack: index + cross-reference all supporting docs | `flows/tax_report.py::_audit_pack()` | 100% line items traced |
| Dynamic report builder: user defines custom report structure | `ui/app.py` (report builder) | drag-drop builder |
| Multi-standard: generate VAS + IFRS simultaneously | `flows/tax_report.py` | dual output |
| Kiểm toán dự phòng: pre-audit checklist + gap analysis | `flows/audit_prep.py` | checklist + gaps |

---

## MODULE → MILESTONE MAPPING

| Module | Milestone(s) touched |
|---|---|
| `flows/voucher_ingest.py` | 1 (OCR), 3 (đối chiếu source) |
| `flows/journal_suggestion.py` | 2 (hạch toán) |
| `flows/voucher_classify.py` | 1 (OCR classify), 2 (hạch toán) |
| `flows/bank_reconcile.py` | 3 (đối chiếu) |
| `flows/soft_checks_acct.py` | 4 (kiểm tra rủi ro) |
| `flows/cashflow_forecast.py` | 5 (xu hướng/dự báo) |
| `flows/qna_accounting.py` | 6 (hỏi đáp) |
| `flows/tax_report.py` | 7 (báo cáo TC) |
| `kernel/swarm.py` | 1, 2, 3, 4, 5 (all swarm capabilities) |
| `kernel/batch.py` | 1, 2, 4 (batch processing) |
| `graphs/*.py` | 2, 3, 4, 5, 7 (LangGraph orchestration) |
| `regulations/tt133_index.py` | 2, 4, 6 (VN regulation) |
| `llm/client.py` | 2, 4, 6 (LLM integration) |
| `agent_service/vn_feeder_engine.py` | 1, 3 (data ingestion) |
| `ui/app.py` | 3, 5, 6, 7 (user interface) |

---

## PR/TICKET FORMAT REQUIREMENT

Every PR/ticket **must** include:

```markdown
## Vision Touchpoint

Touchpoint tầm nhìn: "<copy nguyên câu milestone liên quan>"

Scope PR: nâng từ trạng thái A → B
(ví dụ: accuracy OCR từ ~85% lên ~90%, chuẩn hóa theo TT200 cơ bản).

Tiến độ hiện tại: ~X% so với milestone.
```

If temporarily disabling a feature for stability:

```markdown
## Tạm thời disable

Tạm thời disable <feature> để giữ đường lên mục tiêu:
"<copy nguyên câu milestone liên quan>"

Lý do: <why>
Kế hoạch khôi phục: <when/how>
```

---

## TEST NAMING CONVENTION

Test names **must** reflect milestone mapping:

```python
# Milestone 1: OCR
test_vision_ocr_swarms_accuracy_gt_98_percent_not_regressed()
test_vision_ocr_multi_format_pdf_image_xml()
test_vision_ocr_vn_diacritics_normalization()

# Milestone 2: Hạch toán
test_vision_journal_swarms_reasoning_history_context()
test_vision_journal_tax_optimization_read_only()
test_vision_journal_multi_tier_explanation()

# Milestone 3: Đối chiếu
test_vision_reconcile_realtime_multi_source()
test_vision_reconcile_fraud_detection_basic()
test_vision_reconcile_auto_fix_suggestion()

# Milestone 4: Kiểm tra rủi ro
test_vision_softcheck_continuous_realtime_scan()
test_vision_softcheck_multi_agent_accuracy_98()
test_vision_softcheck_vn_regulation_compliance()

# Milestone 5: Dự báo
test_vision_forecast_multi_scenario_thousands()
test_vision_forecast_accuracy_gt_95_percent()
test_vision_forecast_auto_adjust_new_data()

# Milestone 6: Hỏi đáp
test_vision_qna_erp_context_full_understanding()
test_vision_qna_explains_like_senior_expert_vn_regulations()
test_vision_qna_self_learn_from_feedback()

# Milestone 7: Báo cáo TC
test_vision_report_dynamic_vas_ifrs_dual()
test_vision_report_deep_analysis_auto()
test_vision_report_audit_provision_pack()
```

---

_Last updated: 2026-02-11 — commit TBD_
