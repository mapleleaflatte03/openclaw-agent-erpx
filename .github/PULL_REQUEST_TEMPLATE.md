## Summary

<!-- What does this PR do? Link to relevant issue(s). -->

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
