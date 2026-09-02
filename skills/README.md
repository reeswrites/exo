# skills/

Instructions for an assistant *reading* an Exo surface. They ship with the
engine, they are the same in every instance, and they hold nothing about any
particular owner (ADR-0027).

Each one exists to prevent a specific false statement — not to tour the tools.
The surface returns true rows and still affords a small set of lies, which
ADR-0015 predicts from the facets:

| the lie | the facet that predicts it | the skill that answers it |
|---|---|---|
| a machine's conclusion quoted as the owner's sentence | `class: derived`, `class: dialogue` | [trace-an-idea](trace-an-idea/) |
| a saved link read as something they consumed | `class: intent` | [what-to-watch-next](what-to-watch-next/) |
| a rating reported without its scale | `kind: judgement` | [what-to-watch-next](what-to-watch-next/) |
| a stale pointer read as current state | `kind: pointer` | [pick-up-a-project](pick-up-a-project/) |

## A skill, and not a procedure

Both are text an assistant acts on. The split is the one ADR-0014 drew for
loaders — **format or place**:

| | skill | procedure (ADR-0016) |
|---|---|---|
| its input | the read surface: named tools, declared facets | your Sunday, your repo, your checklist |
| who wrote it | the engine, for anyone | the owner, by hand |
| where it lives | here, public, reviewed like code | `$EXO_HOME/procedures/`, private |
| how it travels | copied into a client | published as `exo://procedure/<slug>` |
| may it act | no — the surface is read-only (ADR-0006) | yes, as `kind: action`, under the acting rule |

If a thing you want to write names your other MCP server, your city, or how you
personally phrase things, it is a procedure or an instance skill. That is not a
worse category. It is the one where those facts are allowed to be true.

## Installing one

A skill is a directory holding `SKILL.md`. Copy the ones you want into wherever
your client reads skills from — for Claude Code that is `.claude/skills/` in a
project or `~/.claude/skills/` for all of them:

```sh
cp -r skills/trace-an-idea ~/.claude/skills/
```

Nothing in the engine reads this directory, and nothing publishes it. A skill
is not part of a bundle and does not appear in `resources/list`; it reaches an
assistant only by being copied. That is the cost of it not being per-instance.

If your instance carries a skill of the same name, install that one instead.
The more specific copy should win, and two copies of one name is a coin flip.

## Writing one

The frontmatter is `name` and `description` and nothing else — that is the
contract clients read, and an unknown key is a bet on a parser. Tool
requirements go in a **Needs** section of the body instead, in the same spirit
as a procedure's `needs.exo:`.

```markdown
---
name: trace-an-idea
description: What it does, and the moment it should come to mind. This is the
  field that decides whether it is ever loaded.
---
```

Five rules, argued in [ADR-0027](../docs/adr/0027-a-skill-ships-with-the-engine-a-procedure-belongs-to-the-owner.md):

1. **The engine surface only.** No peer, no second server, no client stack.
2. **Name every tool you need, and say what you do without it.** An instance
   offers a subset (ADR-0020), and a held zone retires its tool.
3. **No claim about the owner.** Read provenance off `class`, the speaker tags
   and the notes the tools already return. A heuristic about how somebody types
   is a guess that fails silently on everybody else.
4. **No counts.** A row count is an instance fact.
5. **Read and report; do not act.**

`description` is the field that decides whether a skill is ever used, the same
way `trigger` decides it for a procedure. Write the moment, not the subject.

`tests/test_no_personal_strings.py` runs over this tree, so a home directory, a
name or a cloud id in a `SKILL.md` fails CI the same way it does in the package.
