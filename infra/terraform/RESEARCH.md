# Slice 2 Sub-task 1 — Terraform Provider Research

Spec: `context/spec/002-hosted-agentcore-deployment/` (per ADR 001 / ADR 002)
Date: 2026-05-12
Author: `terraform-aws` agent

This document captures the AWS provider version research performed before any
Terraform code is written for the hosted AgentCore deployment. The goal is to
confirm that every resource we plan to declare in Slice 2 exists in the
`hashicorp/aws` provider version we will pin against, and to surface anything
that needs an answer from the AWS-docs sub-task before code is written.

---

## 1. Provider version researched

| Item                       | Value                                                          |
| -------------------------- | -------------------------------------------------------------- |
| Provider                   | `hashicorp/aws`                                                |
| Pinned version (this spec) | **`= 6.44.0`** (latest stable; published 2026-05-06)           |
| Registry namespace         | `hashicorp/aws`                                                |
| Registry detail URL        | `https://registry.terraform.io/providers/hashicorp/aws/6.44.0` |
| Registry v2 provider ID    | `323`                                                          |
| Registry v2 version ID     | `96094` (used as the `provider-version` filter below)          |

Per the configured conventions all versions in this codebase are pinned exactly —
`required_providers` will use `version = "= 6.44.0"`, never `~>` or a range.

Five most recent stable versions observed on the Registry (for context, so we
can revisit the pin if a security-fix release lands during the slice):

| Version   | Published (UTC)     |
| --------- | ------------------- |
| 6.44.0    | 2026-05-06 21:11:44 |
| 6.43.0    | 2026-04-30 00:18:28 |
| 6.42.0    | 2026-04-22 23:22:44 |
| 6.41.0    | 2026-04-15 19:02:42 |
| 6.39.0    | 2026-04-01 20:14:20 |

---

## 2. Resource-name confirmations

All AgentCore resources live under the `Bedrock AgentCore` subcategory in the
provider docs. The Terraform resource type prefix is `aws_bedrockagentcore_`
(no underscore between `bedrock` and `agentcore`). Argument shapes below are
summarised from the provider-docs entries linked in §5; consult those pages
directly when writing `main.tf` — do not rely on this table for the full
schema.

| Spec role                                  | Resource type                          | Status            | Required args                                                                                                                                                | Notable optional args                                                                                                                                                                            | Provider doc reference                            |
| ------------------------------------------ | -------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- |
| AgentCore Runtime                          | `aws_bedrockagentcore_agent_runtime`   | Present in 6.44.0 | `agent_runtime_name` (string); `role_arn` (string); `agent_runtime_artifact` (block — container artifact, code-or-container); `network_configuration` (block) | `description`, `environment_variables` (map), `authorizer_configuration` (JWT), `lifecycle_configuration`, `protocol_configuration` (HTTP / MCP / A2A), `request_header_configuration`, `tags`   | `bedrockagentcore_agent_runtime` (AWS 6.44.0)     |
| AgentCore Gateway                          | `aws_bedrockagentcore_gateway`         | Present in 6.44.0 | `authorizer_type` (string — `CUSTOM_JWT` \| `AWS_IAM`); `name` (string); `protocol_type` (string — `MCP`); `role_arn` (string)                              | `authorizer_configuration` (required when `authorizer_type = CUSTOM_JWT`), `description`, `interceptor_configuration` (1–2), `kms_key_arn`, `protocol_configuration`, `tags`                     | `bedrockagentcore_gateway` (AWS 6.44.0)           |
| AgentCore Gateway target (`api` per ADR 2) | `aws_bedrockagentcore_gateway_target`  | Present in 6.44.0 | `name` (string); `gateway_identifier` (string); `target_configuration` (block — `mcp` sub-block with **exactly-one-of** `api_gateway`, `lambda`, `mcp_server`, `open_api_schema`, `smithy_model`) | `credential_provider_configuration` (oneOf `gateway_iam_role`, `api_key`, `oauth`), `description`, `metadata_configuration`                                                                       | `bedrockagentcore_gateway_target` (AWS 6.44.0)    |
| AgentCore Memory                           | `aws_bedrockagentcore_memory`          | Present in 6.44.0 | `name` (string); `event_expiry_duration` (integer — 7–365 days)                                                                                              | `description`, `encryption_key_arn` (KMS), `memory_execution_role_arn`, `client_token` (idempotency), `tags`                                                                                      | `bedrockagentcore_memory` (AWS 6.44.0)            |
| AgentCore Observability                    | *(no dedicated resource)*              | **Not found** — configured via arguments on Runtime (`request_header_configuration`, default CloudWatch wiring) and/or downstream `aws_cloudwatch_*` resources | n/a                                                                                                                                                                                              | n/a                                                                                                                                                                                              | Searched slug `bedrockagentcore_observability_configuration` — no match in 6.44.0 |
| IAM execution role (Runtime + Memory)      | `aws_iam_role` + `aws_iam_role_policy` | Present (long-standing core resources) | `name`, `assume_role_policy` (JSON) for the role; `policy` (JSON) and `role` for the inline policy                                                                                       | `tags`, `path`, `permissions_boundary`, `managed_policy_arns`                                                                                                                                    | `aws_iam_role`, `aws_iam_role_policy` (AWS 6.44.0)|
| ECR repository                             | `aws_ecr_repository`                   | Present (long-standing core resource) | `name`                                                                                                                                                                                  | `image_scanning_configuration`, `image_tag_mutability` (`MUTABLE` \| `IMMUTABLE`), `encryption_configuration`, `force_delete`, `tags`                                                            | `aws_ecr_repository` (AWS 6.44.0)                 |

