# Device protocol

The application keeps the existing wire format for compatibility. This is a description of the current implementation, not a recommendation for a new cryptographic protocol. See [security boundaries](../SECURITY.md).

## Request authorization

`POST /authorize`, `Content-Type: application/json`:

```json
{
  "cpu_id": "EXAMPLE-CPU-ID",
  "encrypted_info": "<base64-encoded-encrypted-hardware>",
  "tenant": "yuanyi"
}
```

`cpu_id` and `encrypted_info` are required. An omitted tenant selects `lvshui`; YuanYi clients explicitly use `yuanyi`.

Hardware plaintext is UTF-8:

```text
cpu_id|motherboard_id|disk_id|memory_id|ip_address
```

Pad with null bytes to an AES block boundary, encrypt with AES-ECB using the privately provisioned key, then Base64-encode. The outer CPU ID must match the decrypted value. Admission also depends on the selected business line's allowlist, state, expiration, device blocking, rate limits, and daily limit.

An accepted response has these fields:

```json
{
  "auth_code": "<authorization-code>",
  "device_fingerprint": "<16-hex-character-fingerprint>",
  "hardware_changed": false,
  "is_new_device": true,
  "tenant": "yuanyi"
}
```

The fingerprint is the first 16 hexadecimal characters of SHA-256 over the normalized CPU/board/disk/memory string. It is a software-derived identifier, not proof that hardware is genuine.

## Verify a code

`POST /verify_auth_code`:

```json
{"auth_code": "<authorization-code>"}
```

Successful responses contain `valid: true` and a `payload` with CPU, motherboard, disk, memory and issue-time fields. Invalid or expired codes return HTTP 400 with `valid: false` and `error`. The code payload uses DES-CBC with null padding and a configured IV; the last field is a server-local issue timestamp.

Verification checks code decoding/age. It is not a second admission request and does not re-run the CPU allowlist or current device-block policy. Do not treat an old code as an always-current admission decision.

## Errors and examples

Authorization errors use an `error` field, with HTTP 400 for invalid input/admission data, 403 for device blocking or daily-limit failures, 429 for rate limits, and 500 for server failures. Full examples and the current table are maintained in `/docs`.

Use `request_auth.py` for actual hardware collection and compatible encryption. Documentation language/response tabs and the animated flow are local examples; only submitting a live-request form calls the server.
