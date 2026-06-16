LinkedIn integration module for the Google Ads Audit Exporter workspace.

This package keeps LinkedIn audit logic isolated from Google and Meta flows:

- connections and OAuth
- discovery
- mapping
- per-context export
- reporting
- lead sync
- GTM cross-check
- landing page scan
- audit rules

The default mode is read-only. Any future write actions must stay behind an explicit feature flag.