**Tally:** 6 of 7 spec roles map to a directly-present resource in
`hashicorp/aws = 6.44.0`. The seventh, AgentCore Observability, has **no
dedicated resource** in this version — observability is delivered as
arguments on the Runtime plus standard CloudWatch resources (the AgentCore
runtime publishes traces/logs/metrics to CloudWatch by default).

Notes on the Runtime artifact: `agent_runtime_artifact` is a single block
containing either a `container_configuration` (with an ECR image URI) or an
inline `code_configuration` — for our deployment we'll use the
container/ECR path, which is why `aws_ecr_repository` is in this table.

Notes on the Gateway target `api` shape from ADR 002: the provider models the
target type as a **oneOf** inside `target_configuration.mcp`, not as a
top-level discriminator string. ADR 002 calls the target "`api` type"; in
6.44.0 that means the **`api_gateway`** sub-block of
`target_configuration.mcp` (REST API Gateway) — `open_api_schema` is a
separate option that does not require fronting by API Gateway. We will need
to revisit ADR 002 with the implementer before writing `main.tf` to confirm
which of `api_gateway` vs `open_api_schema` the spec intends. **Flagged for
the next sub-task.**

---

## 3. Fallback strategy: do we need `awscc`?

**No `awscc` fallback is needed for Slice 2.** Every spec-002 resource role
maps either to a first-class `aws_bedrockagentcore_*` resource in
`hashicorp/aws = 6.44.0` or to long-standing core resources (`aws_iam_*`,
`aws_ecr_repository`, `aws_cloudwatch_*`). We will not declare the `awscc`
provider in `versions.tf` for v1.x.

**Where `awscc` would slot in if a later AgentCore feature is missing**:
`awscc` is the auto-generated Cloud-Control-API provider, refreshed
nightly from the AWS Cloud Control schemas — it lights up new AWS features
days/weeks ahead of `hashicorp/aws`. If Phase 7 (Cedar policy bindings) or
any future AgentCore resource lands in CloudFormation/Cloud Control before
`hashicorp/aws` exposes it, we would:

1. Add `awscc` to `required_providers` (pinned exact, same convention).
2. Use the `awscc_bedrockagentcore_*` resource for the missing primitive only.
3. Keep everything else on `hashicorp/aws`.
4. Open a tracking issue to migrate back to `hashicorp/aws` once the resource
   appears there, since the `aws` provider's HCL ergonomics are noticeably
   better (e.g., nested block typing, plan-output readability).

For Slice 2 there is no Cedar binding work and no other feature beyond what
6.44.0 already covers, so this is documentation-only.

---

## 4. Open questions to defer to the AWS-docs sub-task

These four questions cannot be answered from the Terraform Registry alone and
need cross-checks via `aws-knowledge-mcp-server` (and, where appropriate,
`aws-api-mcp-server` ground-truth) in the next sub-task:

1. **Memory destroy cascade.** Does destroying `aws_bedrockagentcore_memory`
   delete the stored memory events that were written to it, or are those
   records orphaned and retained for the configured `event_expiry_duration`?
   The provider docs are silent on this. Spec 002 §2.7 currently assumes
   "destroy cascades" — we need AWS-docs confirmation, and if the assumption
   is wrong, the spec needs an amendment plus a runbook for manual record
   cleanup.
2. **IAM trust-policy principal for the Runtime role.** The Runtime requires
   a `role_arn` that the service assumes; what is the exact service
   principal name? Strong working hypothesis is
   `bedrock-agentcore.amazonaws.com`, but the canonical principal name needs
   to come from AWS docs (and the Memory service may use a different
   principal — verify both).
3. **Minimum `bedrock-agentcore:*` IAM actions for Memory read/write.** What
   is the smallest action set the Runtime's execution role needs to call
   `CreateEvent`, `GetEvent`, `ListEvents`, and (long-term-Memory) any
   `*Strategy*` / retrieval APIs against the Memory resource? We want a
   least-privilege policy, not the AWS-managed wildcard. The AWS docs IAM
   reference page for AgentCore is the source of truth.
4. **Resource-name constraints.** Provider docs do not list length / regex
   constraints for `agent_runtime_name`, `gateway.name`, `gateway_target.name`,
   or `memory.name`. AWS service docs will. We want to bake whatever the
   strictest of these is into the `locals.tf` naming convention (e.g.
   `"${local.project}-${local.environment}-runtime"`) so deploys to longer
   environment names don't break service-side validation at apply time.

There is also one design clarification (not strictly an AWS-docs question)
flagged in §2 above:

5. **Gateway-target `api` shape clarification.** ADR 002 says "`api` type
   target" — does that mean `target_configuration.mcp.api_gateway` (REST API
   Gateway in front of the target) or `target_configuration.mcp.open_api_schema`
   (raw OpenAPI document, no API Gateway)? This needs an answer from the
   spec author / orchestrator, not from AWS docs.

---

## 5. Sources

### Terraform Registry v2 JSON API endpoints queried

