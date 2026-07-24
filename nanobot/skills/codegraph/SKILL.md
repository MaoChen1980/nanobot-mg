---
name: codegraph
description: '代码知识图谱分析：调用链、依赖、死代码、热点、架构分析、语义搜索、影响分析。

  当用户要求分析代码结构、查调用关系、找依赖、做架构报告、搜索相似函数、追溯 bug 根因、或评审 PR（风险评估、冲突检测）时激活。'
category: code-analysis
---

# CodeScope Q&A

CodeScope indexes source code into a two-layer knowledge graph — **structure** (functions, calls, imports, classes, modules) and **evolution** (commits, file changes, function modifications) — plus **semantic embeddings** for every function. Supports **Python, JavaScript/TypeScript, C, and Java** (including Hadoop-scale repositories with 8K+ files). This combination enables analyses that grep, LSP, or pure vector search cannot do alone. It can also **fetch GitHub issues and trace bugs to code**, and **review open PRs** — scoring per-PR risk, detecting cross-PR conflicts, identifying auto-merge candidates, and applying GitHub labels.

## When to Use This Skill

### ✅ Works reliably in v0.3.x
- User wants to analyze call chains, callers, callees, or dependencies (via `cg.get_dependencies()`)
- User wants to get file-level entity metadata (functions, classes, signatures) via `cg.get_entity_metadata()`
- User wants a full architecture analysis or report (via CLI: `python -m codegraph analyze`)
- User wants to index or analyze a Java project (via CLI: `python -m codegraph init --lang java`)

### ⚠️ May work but untested / limited in v0.3.x
- User wants to find dead code, hotspots, or architectural layers (pre-built methods missing)
- User wants to understand which classes own or depend on other classes (use `cg.get_entity_metadata()` + manual analysis)
- User wants to review PRs, assess PR risk, or prioritize PR reviews (CLI may work)
- User wants to analyze GitHub issues or bug reports (CLI may work)

### ❌ Not available in v0.3.x
- User wants to find semantically similar functions (semantic search requires `neug` + `CodeScope`)
- User wants to trace code evolution / modification history (`cs.change_attribution()`)
- User wants raw Cypher query access to the graph
- User wants `cs.hotspots()`, `cs.dead_code()`, `cs.module_coupling()`, `cs.layer_discovery()` pre-built methods
- User asks about cross-PR conflicts or which PRs can be merged independently

## Getting Started

### Installation

```bash
# Install codegraph-ai (neug optional — semantic search may fail offline)
pip install codegraph-ai
# Verify: python -m codegraph --help  (NOT: codegraph or codegraph-ai CLI)
```

### Environment Variables (optional)

```bash
# Create Python virtural environment
python -m venv .venv

source .venv/bin/activate

# Point to a pre-built database (skip indexing)
export CODESCOPE_DB_DIR="/path/to/.linux_db"

# Offline mode for HuggingFace models
export HF_HUB_OFFLINE="1"

# Fallback when HuggingFace is unreachable (e.g., network issues in China)
# Use HF mirror or ModelScope for sentence-transformers models:
export HF_ENDPOINT="https://hf-mirror.com"
# https://www.modelscope.cn/models/sentence-transformers/all-MiniLM-L6-v2
```

### Check Index Status

```bash
python -m codegraph status --db .codegraph
```

If no index exists, create one:

```bash
python -m codegraph init --repo . --lang auto --commits 500
```

Supported languages: `python`, `c`, `javascript`, `typescript`, `java`, or `auto` (auto-detects from file extensions).

The `--commits` flag ingests git history (for evolution queries). Without it, only structural analysis is available. Add `--backfill-limit 200` to also compute function-level `MODIFIES` edges (slower but enables `change_attribution` and `co_change`).

To add git history to an existing index (without re-indexing structure):

```bash
python -m codegraph ingest --repo . --db .codegraph --commits 500
python -m codegraph ingest --repo . --db .codegraph --backfill-limit 200   # add MODIFIES edges only
```

## Two Interfaces: CLI vs Python

**Use the CLI** for status and reports:

