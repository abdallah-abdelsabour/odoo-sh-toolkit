# Unofficial status

Odoo.sh has no public API. odouche talks to what the Odoo.sh web interface uses, with your own
session.

- odouche is **not affiliated** with, endorsed by, or supported by Odoo S.A.
- It **can break without notice** whenever Odoo.sh changes.
- It acts with **your own access** to Odoo.sh and nothing more.
- Staying within **your agreement with Odoo** is your responsibility.

## Pacing

odouche makes the requests the web interface would make, one at a time. It repeats only a read that
failed on the network or at a gateway, at most twice and after a pause. Polling for a build or a log never runs faster
than the interface polls.

It sets no other limit: a tool that calls the library in a loop spaces its own calls.

## What Odoo's terms say

Read on 2026-10-01. This is the maintainer's reading of public documents, not legal advice, and
the terms can change after that date.

| Document | What it says |
| --- | --- |
| [Odoo Enterprise Subscription Agreement](https://www.odoo.com/documentation/19.0/legal/terms/enterprise.html), version 13 of 2026-09-24 | Section 6.1: customers of the Cloud Platform keep their accounts secure, do not share their password, make "a reasonable use of the Hosting Services" and observe the Acceptable Use Policy. |
| [Odoo Cloud Acceptable Use Policy](https://www.odoo.com/acceptable-use), last updated 2025-05-07 | Forbids sending large numbers of RPC or API calls without throttling, crawling that affects availability or performance, and security research with automated tools. Throttled calls are "typically acceptable for unsustained usage at a rate of 1 call/second, with no parallel calls". A violation can suspend the subscription without notice. |
| [Odoo.sh FAQ](https://www.odoo.sh/faq) | Use of Odoo.sh is subject to the Acceptable Use Policy. A platform API is not planned. |

None of them forbids automated access outright. What they limit is rate and abuse, which the
pacing rule above is there to respect.