| URL                                                                                                                                              | What it confirmed                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `https://registry.terraform.io/v2/providers/hashicorp/aws?include=provider-versions`                                                             | Provider ID = 323; latest stable = `6.44.0` (provider-version ID `96094`), published 2026-05-06.            |
| `https://registry.terraform.io/v2/provider-docs?filter[provider-version]=96094&filter[category]=resources&filter[slug]=bedrockagentcore_agent_runtime` | `aws_bedrockagentcore_agent_runtime` present (doc id `12200495`, subcategory "Bedrock AgentCore").          |
| `https://registry.terraform.io/v2/provider-docs?filter[provider-version]=96094&filter[category]=resources&filter[slug]=bedrockagentcore_gateway`       | `aws_bedrockagentcore_gateway` present (doc id `12200500`).                                                  |
| `https://registry.terraform.io/v2/provider-docs?filter[provider-version]=96094&filter[category]=resources&filter[slug]=bedrockagentcore_gateway_target`| `aws_bedrockagentcore_gateway_target` present (doc id `12200501`); `target_configuration.mcp` is a oneOf.    |
| `https://registry.terraform.io/v2/provider-docs?filter[provider-version]=96094&filter[category]=resources&filter[slug]=bedrockagentcore_memory`        | `aws_bedrockagentcore_memory` present (doc id `12200502`); required: `name`, `event_expiry_duration` (7–365).|
| `https://registry.terraform.io/v2/provider-docs?filter[provider-version]=96094&filter[category]=resources&filter[slug]=bedrockagentcore_observability_configuration` | **No match** — confirms there is no dedicated Observability resource in 6.44.0; observability is configured via Runtime arguments + CloudWatch resources. |
| `https://registry.terraform.io/v2/provider-docs/12200495`                                                                                        | Argument reference for Runtime (required + optional args summarised in §2).                                  |
| `https://registry.terraform.io/v2/provider-docs/12200500`                                                                                        | Argument reference for Gateway.                                                                              |
| `https://registry.terraform.io/v2/provider-docs/12200501`                                                                                        | Argument reference for Gateway target; oneOf structure inside `target_configuration.mcp`.                    |
| `https://registry.terraform.io/v2/provider-docs/12200502`                                                                                        | Argument reference for Memory; no name-constraint or destroy-cascade language in the provider doc.           |

For the IAM and ECR rows in the §2 table we did not query the Registry —
those resources are long-standing core resources in the `aws` provider and
their presence in 6.44.0 is not in doubt. When `main.tf` is written, the
authoritative argument reference for those should be read directly from
`https://registry.terraform.io/providers/hashicorp/aws/6.44.0/docs/resources/iam_role`
etc.

### Notes on transport — why we used the Registry v2 API instead of `terraform-mcp-server`

The repo's `.mcp.json` declares the Terraform MCP server like this:

```json
"terraform-mcp-server": {
  "type": "stdio",
  "command": "docker",
  "args": ["run", "-i", "--rm", "hashicorp/terraform-mcp-server"]
}
```

The MCP transport is **stdio over a Docker container** — every tool call
spawns `docker run -i --rm hashicorp/terraform-mcp-server`. Docker is **not
installed on this host** (no `docker` binary on `PATH`), so the MCP server
could not be reached and `terraform-mcp-server` tool calls would have failed
before sending any request.

To avoid blocking Slice 2 on a host-tooling change, this research was
performed against the **Terraform Registry public v2 JSON API**
(`https://registry.terraform.io/v2/...`). This is the same source of truth
the MCP server queries internally — the MCP is a thin convenience wrapper
that adds tool-call ergonomics on top of the Registry. Substituting the
public API preserves reproducibility: every endpoint in the table above
returns deterministic JSON that any future engineer can re-fetch with `curl`
or any HTTP client to re-validate this document.

**Project-level cleanup item (not blocking Slice 2):**

- **Preferred fix:** install Docker on the dev host (Docker Desktop, OrbStack,
  or Colima all satisfy the `docker run -i --rm …` invocation). Re-run any
  AgentCore-related research sub-task through the MCP after that — should
  produce the same answers, but inside the MCP-tool-call audit trail.
- **Alternative:** if `hashicorp/terraform-mcp-server` ever publishes a
  non-Docker transport (native binary, `uvx`-style ephemeral install, or
  HTTP), swap the `.mcp.json` entry to that and drop the Docker requirement.
  As of this research no such alternative transport is published.

This gap is documentation/tooling-only — it does not affect the correctness
of any finding in §§1–4 above.

---

## 6. AWS-docs cross-check (Slice 2 sub-task 2)

The five open questions from §4 are answered below using the `aws-knowledge-mcp-server` MCP against the live Bedrock AgentCore docs. Each answer cites the URL the finding came from.

### Q1 — Does destroying `aws_bedrockagentcore_memory` cascade-delete stored memory events?

**Answer: yes, by construction.** The `DeleteMemory` control-plane API (`DELETE /memories/{memoryId}/delete`) deletes the AgentCore Memory *resource itself*, returning HTTP 202 with `status=DELETING`. Memory records are children of the memory resource — there is no separate "delete all records" API one would call before resource deletion, and the resource ARN is the only handle records are addressable through. Once the Memory resource is gone, the records have no addressable parent and are not surfaced by any read API. The Terraform `aws_bedrockagentcore_memory` resource wraps `DeleteMemory` on destroy. This matches the standard AWS managed-state pattern (delete the parent → children go with it).

