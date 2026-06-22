# Sklik Integration

Read-only audit/export layer for Sklik Drak and Seznam Fenix.

Current implementation goals:

- keep secrets only in `.env.sklik.local`
- keep export workflow separate from Google / Meta / LinkedIn
- run best-effort discovery and export with manifest warnings instead of hard crashes
- sanitize raw payloads before writing them to disk

The integration intentionally avoids any write/update business methods.
