---
name: diagram-maker
description: '创建图表/图示：SVG/HTML 或 Excalidraw，支持概念图、架构图、流程图、白板。

  当用户要求画图、画架构图、画流程图、画概念图、或用白板方式草图时激活。'
metadata:
  openclaw:
    emoji: 🧭
category: design
---

# Diagram Maker

Create diagrams as artifacts, not prose. Choose one output mode:

- `clean-svg`: educational concepts, physical systems, processes, lifecycle, simple data flow.
- `architecture-svg`: software/cloud/infra topology, services, databases, queues, trust zones.
- `excalidraw`: editable hand-drawn whiteboard, flowchart, sequence, architecture sketch.

Routing

- User wants editable/collaborative: choose Excalidraw.
- User wants polished standalone browser output: choose SVG/HTML.
- Software architecture with infra components: choose architecture SVG.
- Science, product, process, concept map, physical object: choose clean SVG.
- Unsure: ask one short question only if output format matters; otherwise choose clean SVG.

Workflow

1. Extract nodes, groups, labels, and directed relationships.
2. Pick layout first: left-to-right, top-down, hub-spoke, swimlanes, layered stack, sequence.
3. Keep labels short. Prefer 5-9 main elements over dense diagrams.
4. Generate the file at the requested path, or `./diagram.html` / `./diagram.excalidraw`.
5. Verify syntax by opening/parsing when feasible.

SVG/HTML rules

- Single standalone `.html` file with inline CSS and inline SVG.
- No external fonts, JS, images, gradients, glows, decorative blobs, or remote assets.
- Use semantic colors, not rainbow sequences: neutral, input, process, storage, external, risk.
- Draw connectors before nodes so arrows sit behind boxes.
- Every connector path has `fill="none"` and a marker arrow when directed.
- Leave 24px text padding inside boxes; do not let text touch borders.
- Legend only when symbols/colors are not obvious.

SVG template

Use `references/svg-template.md` as the wrapper and replace `<!-- SVG -->`.

Excalidraw rules

- Save `.excalidraw` JSON with `type`, `version`, `source`, `elements`, and `appState`.
- Use bound text for shape labels. Do not use a nonstandard `label` property.
- Keep bound text immediately after its container in the elements array.
- Minimum labeled shape: 120x60. Minimum body text: 16px.
- Use roughness `1`, `fontFamily: 1`, and simple fills.

For exact Excalidraw element snippets, read `references/excalidraw-patterns.md`.
- **Self-optimization**: 此 Skill 执行结束时，要检查针对 skill 做出优化。
  - Verification 未通过 → 加载 skill-manager 修复：修正步骤、补充边界情况、修复 bug
  - Verification 全部通过 → 加载 skill-manager 优化：简化步骤、增强执行确定性、改进验证标准