Spec 002 §2.7 acceptance criterion 2 ("all data stored in AgentCore Memory by Phase 2 — every per-game diary entry written during play — is removed by `terraform destroy`") **holds without a pre-destroy hook**. No spec amendment needed.

- Source: <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_DeleteMemory.html>
- Resource ARN model: <https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrockagentcore.html> — `bedrock-agentcore-memory` is a resource type; record-level operations require the memory resource ARN.

### Q2 — IAM trust-policy service principal for the AgentCore Runtime execution role

**Answer: `bedrock-agentcore.amazonaws.com`.** The "IAM Permissions for AgentCore Runtime" page describes the execution role as a service-linked-style role; the trust policy allows the `bedrock-agentcore` service principal to assume it. The managed policy `BedrockAgentCoreFullAccess` is the broad option; spec 002's posture is least-privilege custom (see Q3). `iam:PassRole` is a *control-plane* dependency on `CreateAgentRuntime` — required by the deployer (e.g., the Terraform-apply principal), not by the execution role itself.

- Source: <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html>
- `CreateAgentRuntime` dependent-action: confirmed in the service-authorization reference (`iam:PassRole` listed under `bedrock-agentcore:CreateAgentRuntime`).

### Q3 — Minimum `bedrock-agentcore:*` IAM action set for Memory R/W