```bash
python -m codegraph status --db .codegraph
python -m codegraph analyze --db .codegraph --output report.md
python -m codegraph init --repo . --lang auto --commits 200
```

> ⚠️ CLI is `python -m codegraph` — NOT `codegraph` or `codegraph-ai` (these may be installed but are different packages).

**Use the Python API** for queries and custom analyses:

```python
from codegraph.core import CodeGraph

cg = CodeGraph(project_path="/path/to/repo")

# Dependency graph
deps = cg.get_dependencies(entry_point="/path/to/file.kt", depth=3)

# Entity metadata (functions, classes)
meta = cg.get_entity_metadata(file_path="/path/to/file.kt")

# Line numbers for an entity
lines = cg.get_lines_numbers(entity_name="myFunction", file_path="/path/to/file.kt")

# Usage graph (who calls what)
graph = cg.usage_graph(entity_name="MyClass")
```

The Python API is more powerful — it gives you structured access to the graph.

## Core Python API

### Raw Queries

These are the building blocks for any custom analysis:

| Method                                  | What it does                                                           |
| --------------------------------------- | ---------------------------------------------------------------------- |
| `cg.get_dependencies(entry_point, depth)` | Get dependency graph from entry point to N hops depth              |
| `cg.get_entity_metadata(file_path)`     | Get all functions/classes in a file with their signatures             |
| `cg.get_lines_numbers(entity_name, file_path)` | Get line numbers where entity is defined/called                |
| `cg.usage_graph(entity_name)`           | Build usage graph for a given entity                                   |

### Structural Analysis (via Python API + GraphQL)

CodeGraph v0.3.x uses `graphene-django` under the hood. After indexing, you can query the graph:

```python
from codegraph.core import CodeGraph
cg = CodeGraph(project_path="/path/to/repo")

# Get class relationships (via get_entity_metadata + manual traversal)
# See "Class Dependency Relationships" section for details
```

### Class Dependency Relationships (UML-Style)

CodeGraph v0.3.x extracts class relationships (COMPOSES, AGGREGATES, INHERITS) during indexing. Use the CLI to generate a class diagram:

```bash
python -m codegraph analyze --db .codegraph --output class_report.md
```

Or use the Python API to explore relationships:

```python
from codegraph.core import CodeGraph

cg = CodeGraph(project_path="/path/to/repo")

# Get file metadata to find classes and their fields
meta = cg.get_entity_metadata("/path/to/MyClass.kt")
# meta contains class definitions, method signatures, field types
# Use field type annotations to infer composition/aggregation

# Build usage graph to find callers/callees
graph = cg.usage_graph("MyClass")
```

> ⚠️ **Cypher queries (`cs.conn.execute(...)`) are NOT available in v0.3.x.** The old `CodeScope` class (which supported raw Cypher) is not present in the installed version. Only the four `CodeGraph` methods are available. If you need Cypher-level access, use `python -m codegraph query` (if supported) or file a feature request.

### Semantic Search

| Method                                          | What it does                                                            |
| ----------------------------------------------- | ----------------------------------------------------------------------- |
| `cs.similar(function, scope, topk=10)`          | Find functions similar to a given function within a module scope        |
| `cs.cross_locate(query, topk=10)`               | Find semantically related functions, then reveal call-chain connections |
| `cs.semantic_cross_pollination(query, topk=15)` | Find similar functions across distant subsystems                        |

### Evolution (requires `--commits` during init)

| Method                                                            | What it does                                           |
| ----------------------------------------------------------------- | ------------------------------------------------------ |
| `cs.change_attribution(func_name, file_path=None, limit=20)`      | Which commits modified a function? (requires backfill) |
| `cs.co_change(func_name, file_path=None, min_commits=2, topk=10)` | Functions that are always modified together            |
| `cs.intent_search(query, topk=10)`                                | Find commits matching a natural-language intent        |
| `cs.commit_modularity(topk=20)`                                   | Score commits by how many modules they touch           |
| `cs.hot_cold_map(topk=30)`                                        | Module modification density                            |

### Report Generation

```python
from codegraph.analyzer import generate_report
report = generate_report(cs)  # full architecture analysis as markdown
```

Or via CLI:

