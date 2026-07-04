# 0024 — Container orchestration service + job-dispatch engine backends

## Status: Design (not yet implemented)

## Motivation

Some capabilities arc needs can't run in-process on the host:

- **Heavy engines with hostile install stories.** `angr` (symbolic execution)
  ships wheels only for arm64-macOS and manylinux-x86_64. On x86_64 macOS —
  the current arc dev box — there is no wheel, so `pip install angr` tries a
  source build that needs a Rust toolchain and fails. Bundling Rust into a
  plugin is a non-starter; a plugin must "come with everything it needs."
- **Untrusted execution.** Dynamic analysis (a future `triton` engine, running
  or detonating target binaries) must not execute on the host unsandboxed.
- **Awkward, conflicting, or non-Python toolchains** generally — anything whose
  dependencies don't want to live in arc's venv.

The common resolution is: **the environment is itself a dependency.** A
capability ships a *recipe for the environment it runs in* (a container image),
and something provisions and runs it on demand. This design specifies that
"something" — a **standalone container-orchestration service** — and the
**job-dispatch model** by which arc capabilities use it.

This supersedes the earlier assumption (see 0020–0022 sub-agent work, and the
initial `arc-plugin-angr` scaffold) that an engine plugin imports its engine
in-process. It does not. It authors a job and dispatches it.

## The central realization

An engine capability is **not an in-process function call** and **not a
persistent RPC service** the plugin talks to. It is a **job**:

> agent intent → a plugin **authors a structured spec** → the orchestration
> service **dispatches a container job** (a pre-baked image + the spec injected
> at runtime) → a **fixed, tested engine** inside the container runs the spec →
> results (JSON + artifacts) flow back → the plugin formats them for the agent.

Two consequences fall out of this and drive the whole design:

