---
name: church-admin
description: Use when the user wants FirstUPCJax Planning Center volunteer schedules, service plans, assignments, or official Scheduling Emails with Accept and Decline buttons through FUPCJ Server.
---

Use the connected FirstUPCJax Admins tools. The server enforces the invited Vision
account's role; possession of this plugin does not grant church access. Never ask
for the provider credential in chat. If connection fails, use Connect and sign in
through Vision with the invited email. Never claim installation proves connectivity.

Interpret dates in America/New_York. Resolve named people with find_people; inspect
service types, future plans, exact service times, positions and availability. Do
not guess IDs or reuse historical IDs as active assignments. Ask only when names,
services or conflicts are genuinely ambiguous. Preview the exact assignment.
Schedule unconfirmed, preserving the person's ability to Accept or Decline.
Reuse an existing exact assignment; never override a decline or a conflict without
the user's specific instruction. Verify the returned assignment and service date.

When the user explicitly asks to notify a scheduled person, use
send_scheduling_email with the verified assignment, person, plan and service-type
IDs and a fresh request_id of 16–100 ASCII letters/digits/hyphens/underscores.
Reuse that request_id if the same call's response is uncertain. One request covers
one person. A request to schedule and notify authorizes these two actions together;
do not insert an unnecessary extra confirmation for a verified, unambiguous match.
Do not send if the user asked only to prepare or preview.

prepare_notification only prepares a notification. It does not dispatch one.
Poll notification_job using the returned job_id. Only state=sent confirms this
attempt was recorded by Planning Center. already_sent means no new email was sent.
queued, running, waiting_for_login and sending are pending. For waiting_for_login,
ask the owner to run the installed Planning Center sign-in helper on FUPCJ Server.
For needs_review, never resubmit or set resend=true automatically: inspect the exact
plan and provider timestamp first. An explicit resend request is required to resend
an already-sent notification. Report scheduling and email outcomes separately.

Official notifications use Planning Center's own Scheduling Email composer/template
and response links. Never substitute Gmail or fabricate Accept/Decline URLs.
If the UI changed or a recipient is ambiguous, report the failed job and exact plan
link; preserve the verified assignment. The worker never uses a generic remote
shell, arbitrary URL browser task, or invented email dispatch API.

The owner manages invited emails and viewer/scheduler roles at
https://jrdn-r.github.io/vision/admins/ . Each person signs into their own Vision
account and connects the plugin; a ChatGPT email address is not assumed to match
Vision. Revocation blocks future calls and queued sends. Keep all credentials,
access tokens, saved browser state and internal logs out of replies.
