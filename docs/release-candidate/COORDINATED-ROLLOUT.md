# Coordinated rollout gates

Backend migration head: c1e2f3a4b5c6. Apply additive migrations before enabling new dashboard/student clients. Keep authoritative enrollment pull disabled until the Sampark source endpoint, machine key, identity mapping and revocation workflow pass staging acceptance. Verify course/college assignments before scoped staff rollout. Legacy report guards have incomplete workflow parity and must not be deployed alone.

Before production: verified database backup reference, CI on the exact release SHAs, authenticated staging checks for authoring/publication/count delivery/answer recovery/course access/personal grant and revoke, and a tested rollback retaining additive schema. Configure matching production AWS account guards; never disable them to force release.

Production workflow dispatch is separate from pushing a candidate branch. Unimplemented human/video, adaptive, provider and visual features must not be represented as delivered.