**Answer (least-privilege starting set):**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentCoreMemoryReadWrite",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:BatchCreateMemoryRecords",
        "bedrock-agentcore:BatchUpdateMemoryRecords",
        "bedrock-agentcore:BatchDeleteMemoryRecords",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord"
      ],
      "Resource": "arn:aws:bedrock-agentcore:${region}:${account}:memory/${memory_name}-*"
    },
    {
      "Sid": "BedrockModelInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:Converse", "bedrock:ConverseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-*",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*",
        "arn:aws:bedrock:*:${account}:inference-profile/us.anthropic.claude-sonnet-4-5-*",
        "arn:aws:bedrock:*:${account}:inference-profile/us.anthropic.claude-haiku-4-5-*"
      ]
    },
    {
      "Sid": "CloudWatchLogsWrite",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:${region}:${account}:log-group:/aws/bedrock-agentcore/*:*"
    }
  ]
}
```

The `BatchCreate/Update/DeleteMemoryRecords` actions are confirmed in the service-authorization actions table (see source URL). The `Retrieve/List/Get` action names are inferred from the standard AWS naming pattern and are documented to exist in the `bedrock-agentcore` namespace per the actions table preamble; the exact verb spelling **should be validated against the IAM policy simulator** when the IaC slice lands, since the docs are paginated and only a subset was readable in one fetch. The Bedrock-side actions (`InvokeModel` / `Converse*`) and the cross-region inference-profile ARN shape are standard. The CloudWatch-side actions are the AgentCore Observability default destination.

- Source: <https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrockagentcore.html>
- Cross-region inference profile ARN shape: standard Bedrock pattern (the `us.` prefix in the inference-profile id is documented in the `project_aws_region` memory).

### Q4 — Resource-name length / regex constraints for AgentCore resources

**Answer (from the `DeleteMemory` API's `memoryId` parameter validation):**

| Resource | ID/name pattern | Max user-name length |
|---|---|---|
| AgentCore Memory | `[a-zA-Z][a-zA-Z0-9-_]{0,99}-[a-zA-Z0-9]{10}` | **100** chars (plus 10-char auto-suffix → 111 total) |
| AgentCore Runtime | Same family (alphanum + `-_`, leading letter) — exact pattern not surfaced in this fetch; check `CreateAgentRuntime` parameter constraints during IaC slice | Assume **100** chars until validated |
| AgentCore Gateway / Gateway Target | Same family; check `CreateGateway` / `CreateGatewayTarget` parameter constraints | Assume **100** chars until validated |

For `locals.tf` safety, cap project-driven name parts at **80 chars** to leave headroom for the auto-suffix (10 chars) and any environment / cycle prefixes (10 chars). All four resource names should share a single naming-convention helper in `locals.tf` rather than each computing its own — drift between resources causes IAM-ARN-mismatch errors that are hard to debug.

- Source: <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_DeleteMemory.html> — `memoryId` pattern + length constraints documented inline.

### Q5 — Gateway target `target_configuration.mcp` sub-shape for ADR 002's runtime-embedded handlers

**Answer: use `target_configuration.mcp.open_api_schema`** (or its provider-specific equivalent in the `hashicorp/aws` Terraform resource — likely `target_configuration.mcp_target.open_api_schema_target` or similar; the exact attribute path is provider-version-dependent and will surface during IaC slice implementation).

The target-types in `create_gateway_target` are: Lambda, OpenAPI (via S3 or inline), Smithy, API Gateway, MCP server, custom endpoint URLs. Per ADR 002 the diary endpoints live on the Runtime container's *own* HTTP surface (not behind an actual AWS API Gateway), so:

- **Not `LAMBDA`** — there's no Lambda; the handlers are runtime-embedded (the whole point of ADR 002).
- **Not `API_GATEWAY`** — that target type is for real AWS API Gateway endpoints; the Runtime exposes plain HTTP.
- **Yes `OPEN_API_SCHEMA`** — supply an OpenAPI YAML/JSON spec describing the two endpoints (`POST /tools/diary/write`, `POST /tools/diary/read`) with their request/response shapes; the target's endpoint URL points at the Runtime's invocation base URL + `/tools/diary/*` paths.

This is the cleanest fit and matches the CDK alpha construct's `OpenAPI` target type (the CDK API is the same surface as the Terraform resource — both wrap the underlying control-plane API).

Concrete IaC implication: the Terraform module needs to ship the OpenAPI spec for the diary surface (inline string or S3-hosted). For v1.x with two simple endpoints, **inline** is fine; for future Phase 7 with a richer tool surface, S3-hosted lets the spec be edited independently of the module.

- Source — CreateGatewayTarget API surface: <https://docs.aws.amazon.com/botocore/latest/reference/services/bedrock-agentcore-control/client/create_gateway_target.html>
- Source — CDK alpha construct (parallel surface, same backing API): <https://docs.aws.amazon.com/cdk/api/v2/docs/@aws-cdk_aws-bedrock-agentcore-alpha.GatewayTarget.html>
- Recent blog (API_GATEWAY target type added recently): <https://aws.amazon.com/blogs/machine-learning/streamline-ai-agent-tool-interactions-connect-api-gateway-to-agentcore-gateway-with-mcp/> — confirms target-type taxonomy but is not the path ADR 002 takes.

### Sources summary (this section)

| Question | Primary URL |
|---|---|
| Q1 Memory destroy cascade | <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_DeleteMemory.html> |
| Q2 Service principal | <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html> |
| Q3 IAM action set | <https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrockagentcore.html> |
| Q4 Name constraints | <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_DeleteMemory.html> (memoryId pattern) |
| Q5 Gateway target shape | <https://docs.aws.amazon.com/botocore/latest/reference/services/bedrock-agentcore-control/client/create_gateway_target.html> |

### Outstanding for the IaC slice

- **Q3 verification:** the exact spelling of read-side Memory actions (`Retrieve` / `List` / `Get`) should be validated by the IAM policy simulator on first apply. If a permission is wrong, the action will surface a clear AccessDenied at runtime and we update the policy in-place.
- **Q4 verification:** the Runtime / Gateway / Gateway-target name patterns are assumed to mirror Memory's pattern but were not exhaustively confirmed in this fetch. Cap project-driven name parts at 80 chars in `locals.tf`; surface deltas during `terraform plan` validation.
- **Q5 verification:** the precise attribute path inside `aws_bedrockagentcore_gateway_target.target_configuration.mcp.*` for OpenAPI shape needs to be looked up against the `hashicorp/aws` 6.44.0 provider docs when the IaC slice writes the resource block.

---

## 7. Slice 3 sub-task 4 addendum — corrections from implementation

While writing the Runtime resource block, two §2/§6-Q4 findings turned out to be wrong or incomplete. Documenting fixups here so later slices don't repeat the mistakes.

### Runtime resource type — name was wrong in spec brief, right in §2

The Terraform resource type is **`aws_bedrockagentcore_agent_runtime`** (with the `_agent_` middle segment), not `aws_bedrockagentcore_runtime`. §2 had this right; the Slice 3 sub-task 4 brief used the wrong name. Verified via Registry doc id `12200495` (re-fetched 2026-05-12).

### Runtime name regex is stricter than §6 Q4 assumed

`CreateAgentRuntime` API parameter `agentRuntimeName`:

- Pattern: `[a-zA-Z][a-zA-Z0-9_]{0,47}` — leading letter, alphanumerics + **underscores only**, no dashes.
- Length cap: **48 chars** (not 100 as Q4 assumed by analogy with Memory).
- Source: <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateAgentRuntime.html>

`locals.runtime_name` derives a runtime-safe name from `local.name_prefix` by replacing `-` with `_` and truncating to 48 chars. For `environment=demo` this resolves to `graphia_demo_runtime` (20 chars, well within the cap).

The `agentRuntimeId` regex (`[a-zA-Z][a-zA-Z0-9_]{0,99}-[a-zA-Z0-9]{10}`) that §6 Q4 cited is the **server-generated ID**, not the user-supplied name — distinct fields. Q4 conflated them.

### No invocation URL attribute on the Runtime resource

The provider exports `agent_runtime_arn`, `agent_runtime_id`, `agent_runtime_version`, and `workload_identity_details` — **no `invocation_url` / `endpoint` attribute**. Clients invoke the Runtime via the data-plane `InvokeAgentRuntime` API using the ARN. The module's `runtime_invocation_url` output is therefore wired to `agent_runtime_arn`; the output name is preserved for spec-level continuity but the description clarifies the value.

Source: Registry doc id `12200495` Attribute Reference section.

### Network mode values

`network_configuration.network_mode` accepts `PUBLIC` | `VPC`. With `VPC`, a `network_mode_config { security_groups = [...], subnets = [...] }` sub-block is required. Spec 002 uses `PUBLIC`.

### Resources actually created by Slice 3 sub-task 4

`terraform plan -var environment=demo -var owner=<email>` produces **5 to add**:

1. `aws_ecr_repository.runtime` — `graphia-demo-runtime`
2. `aws_cloudwatch_log_group.runtime` — `/aws/bedrock-agentcore/graphia-demo-runtime`
3. `aws_iam_role.runtime` — `graphia-demo-runtime`
4. `aws_iam_role_policy.runtime` — `graphia-demo-runtime-inline` (inline policy on the role above)
5. `aws_bedrockagentcore_agent_runtime.this` — `graphia_demo_runtime`

All five carry `Project=Graphia`, `ManagedBy=Terraform`, `Environment=<env>`, `Owner=<email>` via the provider's `default_tags` block.

---

## 8. Slice 6 sub-task 2 addendum — AgentCore Memory resource shape & IAM action set

Slice 6 sub-task 1 implemented `AgentCoreMemoryDiaryStore` against the actual `bedrock_agentcore.memory.MemoryClient` API in `bedrock-agentcore==1.9.0` (methods `create_event` / `list_events`), not the `AgentCoreMemory` + `store` / `search` surface the `langgraph-agentcore` skill references — the skill is stale. This sub-task provisions the Memory resource the client points at, grants least-privilege IAM access, and plumbs the ID into the Runtime container.

### Memory resource shape (provider 6.44.0)

The `aws_bedrockagentcore_memory` argument schema differs in two ways from §6 Q3's assumptions:

| Field | Required? | Constraint | Notes |
|---|---|---|---|
| `name` | Yes | regex `[a-zA-Z][a-zA-Z0-9_]{0,47}` | Same family as Runtime — underscores only, no dashes, max **48 chars**. |
| `event_expiry_duration` | Yes | integer, **7–365** days (provider validation) | AWS API doc lists range 3–365 but the Terraform schema clamps to 7. We picked **90** days (Microgrid precedent; gives developers headroom for post-game inspection; Slice 10 `terraform destroy` drops the resource anyway). |
| `description` | No | string ≤ 4096 chars | We set a human-readable description. |
| `encryption_key_arn` | No | KMS key ARN | Omitted → AWS-managed encryption (the personal-reference-project posture from ADR 001). |
| `memory_execution_role_arn` | No | IAM role ARN | Omitted — required only when a memory strategy invokes a Bedrock model on the Memory's behalf, which we don't use. |
| `memory_strategies` | **N/A — not a field on this resource** | — | Strategies are a *separate* resource (`aws_bedrockagentcore_memory_strategy`). To get the "raw events only" mode the SDK needs, simply **don't declare a strategy resource**. This is cleaner than the spec brief's "minimum memory_strategies" hedge. |

Exports: `arn` (used in the IAM statement below), `id` (used in the Runtime env var + the `memory_id` output). The `id` is the canonical identifier — it's the value `MemoryClient.create_event(memory_id=...)` and `list_events(memory_id=...)` accept.

### Local: `memory_name`

```hcl
memory_name = substr(replace("${local.name_prefix}_memory", "-", "_"), 0, 48)
```

Same dash→underscore + 48-char-truncate transform as `runtime_name`. For `environment=demo` resolves to `graphia_demo_memory` (19 chars).

### IAM action set granted to the Runtime role on the Memory ARN

```hcl
statement {
  sid     = "AgentCoreMemoryReadWrite"
  effect  = "Allow"
  actions = [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:ListEvents",
  ]
  resources = [aws_bedrockagentcore_memory.this.arn]
}
```

Verified against:

- `bedrock-agentcore:CreateEvent` — service-authorization reference; resource type `memory`; required permission per the boto3 `create_event` API doc: <https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/create_event.html>
- `bedrock-agentcore:ListEvents` — same reference; resource type `memory`; required permission per the boto3 `list_events` API doc: <https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/list_events.html>
- Memory resource-type ARN: `arn:${Partition}:bedrock-agentcore:${Region}:${Account}:memory/${MemoryId}` — service-authorization reference §"Resource types defined by Amazon Bedrock Agentcore".

**Actions explicitly NOT granted** (and why) — these are the §6 Q3 action set, which targets a *different* surface than the SDK uses:

- `bedrock-agentcore:BatchCreateMemoryRecords` / `BatchUpdateMemoryRecords` / `BatchDeleteMemoryRecords` / `RetrieveMemoryRecords` / `ListMemoryRecords` / `GetMemoryRecord` — these manipulate the *memory records* surface that backs strategy-extracted memories (semantic, summarisation, etc.). Our application uses raw events, not records. Granting them would be unused least-privilege bloat.

The deprecation of the §6 Q3 action set is the headline correction this sub-task adds over the spec-time research.

### Runtime `environment_variables` plumbing

`aws_bedrockagentcore_agent_runtime` exposes a native `environment_variables` field (`map(string)`). We populate:

```hcl
environment_variables = {
  GRAPHIA_MEMORY_ID = aws_bedrockagentcore_memory.this.id
}
```

The implicit `aws_bedrockagentcore_memory.this.id` reference establishes the Memory → Runtime ordering Terraform needs (no explicit `depends_on` necessary). No fallback (S3, image-bake, redeploy step) needed — native plumbing works.

`src/graphia/config.py::load_config()` reads `GRAPHIA_MEMORY_ID`; `make_diary_store(config)` passes it to `AgentCoreMemoryDiaryStore(memory_id=...)`.

### Plan delta (Slice 6 sub-task 2)

`./tf plan -var environment=demo -var owner=<email>` produces **1 to add, 2 to change, 0 to destroy** on top of the deployed Slice 3 baseline:

1. **add** `aws_bedrockagentcore_memory.this` — `graphia_demo_memory`, 90-day expiry.
2. **change (in-place)** `aws_iam_role_policy.runtime` — adds the `AgentCoreMemoryReadWrite` statement.
3. **change (in-place)** `aws_bedrockagentcore_agent_runtime.this` — adds `environment_variables = { GRAPHIA_MEMORY_ID = ... }`. The Runtime's `agent_runtime_version` rolls forward as a side effect, which is expected — AgentCore versions any change to the Runtime resource.
4. **add** `output.memory_id`.

### Quirks the next sub-task (3) needs to know

- The `memory_id` returned by the Terraform `id` attribute has the shape `${name}-${10charRandomSuffix}` (e.g. `graphia_demo_memory-AbCdEfGhIj`) — the SDK accepts this as-is, no parsing needed. `make redeploy` should pipe `terraform output -raw memory_id` straight into the `.env` line for local-mode parity.
- The Runtime's `environment_variables` map is server-side — updating it bumps `agent_runtime_version`. Sub-task 3's diary-write integration triggers no Terraform redeploy as long as the env-var name + value-source don't change; only image pushes / IAM-policy edits will trigger Runtime re-versioning beyond the one-time bump in this sub-task's plan.
- The `bedrock-agentcore:CreateEvent` action supports two scope-restricting condition keys — `bedrock-agentcore:sessionId` and `bedrock-agentcore:actorId` (service-authorization reference). Sub-task 3 does not need them — the application already scopes calls with the correct keys at the SDK call site, and the resource-level scoping above is already minimal — but if a future hardening pass wants to assert *defence-in-depth*, those conditions are the lever.

---

## 9. Slice 7 sub-task 2 addendum — AgentCore Gateway + Gateway targets

Provisions a single Gateway in front of the Runtime with IAM-authorized inbound auth, and two `mcp_server`-typed targets that surface the diary tools. The work also re-derives §6 Q5 (the Gateway target shape question) against the live target-type taxonomy and corrects the prior `open_api_schema` recommendation.

### Gateway resource shape (provider 6.44.0)

Required top-level args (per Registry doc id `12200500`, re-fetched 2026-05-13):

| Field | Required | Value used | Notes |
|---|---|---|---|
| `name` | Yes | `local.gateway_name` (= `graphia-demo-gateway` for `environment=demo`) | Pattern is the **Memory-style family** (`[a-zA-Z][a-zA-Z0-9-_]{0,99}`) — dashes are allowed and the cap is 100 chars, both stricter-than-Runtime. We keep the dash form for readability and parity with `name_prefix`. |
| `role_arn` | Yes | `aws_iam_role.gateway.arn` | Dedicated execution role with `sts:AssumeRole` trust for `bedrock-agentcore.amazonaws.com`. |
| `authorizer_type` | Yes | `AWS_IAM` | See "Inbound auth choice" below. |
| `protocol_type` | Yes | `MCP` | Only value the provider accepts in 6.44.0. |
| `description` | Optional | Short human-readable string | Surfaces in the AWS console + control-plane responses. |
| `authorizer_configuration` | Conditional (Required iff `authorizer_type=CUSTOM_JWT`) | **Omitted** for `AWS_IAM` | Cognito user-pool dependency only kicks in if we go JWT. |
| `protocol_configuration.mcp` | Optional | **Omitted** | Defaults are fine for v1.x. Sub-block carries `instructions`, `search_type=SEMANTIC`, `supported_versions`. |

Exported attributes used downstream: `gateway_arn`, `gateway_id`, `gateway_url`.

### Inbound auth choice — AWS_IAM (not CUSTOM_JWT)

The AgentCore Gateway inbound-auth documentation (<https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html>) describes IAM-based inbound auth as a first-class option: any IAM identity with `bedrock-agentcore:InvokeGateway` on the gateway ARN can call the Gateway with SigV4-signed requests. For Graphia's single-developer footprint this is the lowest-friction choice — the Runtime's execution role and the developer's local SSO session (used by local-mode through the standard credential chain) both already authenticate against AWS the same way. CUSTOM_JWT would force a Cognito user pool plus a token-minting dance that doesn't earn its complexity for a personal reference project. Confirmed: the provider's `authorizer_configuration` block is **only required when `authorizer_type=CUSTOM_JWT`**, so `AWS_IAM` works with no further config.

### Gateway target shape — `mcp_server`, not `open_api_schema` (corrects §6 Q5)

Re-deriving §6 Q5 against the live `gateway-target-MCPservers` doc and the corresponding `aws_bedrockagentcore_gateway_target` provider schema (Registry doc id `12200501`):

- The target-config oneOf inside `target_configuration.mcp` lists five variants: `api_gateway`, `lambda`, `mcp_server`, `open_api_schema`, `smithy_model`.
- AgentCore documents that **MCP servers hosted on AgentCore Runtime are a first-class supported target** via the `mcp_server` variant with IAM (SigV4) outbound authorization. The Runtime's MCP endpoint URL pattern is `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${URL_ENCODED_ARN}/invocations`.
- `open_api_schema` would still work in principle, but it requires the Runtime to expose a *real* HTTPS endpoint resolvable from the Gateway service — which AgentCore Runtime data-plane invokes don't — *or* an inline/S3 OpenAPI doc plus a custom HTTP endpoint. `mcp_server` skips that whole detour by using the Runtime's documented MCP contract.
- **ADR 002 contract drift to flag**: the spec brief describes `/tools/diary/{write,read}` HTTP routes on the Runtime container. The `mcp_server` target type instead requires the Runtime container to speak MCP at `/mcp` (path is fixed by AgentCore — see `runtime-mcp.html`). Sub-task 3 must therefore re-implement the diary endpoints as MCP tools (FastMCP / `@mcp.tool()`) rather than custom Starlette routes. This is documented as a comment on the Gateway-target resource blocks in `main.tf`.

Each Gateway target carries:

| Field | Value |
|---|---|
| `name` | `graphia-diary-write` / `graphia-diary-read` — Gateway-target name regex is `^([0-9a-zA-Z][-]?){1,100}$` (verified by `terraform validate` error message — strictly alphanumerics + optional single dash separator, **no underscores**, distinct from both Gateway's and Memory's regex). Hyphens are used for readability and parity with `name_prefix`. |
| `gateway_identifier` | `aws_bedrockagentcore_gateway.this.gateway_id` |
| `target_configuration.mcp.mcp_server.endpoint` | The Runtime's MCP invocation URL — built once in `locals.runtime_mcp_endpoint` using `urlencode(aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn)`. |
| `credential_provider_configuration.gateway_iam_role {}` | Empty block — Gateway uses its own execution role to SigV4-sign the outbound request against the Runtime ARN. This is the supported pairing for Runtime-hosted MCP servers per the `gateway-target-MCPservers` doc's "Authorization strategy" table. |
| `description` | Human-readable summary of the tool's request/response shape (per the brief). |

Tool schemas are *not* declared inline on the target — Gateway's **implicit synchronization** (on `CreateGatewayTarget`) calls `tools/list` against the Runtime MCP server and caches the schema. Sub-task 3 owns the FastMCP `@mcp.tool()` decorations that define the request/response shapes.

### IAM action names (verified against AWS docs)

| Direction | Action | Scope | Source |
|---|---|---|---|
| Caller (Runtime / local-mode) → Gateway | `bedrock-agentcore:InvokeGateway` | `aws_bedrockagentcore_gateway.this.gateway_arn` | `gateway-inbound-auth.html` example IAM policy. |
| Gateway → Runtime (target outbound) | `bedrock-agentcore:InvokeAgentRuntime` | `aws_bedrockagentcore_agent_runtime.this.agent_runtime_arn` | `API_InvokeAgentRuntime.html` operation reference + AgentCore service-authorization page. |

### Bullet-point findings for the next sub-task (Slice 7 sub-task 3)

- **Switch the diary endpoints to MCP**: the Runtime container's diary surface must be an MCP server (`FastMCP(host="0.0.0.0", stateless_http=True)`) exposing `diary_write(game_id, player_id, night_index, content)` and `diary_read(game_id, player_id)` as `@mcp.tool()`s — NOT `/tools/diary/*` Starlette HTTP routes. The Runtime's MCP path is fixed at `/mcp` by the AgentCore runtime-MCP contract.
- **Set the Runtime's `protocol_configuration.server_protocol = "MCP"`**: a follow-up Terraform change on `aws_bedrockagentcore_agent_runtime.this` configures the Runtime to expect MCP on inbound invocations. Skipping it makes the Gateway target's `tools/list` sync fail at `CreateGatewayTarget` time. Either land this in sub-task 3's TF delta, or revisit the protocol attribute alongside the agent-side refactor.
- **Gateway invocation URL shape**: `gateway_url` exported attribute resolves to `https://${gateway-id}.gateway.bedrock-agentcore.${region}.amazonaws.com/mcp` (verified by reading provider `Attribute Reference`). The agent's in-container MCP client (`streamablehttp_client`) points at this URL with SigV4 auth.
- **`GRAPHIA_GATEWAY_ID` env var** carries the Gateway ID into the Runtime container; the agent uses it (plus `var.region`) to construct the gateway URL. Alternative: surface the full URL as `GRAPHIA_GATEWAY_URL` instead — slightly cleaner for the application, but couples the env-var value to a service-managed URL format that could change. Stick with the ID and let the application build the URL.
- **MCP-session pass-through**: per the `gateway-target-MCPservers` "Tip", add `Mcp-Session-Id` to the target's `metadataConfiguration` (or enable Gateway sessions on the Gateway resource) for lower latency. Not blocking sub-task 3 — fall-forward optimisation.
- **Implicit sync caveat**: `CreateGatewayTarget` synchronously calls `tools/list` against the Runtime MCP endpoint. On first apply, the Runtime must already be alive *and* speaking MCP. If sub-task 3's container image hasn't been pushed before sub-task 6's `terraform apply`, the gateway-target creation will fail. Sequencing: push image first (`make push`), then `terraform apply`.

### Sources (this section)

| Topic | URL |
|---|---|
| `aws_bedrockagentcore_gateway` schema | <https://registry.terraform.io/v2/provider-docs/12200500> (provider 6.44.0) |
| `aws_bedrockagentcore_gateway_target` schema | <https://registry.terraform.io/v2/provider-docs/12200501> (provider 6.44.0) |
| AgentCore Gateway inbound auth | <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html> |
| AgentCore Gateway permissions setup | <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-prerequisites-permissions.html> |
| MCP-server targets (Runtime-as-MCP-server, IAM outbound) | <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html> |
| Deploy MCP servers in AgentCore Runtime (MCP URL pattern, `/mcp` path) | <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html> |
| `InvokeAgentRuntime` data-plane API | <https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html> |

