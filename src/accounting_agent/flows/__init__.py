"""Accounting Flows – Journal Suggestion & Bank Reconciliation.

These are integrated into the Celery dispatch_run via run_type:
  - "journal_suggestion" → flow_journal_suggestion()
  - "bank_reconcile"     → flow_bank_reconcile()

Both flows are READ-ONLY: they create proposals/flags but NEVER post to ERP.
"""
