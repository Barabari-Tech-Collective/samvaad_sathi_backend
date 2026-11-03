# ADR-2026-11-03: Programme-scoped learner roster independent of courses

**Status:** Proposed

**Date:** 3 November 2026

**Deciders:** Product and engineering

## Context

Placement teams need to import a CSV/XLSX learner list, invite people by email,
and schedule AI or human interviews before a learner has enrolled in a course.
Today, `course_id` is both a useful learning filter and the authorization scope
for scheduling. That coupling makes a zero-enrollment course look like an empty
placement roster. Removing course checks would be unsafe: it would expose every
learner to staff who happen to have placement access.

## Decision

Introduce a programme-scoped roster as a separate authorization and lifecycle
model. Courses remain optional learning/reporting groups; they are not required
for invitation, placement assignment, or interview scheduling.

Each imported learner is a `programme_roster_member` owned by one programme/team
scope. It holds a normalized email, optional external learner ID, display name,
import batch, invite status, and an optional claimed `user_id`. The composite
scope plus normalized email/external ID is unique. A roster member is immutable
history; a later spreadsheet import updates the member only with an idempotency
key and a recorded audit event.

An assignment is created for a roster member, not by assuming the learner already
has a product account. If it is unclaimed, the invitation is queued through
Central Auth and the assignment stays pending. On the first verified Central
Auth sign-in, the service atomically claims matching pending entries using the
central identity and normalized email, then publishes the existing assignment
to the student dashboard. A claimed entry may be linked to one local user only.

Staff can query and act on roster members only when they have active membership
of the owning programme/team, or explicitly granted programme-wide access.
All list endpoints are indexed and paginated; no browser receives a full
roster by default.

## Options considered

### Keep courses as the roster

This is already implemented and preserves scope, but fails the placement workflow
for learners who have not joined a course.

### Remove course authorization

This would be quick, but it makes a global student directory and allows a staff
member to discover or schedule learners outside their responsibility. Rejected.

### Programme roster with optional course links

This preserves least-privilege access while supporting pre-enrolment invitation
and scheduling. It adds a migration and claim workflow, but cleanly separates
programme operations from learning delivery. Chosen.

## Consequences and guardrails

- Imports accept CSV/XLSX only, enforce byte/row/column limits, stream where
  possible, normalize email with a single implementation, and never execute
  spreadsheet formulas.
- A row is rejected when its email is malformed, its identity is ambiguous, or
  it conflicts with an already-claimed account outside the programme scope.
- Invite sends use a durable outbox and an idempotency key. A retry cannot issue
  duplicate invitations or duplicate interview assignments.
- Human and AI interview creation verifies the roster scope in the same database
  transaction as the assignment. Pending learners cannot receive a meeting link
  until their invite is accepted; staff see the pending state instead.
- Existing course-bound records keep their course scope. The rollout adds a
  nullable `roster_member_id` with exactly one valid audience scope per new
  assignment, rather than rewriting historic records.
- Audit events record imports, invite requests, claims, revocations, assignments,
  and releases. Access revocation immediately removes the learner from staff
  reads and cancels unsent invitations.

## Delivery order

1. Add the roster, import-batch, invite-outbox, and audit migrations plus
   programme-scope authorization tests.
2. Build CSV/XLSX preview, validation, idempotent commit, and invite dispatch.
3. Claim unclaimed roster entries in the fixed Central Auth callback.
4. Add roster-member support to AI and human interview assignment APIs, then
   update the staff scheduler and learner dashboard.
5. Run migration, concurrency (1,000 simultaneous claims/imports), authorization,
   and end-to-end invitation-to-dashboard tests before enabling it in staging.