```bash
python -m codegraph analyze --db .codegraph --output reports/analysis.md
```

The report covers: overview stats, subsystem distribution, top modules, architectural layers (with Mermaid diagrams), bridge functions, fan-in/fan-out hotspots, cross-module coupling, evolution hotspots, and dead code density.

## Java Support

CodeScope includes a full Java adapter that handles enterprise-scale repositories like Apache Hadoop (~8K files, ~97K functions indexed in ~3.5 minutes).

### What Gets Indexed

| Element           | Graph Node/Edge                    | Notes                                       |
| ----------------- | ---------------------------------- | ------------------------------------------- |
| Classes           | `Class` node                       | Includes generics, annotations              |
| Interfaces        | `Class` node                       | `extends` → `INHERITS` edge                 |
| Enums             | `Class` node                       | Enum methods extracted                      |
| Methods           | `Function` node                    | Full generic signatures, JavaDoc            |
| Constructors      | `Function` node (name=`<init>`)    | Including `super()` calls                   |
| Method calls      | `CALLS` edge                       | Receiver context preserved (`obj.method()`) |
| `new` expressions | `CALLS` edge to `ClassName.<init>` | Constructor invocations                     |
| Imports           | `IMPORTS` edge (file→file)         | Single, wildcard, static                    |
| Inner classes     | `Class` node (name=`Outer.Inner`)  | Prefixed with outer class                   |
| Inheritance       | `INHERITS` edge                    | `extends` + `implements`                    |

### Indexing a Java Project

```bash
python -m codegraph init --repo /path/to/java-project --lang java --commits 500
```

Or with auto-detection (auto-detects `.java` files):

```bash
python -m codegraph init --repo /path/to/java-project --lang auto
```

### Java-Specific Exclusions

By default, these directories are excluded when indexing Java projects: `target/`, `build/`, `.gradle/`, `.idea/`, `.settings/`, `bin/`, `out/`, `test/`, `tests/`, `src/test/`.

### Java Query Examples

```python
# Find all classes that extend a specific class
list(cs.conn.execute("""
    MATCH (c:Class)-[:INHERITS]->(p:Class {name: 'FileSystem'})
    RETURN c.name, c.file_path
"""))

# Find all methods in a specific class
list(cs.conn.execute("""
    MATCH (c:Class {name: 'DefaultParser'})-[:HAS_METHOD]->(f:Function)
    RETURN f.name, f.signature
"""))

# Find constructor call chains
list(cs.conn.execute("""
    MATCH (f:Function)-[:CALLS]->(init:Function {name: '<init>'})
    WHERE init.class_name = 'Configuration'
    RETURN f.name, f.file_path LIMIT 10
"""))
```

## Bug Root Cause Analysis

CodeScope can fetch GitHub issues and map them to code using the graph + vector infrastructure. This is the core workflow for answering questions like "why does this project have so many bugs?" or "where in the code does this bug come from?"

### Prerequisites

- A code graph must already be indexed for the target repository
- `gh` CLI must be installed and authenticated (`gh auth login`)

### Bug Analysis API

#### Single Issue Analysis

```python
# Analyze a specific GitHub issue against the indexed code graph
result = cs.analyze_issue("owner", "repo", 1234, topk=10)
print(result.format_report())
```

This:

1. Fetches the issue from GitHub (or loads from cache)
2. Parses file paths, function names, and stack traces from the issue body
3. Matches extracted paths to File nodes in the graph
4. Uses semantic search (`cross_locate`) to find related code
5. Traces callers of mentioned functions via `impact()`
6. Ranks and returns root cause candidates with explanation

#### Batch Bug Analysis

```python
# Analyze top-k bug issues and get aggregated hotspot data
results = cs.analyze_top_bugs("owner", "repo", k=10, label="bug")
for r in results:
    print(f"#{r.issue.number}: {r.issue.title}")
    for c in r.candidates[:3]:
        print(f"  {c.function_name} ({c.file_path}) score={c.score:.2f}")
```

#### CLI Commands