1. **The plugin authors *data*, not *code*.** It fills in a validated spec
   (e.g. angr's `SolveRequest`). It does **not** generate an engine script. The
   engine logic is fixed, reviewed, version-pinned, and lives in the image. The
   model decides *what* (which addresses, which input source — read off the
   decompilation); the engine owns *how* (correctly driving angr). Model-authored
   engine code is rejected as the default path — the model hallucinates engine
   APIs, and generated-code-per-job is non-deterministic and a wider attack
   surface. A **raw-script job type is retained as a gated escape hatch** for
   novel analyses the schema can't express (sandboxed, ephemeral, opt-in).

2. **The arc side never imports the engine.** `angr` lives *only* in the Linux
   container image, where its wheels install cleanly. The arc-side plugin has
   **zero** dependency on angr. This eliminates the x86_64-macOS install problem
   entirely, and generalizes to every future heavy engine.

## Substrate decision: Docker now, not Kubernetes

The workloads arc needs are **parallel independent jobs + a few cooperating
groups + a few persistent tool-servers, on one host**:

- Parallel independent jobs = launch N containers. Docker does this natively; no
  scheduler required.
- Cooperating containers = a shared network + ordered services = a **Docker
  Compose project**. Compose *is* the "working together" primitive on one host.
- Short vs long lived = a job (`--rm`) vs a service (restart policy).

Kubernetes buys multi-node scheduling, self-healing at fleet scale, service
discovery across a fleet, and horizontal scaling — none of which is a current
requirement — at the cost of a control plane to operate, RBAC/kubeconfig, a
registry, YAML sprawl, and seconds-slow scheduling. **Not now.**

**Named upgrade path:** if a trigger fires — a multi-node proxmox cluster to
schedule across, self-healing services at fleet scale, or parallelism that
outgrows one host — adopt **k3s** (lightweight K8s) on a proxmox VM behind the
same service interface (below). Not full K8s.

**Substrate stays behind the interface** (same lesson as the engine seam): the
service API speaks *workloads*, and the backend compiles them to `docker run`×N
/ a Compose project now, or k3s Jobs/Deployments later — swappable without
callers noticing.

## Architecture — four layers

```
  ┌───────────────────────────────────────────────────────────┐
  │ arc agent (the model)                                      │
  └───────────────┬───────────────────────────────────────────┘
                  │ tools (MCP)
  ┌───────────────▼───────────────┐   ┌───────────────────────┐
  │ MCP seam                       │   │ dispatcher plugins     │
  │ (model-facing veneer over the  │   │ arc-plugin-angr, …     │
  │  service; container_* tools)   │   │ author spec + dispatch │
  └───────────────┬───────────────┘   └───────────┬───────────┘
                  │ native API/client              │ native API/client
  ┌───────────────▼────────────────────────────────▼───────────┐
  │ Orchestration Service (standalone, arc-agnostic)            │
  │  workloads / groups / batch · lifecycle · labels · mounts   │
  │  · limits · reaping · build|pull|base+provision             │
  └───────────────┬────────────────────────────────────────────┘
                  │ docker-py (socket)
  ┌───────────────▼────────────────────────────────────────────┐
  │ Docker daemon (host, or inside a proxmox VM later)          │
  └─────────────────────────────────────────────────────────────┘
```

1. **The Orchestration Service** — standalone, arc-agnostic, its own native API
   (HTTP or gRPC). Owns nothing that Docker already owns; it is a **control
   plane** that adds arc-aware ownership, lifecycle policy, and a clean
   interface. **Stateless: all orchestration state lives in container labels**
   (`arc.managed`, `arc.owner`, `arc.lifecycle`, `arc.purpose`, `arc.ttl`,
   `arc.created`). "What's running" is always `docker ps --filter
   label=arc.managed`, reconstructable after any restart, single source of
   truth. A sqlite metadata store is deferred until labels prove insufficient.
   Access to Docker via the **`docker-py` SDK** over the socket (a legitimate
   hard dependency here — orchestration is this service's whole job).

2. **The MCP seam** — a thin MCP server translating the service's operations
   into agent tools (`container_run`, `container_exec`, `container_logs`,
   `container_stop`, `container_rm`, `container_list`, `image_pull/build`).
   Chosen over an arc plugin because the service is client-agnostic infra:
   arc today, other MCP clients tomorrow, matching the existing proxmox-MCP
   pattern. arc consumes MCP tools identically to plugin tools.

3. **Dispatcher plugins** (e.g. `arc-plugin-angr`) — thin arc plugins that build
   a structured spec and dispatch a job through the service's **native
   API/client** (not through MCP — MCP is the model-facing veneer; programmatic
   callers use the real API). They parse the result and format it for the agent.
   They do **not** import the engine.

4. **The Docker daemon** — host now; inside a proxmox VM later (proxmox provides
   the *hosts*, the service orchestrates *workloads* on them, arc is the
   *client* — clean separation, and the VM-isolation-plus-container-ergonomics
   combo we want for dynamic analysis).

## The service API (native)

Modeled on **workloads**, not raw containers, so parallel/cooperating/persistent
are first-class and substrate-independent.

```
EnvSpec            # how to obtain the environment
  = { image: "python:3.11-slim" }                    # pull + run
  | { build: { context, dockerfile } }               # build (cache by hash)
  | { base: "debian:12", provision: ["apt-get …"] }  # no-Dockerfile case
  + mounts:  [ { host, container, ro } | { volume, container } ]
  + env:     { KEY: val }
  + limits:  { cpu, mem }
  + network: none | bridge         # DEFAULT none (untrusted by default)

WorkloadSpec
  env:       EnvSpec
  command:   [...]                 # entrypoint override / job command
  stdin:     bytes?                # inject the job payload (e.g. SolveRequest JSON)
  lifecycle: ephemeral | persistent
  ttl:       seconds?              # reaping horizon for ephemeral
  timeout:   seconds?              # hard kill
  name:      str?                  # for persistent (find-or-create)

GroupSpec  = { name, workloads: [WorkloadSpec], network: shared, order: [...] }
BatchSpec  = { workloads: [WorkloadSpec] }   # N independent, in parallel
```

Operations:

```
run_job(WorkloadSpec)   -> { exit, stdout, stderr, artifacts[], duration }   # one-shot
ensure_env(WorkloadSpec)-> handle   # idempotent find-or-create (persistent)
exec(handle, command)   -> { exit, stdout, stderr }
run_batch(BatchSpec)    -> [ result ]    # parallel
run_group(GroupSpec)    -> { per-workload results }   # compose project
stop(name) / rm(name) / logs(name) / list(filter)
build(EnvSpec) / pull(image)
reap(owner|ttl)         # sweep ephemeral by owner / expiry
```

## Lifecycle & reaping

- **Ephemeral (one-shot):** run, capture stdout/exit/artifacts, auto-remove
  (`--rm`). This is the engine path ("solve → JSON → destroy"). Reaped by
  `--rm`, plus a session-end sweep, plus a TTL sweep on service startup for
  orphans.
- **Persistent:** stays up, named, reconnected across restarts by label lookup;
  `ensure_env` is idempotent (find-or-create). A running tool-server or a
  long-lived analysis box you `exec` into repeatedly.

## Safety (built in from day one)

The downstream job is running untrusted analysis targets, so the defaults are
sandbox-first: **`network=none` by default**, cpu/mem limits required, drop
capabilities, read-only rootfs where feasible, no host network / privileged
unless explicitly opted in per-spec. These knobs are the difference between a
sandbox and a foothold.

## First consumer: `arc-plugin-angr` refactor

The already-scaffolded `arc-plugin-angr` (in-process engine) is re-shaped, and
nothing is wasted — it relocates:

| Piece today | Becomes |
|---|---|
| `engine.py` (imports angr) | the **image entrypoint** — baked into `arc-angr:<pin>`, runs the spec |
| `cli.py` | the **container command** — `angr solve --json-stdin` reads the spec, writes JSON |
| `spec.py` (`SolveRequest`) | the **wire contract** — imported by both the dispatcher and the image |
| `tools/…` + `plugin.py` | the **dispatcher** — builds `SolveRequest`, calls `run_job`, formats results. **No angr import.** |

Image: `debian`/`python` base + `pip install angr` (manylinux wheel) + the CLI.
Built rarely, pinned. Per job: the plugin sends `run_job(WorkloadSpec{ env:
image=arc-angr:pin, command: [angr, solve, --json-stdin], stdin: <SolveRequest>,
mounts: [binary ro, out rw], network: none, lifecycle: ephemeral, timeout })`.

Anti-pattern explicitly rejected: rebuilding the image per analysis. Bake the
engine once; inject the spec at runtime.

## Repository layout

Mirrors the `arc-plugin-ghidra` shape (plugin code beside the artifact it ships):

```
arc-plugin-angr/
  src/arc_plugin_angr/       dispatcher plugin (no angr dep)
    plugin.py, cli.py(dispatch), spec.py(shared contract), client (calls the service)
  image/                     the container image
    Dockerfile               debian + angr + the engine/CLI
    engine.py, entrypoint     the fixed, tested engine (was engine.py)
  tests/

<orchestration service repo>   standalone, arc-agnostic
  service/                   native API, docker-py backend, label state, reaping
  mcp/                       the MCP seam (container_* tools)
  client/                   python client lib used by dispatcher plugins
```

Naming of the service package/codename is open (see below).

## Build order

1. **Orchestration service, Docker backend** — `run_job` / `ensure_env` / label
   state / reaping / EnvSpec (image|build|base+provision) / mounts / limits /
   `network=none` default. The minimum that runs a one-shot job and returns
   stdout+artifacts.
2. **Python client lib** + a smoke CLI for the service.
3. **`arc-plugin-angr` refactor to dispatcher** + the `arc-angr` image. First
   real end-to-end: agent → spec → job → angr in container → result. Validates
   `stripped_crypto_test` without any angr on the host.
4. **MCP seam** — `container_*` tools for agent-driven container use.
5. **Groups/batch** (Compose backend) when a cooperating/parallel need is real.
6. **k3s / proxmox backends** — only when a trigger condition fires (multi-node,
   fleet self-healing, parallelism beyond one host). Same interface.

## Decisions locked (from design discussion)

- Standalone service, **not** an arc plugin; arc integrates via a thin **MCP**
  seam; dispatcher plugins + engines use the **native API/client**.
- **Docker + Compose** substrate; **k3s-on-proxmox** as the named, deferred
  upgrade path behind the workload interface. No K8s now.
- **Spec-driven jobs (validated data)** are the primary path; a **gated
  raw-script job** is the escape hatch. Engine logic is **baked into the image
  once**; the spec is **injected per job**.
- **Stateless control plane** — state lives in container labels; sqlite deferred.
- **`docker-py`** SDK for daemon access.
- Dispatcher plugins have **no engine dependency**; the engine lives only in the
  image. (Resolves the angr-on-macOS install problem for good.)
- Sandbox-first defaults: `network=none`, resource limits, dropped caps.

## Open questions

- **Service name / codename** — provisional "orchestration service"; pick one.
- **Native transport** — HTTP (simple, curl-able) vs gRPC (typed, streaming
  logs). Lean HTTP first.
- **Result artifacts** — how large outputs come back: bind-mounted out-dir
  (simple) vs streamed vs pushed to GCS (0021) for big blobs. Lean out-dir now,
  GCS for large.
- **Image distribution** — build-on-first-use locally vs a prebuilt image in a
  registry (GHCR). Lean build-on-first-use; registry when images stabilize.
- **Where the service runs** — host daemon now; when proxmox becomes the host
  layer, does the service run on the host talking to a remote Docker, or inside
  the VM? (Ties to the proxmox-as-host-layer decision.)
- ~~**MCP vs also an arc plugin**~~ — **Resolved:** MCP is the seam. But arc
  cannot consume MCP servers yet, so **0025 (MCP client integration) is a
  prerequisite** and lands first. Native arc-observability on container ops comes
  for free there (adapted MCP tools are real arc Tools → gates, `tool.call.*`
  events, replay). Build 0024's MCP seam only after 0025 steps 1–4.

## State

Design only. No code. Supersedes the in-process assumption in the current
`arc-plugin-angr` scaffold; that plugin is refactored to a dispatcher under
build-order step 3.
