# Provider Failure Recovery Runbook

Covers both external providers Matchwell integrates with today: Stripe
(billing) and the pilot's manual/synthetic screening intake. Both share
the same idempotent-receipt-and-retry shape, so this runbook is written
once and applied to both.

## Shared model

Every provider callback (a real Stripe webhook, or a normalized screening
provider event) is recorded as a receipt row
(`billing_webhook_receipts` / `screening_event_receipts`) **before**
anything else happens:

- The receipt's `(provider, provider_event_id)` pair is unique. A replayed
  or duplicate delivery is acknowledged (so the provider does not keep
  retrying) without reprocessing or overwriting the original outcome.
- `applied = false` until the event is fully, successfully processed.
  Nothing here ever claims success prematurely.
- `unresolved_reason` is a short, safe, constrained code (never a raw
  payload, screening report, or provider secret) explaining why an event
  did not apply: `unrecognized_event_type`, `unresolvable_member`,
  `out_of_order_event` (billing only), or a `ScreeningReasonCode` value
  (screening only).
- `center_id` is populated only once the event resolves to a member. An
  event that never resolves has `center_id = NULL` and is therefore never
  shown to any Center admin's failure queue -- see "Unresolvable events"
  below.

## Triage: Billing → Webhook failures / Screening failures

Sign in as an administrator and open **Pilot operations → Billing →
Webhook failures** or **Screening failures**. Each row shows the
provider, event type, event ID, safe reason code, and received time --
nothing sensitive.

For each unapplied event:

1. **`unrecognized_event_type`**: the provider sent an event type this
   pilot does not model. Confirm in the provider's own dashboard whether
   this event type needs to be added to `ProviderEventType` /
   `ScreeningReasonCode`; if so, that is a code change, not an operator
   action.
2. **`unresolvable_member` / screening's "unknown case" outcome**: the
   provider's customer/reference ID does not match any Matchwell record.
   Common causes: the checkout session was started but the member record
   was later deleted (should not happen in this pilot), or a
   test/duplicate provider account was used. Cross-reference the
   provider's own dashboard by the event ID to identify the real member,
   then take the matching manual action (e.g.
   `grant_complimentary_entitlement` / `record_screening_status`) once you
   know who it is.
3. **`out_of_order_event`** (billing only): a newer state already applied;
   this older event is correctly ignored. No action needed unless the
   *newer* state also looks wrong, in which case investigate the provider
   event history directly in Stripe's dashboard.

## Unresolvable events without a Center

This pilot has only one privileged scope: Center administrator. There is
no platform-wide/global-scope role. An event that never resolves to a
member (and therefore never resolves to a Center) is, by design, **never
shown to any Center admin** -- showing it to any single Center's admin
would leak the existence of a cross-Center or unattributed provider event
to someone who should not see it.

**Operational consequence:** those receipts are only recoverable by an
engineer with direct database access, querying
`billing_webhook_receipts` / `screening_event_receipts` for
`center_id IS NULL AND applied = false`. This is an accepted, deliberate
tradeoff for the pilot's single-admin-scope model; document any recurring
pattern here so a future milestone can decide whether a platform-scope
role is warranted.

## Recovery, replay, and idempotency evidence

- `tests/test_billing_repository.py` exercises: signature verification,
  duplicate/replayed event IDs, out-of-order subscription updates,
  unresolved-member events, and refunds -- all without ever storing a
  payment instrument.
- `tests/test_account_hardening.py::test_process_screening_provider_event_is_idempotent_and_scoped`
  exercises the equivalent flows for screening: a normal apply, an exact
  replay (acknowledged, not reprocessed), a malformed event, and an
  unresolvable reference -- confirming each unapplied receipt is still
  safely queryable directly even though it is invisible to the Center
  admin queue.
- Both pipelines are exercised through `PilotService.process_billing_webhook_event`
  / `PilotService.process_screening_provider_event`, so an administrator
  or a future real screening adapter has one call to make and one
  idempotency guarantee to rely on.

## What this pilot does not do

- It does not add a live screening provider integration or any real
  provider secret. `ScreeningProviderEvent` is a normalized, provider-neutral
  shape a future adapter would construct after verifying a real provider's
  signature; today it is only exercised with synthetic events in tests.
- It does not automatically retry a failed event on a timer. Recovery is
  operator-triggered through the admin queue today.
- It does not store a screening report, payment instrument, or any free
  text from either provider, in the receipt or anywhere else.
