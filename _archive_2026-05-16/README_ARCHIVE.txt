ARCHIVED — ab_review.py (LLMRoute A/B test review script)
==========================================================
Original purpose:    Pull /root/llmroute/ab_test.jsonl from VPS via SSH and
                     compare 3-tier vs 5-tier routing performance over a
                     one-week A/B window.
Script header date:  2026-03-23 (one-week A/B run scheduled then)
Archived on:         2026-05-16
Archived by:         Lucas (session handoff from chat-Claude)

Why archived:
  - References stale Hetzner IP 72.62.168.181 in 4 places (lines 18, 110,
    128, 138). Canonical Hetzner IP is now 5.78.179.50. The 72.62 host is
    still alive but we no longer have SSH access (key not in authorized_keys).
  - Source log /root/llmroute/ab_test.jsonl does NOT exist on the current
    canonical VPS (5.78.179.50). Nothing to read even if IPs were fixed.
  - Never wired into cron on Cutter74 OR Hetzner.
  - Never referenced by Mother orchestrator or any other live script.
  - The architecture it was built to A/B test (3-tier vs 5-tier upgrade)
    has been fully superseded by the May 15 cascade ladder
    (T1 DeepSeek → T2 Gemini 2.5 Flash → T3 OpenClaw → T4 Sonnet 4.6).
    The original A/B comparison is no longer meaningful.

If cascade-tier telemetry analysis is wanted in the future:
  - The data already lives in /root/llmroute/route_log.jsonl on Hetzner.
  - Per-call: tier, attempted_tier, model, fallback_step, status,
    failure_reason, prompt_tokens, completion_tokens, cost_usd, latency_ms.
  - Build fresh — do not resurrect this script.

Safe to permanently remove after 2026-08-16 (90-day grace period).