```bash
# Fetch and parse a single issue (no graph needed)
codegraph fetch-issue owner repo 1234

# Fetch top-k bugs from a repo
codegraph fetch-bugs owner repo --top 10 --label bug

# Analyze a single bug against the code graph
python -m codegraph analyze-bug owner repo 1234 --db .codegraph --topk 10

# Batch analyze top bugs
python -m codegraph analyze-bugs owner repo --db .codegraph --top 10 --label bug
```

#### Lower-Level Components

For custom analysis pipelines, the components can be used individually:

```python
from codegraph.issue_fetcher import fetch_and_parse_issue
from codegraph.bug_locator import (
    resolve_paths_to_files,
    find_semantic_matches,
    trace_callers,
    rank_root_causes,
    analyze_bug,
)

# Fetch and parse (with caching)
issue = fetch_and_parse_issue("owner", "repo", 1234)
print(issue.extracted_paths)   # file paths found in body
print(issue.extracted_funcs)   # function names from stack traces
print(issue.linked_commits)    # merge commit SHAs from linked PRs

# Match paths to graph nodes
path_matches = resolve_paths_to_files(cs, issue.extracted_paths)

# Semantic search using issue description
semantic_matches = find_semantic_matches(cs, f"{issue.title}\n{issue.body}")

# Trace callers of mentioned functions
caller_traces = trace_callers(cs, issue.extracted_funcs, max_hops=2)

# Combine into ranked candidates
candidates = rank_root_causes(path_matches, semantic_matches, caller_traces, issue.extracted_funcs)
```

### Scoring System

Root cause candidates are scored by combining multiple signals:

| Signal              | Score     | Description                                                |
| ------------------- | --------- | ---------------------------------------------------------- |
| Direct mention      | +1.0      | Function name appears in issue body/stack trace            |
| File path match     | +0.8      | Function is in a file mentioned in the issue               |
| Semantic match      | +score    | Raw cosine similarity (0.0-1.0) from `cross_locate`        |
| Caller relationship | +0.5/hops | Function calls a mentioned function (decays with distance) |

### Issue Cache

Parsed issues are cached at `~/.codegraph/issue_cache/{owner}_{repo}_{number}.json`. Cache hits skip the GitHub API call entirely (sub-millisecond). To force a refresh, pass `use_cache=False` or use `--no-cache` on CLI.

```python
from codegraph.issue_cache import clear_cache
clear_cache(owner="openclaw", repo="openclaw")  # clear specific repo
clear_cache()  # clear all
```

### Stack Trace Parsing

The parser automatically extracts file paths and function names from stack traces in Python, C/C++, JavaScript/Node.js, Go, and Rust formats. It also extracts `func_name()` references in backticks and inline code.

## PR Review and Analysis

CodeScope can analyze open PRs against the indexed code graph to compute structural risk scores, detect cross-PR conflicts, and generate prioritized review reports.

### Prerequisites

- A code graph must already be indexed for the target repository
- `gh` CLI must be installed and authenticated (`gh auth login`)
- `GITHUB_TOKEN` environment variable recommended to avoid rate limiting

### Unified Pipeline (CLI)

Two subcommands: `prepare` (analyze + write to DB) and `label` (apply GitHub labels + comments).

```bash
# Phase 1: Analyze PRs, detect conflicts, write to graph DB (full rebuild)
# Pipeline: cross-PR analysis → single-PR risk scoring → report + labels
python -m codegraph pr-review prepare --db .codegraph

# Filter by author during prepare:
python -m codegraph pr-review prepare --db .codegraph --author someone

# Override auto-detected GitHub repo (owner/repo):
python -m codegraph pr-review prepare --db .codegraph --repo owner/repo

# Skip per-PR risk scoring (conflict-only, faster):
python -m codegraph pr-review prepare --db .codegraph --skip-single-pr

# Phase 2: Apply labels and post conflict comments from graph DB
python -m codegraph pr-review label --db .codegraph

# Label with dry-run (preview without API calls):
python -m codegraph pr-review label --db .codegraph --dry-run
```

Required arg: `--db`. Local repo path derived from `--db` parent. GitHub repo auto-detected from `git remote get-url origin` (or specified via `--repo`). Optional: `--author`, `--output`, `--skip-single-pr` (prepare); `--dry-run` (label).

