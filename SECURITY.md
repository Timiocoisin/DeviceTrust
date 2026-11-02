# Security boundaries

DeviceTrust publishes an existing authorization application and its device protocol. A public repository is not evidence that the application is ready for public-internet deployment.

## Current limitations

- Account registration is open, and password reset accepts an email and a new password without proving ownership of that email. Restrict these routes to a controlled operator environment or replace the workflow with verified reset tokens before external deployment.
- Console sessions do not implement administrator/user RBAC. Business-line scoping is logical data separation, not per-user tenant permissions.
- Device payloads use AES-ECB and authorization codes use DES-CBC with a configured IV and null padding. These are legacy interoperability choices without authenticated encryption. Do not reuse this design for a new security protocol.
- Hardware identifiers can be copied or spoofed. A fingerprint is not hardware-backed attestation. Code verification checks decoding/age, not current allowlist or blocking state.
- Event distribution and rate-limit state are in-process. Multiple workers do not automatically share these guarantees.
- Client-IP decisions use request/proxy information. Only trust forwarded headers from a correctly configured, controlled proxy.
- The bundled Werkzeug runner and HTTP examples are for development/trusted-network evaluation. External deployment needs suitable serving, TLS, session protection and an explicit threat model.

## Secrets and operational data

There are no default protocol keys in the published source. Generate private configuration with `scripts/init_env.py`, provision it securely to clients, and keep `.env`, `auth_code.bin`, logs, real device inventories, and database exports outside version control.

If a key is exposed, remove access to the deployment and coordinate server/client key rotation. Existing codes and clients may stop working after a rotation. Deleting a Git commit is not sufficient to recover a leaked secret.

## Reporting

Do not open a public issue containing credentials, keys, personal data, or operational device records. Use GitHub private vulnerability reporting when it is enabled, or contact the repository maintainer privately. Include a minimal reproduction with synthetic values.
