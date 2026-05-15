# ADR 004: Gateway Target IAM Auth via AWS-CLI Post-Apply Workaround

- **ADR Number:** 004
- **Title:** Gateway Target IAM Auth via AWS-CLI Post-Apply Workaround
- **Status:** **Superseded by [ADR 005](005-gateway-tools-via-lambda-targets.md) (2026-05-15)** — this ADR worked around a `hashicorp/aws 6.44.0` provider gap specific to `mcp_server` Gateway targets. ADR 005's pivot to `lambda`-type targets removed `mcp_server` targets entirely; implementation then confirmed `lambda`-type targets carry *no* equivalent provider gap, so Terraform manages them natively and the `make gateway-auth` workaround was deleted. The decision recorded here no longer applies to any live code. Retained for the provider-gap history and the §7 pivot narrative. See the §8 addendum below.
- **Date:** 2026-05-13
- **Authors:** Alexey Tigarev

---

## 1. Context

Slice 7 of spec 002 (Gateway-fronted MCP tool surface, per ADR 002's runtime-embedded handlers) provisions an `aws_bedrockagentcore_gateway` plus two `aws_bedrockagentcore_gateway_target` resources backed by the Runtime's MCP server. The Gateway uses `AWS_IAM` inbound auth; the Gateway-to-Runtime outbound leg also requires IAM auth, which the AgentCore API expresses as `IamCredentialProvider { service, region? }` per the [Bedrock AgentCore Control API reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_IamCredentialProvider.html).

The blocker surfaced at Slice 7 sub-task 6's `terraform apply`: both Gateway target resources failed with `ValidationException: IamCredentialProvider is required for mcpServer targets using IAM authentication`. Investigation found the `hashicorp/aws` provider tracks the AgentCore service surface incompletely:

- `hashicorp/aws == 6.44.0` (latest as of 2026-05-13) exposes a `credential_provider_configuration { gateway_iam_role {} }` sub-block on the gateway target resource, but its `Expand` hard-codes `CredentialProvider = nil` on the wire — there is no HCL surface for the required SigV4 `service` field. Provider releases 6.40.0–6.44.0 carry the same gap.
- Two upstream PRs propose adding the field: [#47626](https://github.com/hashicorp/terraform-provider-aws/pull/47626) (open since 2026-04-24) and [#47457](https://github.com/hashicorp/terraform-provider-aws/pull/47457) (open with one approving review since 2026-04-17). Neither has a committed merge target.
- The `awscc` provider's resource index at v1.83.0 confirms `awscc_bedrockagentcore_gateway_target` does not exist — no provider-level fallback.

Without an outbound auth mechanism, Gateway cannot reach the Runtime's MCP `/mcp` endpoint, and Slice 7's USER smoke (the headline ADR 002 architectural demonstration) is unverifiable. The decision is forced by the AWS provider lagging the AgentCore service evolution, with no in-IaC workaround available through the provider ecosystem alone.

---

## 2. Alternatives Considered

### Alternative 1: Wait for upstream PR (#47626 or #47457) to merge

Block Slice 7's USER smoke until the AWS provider gains native `IamCredentialProvider` support, then add the configuration block to both gateway target resources.

- **Pros:**
  - **Single-tool deployment surface.** Once the PR ships, `terraform apply` is the entire deploy. No CLI side-step, no Makefile target.
  - **Zero out-of-band drift in Terraform state.** Everything the Runtime needs lives in `main.tf`; `terraform plan` reflects the true desired state.
  - **No new failure modes to maintain.** Provider upgrade is the only change required to unlock native support.
- **Cons:**
  - **Indefinite blocking on Slice 7 USER smoke.** Both PRs are open with no committed merge target; Spec 002 stays at `Status: Draft`.
  - **Stretches Slice 4's deployed Runtime as a dead-end demo.** The Runtime + Memory + Nova path works end-to-end (Slice 6 verified). Without Gateway-MCP wired, the ADR 002 architectural claim sits as code that never executes.
  - **No leverage to accelerate the PR.** Graphia is a single-developer reference project; waiting means accepting Hashicorp's merge cadence as-is.
  - **Conflict with workflow-first prioritisation.** ADR 003 explicitly accepted operational complexity for tactical movement; waiting on an upstream merge is the opposite trade.

### Alternative 2: `null_resource` + `local-exec` inside Terraform

Add a `null_resource` per Gateway target whose `local-exec` provisioner invokes `aws bedrock-agentcore-control update-gateway-target` to attach `IamCredentialProvider`. `triggers` keyed off the target IDs re-run the provisioner if the targets are replaced.

- **Pros:**
  - **Single-command deploy still works.** `terraform apply` creates the targets *and* attaches IAM auth in one operation; no separate Makefile target.
  - **Drift remains visible to Terraform.** The `null_resource` is in state; `terraform plan` reflects the workaround as an explicit resource rather than an invisible operator dance.
  - **Reversible cleanly when the PR ships.** Delete the `null_resource` blocks in one commit; `terraform apply` removes them from state.
  - **Self-documenting in IaC.** A future contributor reading `main.tf` sees the workaround inline.
- **Cons:**
  - **AWS CLI must exist inside the `./tf` container.** The `hashicorp/terraform:1.13.1` image has no `aws` binary baked in; `local-exec`-style shell-outs would fail unless we maintain a custom container image or bind-mount the host's CLI.
  - **Re-run trigger semantics are subtle.** `null_resource.triggers` re-fires on target replacement but not on AWS-side credential-provider drift; hidden state divergence is possible.
  - **Conflates IaC and operations.** Terraform is for declarative resource state; pushing operational glue into it compromises the "Terraform = desired state" mental model.

### Alternative 3 *(chosen)*: Two-step apply via Terraform + `make gateway-auth` Makefile target

Terraform omits the `credential_provider_configuration` block on both gateway target resources (provider-supported for `mcp_server`: *"If using mcp_server in mcp block with no authorization, it should not be specified."*). A new `make gateway-auth` Makefile target shells out to `aws bedrock-agentcore-control update-gateway-target` post-apply, attaching `{credentialProviderType: GATEWAY_IAM_ROLE, credentialProvider: {iamCredentialProvider: {service: bedrock-agentcore}}}` to each target. The target is chained into `make deploy` and `make redeploy` so the auth attachment is automatic.

- **Pros:**
  - **Unblocks Slice 7 immediately.** The USER smoke can verify Gateway-MCP end-to-end today.
  - **Separation of concerns: Terraform vs. operational glue.** Terraform stays declarative; the operational glue lives in the Makefile, which is already this project's idiom for cross-tool workflows (per `feedback_makefile_build_tooling.md`).
  - **Idempotent + reproducible from a fresh account.** `update-gateway-target` with the same payload twice is a no-op; the chain runs cleanly on any deploy.
  - **Trivial unwind path when PR ships.** ~10-line diff: add the block back to `main.tf`, delete the `gateway-auth` target, remove the chain entries.
- **Cons:**
  - **Visible drift between Terraform state and deployed configuration.** `credentialProviderConfigurations` is populated post-CLI-call but Terraform's view of the targets lacks the field; `terraform plan` doesn't see it.
  - **Host-side AWS CLI dependency.** `make gateway-auth` shells out from the host (the `./tf` container has no CLI). New contributors need AWS CLI 2.28+ installed locally — a host-side dependency the rest of the IaC layer doesn't have.
  - **Discoverability split across three files.** The workaround is documented in `main.tf` (inline comment), the `Makefile` (`gateway-auth` target), and `RESEARCH.md §10`. Architectural intent isn't in one place.
  - **Single-tool deployment surface foregone.** The two-step (`tf-apply` + `gateway-auth`) is chained for convenience but the conceptual model is still two-step.

---

## 3. Decision

Adopt **Alternative 3**: AgentCore Gateway targets are provisioned in two steps. Terraform omits the `credential_provider_configuration` block (provider gap in `hashicorp/aws == 6.44.0`); `make gateway-auth` attaches `IamCredentialProvider` to each target via `aws bedrock-agentcore-control update-gateway-target` post-apply. The Makefile target is chained into `make deploy` and `make redeploy` so the auth attachment is automatic. Workaround remains in place until `hashicorp/terraform-provider-aws` PR #47626 (or the sibling #47457) ships native support.

---

## 4. Decision Rationale

The primary rationale is *fastest path to a verifiable Slice 7 demonstration*, accepting a small, contained workaround as operational debt with a known unwind path.

Waiting for `hashicorp/terraform-provider-aws` PR #47626 (Alt 1) trades indefinite blocking on Slice 7 for IaC purity — both candidate PRs are open with no committed merge target, and Graphia has no leverage to accelerate the cadence. The cost of blocking indefinitely outweighs the cost of carrying a small workaround for the unknown duration.

Pushing the workaround inside Terraform via `null_resource` + `local-exec` (Alt 2) keeps state visibly in IaC but conflates declarative state with operational glue, requires baking AWS CLI into the `./tf` container, and introduces subtle re-run-trigger semantics. The `terraform-conventions` skill discourages this pattern; adopting it locally would set a precedent that conflicts with the wider convention.

The chosen two-step approach — Terraform declares what it can express, the Makefile carries the operational glue (already this project's idiom per `feedback_makefile_build_tooling.md`) — keeps Terraform's declarative posture intact, separates concerns cleanly, and concentrates the unwind to ~10 lines of diff once the provider catches up. Visible drift between Terraform state and deployed configuration is accepted, mitigated by RESEARCH.md §10 and the inline `main.tf` comment.

Primary rationale category: **lowest cost / fastest to ship**.

---

## 5. Decision Consequences

**Trade-offs accepted:**

- Visible drift between Terraform state and deployed configuration — `terraform plan` doesn't see the post-CLI `credentialProviderConfigurations`; RESEARCH.md §10 + the inline `main.tf` comment are the paper trail.
- Host-side AWS CLI dependency — `make gateway-auth` requires AWS CLI 2.28+ installed locally; the rest of the IaC layer doesn't have this dependency.
- Discoverability split across three files — `main.tf`, `Makefile`, `RESEARCH.md §10`. Architectural intent isn't in one place.
- Single-tool deployment surface foregone — even when chained, the conceptual model remains two-step.

**Future implications:**

- Unwind path is well-defined when PR #47626 (or #47457) ships: add `credential_provider_configuration { iam_role_credential_provider { service = "bedrock-agentcore" } }` (or whatever the merged shape ends up being) back to both gateway target resources; delete the `make gateway-auth` target and its chain entries in `deploy` / `redeploy`; pin the provider to the version that ships the change. ~10-line diff.
- Pattern available for similar provider gaps — AgentCore (and other young AWS-service surfaces) will likely exhibit the same lag-behind-service pattern in future. The Terraform-omits + Makefile-CLI-shim shape this ADR establishes is reusable.
- Cedar / policy enforcement on Gateway (deferred to Phase 7 per ADR 002) requires the IAM auth attachment to remain live — unwinding the workaround mustn't accidentally drop the auth.

**Technical debt incurred:**

- Small + mechanical — the workaround is contained: one `make gateway-auth` target + `main.tf` inline comments + the Makefile chain entries + `RESEARCH.md §10`. Unwind is mechanical with no state migration.
- Carrying CLI-payload schema as a moving part — if AWS evolves the `IamCredentialProvider` shape before the provider catches up, the JSON payload in `make gateway-auth` may need updating.
- Coupling on `make redeploy` correctness — every redeploy runs `gateway-auth`; a transient AWS CLI failure fails the whole chain. Mitigated by `set -e` propagating the failure visibly.

---

## 6. References

- Related ADRs:
  - [ADR 002 — Runtime-Embedded Gateway Tool Handlers](002-runtime-embedded-gateway-tool-handlers.md) — parent ADR; ADR 002's architectural claim (Gateway sits in front of the same Runtime container) depends on Gateway being reachable from itself, which this ADR's workaround restores after the provider gap surfaced.
- Related specs:
  - [Spec 002 functional spec §2.4](../spec/002-hosted-agentcore-deployment/functional-spec.md) — names the Gateway-fronted MCP surface as the Phase 2 demonstration target.
  - [Spec 002 technical considerations §2.7](../spec/002-hosted-agentcore-deployment/technical-considerations.md) — the Gateway tool shape (`api`-type targets pointing back at the Runtime's HTTP surface).
  - [`infra/terraform/RESEARCH.md` §9–§10](../../infra/terraform/RESEARCH.md) — Gateway provider research and the credential-provider-gap analysis with upstream PR references.
- External docs:
  - [`IamCredentialProvider` (Bedrock AgentCore Control API reference)](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_IamCredentialProvider.html) — the API shape the CLI invocation populates.
  - [`create-gateway-target` (Bedrock AgentCore Control API reference)](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateGatewayTarget.html) — the API the `make gateway-auth` target calls.
  - [Gateway → MCP servers (AgentCore developer guide)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html) — confirms `IamCredentialProvider` is the supported outbound-auth pairing for `mcpServer` targets.
  - Upstream PRs: [`hashicorp/terraform-provider-aws#47626`](https://github.com/hashicorp/terraform-provider-aws/pull/47626) and [`#47457`](https://github.com/hashicorp/terraform-provider-aws/pull/47457) — track these for the unwind trigger.

---

## 7. Addendum (2026-05-14) — Implementation pivot: Make owns gateway targets entirely

The original implementation (this ADR's §3 as first written) attempted "Terraform creates the targets without `credential_provider_configuration`; `make gateway-auth` attaches `IamCredentialProvider` post-apply via `update-gateway-target`." The first end-to-end deploy uncovered two compounding API constraints that made that shape unworkable:

1. **`CreateGatewayTarget` synchronously probes the Runtime's MCP endpoint** via a `tools/list` call. With no `credentialProviderConfigurations` attached at create time, the probe hits the Runtime's SigV4-required `/mcp` endpoint without auth, fails with `Missing Authentication Token`, and the target lands in `FAILED` state. Terraform errors waiting for `READY` and the target is orphaned outside Terraform state.
2. **`UpdateGatewayTarget` requires the full target spec**, not a partial credential-config patch. Attaching `IamCredentialProvider` post-create would require the Makefile to replicate `target_configuration`, names, descriptions — i.e. duplicate Terraform's job, then fight Terraform's drift-detection on subsequent applies.

**Pivot.** Gateway targets are removed from `infra/terraform/main.tf` entirely. `make gateway-auth` owns their full lifecycle: `create-gateway-target` with `IamCredentialProvider` attached inline at create time (the call AWS accepts on a single-shot basis), idempotent (skip if READY by name; delete-and-recreate if missing or in `FAILED`). Terraform exposes `runtime_mcp_endpoint` as an output so the Makefile reads the URL-encoded ARN from one place. The Gateway resource itself stays in Terraform.

**§3 is corrected to reflect this**: "Terraform owns the Gateway resource; `make gateway-auth` owns target creation with full `IamCredentialProvider` attached inline."

**§5 trade-offs revised**:
- The "visible drift between Terraform state and deployed configuration" trade-off is *eliminated* — Terraform doesn't track targets at all, so there's no drift to manage.
- The "host-side AWS CLI dependency" trade-off remains and is amplified — the CLI now handles create as well as update, so `make gateway-auth` failure modes are broader.
- The "discoverability split across three files" trade-off intensifies — the workaround is more invasive (Terraform no longer references targets at all; their existence is implied by `make gateway-auth`'s body). The `main.tf` comment block on `local.runtime_mcp_endpoint` and the new RESEARCH.md §10 addendum carry the explanation.
- Net assessment: this shape is *cleaner* than the original §3 because the responsibility split is honest (Terraform models the resources its provider can express; the Makefile models the resources it can't), and the unwind is still well-defined.

**§5 unwind path revised**: when PR #47626 ships and `hashicorp/aws` exposes `IamCredentialProvider { service }`, re-introduce `aws_bedrockagentcore_gateway_target.diary_write` + `.diary_read` in `main.tf` with the credential config inline; delete `make gateway-auth`'s create-or-update body (or shrink it to a no-op); the next `terraform apply` adopts the existing targets via `terraform import` (or fresh-creates them after a manual delete). Diff size remains in the ~20-line range, slightly larger than the §5 estimate but still concentrated.

The architectural decision (separation of Terraform's declarative posture from the Makefile's operational glue) is unchanged. The implementation pivoted further down: Terraform stopped pretending to manage targets at all, rather than managing them with an incomplete config.

## 8. Addendum (2026-05-15) — Superseded: Lambda targets have no provider gap

ADR 005 pivoted the Gateway tool surface from `mcp_server` targets to `lambda` targets. That pivot dissolves this ADR's premise rather than adjusting it:

- The `hashicorp/aws 6.44.0` provider gap this ADR worked around (`IamCredentialProvider` not expressible for `mcpServer` targets) is **specific to `mcp_server` targets**. `aws_bedrockagentcore_gateway_target` of `target_type = lambda` is fully expressible in the provider — credential configuration and all — so Terraform owns the targets declaratively again.
- `make gateway-auth` was **removed** from the Makefile. Neither the two-step apply (this ADR's §3) nor the Make-owns-targets pivot (§7) survives in live code.
- ADR 005 §5 had flagged this as an open item ("whether the same gap exists for Lambda targets ... to be verified during implementation"). The implementation answered it: **no gap**.

This ADR is retained as the historical record of the `mcp_server` provider gap and the workaround iterations it drove. It is not a live decision. The unwind path in §5 / §7 is moot — there is nothing to unwind, because `lambda` targets never needed the workaround.
