# Design Doc: Accounting Agent Layer ERPX — Nghiệp vụ Kế toán Ngoài Luồng Ghi Sổ

> **Status**: Source of Truth  
> **Last updated**: 2026-02-07  
> **Scope**: `/root/accounting-agent-layer` repository ONLY

---

## 1. Overview

Accounting Agent Layer hỗ trợ nghiệp vụ kế toán **ngoài luồng ghi sổ** trong hệ thống ERPX.

### Core Principles
- Agent chỉ **READ-ONLY** ERPX (qua API / replica / mocked API).
- **KHÔNG** có endpoint ghi vào ERPX core (không post bút toán, không sửa số tiền/tài khoản, không khóa/mở kỳ).
- Agent chỉ ghi **OUTPUT PHỤ TRỢ**: proposals / approvals / audit append-only / evidence / files / attachments / exports / exceptions / reminder-log / close-checklist / kb-index.
- Cơ chế **3-tier gating** + **maker-checker approvals 2 lớp** cho rủi ro cao + **audit append-only** truy vết.

---

## 2. Tám Nhóm Tác Vụ Phụ Trợ

| # | Run Type          | Mô tả                                         | Output Table                |
|---|-------------------|------------------------------------------------|-----------------------------|
| 1 | `attachment`      | Attachments matcher (OCR → parse → match ERPX) | `agent_attachments`         |
| 2 | `tax_export`      | VAT invoice list export (XLSX)                 | `agent_exports`             |
| 3 | `working_papers`  | Working papers kỳ (XLSX)                       | `agent_exports`             |
| 4 | `soft_checks`     | Soft checks / exceptions                       | `agent_exceptions`          |
| 5 | `ar_dunning`      | AR dunning reminders + log                     | `agent_reminder_log`        |
| 6 | `close_checklist` | Close checklist                                | `agent_close_tasks`         |
| 7 | `evidence_pack`   | Evidence packs (ZIP)                           | `agent_evidence_packs`      |
| 8 | `kb_index`        | KB docs index                                  | `agent_kb_docs`             |

Ngoài ra: `contract_obligation` bao gồm ingest → extract → reconcile → proposals.

---

## 3. Data Model Tối Thiểu

### Bảng nền
- `agent_runs` — run_id, run_type, trigger_type, status, idempotency_key, cursor_in/out, stats
- `agent_tasks` — task_id, run_id, task_name, status, input_ref, output_ref
- `agent_logs` — log_id, run_id, task_id, level, message, context, ts

### Bảng kết quả
- `agent_attachments` — matched attachment → ERP object
- `agent_exports` — export file (VAT list, working papers)
- `agent_exceptions` — soft check exceptions
- `agent_reminder_log` — AR dunning reminder records
- `agent_close_tasks` — close checklist items
- `agent_evidence_packs` — evidence pack ZIPs
- `agent_kb_docs` — KB document index
- `agent_feedback` — user feedback on agent outputs

### Bảng contract obligation
- `agent_contract_cases` — case container
- `agent_source_files` — uploaded sources (PDF/email/audio)
- `agent_extracted_text` — OCR/pdfplumber output
- `agent_email_threads` — parsed email threads
- `agent_audio_transcripts` — audio transcripts (feature-flag)
- `agent_obligations` — extracted obligations
- `agent_obligation_evidence` — evidence snippets per obligation
- `agent_erpx_links` — liên kết nghĩa vụ ↔ thực thể ERPX (agent-side, read-only)
- `agent_proposals` — proposals (3-tier gating)
- `agent_approvals` — maker-checker approvals
- `agent_audit_log` — append-only audit trail

---

## 4. API Design

### ERPX → Agent (Read-Only)
Agent đọc từ ERPX qua `ErpXClient`:
- `/erp/v1/journals`
- `/erp/v1/partners`
- `/erp/v1/contracts`
- `/erp/v1/payments`
- `/erp/v1/vouchers`
- `/erp/v1/invoices`
- `/erp/v1/ar/aging`
- `/erp/v1/assets`
- `/erp/v1/close/calendar`

### Agent → Output (Phụ trợ)
- `POST /agent/v1/runs` — trigger a workflow run
- `GET /agent/v1/runs`, `/tasks`, `/logs` — monitoring
- `POST /agent/v1/attachments`, `/exports`, `/exceptions`, `/reminders/log`, `/close/tasks`, `/evidence`, `/kb/index` — register outputs
- `GET /agent/v1/contract/cases`, proposals, approvals, audit
- `POST /agent/v1/contract/proposals/{id}/approvals` — maker-checker

### Health
- `GET /healthz`, `GET /readyz` — root health
- `GET /agent/v1/healthz`, `GET /agent/v1/readyz` — V1 aliases

---

## 5. 5C: Three-Tier Gating

| Tier | Điều kiện                                                  | Output                             |
|------|------------------------------------------------------------|--------------------------------------|
| 1    | Đủ trường + bằng chứng mạnh + confidence >= threshold     | Proposal cụ thể (draft/reminder/accrual) |
| 2    | Tín hiệu chưa đủ hoặc conflict                           | Tóm tắt + yêu cầu confirm           |
| 3    | Thiếu dữ liệu (amount/date/trigger missing)              | Chỉ báo thiếu gì                     |

### Maker-Checker Approvals
- **Risk = high**: Require 2 distinct approvers (khác maker).
- **Risk = medium/low**: Require 1 approver (khác maker).
- `evidence_ack = true` bắt buộc (đã xem bằng chứng).
- Creator (maker) không thể approve chính proposal của mình.

### Audit Append-Only
- `agent_audit_log` chỉ INSERT (không UPDATE/DELETE).
- Mỗi action (proposal.create, proposal.approve, proposal.reject) ghi 1 event.

---

## 6. Deploy: 6-Node k3s

### Node Roles
| Node | Roles                          |
|------|--------------------------------|
| 1    | control-ingress (API, scheduler, UI, erpx-mock) |
| 2    | data-core (postgres, redis, minio) |
| 3    | pool-ocr (worker-ocr)         |
| 4    | pool-export (worker-export)    |
| 5    | pool-io (worker-io)            |
| 6    | pool-standby (worker-standby)  |

### Resource Limits
4 vCPU / 16 GB per node.

### Overlays
- `deploy/k8s/overlays/prod-6nodes/` — production with nodeSelector per role
- `deploy/k8s/overlays/staging-single/` — single-node (no nodeSelector)
- `deploy/k8s/overlays/staging/` — legacy staging overlay (deprecated, use staging-single)

### Staging Single-Node
Chạy tất cả services trên 1 node (no nodeSelector, reduced replicas).
Không phá cấu hình prod 6-node.

---

## 7. Image Registry

GHCR: `ghcr.io/mapleleaflatte03/accounting-agent-layer/<service>:<tag>`

Production sử dụng `imagePullSecrets: [{name: ghcr-pull}]`.
Script tạo secret: `scripts/k8s/create_ghcr_pull_secret.sh`.

---

## 8. Scheduler

- Config: `config/schedules.yaml` (cron schedules + pollers).
- Config: `config/workflows.yaml` (workflow definitions).
- Env var expansion: `${VAR}` — **fail-fast** nếu biến thiếu.
- Pollers: watch MinIO drop buckets for new files.

---

## 9. Files Changed Summary

Xem `git log --oneline` trên branch `fix/production-ready-accounting-agent-layer`.