### Python API (for agents / scripts)

For programmatic use within the same Python process, use `PRReview` — a
high-level wrapper that manages CodeScope lifecycle automatically.

```python
from codegraph.pr_api import PRReview

# Full pipeline in 2 lines
with PRReview(db=".codegraph") as pr:
    pr.prepare()                # fetch PRs → graph DB → scoring → report
    pr.label(dry_run=True)      # preview labels without API calls

# Query after prepare (works across sessions once DB has data)
with PRReview(db=".codegraph") as pr:
    # Conflicts
    pr.conflict_prs_of("100")           # → ["101", "102"]

    # Risk
    pr.risk("100")                      # → {"number": "100", "risk_level": "HIGH", ...}

    # Classification
    pr.auto_merge_candidates()          # → [{"number": "200", ...}, ...]
    pr.conflicting_groups()             # → [["100", "101"], ["103"]]

    # All PRs in DB
    pr.all_prs()                        # → [{"number": "100", ...}, ...]

    # Functions changed by a specific PR (added / modified / deleted)
    import json
    cs = pr._open_cs()
    rows = list(cs.conn.execute(
        f"MATCH (pr:PR {{id: {json.dumps('439')}}})-[c:CHANGES]->(f:Function) "
        f"RETURN c.info AS change_type, f.name, f.file_path "
        f"ORDER BY c.info, f.name"
    ))
    for change_type, name, path in rows:
        print(f"  [{change_type}] {name} ({path})")
    # change_type: 'hunk' (modified), 'new' (added), 'deleted', 'related' (newly calls)
```

All query methods return structured Python objects — no text parsing
required. The CLI and Python API share the same underlying implementation
(`run_prepare` / `run_label` / graph DB), so you can `prepare` via CLI
and query via Python, or vice versa.

For lower-level components (PRScorer, CrossPRAnalyzer, etc.), see:

```python
from codegraph.pr_analysis import GitHubClient, GraphAnalyzer, PRScorer, CrossPRAnalyzer
gh = GitHubClient(repo='owner/repo')
scorer = PRScorer(GraphAnalyzer(cs, repo_dir), repo_dir, gh)
result = scorer.analyze(gh.pr_to_entry(pr), output_dir='/tmp')  # risk_score, risk_level, peak_blast...

cross = CrossPRAnalyzer(cs, repo_dir, gh)
cross.prepare(pr_ids)  # index PR nodes into graph
cross.connected_components()  # {root: [pr_ids]} — detects conflicts
cross.update_pr_labels(assignments)  # persist labels to graph DB

# Load PR results from graph DB (no GitHub API needed)
all_results, components = cross.load_from_graph()

# Build and apply labels from analysis results
from codegraph.pr_labeler import build_label_assignments, apply_labels
assignments = build_label_assignments(all_results, components)
apply_labels(assignments, repo='owner/repo', create_labels=True)
```

For detailed workflows, Cypher patterns, and CrossPRAnalyzer query dimensions, see [pr-analysis.md](./pr-analysis.md).

### Report Structure (3 sections)

1. **Auto-merge Candidates**: LOW risk, no interface/config changes, singleton component
2. **Independent Review**: Non-trivial PRs with no cross-PR conflict
3. **Conflicting PR Groups**: PRs sharing code/call paths via connected-components (DSU)

Risk levels: CRITICAL (≥12), HIGH (≥7), MEDIUM (≥3), LOW (<3), UNKNOWN (when `--skip-single-pr`). Key signals: blast_radius (3.0×), no_test_coverage (2.0×), interface_change (2.5×), dead_code (1.5×).

### Applying Labels and Conflict Comments

After running `python -m codegraph pr-review prepare`, run `python -m codegraph pr-review label` to apply category labels to GitHub PRs and post conflict comments:

```bash
# Apply labels and post conflict comments:
python -m codegraph pr-review label --db .codegraph

# Preview without making API calls:
python -m codegraph pr-review label --db .codegraph --dry-run
```

The `label` subcommand reads PR labels from the graph DB (`pr.label` column) — no re-analysis needed. For conflicting PRs (labelled `conflicting-group-N`), it also posts a comment on the GitHub PR listing shared functions and other conflicting PRs.

