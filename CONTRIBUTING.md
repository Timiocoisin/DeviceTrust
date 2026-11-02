# Contributing

Keep changes focused and describe the trigger, expected behavior, and validation in a pull request. Use synthetic devices and a separate test database; never include configuration secrets, real fingerprints, exported logs, or authorization codes in an issue or patch.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The offline suite generates its own process-local test keys and does not require MySQL. Test database-backed policy, tenant filtering, CSV handling, and WebSocket changes separately against a disposable instance. Do not run startup migrations against an operational database as part of an unrelated change.

Protocol changes must account for deployed clients, existing authorization codes, null-padding behavior, and clock/expiration semantics. Keep documentation examples aligned with actual response fields. Do not add fixed fallback secrets to make tests or setup easier.

Frontend changes should retain usable keyboard focus, responsive tables, reduced-motion support, and explicit distinctions between local examples and live requests. The application uses plain Flask templates and local assets; avoid introducing a build pipeline for a small presentation change.

Report security issues privately as described in [SECURITY.md](SECURITY.md).
