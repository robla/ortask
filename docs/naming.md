# Naming Analysis: From status.py to ...

This document tracks the exploration of a more descriptive name for `status.py`. The goal is a name that suggests both **Org-mode storage** and **task/todo tracking**, while preserving the `.py` suffix.

## Candidate Analysis

### The "High-Signal" Names

*   **`ortask.py`** (ORg-TASK)
    *   **Vibe:** Professional and unambiguous.
    *   **Pros:** Immediately clear to anyone in the Org-mode ecosystem.
    *   **Cons:** Slightly longer to type than the "hacker" abbreviations.
    *   **Collisions:** A niche React/MongoDB web app exists, but no CLI tool or Python script.
*   **`otask.py`** (O-TASK)
    *   **Vibe:** Clean and modern.
    *   **Pros:** Very short, completely unclaimed on PyPI and GitHub as a primary filename.
    *   **Cons:** "O" is slightly more ambiguous than "OR" or "ORG."

### The "Punchy" Abbreviations

*   **`orta.py`** (OR-TA)
    *   **Vibe:** Catchy and unique.
    *   **Pros:** Excellent "hand-feel" for a CLI tool.
    *   **Cons:** Strong association with **Orta Therox**, a very famous software engineer (TypeScript/CocoaPods). Developers might assume it's his tool.
*   **`ortas.py`** (OR-TAS)
    *   **Vibe:** Classic Unix utility (like `crontab` or `rsync`).
    *   **Pros:** Very fast to type.
    *   **Cons:** "Ortas" means "partner" or "middle" in some languages and appears in niche 3D modeling scripts (SlicerMorph).
*   **`ort.py`** (O-R-T)
    *   **Vibe:** Extreme brevity.
    *   **Pros:** Shortest possible name.
    *   **Cons:** **Heavily crowded.** Massive brand collision with **ONNX Runtime (ORT)** and the **OSS Review Toolkit (ORT)**.

### The "Hacker" Choice

*   **`ortrk.py`** (OR-TRK)
    *   **Vibe:** Highly technical/abbreviated.
    *   **Pros:** Very short.
    *   **Cons:** Hard to say out loud; looks like a tracking slug or an obfuscated filename.

## Comparison Table

| Candidate | Clarity | Brevity | Collision Risk | Vibe |
| :--- | :--- | :--- | :--- | :--- |
| **`ortask.py`** | High | Medium | Low | Professional |
| **`otask.py`** | Medium | High | Very Low | Clean |
| **`orta.py`** | Medium | High | High (Person) | Original |
| **`ortas.py`** | Medium | High | Medium | Unix-like |
| **`ort.py`** | Low | Very High | Very High | Crowded |
| **`ortrk.py`** | Medium | High | Very Low | Technical |

## Final Recommendations

1.  **`ortask.py`**: The best choice for clarity and long-term maintainability. It is the most "robust" name.
2.  **`orta.py`**: The best choice if you want a short, punchy name with a bit of "personality," provided you don't mind the association with Orta Therox.
3.  **`otask.py`**: The best middle ground—short, clear enough, and completely unique in the Python namespace.

**Gemini's Pick:** `ortask.py`. It strikes the perfect balance between being an "obvious" tool for the job and staying out of the way of other major projects.

## Codex Notes

My view is slightly narrower: `ortask.py` is the best option because
it is still short, but it does not force future readers to decode an
abbreviation. The strongest alternatives (`otask.py`, `orta.py`,
`ortas.py`) all give up some clarity for a small typing win.

If you are leaning toward `ortask.py`, aliasing it to `ort` is a good
split between discoverability and daily ergonomics:

- `ortask.py` remains the readable script name in the repository
- `ort` becomes the fast interactive command you actually type

That avoids the main problem with naming the file `ort.py` directly:
`ort` is terse and pleasant at the shell, but too opaque as a source
filename. As a command alias, that opacity matters much less.

### Codex Ranking

1. `ortask.py` with shell alias `ort`
2. `otask.py`
3. `ortas.py`

I would avoid `ort.py` and `ortrk.py` as primary filenames. `ort.py`
is too ambiguous, and `ortrk.py` looks compressed enough that people
will need to remember what it stands for.