Labels are computed during `prepare` from the analysis results (connected components + risk scores) and persisted to PR nodes in the graph DB (`pr.label` column, semicolon-delimited).

Label scheme:

| Category                       | Label                  | Color           |
| ------------------------------ | ---------------------- | --------------- |
| Auto-merge Candidates (Part 1) | `auto-merge-candidate` | Green           |
| Independent Review (Part 2)    | `independent-review`   | Yellow          |
| Conflicting Group N (Part 3)   | `conflicting-group-N`  | Red/Orange/Blue |
| Any conflicting PR (Part 3)    | `conflicting-pr`       | Red             |

### Follow-up Exploration

PR-specific follow-up questions are automatically included in `python -m codegraph explore` when PR nodes exist in the graph DB (i.e., after `python -m codegraph pr-review prepare`). PR exploration is a question template set integrated into `explore`. To query a specific PR's details (conflicts, changed functions), use the `PRReview` Python API.

```bash
# After pr-review prepare, explore includes PR questions automatically:
python -m codegraph explore --db .codegraph --top 15

# Interactive exploration (including PR follow-up questions):
python -m codegraph explore --db .codegraph

# Focus on PR-specific questions (use reviewer role):
python -m codegraph explore --db .codegraph --role reviewer

# Filter to only architecture questions (exclude PR patterns):
python -m codegraph explore --db .codegraph --type architecture

# Filter to only risk questions:
python -m codegraph explore --db .codegraph --type risk

# Filter to only PR review questions:
python -m codegraph explore --db .codegraph --type pr-review --role reviewer
```

The `--type` filter controls which question categories appear:

- `all` (default): all categories mixed together
- `architecture`: structural design questions (fan-in, coupling, cycles)
- `risk`: risk-focused questions (structural risk + PR risk)
- `evolution`: git history questions (change attribution, modification patterns)
- `hotspot`: frequently modified code questions
- `pr-review`: PR-specific questions (impact, conflicts, test coverage)

When `--type pr-review` is specified, only PR-related questions are shown.

## How to Route Questions

The key decision is: **does the user want an exact structural answer, a fuzzy semantic one, or a bug-to-code mapping?**

