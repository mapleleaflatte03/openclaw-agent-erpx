# Nguyên Tắc Thiết Kế Tiered Agent (Accounting Agent Layer ERPX)

## 1. An toàn pháp lý là ưu tiên tuyệt đối
- Không tự động ghi sổ hoặc sửa số liệu ERPX.
- Luôn có maker-checker (duyệt 2 lớp cho rủi ro cao) + evidence pack + nhật ký bất biến.
- Disclaimer rõ ràng: Agent chỉ trợ giúp, không thay trách nhiệm nghề nghiệp.

## 2. Giá trị chính đến từ Tier B (tóm tắt nghĩa vụ + gom bằng chứng)
- Tier B phải tiết kiệm thời gian ngay lần đầu dùng (precision cao cho phần quan trọng, recall cân bằng qua candidate).
- UI tách rõ: high-confidence (ít nhưng chắc) + candidate (giới hạn số lượng, ẩn thêm nếu cần) để tránh noise.

## 3. Khởi đầu bằng rule + gating + feedback, không lao vào ML nặng
- Rule + pattern + gating để ra kết quả an toàn từ đầu.
- Feedback như feature: micro (nút Đúng/Sai) + implicit (hành vi duyệt/sửa/xóa) + review đợt định kỳ.
- Dài hạn: Dùng feedback để nâng cấp rule/hybrid nhẹ (không fine-tune LLM lớn sớm).

## 4. Propose luôn nằm sau nhiều lớp kiểm soát
- Gating tier + phân loại rủi ro + duyệt (2 lớp cho cao).
- Kiểm tra định kỳ mẫu ngẫu nhiên + drift-alert tự động (tỉ lệ reject cao → hạ confidence hoặc flag).

## 5. Audio và use-case nặng là tầng sau
- Audio optional, tách pipeline riêng, quota chặt (không ảnh hưởng core PDF/email).
- Ưu tiên ổn định Tier B trên PDF/email trước.

## 6. Đối tượng triển khai đầu tiên: pilot nội bộ / mid-market ngành rối
- Không cố ôm SME từ đầu.
- Dùng pilot mid-market (xây dựng/dịch vụ/dự án) để lấy case study + data thật, sau hạ dần xuống SME.

> File này là source-of-truth cho kiến trúc. Mọi thay đổi phải được đối chiếu với bộ nguyên tắc này.
