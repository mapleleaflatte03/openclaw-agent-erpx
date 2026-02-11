## Summary

<!-- What does this PR do? Link to relevant issue(s). -->

## Vision Touchpoint

<!-- MANDATORY: copy the exact milestone sentence(s) this PR touches. Do NOT modify wording. -->

Touchpoint tầm nhìn:
<!-- Pick one or more from below and paste as-is:
- "Swarms xử lý hàng loạt đa định dạng với accuracy >98%, tự chuẩn hóa theo quy định VN mới nhất, lưu bản sao + audit trail."
- "Swarms reasoning ngữ cảnh lịch sử + chính sách DN, gợi ý bút toán tối ưu thuế, giải thích đa tầng, read-only 100%."
- "So khớp real-time đa nguồn (ngân hàng, thuế điện tử), phát hiện gian lận cơ bản, gợi ý khắc phục tự động."
- "Quét liên tục real-time, dự đoán rủi ro phổ biến, multi-agent đạt accuracy ~98%, theo chuẩn VN."
- "Dự báo đa kịch bản (hàng nghìn), accuracy >95%, tự điều chỉnh theo dữ liệu mới + sự kiện."
- "Agent hiểu ngữ cảnh toàn ERP + pháp lý VN, diễn giải như chuyên gia cấp cao, tự học từ feedback."
- "Tạo báo cáo động đa chuẩn (VAS/IFRS), tổng hợp + phân tích sâu tự động, dự phòng kiểm toán."
-->

Scope PR: <!-- nâng từ trạng thái A → B (ví dụ: accuracy OCR từ ~85% lên ~90%) -->

Tiến độ hiện tại: <!-- ~X% so với milestone -->

## Type of Change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Docs / CI / infra only

## Checklist

- [ ] `ruff check .` — passes
- [ ] `pytest -q` — passes
- [ ] `python scripts/export_openapi.py && git diff --exit-code openapi/` — no drift
- [ ] `docker compose config -q` — valid
- [ ] Kustomize overlays render (`kubectl kustomize deploy/k8s/overlays/<overlay>`)
- [ ] Design Doc constraints respected (ERPX read-only, audit append-only)
- [ ] Vision milestone wording **NOT modified** (see docs/VISION_ROADMAP.md)

## Evidence

```
compileall=0
ruff=0
pytest=0
openapi_drift=0
compose=0
```

## Risk / Notes

<!-- Any risks, rollback steps, or migration notes? -->
<!-- If temporarily disabling a feature:
  Tạm thời disable <feature> để giữ đường lên mục tiêu: "<copy nguyên câu milestone>"
  Lý do: <why>
  Kế hoạch khôi phục: <when/how>
-->
