# Security Policy

## Supported versions

Only the latest revision on the default branch and the currently deployed
closed-pilot release receive security updates. Older commits, branches, and
local development snapshots are unsupported.

## Reporting a vulnerability

Do **not** open a public issue, pull request, or discussion for a suspected
vulnerability.

Use GitHub's private vulnerability reporting for this repository:

1. Open the repository's **Security and quality** tab.
2. Select **Advisories**.
3. Select **Report a vulnerability**.

If private vulnerability reporting is unavailable, contact the repository
owner directly through GitHub without including exploit details in a public
channel.

Include:

- the affected component and revision;
- a concise description of the impact;
- reproducible steps or a minimal proof of concept;
- conditions required for exploitation; and
- any suggested mitigation.

Never include real member data, production credentials, authentication tokens,
screening information, payment data, counselor notes, assessment answers, or
message content. Use synthetic identifiers and redact secrets.

## Response expectations

The repository owner will acknowledge a complete report as soon as practical,
normally within five business days. Validation, remediation, and disclosure
timelines depend on severity and operational risk. Reporters will receive
status updates when practical.

Please allow the project a reasonable opportunity to investigate and remediate
the issue before public disclosure. Good-faith research that avoids privacy
violations, data destruction, service disruption, social engineering, and
access beyond what is necessary to demonstrate the issue is welcome.

## Security boundaries

Matchwell treats authentication, authorization, Center isolation, safety
controls, immutable audit events, provider webhook verification, secrets,
screening summaries, private messages, assessments, and counselor information
as security-sensitive surfaces.
