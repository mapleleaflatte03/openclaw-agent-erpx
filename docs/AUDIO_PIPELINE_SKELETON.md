# Audio Pipeline — Skeleton & Roadmap

> Trạng thái: **Chưa triển khai** — tài liệu này mô tả kiến trúc dự kiến.
> Nguyên tắc #5: "Audio optional, tách pipeline riêng, quota chặt."

---

## 1. Mục tiêu

Cho phép Agent nhận audio (ghi âm cuộc họp, ghi chú bằng giọng nói) làm đầu vào
bổ sung cho hợp đồng, phục vụ trích xuất nghĩa vụ / bằng chứng.

## 2. Kiến trúc dự kiến

```
┌────────────┐     ┌──────────────┐     ┌────────────────┐
│  Upload UI │────▶│  Audio Queue │────▶│  STT Worker    │
│ (Streamlit)│     │  (Celery)    │     │ (Whisper/API)  │
└────────────┘     └──────────────┘     └───────┬────────┘
                                                │
                                        transcript (text)
                                                │
                                        ┌───────▼────────┐
                                        │  Existing       │
                                        │  Obligation     │
                                        │  Pipeline       │
                                        └─────────────────┘
```

### Thành phần

| # | Component            | Mô tả                                              | Ưu tiên |
|---|----------------------|-----------------------------------------------------|---------|
| 1 | Upload endpoint      | `POST /agent/v1/audio/upload` — nhận file ≤50 MB   | P1      |
| 2 | Audio Celery task    | `transcribe_audio` — gọi STT, lưu transcript       | P1      |
| 3 | STT adapter          | Interface cho Whisper local / OpenAI Whisper API    | P1      |
| 4 | Quota guard          | Rate limit per user, max file size, daily cap       | P1      |
| 5 | Transcript → Oblig.  | Pipe transcript text vào obligation extraction      | P2      |
| 6 | Speaker diarization  | Phân biệt người nói (optional)                     | P3      |

## 3. Quota & giới hạn

- Max file size: **50 MB** (≈ 30 phút MP3 128kbps)
- Daily cap per tenant: **10 files** (configurable)
- Supported formats: `.mp3`, `.wav`, `.m4a`, `.ogg`
- Timeout: 5 phút / file

## 4. Điều kiện kích hoạt (Gate)

Chỉ triển khai audio khi:
1. Tier B obligation extraction trên PDF/email ổn định (reject rate < 15% trong 30 ngày)
2. Có ít nhất 1 pilot partner yêu cầu audio
3. Infra STT đã sẵn sàng (GPU node hoặc API budget)

## 5. Migration plan

```
0006_add_audio_transcript.py
- Table: audio_transcript
  - id (PK)
  - case_id (FK → agent_contract_cases)
  - file_name
  - file_size_bytes
  - duration_seconds
  - transcript_text
  - stt_model
  - status (queued | processing | done | error)
  - created_at
```

## 6. Rủi ro

| Rủi ro                          | Giảm thiểu                                    |
|---------------------------------|-----------------------------------------------|
| Chi phí STT API cao             | Quota chặt + fallback Whisper local            |
| Chất lượng transcript thấp      | Hiển thị transcript để user review trước dùng  |
| Ảnh hưởng core pipeline         | Tách queue riêng, không share worker pool      |

---

> Khi bắt đầu triển khai, tạo issue trên GitHub và link về tài liệu này.
