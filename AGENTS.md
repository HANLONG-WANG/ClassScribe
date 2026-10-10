<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **ClassScribe**.

GitNexus provides graph-based code intelligence for exploring architecture, dependencies, execution flows, and potential change impact.

GitNexus is an optional resource. Codex independently decides whether, when, and how to use its tools based on the task and available context. Direct source inspection, text search, language servers, Serena, and other development tools are equally valid alternatives.

## Available Capabilities

- `query({search_query: "concept"})`: Explore concepts, related components, and execution flows.
- `context({name: "symbolName"})`: Inspect a symbol, its relationships, and surrounding context.
- `impact({target: "symbolName", direction: "upstream"})`: Estimate callers, affected processes, and potential change impact.
- `detect_changes({scope: "all"})`: Examine graph-level changes and affected symbols or flows.
- `trace`: Follow execution paths across components.
- `rename`: Perform graph-aware symbol renaming.
- `explain({target: "fileOrSymbol"})`: Explore security and taint findings when PDG analysis is available.

These capabilities can be used individually or combined with other development tools.

## Interpretation and Limitations

GitNexus provides useful but potentially incomplete static analysis.

- HIGH or CRITICAL risk indicates potentially significant change impact.
- UNKNOWN risk indicates that the analysis could not establish a reliable impact assessment.
- Missing callers or relationships do not establish that a symbol is unused.
- Low reported impact does not guarantee that a change is safe.
- Dynamic dispatch, reflection, dependency injection, framework registration, generated code, configuration, and cross-language behavior may not be fully represented.

Source inspection, text search, other code intelligence tools, and project validation commands can provide complementary evidence.

Discrepancies between tools, source code, and diagnostics may reveal incomplete analysis or underlying issues worth investigating.

## Index Maintenance

GitNexus maintains a repository knowledge graph and optional embeddings.

Index status can be inspected through GitNexus tools or CLI commands. Whether and when to refresh the index is entirely at Codex's discretion.

### Sandbox and CUDA Requirements

**If Codex chooses to perform GitNexus indexing or re-indexing, the indexing command MUST execute outside the sandbox.**

This requirement applies to any operation that creates, refreshes, or rebuilds the GitNexus repository index, including operations that generate embeddings.

- Request sandbox escalation or elevated execution permission before running an indexing command.
- Execute indexing in the host environment, outside the Codex sandbox, so GitNexus can access the local GPU and CUDA runtime.
- Do not silently fall back to sandboxed indexing if escalation is denied or unavailable. Report the limitation instead.
- This restriction applies to indexing operations, not ordinary read-only GitNexus queries or analysis tools.

The host supports NVIDIA CUDA, which is the intended backend for local embedding generation.

When generating local embeddings, the following command illustrates the preferred configuration:

    gitnexus analyze --index-only --embeddings --embedding-device cuda

If the repository-local runner is available, it can also be used:

    node .gitnexus/run.cjs analyze --index-only --embeddings --embedding-device cuda

The `--index-only` option prevents GitNexus from rewriting agent instruction files or installing skills during indexing.

The `--embeddings` option enables embedding generation, and `--embedding-device cuda` selects the CUDA backend.

CUDA availability depends on the host runtime and installed dependencies; running outside the sandbox enables access but does not itself guarantee successful GPU initialization.

Indexing remains optional. The sandbox escalation requirement applies only when Codex has independently decided to perform indexing.

## Resources

| Resource                                     | Description                             |
| -------------------------------------------- | --------------------------------------- |
| `gitnexus://repo/ClassScribe/context`        | Repository overview and index freshness |
| `gitnexus://repo/ClassScribe/clusters`       | Functional areas                        |
| `gitnexus://repo/ClassScribe/processes`      | Execution flows                         |
| `gitnexus://repo/ClassScribe/process/{name}` | Detailed execution traces               |

## Reference Skills

Additional documentation is available for specialized tasks:

| Topic                    | Skill                                              |
| ------------------------ | -------------------------------------------------- |
| Architecture exploration | `.claude/skills/gitnexus-exploring/SKILL.md`       |
| Impact analysis          | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Debugging                | `.claude/skills/gitnexus-debugging/SKILL.md`       |
| Refactoring              | `.claude/skills/gitnexus-refactoring/SKILL.md`     |
| Tool reference           | `.claude/skills/gitnexus-guide/SKILL.md`           |
| CLI operations           | `.claude/skills/gitnexus-cli/SKILL.md`             |

These references are available when their additional details are useful.

<!-- gitnexus:end -->
