# QA Regression Report — 2026-02-12 07:03:03Z

- UI: `https://app.welliam.codes`
- API: `https://app.welliam.codes/agent/v1`
- Scope: 9-tab smoke + OCR/Journal/Reconcile/Risk/Forecast/Q&A/Reports/Feeder

## Tab Smoke

| Tab | Result |
|---|---|
| dashboard | PASS |
| ocr | PASS |
| journal | PASS |
| reconcile | PASS |
| risk | PASS |
| forecast | PASS |
| qna | PASS |
| reports | PASS |
| settings | PASS |

## Flow Results

| Flow | Status |
|---|---|
| ocr | PASS |
| journal | PASS |
| reconcile | PASS |
| risk | PARTIAL |
| forecast | PASS |
| qna | FAIL |
| reports | PARTIAL |
| feeder | PASS |

## Evidence (Endpoint Highlights)

### ocr
```json
[
  {
    "attachments_event": {
      "ts": 1770879715.544284,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/attachments",
      "status": 200,
      "post_data": ""
    }
  },
  {
    "vouchers_before": 0,
    "vouchers_after": 1,
    "vouchers_status_code": 200
  }
]
```

### journal
```json
[
  {
    "review_event": {
      "ts": 1770879723.6968405,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/acct/journal_proposals/6ede8cb8-fa7a-4b63-b2e4-82fd5a939978/review",
      "status": 200,
      "post_data": "{\"status\":\"approved\",\"reviewed_by\":\"web-user\"}"
    }
  }
]
```

### reconcile
```json
[
  {
    "auto_match_run_event": {
      "ts": 1770879730.5513186,
      "method": "GET",
      "url": "https://app.welliam.codes/agent/v1/runs/ac2bb8ce-a67f-4971-930a-013e7d349b0c",
      "status": 200,
      "post_data": ""
    }
  },
  {
    "manual_match_event": {
      "ts": 1770879732.6176145,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/acct/bank_match",
      "status": 200,
      "post_data": "{\"bank_tx_id\":\"62122e20-5a29-4f29-8f02-700c5f7a9b33\",\"voucher_id\":\"d19e0525-ccf7-4c8b-b3c5-d2f877a87792\",\"method\":\"manual\",\"matched_by\":\"web-user\"}"
    }
  },
  {
    "auto_before": 0,
    "auto_after": 0,
    "manual_before": 0,
    "manual_after": 1,
    "bank_tx_status_code": 200
  }
]
```

### risk
```json
[
  {
    "resolve_event": {
      "ts": 1770879736.4805222,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/acct/anomaly_flags/140e76dc-d198-43f1-9aa8-d15e961fee60/resolve",
      "status": 409,
      "post_data": "{\"resolution\":\"resolved\",\"resolved_by\":\"web-user\"}"
    }
  }
]
```

### forecast
```json
[
  {
    "forecast_get_event": {
      "ts": 1770879739.7757592,
      "method": "GET",
      "url": "https://app.welliam.codes/agent/v1/acct/cashflow_forecast?horizon_days=365",
      "status": 200,
      "post_data": ""
    }
  },
  {
    "forecast_run_code": 200,
    "forecast_run_body": "{'run_id': '7b6e6b0e-914b-4fab-80ac-3f5f62388c66', 'status': 'queued', 'idempotency_key': 'ba7e3cf325b02dc766fabc80313bd5e29a8a863c'}"
  }
]
```

### qna
```json
[
  {
    "api_checks": [
      {
        "code": 200,
        "llm_used": true,
        "has_reasoning_chain": false
      },
      {
        "code": 200,
        "llm_used": true,
        "has_reasoning_chain": false
      },
      {
        "code": 200,
        "llm_used": true,
        "has_reasoning_chain": false
      }
    ]
  },
  {
    "ui_qna_event": null,
    "ui_feedback_event": null
  }
]
```

### reports
```json
[
  {
    "api_validate_code": 200,
    "api_preview_code": 200,
    "api_generate_code": 200,
    "api_generate_body": "{'id': '6aabc565-8ff7-490a-9c46-29107b3ee383', 'report_id': '6aabc565-8ff7-490a-9c46-29107b3ee383', 'type': 'balance_sheet', 'period': '2026-02', 'version': 1, 'format': 'json', 'download_url': '/agent/v1/reports/6aabc565-8ff7-490a-9c46-29107b3ee383/download', 'created_at': '2026-02-12T07:02:41.444598Z'}"
  },
  {
    "ui_preview_event": {
      "ts": 1770879763.0575376,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/reports/preview",
      "status": 200,
      "post_data": "{\"type\":\"balance_sheet\",\"standard\":\"VAS\",\"period\":\"2026-02\"}"
    },
    "ui_validate_event": {
      "ts": 1770879767.1084921,
      "method": "GET",
      "url": "https://app.welliam.codes/agent/v1/reports/validate?type=balance_sheet&period=2026-02",
      "status": 200,
      "post_data": ""
    },
    "ui_generate_event": null
  }
]
```

### feeder
```json
[
  {
    "start_event": {
      "ts": 1770879773.941495,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/vn_feeder/control",
      "status": 200,
      "post_data": "{\"action\":\"start\",\"events_per_min\":3}"
    },
    "inject_event": {
      "ts": 1770879778.2397575,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/vn_feeder/control",
      "status": 200,
      "post_data": "{\"action\":\"inject_now\",\"events_per_min\":3}"
    },
    "stop_event": {
      "ts": 1770879780.8395872,
      "method": "POST",
      "url": "https://app.welliam.codes/agent/v1/vn_feeder/control",
      "status": 200,
      "post_data": "{\"action\":\"stop\",\"events_per_min\":3}"
    },
    "status_before": {
      "running": true,
      "events_per_min": 3,
      "total_events_today": 0,
      "last_event_at": "",
      "avg_events_per_min": 0,
      "sources": [],
      "updated_at": ""
    },
    "status_after_inject": {
      "running": true,
      "events_per_min": 3,
      "total_events_today": 0,
      "last_event_at": "",
      "avg_events_per_min": 0,
      "sources": [],
      "updated_at": ""
    },
    "status_after_stop": {
      "running": false,
      "total_events_today": 1,
      "last_event_at": "2026-02-12T07:02:53.978193",
      "avg_events_per_min": 8.7,
      "sources": [
        {
          "source_name": "APPEN_VN_OCR",
          "total": 169,
          "sent_count": 1,
          "pct_consumed": 0.6
        },
        {
          "source_name": "RECEIPT_OCR",
          "total": 161,
          "sent_count": 0,
          "pct_consumed": 0.0
        },
        {
          "source_name": "MC_OCR_2021",
          "total": 170,
          "sent_count": 0,
          "pct_consumed": 0.0
        }
      ],
      "updated_at": "2026-02-12T07:03:00.829729",
      "events_per_min": 3
    },
    "codes": [
      200,
      200,
      200
    ]
  }
]
```

## Console Errors

- error: Failed to load resource: the server responded with a status of 409 ()

## Remaining Issues

- risk: PARTIAL
- qna: FAIL
- reports: PARTIAL