| User asks...                                                 | Best approach                                                                                                               |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| "Who calls `free_irq`?"                                      | Cypher: `MATCH (c:Function)-[:CALLS]->(f:Function {name: 'free_irq'}) RETURN c.name, c.file_path`                           |
| "Find functions related to memory allocation"                | `cs.vector_only_search("memory allocation")` or `cs.cross_locate("memory allocation")`                                      |
| "What's the most complex function?"                          | `cs.hotspots(topk=1)`                                                                                                       |
| "Is there dead code in the networking stack?"                | `cs.dead_code()` then filter by file path                                                                                   |
| "How has `schedule()` changed recently?"                     | `cs.change_attribution("schedule", "kernel/sched/core.c")`                                                                  |
| "Which modules are tightly coupled?"                         | `cs.module_coupling(topk=20)`                                                                                               |
| "Generate a full architecture report"                        | `python -m codegraph analyze --db .codegraph --output report.md`                                                            |
| "What's the architectural role of `mm/`?"                    | `cs.layer_discovery()` then find `mm` entries                                                                               |
| "Which functions act as API boundaries?"                     | `cs.bridge_functions(topk=30)`                                                                                              |
| "Find commits about fixing race conditions"                  | `cs.intent_search("fix race condition")`                                                                                    |
| "What functions are always changed together with `kmalloc`?" | `cs.co_change("kmalloc")`                                                                                                   |
| "Why does this project have so many bugs?"                   | `cs.analyze_top_bugs("owner", "repo", k=10)` then aggregate hotspots                                                        |
| "Analyze issue #1234 from GitHub"                            | `cs.analyze_issue("owner", "repo", 1234)`                                                                                   |
| "What code is related to this bug?"                          | `cs.analyze_issue(...)` or manual `cross_locate(bug_description)`                                                           |
| "Find the root cause of the crash in issue #42"              | `cs.analyze_issue("owner", "repo", 42)`                                                                                     |
| "Which modules have the most bugs?"                          | `cs.analyze_top_bugs(...)` then aggregate by file/module                                                                    |
| "Index this Java project"                                    | `python -m codegraph init --repo . --lang java`                                                                              |
| "What classes extend FileSystem in Hadoop?"                  | Cypher: `MATCH (c:Class)-[:INHERITS]->(p:Class {name: 'FileSystem'}) RETURN c.name, c.file_path`                            |
| "Find all constructors called in this module"                | Cypher: `MATCH (f:Function)-[:CALLS]->(init:Function {name: '<init>'}) WHERE f.file_path CONTAINS 'module' RETURN ...`      |
| "Draw a class diagram / show class UML"                      | Query `COMPOSES`, `AGGREGATES`, `INHERITS` edges and render as Mermaid `classDiagram`                                       |
| "What does `Llama` own / compose?"                           | Cypher: `MATCH (c:Class {name:'Llama'})-[:COMPOSES]->(t:Class) RETURN t.name`                                               |
| "Which class holds a reference to `KVCacheManager`?"         | Cypher: `MATCH (c:Class)-[:COMPOSES\|AGGREGATES]->(t:Class {name:'KVCacheManager'}) RETURN c.name`                          |
| "Show all optional dependencies of `GPUModelRunner`"         | Cypher: `MATCH (c:Class {name:'GPUModelRunner'})-[:AGGREGATES]->(t:Class) RETURN t.name`                                    |
| "Review all open PRs and generate report"                    | `python -m codegraph pr-review prepare --db .codegraph`                                                                      |
| "Which PRs can be auto-merged?"                              | Run `python -m codegraph pr-review prepare`, check Part 1 of report                                                         |
| "Are there conflicting PRs?"                                 | Run `python -m codegraph pr-review prepare`, check Part 3 (connected components)                                            |
| "What's the risk of PR #42?"                                 | `PRScorer.analyze(entry)` for per-PR scoring                                                                                |
| "What's the blast radius of this PR?"                        | `PRScorer.analyze(entry)` → `result['peak_blast']` and call graph viz                                                       |
| "Which PRs modify the same function?"                        | `CrossPRAnalyzer.connected_components()` → same-function edge type                                                          |
| "Label PRs with their review category"                       | `python -m codegraph pr-review label --db .codegraph`                                                                       |
| "Post conflict comments on PRs"                              | `python -m codegraph pr-review label --db .codegraph` (automatic for conflicting PRs)                                       |
| "Preview labels/comments without applying"                   | `python -m codegraph pr-review label --db .codegraph --dry-run`                                                             |
| "Explore PR follow-up questions interactively"               | `python -m codegraph explore --db .codegraph` (auto-includes PR patterns if `prepare` was run)                             |
| "Query a specific PR's conflicts"                            | `PRReview.conflict_prs_of("42")` — returns list of conflicting PR numbers                                                   |
| "Query a specific PR's changed functions"                    | Cypher: `MATCH (pr:PR {id: '42'})-[c:CHANGES]->(f:Function) RETURN c.info, f.name, f.file_path`                             |
| "Compare two PRs for overlap"                                | Cypher: `MATCH (pr1:PR {id: '42'})-[c1:CHANGES]->(f:Function)<-[c2:CHANGES]-(pr2:PR {id: '43'}) RETURN f.name, f.file_path` |
| "Show only architecture questions"                           | `python -m codegraph explore --db .codegraph --type architecture`                                                             |
| "Show only PR review questions"                              | `python -m codegraph explore --db .codegraph --type pr-review --role reviewer`                                                |
| "Show top PR risk questions"                                 | `python -m codegraph explore --db .codegraph --top 15 --role reviewer`                                                        |
| "Full PR review pipeline: analyze, label, explore"           | 1) `python -m codegraph pr-review prepare` 2) `python -m codegraph pr-review label` 3) `python -m python -m codegraph explore --db .codegraph` |

For **novel investigations** not covered by pre-built methods, compose raw Cypher queries. See [patterns.md](./patterns.md) for templates. For bug analysis patterns, see [bug-analysis.md](./bug-analysis.md).

## Skill Limitations (v0.3.x)

