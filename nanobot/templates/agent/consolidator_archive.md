Extract key facts from this conversation. Ignore trivial interactions, short acknowledgments, or minor conversational fillers. Only output items matching these categories, skip everything else:
- User facts: personal info, preferences, stated opinions, habits
- Decisions: choices made, conclusions reached
- Solutions: working approaches discovered through trial and error, especially non-obvious methods that succeeded after failed attempts
- Events: plans, deadlines, notable occurrences
- Preferences: communication style, tool preferences
- Process milestones: key stages of the discussion, logic shifts, or how a concept evolved
- Contextual insights: underlying goals, unstated needs identified, or environmental constraints

Priority: user corrections and preferences > solutions > process milestones > decisions > events.

Skip: code patterns, unnecessary details, minor chatter, or anything already captured in existing memory.

Process writing style: Use "A -> B" or directional verbs to capture logic shifts and evolution concisely.

Output as concise bullet points, one fact per line. No preamble, no commentary.
If nothing noteworthy happened, output: (nothing)
