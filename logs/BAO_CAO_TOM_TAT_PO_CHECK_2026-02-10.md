# Báo cáo ngắn (<= 1 trang) — PO Check ERP AI Kế toán (Accounting Agent Layer)

**Ngày:** 2026-02-10  
**Tester:** QA Senior (blackbox; không sửa code/config; không ghi secret/internal URL)  

## Môi trường test

- **Mode A (web public):** kiểm thử UI tại `https://app.welliam.codes/` + sanity `/healthz`, `/readyz`.  
- **Mode B (API nội bộ):** có quyền gọi Agent API qua loopback/NodePort với header `X-API-Key` (không nêu key).  

## Kết luận nhanh: ĐÚNG / CHƯA ĐÚNG ý tưởng PO?

**CHƯA ĐÚNG** với ý tưởng PO ở thời điểm kiểm thử, vì 3/4 mục lõi bị fail hoặc chỉ partial.

## Đánh giá theo 4 mục PO

1) **Q&A kế toán VN (TT200/TT133, ví dụ VND, viện dẫn Thông tư): ❌ FAIL**  
- Test 3 câu tối thiểu:
  - “So sánh TK 131 vs 331”: trả lời generic “Xin lỗi…” (không viện dẫn Thông tư).  
  - “Khi nào dùng TK 642 thay vì 641”: trả lời generic “Xin lỗi…” (không viện dẫn Thông tư).  
  - “Khấu hao TSCĐ hữu hình 30 triệu/3 năm”: không trả lời nghiệp vụ, yêu cầu “cung cấp số chứng từ cụ thể”.  
- Có trường hợp trả lời **sai nghiệp vụ/tài khoản** trong lịch sử Q&A (evidence: `output/playwright/acc-06-qna-history-expanded.png`).  
- **Rủi ro CoT:** API trả `meta.reasoning_chain` và UI có expander “Chi tiết xử lý” hiển thị chuỗi này (chuỗi mang tính kỹ thuật/ASCII), không phù hợp tiêu chí “không lộ reasoning/CoT”.

2) **Chuỗi ERP mô phỏng (Chứng từ → Bút toán → Bất thường → Dòng tiền → Kiểm tra & Báo cáo): ⚠️ PARTIAL**  
- Các tab chính load được và có dữ liệu demo/fixtures (evidence: `output/playwright/ui-18-tab-vouchers.png`, `output/playwright/ui-14-tab-journal-proposals.png`, `output/playwright/ui-15-tab-anomaly-flags.png`, `output/playwright/ui-17-tab-cashflow.png`).  
- Tuy nhiên “chuỗi tự chạy theo stream” (mục 3) không đạt, và UI Trigger (mục 4) đang block một số run cần thiết để user tự vận hành bằng UI.

3) **VN Invoice Data Stream + Command Center: ❌ FAIL**  
- Tab Command Center có, nhưng metrics = 0, sources trống; Start/Inject không thấy thay đổi sau quan sát/poll.  
- API control trả “ok” nhưng `vn_feeder/status` không đổi (running=false, totals=0, sources=[]).  
- Không thấy chứng từ mới xuất hiện ngẫu nhiên 1–5/min.  
- Evidence: `output/playwright/ui-21-tab-command-center.png`, `output/playwright/acc-12-command-center-after-inject-click.png`.

4) **UI “Tạo tác vụ” nhập period YYYY-MM và chạy được voucher_ingest/soft_checks: ❌ FAIL**  
- Với `voucher_ingest`: UI **không có ô nhập `period`** (trong khi API yêu cầu bắt buộc) → bấm chạy dẫn tới lỗi/không tạo run đúng. Evidence: `output/playwright/acc-03-trigger-voucher-ingest-selected.png`, `output/playwright/acc-04-trigger-voucher-ingest-run-error.png`.  
- Với `tax_export`: UI cũng không thấy ô `period` (evidence: `output/playwright/acc-07-trigger-tax-export-selected.png`).  

## Top 3 bug/issue cần fix trước khi cho user thật dùng

1) **P0: VN Feeder/Command Center không hoạt động end-to-end**  
Không có “data stream sống”, control không có hiệu ứng, metrics/sources không cập nhật, không có chứng từ mới theo phút.

2) **P0/P1: UI Trigger thiếu input bắt buộc (`period`, `source`) cho run_type quan trọng**  
Block user chạy `voucher_ingest`/`tax_export` từ UI; trái yêu cầu “chạy được từ UI không lỗi”.

3) **P0: Q&A sai nghiệp vụ + thiếu viện dẫn + rủi ro lộ reasoning_chain**  
Hallucination tài khoản là rủi ro vận hành; đồng thời UI đang có xu hướng hiển thị chuỗi “reasoning_chain” dạng kỹ thuật.

