# Workflows & Skills Mapping (8 tác vụ)

Nguồn cấu hình: `config/workflows.yaml` (mapping step/skill/queue + idempotency + retry/rate limit mặc định).

## Nguyên tắc chung
- **Run idempotency**: `agent_runs.idempotency_key` là unique. Caller có thể gửi header `Idempotency-Key` khi `POST /agent/v1/runs`. Nếu không gửi, `agent-service` tự tạo key bằng `make_idempotency_key(...)`.
- **Output idempotency**: enforced bằng unique constraints trong các bảng `agent_*` (attachments/export/exception/reminder/close/evidence/kb).
- **Retry policy (task-level)**:
  - ERPX HTTP retry + rate limit: `ErpXClient` dùng Tenacity + rate limiter theo env:
    - `ERPX_RATE_LIMIT_QPS`, `ERPX_TIMEOUT_SECONDS`
    - `ERPX_RETRY_MAX_ATTEMPTS`, `ERPX_RETRY_BASE_SECONDS`, `ERPX_RETRY_MAX_SECONDS`
  - Celery retry cho `dispatch_run` (lỗi transient): theo env:
    - `TASK_RETRY_MAX_ATTEMPTS` (mặc định 3)
    - `TASK_RETRY_BACKOFF_SECONDS` (mặc định 2, exponential backoff)
- **Queue routing**:
  - `ocr`: OCR/PDF text extraction
  - `export`: VAT/working papers/report generation
  - `io`: upload/download/pack/register outputs
  - `default`: rules/checks nhẹ

## 8 tác vụ (run_type)

### 1) `attachment` (Thu thập & gắn chứng từ)
- Trigger: event (MinIO drop bucket) hoặc manual
- Input: `file_uri`, optional `period`
- Steps/skills:
  - `extract_text` → `pdf_text_or_ocr` (pdfplumber → OCR fallback cho PDF scan + ảnh)
  - `parse_keys` → `parse_doc_keys`
  - `match` → `match_erpx_object` (rule + fuzzy)
  - `attach` → `store_attachment` (upload MinIO + insert `agent_attachments`)
- Output:
  - `agent_attachments` + object ở bucket attachments
  - nếu mismatch: `agent_exceptions` (`exception_type=attachment_mismatch`)
- Idempotency:
  - run key: `attachment:{file_hash}`
  - unique output: (`file_hash`,`erp_object_type`,`erp_object_id`)

### 2) `tax_export` (VAT list)
- Trigger: schedule cuối kỳ / manual
- Input: `period=YYYY-MM`, optional `force_new_version`
- Steps/skills:
  - `pull_invoices` → `erpx_pull_invoices`
  - `validate` → `validate_invoices`
  - `export_xlsx` → `export_vat_list_xlsx`
- Output:
  - `agent_exports` (`export_type=vat_list`) + object ở bucket exports
  - nếu thiếu field: `agent_exceptions` (`vat_export_missing_fields`)
- Idempotency:
  - unique output: (`export_type`,`period`,`version`)
  - nếu không `force_new_version`: reuse version mới nhất

### 3) `working_papers`
- Trigger: schedule cuối tháng / manual
- Input: `period`, optional `force_new_version`
- Steps/skills:
  - `pull_balances` → `erpx_pull_balances` (MVP: dùng AR aging)
  - `fill_templates` → `fill_working_papers_template`
  - `export_bundle` → `export_working_papers_xlsx`
- Output: `agent_exports` (`export_type=working_paper`) + object ở bucket exports
- Idempotency: (`export_type`,`period`,`version`) (reuse nếu không force)

### 4) `soft_checks`
- Trigger: schedule hàng ngày/tuần / manual
- Input: `updated_after` (cursor ISO), optional `period`, optional `force_new_version`
- Steps/skills:
  - `pull_delta` → `erpx_pull_updated_objects`
  - `checks` → `soft_checks_rules`
  - `export_report` → `export_soft_checks_report` (CSV)
- Output:
  - `agent_exceptions` (signature unique)
  - `agent_exports` (`export_type=soft_checks`) + report ở bucket exports
- Idempotency: exception signature unique; report reuse nếu không force

### 5) `ar_dunning` (Nhắc nợ AR)
- Trigger: schedule hằng ngày
- Input: `as_of=YYYY-MM-DD`, optional `policy_window_days` (default 30)
- Steps/skills:
  - `pull_ar_aging` → `erpx_pull_ar_aging`
  - `apply_policy` → `ar_dunning_policy` (stage 1/2/3 theo overdue_days)
  - `notify` → `send_reminders` (SMTP optional; luôn ghi log)
- Output: `agent_reminder_log`
- Idempotency:
  - không gửi trùng `invoice_id + reminder_stage` trong cửa sổ `policy_window_days`
  - enforced bằng query `sent_at >= cutoff` + `policy_key` (unique)

### 6) `close_checklist`
- Trigger: theo `close_calendar`
- Input: `period`
- Steps/skills:
  - `pull_close_calendar` → `erpx_pull_close_calendar`
  - `upsert_close_tasks` → upsert `agent_close_tasks`
  - `nudge` → nhắc trước hạn/đến hạn (update `last_nudged_at`)
- Output: `agent_close_tasks`
- Idempotency: (`period`,`task_name`) unique

### 7) `evidence_pack`
- Trigger: manual khi có issue/exception
- Input: `exception_id` hoặc `issue_id`
- Steps/skills:
  - `collect` → `collect_evidence_refs`
  - `pack` → `pack_evidence_zip`
  - `register` → insert `agent_evidence_packs`
- Output: `agent_evidence_packs` + zip ở bucket evidence
- Idempotency: (`issue_key`,`version`) unique (MVP: version=1, reuse nếu đã có)

### 8) `kb_index`
- Trigger: event (file mới trong drop/kb) hoặc manual
- Input: `file_uri`, optional `doc_type/title/version/effective_date`
- Steps/skills:
  - `extract_text` → `pdf_text_or_ocr`
  - `extract_meta` → `extract_kb_metadata` (MVP: rule-based)
  - `index` → `keyword_index` (MVP: keyword list)
  - `register` → insert `agent_kb_docs`
- Output:
  - `agent_kb_docs`
  - extracted text ở bucket kb (s3 uri)
- Idempotency: (`file_hash`,`version`) unique

