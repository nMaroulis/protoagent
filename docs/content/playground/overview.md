---
title: Playground
description: Small apps in the monorepo used as agent targets and demos.
---

The `playground/` folder contains small applications that can be used as coding
targets while developing ProtoAgent. They are not the runtime engine.

## Recipe Recommendation

Path:

```text
playground/recipe-reco/
```

Shape:

| File | Role |
| --- | --- |
| `api.py` | Flask API. |
| `app.py` | App entrypoint. |
| `client.py` | Example client. |
| `recommender.py` | Recommendation loop. |
| `filters.py` | Ingredient and tag filters. |
| `scoring.py` | Intentionally simple scoring logic. |
| `recipes.py` | Seed recipes. |
| `storage.py` | In-memory recipe store. |
| `models.py` | Data models. |

Good ProtoAgent tasks:

```text
explain @playground/recipe-reco/recommender.py
refactor the recipe scoring logic and keep behavior understandable
add tests for ingredient matching edge cases
```

## Taskflow

Path:

```text
playground/taskflow/
```

Shape:

| File | Role |
| --- | --- |
| `server.py` | Flask routes for task list, create, complete, search, stats. |
| `service.py` | Task business logic. |
| `storage.py` | In-memory task store. |
| `models.py` | Task model. |
| `utils.py` | Serialization helpers. |
| `client.py` | Example client. |
| `app.py` | App entrypoint. |

Good ProtoAgent tasks:

```text
explain the taskflow route structure
add validation for empty task titles
propose tests for task completion and search
```

## Why Keep Playground Apps Small

Small apps are useful because they let you validate:

1. Context Loom ranking.
2. Explorer read/search behavior.
3. Coder diff previews.
4. Approval flow.
5. Session memory.
6. Trace and timeline quality.

They should stay simple enough that a local model can reason about them without
expensive context or many exploratory turns.