The installed version (`codegraph-ai 0.3.1`) has reduced functionality compared to the SKILL.md examples:

| Feature                     | SKILL.md Example         | v0.3.x Actual           | Status       |
| --------------------------- | ------------------------ | ----------------------- | ------------ |
| Python API                  | `CodeScope` class        | `CodeGraph` class       | ✅ Working   |
| Structural methods          | `cs.hotspots()`, etc.    | `get_dependencies()`, etc. | ⚠️ Different |
| Cypher raw queries          | `cs.conn.execute(...)`   | Not available           | ❌ Missing   |
| Semantic search             | `cs.similar()`, `cross_locate()` | Not available       | ❌ Missing   |
| Evolution analysis          | `cs.change_attribution()` | Not available           | ❌ Missing   |
| Bug analysis (GitHub)       | `cs.analyze_issue()`     | CLI may work            | ⚠️ Untested  |
| PR review                   | CLI commands             | CLI may work            | ⚠️ Untested  |

**If you need full CodeScope capabilities:** Check if a newer version is available via `pip install --upgrade codegraph-ai`, or use the `--no-deps` variant to see if `CodeScope` is in a different package.

## Important Filters for Cypher

When writing Cypher queries, these filters prevent misleading results:

- **`f.is_historical = 0`** — exclude deleted/renamed functions that are still in the graph as historical records
- **`f.is_external = 0`** (on File nodes) — exclude system headers/library files
- **`c.version_tag = 'bf'`** — only backfilled commits have `MODIFIES` edges; non-backfilled commits only have `TOUCHES` (file-level) edges
- **Always use `LIMIT`** — large codebases can return hundreds of thousands of rows

## Checking Data Availability

Before running evolution queries, check what's available:

```python
# How many commits are indexed?
list(cs.conn.execute("MATCH (c:Commit) RETURN count(c)"))

# How many have MODIFIES edges (backfilled)?
list(cs.conn.execute("MATCH (c:Commit) WHERE c.version_tag = 'bf' RETURN count(c)"))
```

If no commits exist, evolution methods will return empty results — guide the user to run `python -m codegraph ingest` first. If commits exist but aren't backfilled, `TOUCHES` (file-level) queries still work but `MODIFIES` (function-level) queries won't.

## Troubleshooting

| Error                              | Cause                                  | Fix                                                                               |
| ---------------------------------- | -------------------------------------- | --------------------------------------------------------------------------------- |
| `Database locked`                  | Crashed process left neug lock         | `rm <db>/graph.db/neugdb.lock`                                                    |
| `Can't open lock file`             | zvec LOCK file deleted                 | `touch <db>/vectors/LOCK`                                                         |
| `Can't lock read-write collection` | Another process holds lock             | Kill the other process                                                            |
| `recovery idmap failed`            | Stale WAL files                        | Remove empty `.log` files from `<db>/vectors/idmap.0/`                            |
| HuggingFace model download fails   | Network/firewall blocks huggingface.co | Use `HF_ENDPOINT="https://hf-mirror.com"` or ModelScope (see Getting Started tip) |
| `ImportError: cannot import name 'CodeScope'` | SKILL.md references wrong class (`CodeScope` vs actual `CodeGraph`) | Use `from codegraph.core import CodeGraph` — v0.3.x uses `CodeGraph`, not `CodeScope` |
| CLI `codegraph: error: No such option` | Wrong CLI command (uses `codegraph` instead of `python -m codegraph`) | Always use `python -m codegraph` — the `codegraph` binary is a different package |

The CLI auto-cleans lock issues on startup when possible.

## References

- **[schema.md](./schema.md)** — Full graph schema: node types, edge types, properties, Cypher syntax notes
- **[patterns.md](./patterns.md)** — Ready-to-use Cypher query templates and composition strategies
- **[bug-analysis.md](./bug-analysis.md)** — Bug analysis workflows: single issue, batch analysis, hotspot aggregation, custom pipelines
- **[pr-analysis.md](./pr-analysis.md)** — PR analysis workflows: per-PR scoring, cross-PR conflict detection, Cypher patterns, CrossPRAnalyzer usage
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
