---
title: Packages
summary: Installing packs via kt install, the kohaku.yaml manifest, @pkg/ references, and publishing your own pack.
tags:
  - guides
  - package
  - distribution
---

# Packages

For readers sharing creatures, terrariums, tools, or plugins across projects.

A KohakuTerrarium package is a directory with a `kohaku.yaml` manifest. It can contain creatures, terrariums, custom tools, plugins, triggers, I/O modules, procedural skills, controller commands, user slash commands, prompt fragments, framework-hint overrides, and LLM presets. `kt install` puts it under `~/.kohakuterrarium/packages/<name>/` and the `@<name>/path` syntax references anything inside it.

Concept primer: [boundaries](../concepts/boundaries.md) — packages are how the framework makes "share reusable pieces" cheap.

## The official pack: `kt-biome`

The first package most people install is `kt-biome` — the showcase pack containing `swe`, `reviewer`, `researcher`, `ops`, `creative`, `general`, `root` creatures, terrariums like `swe_team` and `deep_research`, and a handful of plugins.

```bash
kt install @kt-biome
kt run @kt-biome/creatures/swe
```

The `@kt-biome` short form resolves via the marketplace (see below); `kt install https://github.com/Kohaku-Lab/kt-biome.git` still works if you'd rather bypass it.

Study `kt-biome` as a reference when you build your own pack.

## Manifest: `kohaku.yaml`

```yaml
name: my-pack
version: "0.1.0"
description: "My shared agent components"

creatures:
  - name: researcher           # folder at creatures/researcher/

terrariums:
  - name: research_team        # folder at terrariums/research_team/

tools:
  - name: my_tool
    module: my_pack.tools.my_tool
    class: MyTool

plugins:
  - name: my_guard
    module: my_pack.plugins.my_guard
    class: MyGuard

io:
  - name: discord_input
    module: my_pack.io.discord
    class: DiscordInput

triggers:
  - name: webhook
    module: my_pack.triggers.webhook
    class: WebhookTrigger

skills:
  - name: repo-surgery
    path: skills/repo-surgery
    description: Shared repo surgery workflow

commands:
  - name: handoff
    module: my_pack.commands.handoff
    class: HandoffCommand

user_commands:
  - name: deploy
    module: my_pack.user_commands.deploy
    class: DeployCommand

prompts:
  - name: git-safety
    path: prompts/git-safety.md

framework_hints:
  framework.execution_model.dynamic: |
    Use background work aggressively, but never duplicate it.

llm_presets:
  - name: my-custom-model

python_dependencies:
  - httpx>=0.27
  - pymupdf>=1.24
```

Folder layout:

```
my-pack/
  kohaku.yaml
  creatures/researcher/config.yaml
  terrariums/research_team/config.yaml
  prompts/git-safety.md
  skills/repo-surgery/SKILL.md
  my_pack/                     # installable python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
    io/discord.py
    triggers/webhook.py
    commands/handoff.py
    user_commands/deploy.py
```

Python modules resolve by dotted path (`my_pack.tools.my_tool:MyTool`). Configs resolve via `@my-pack/creatures/researcher`.

`python_dependencies` are installed by `kt install` when Python deps are declared.

### Newer manifest slots

Beyond `tools`, `plugins`, and `llm_presets`, packages can now contribute:

- `io:` — package-resolved input/output module classes
- `triggers:` — package-resolved trigger classes
- `skills:` — procedural skill bundles (`SKILL.md`) discoverable by creatures
- `commands:` — controller `##name##` commands
- `user_commands:` — slash commands the human can type
- `prompts:` / `templates:` — reusable Jinja include fragments for prompts
- `framework_hints:` — package-level overrides for the built-in framework-hint prose

Collision policy is intentionally mixed:

- tools/plugins/io/triggers/user commands/controller commands use a shared name
  namespace and collisions are treated as errors or explicit overrides,
- procedural skills are the exception: they are last-wins, with narrower scope
  (project/user/creature) overriding package-shipped copies.

## Install modes

### Marketplace spec (`@name`)

```bash
kt install @kt-biome              # newest non-yanked version
kt install @kt-biome@v1.2.0       # explicit version pin
kt install @myfork/kt-biome       # name restricted to a specific source
```

The `@`-prefix form resolves through the marketplace ([see below](#the-marketplace-and-name-resolution)) to a git URL, then clones into `~/.kohakuterrarium/packages/<name>/` the same way `kt install <git-url>` does. **Editable mode is unsupported for `@` specs** — clone first, then install with `-e`.

### Git URL (clone)

```bash
kt install https://github.com/you/my-pack.git
```

Clones into `~/.kohakuterrarium/packages/my-pack/`. Update with `kt update my-pack`.

### Local path (copy)

```bash
kt install ./my-pack
```

Copies the folder in. Update by re-running `kt install` or editing the copy directly.

### Local path (editable)

```bash
kt install ./my-pack -e
```

Writes `~/.kohakuterrarium/packages/my-pack.link` pointing at the source directory. Edits in the source are visible immediately — no re-install needed. Great for iterating during development.

### Uninstall

```bash
kt uninstall my-pack
```

## Resolving `@pkg/path`

`@my-pack/creatures/researcher` →

- If `my-pack.link` exists: follow the pointer.
- Else: `~/.kohakuterrarium/packages/my-pack/creatures/researcher/`.

Used by `kt run`, `kt terrarium run`, `kt edit`, `kt update`, `base_config:` inheritance, and programmatic loaders such as `Terrarium.with_creature(...)`, `engine.add_creature(...)`, `Studio.sessions.start_creature(...)`, and lower-level `Agent.from_path(...)`.

## The marketplace and `@name` resolution

[TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) is the public marketplace for KohakuTerrarium packages.  It's a public GitHub repo containing one YAML file (`registry.yaml`) plus a per-package entry directory.  `kt install @<name>` reads that file to resolve the name to a git URL, then installs normally.

The framework fetches and caches the index at `~/.kohakuterrarium/marketplace/cache.json` with a 1-hour TTL (ETag-revalidated against upstream).  Cold-cache offline = clear error.  Warm-cache offline = silent fallback to cached data with a warning log.

### CLI verbs

```bash
kt marketplace            # alias for `list`: show configured sources
kt marketplace list
kt marketplace refresh    # force cache bust + re-fetch
kt marketplace search [query] [--tag <t>] [--author <a>] [--json]
kt marketplace info @<name>

kt marketplace add <url> [--alias <name>]   # add a custom source
kt marketplace remove <url-or-alias>
kt marketplace reset                         # restore the default-only source list
```

### Spec syntax

| Form | Resolves to |
|---|---|
| `@kt-biome` | Newest non-yanked version of `kt-biome` from the first source that lists it |
| `@kt-biome@v1.2.0` | Exact version pin (yanked versions allowed for reproducibility) |
| `@myfork/kt-biome` | `kt-biome` restricted to the source aliased `myfork` |

### Configuring sources

The default source list is just TerrariumMarket.  Add a fork or your own server:

```bash
kt marketplace add https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml --alias myfork
```

Sources are merged in lookup order; the first occurrence of a name wins (shadowing is logged).  Settings persist under `~/.kohakuterrarium/marketplace-sources.json`.

Env-var overrides (one-shot, no settings file write):

```bash
KT_MARKETPLACE_SOURCES=https://a.test/r.yaml,https://b.test/r.yaml kt marketplace search
KT_MARKETPLACE_CACHE_TTL=0 kt marketplace search   # bypass cache for this call
```

### Browsing from the app

The desktop / web app's **Settings → Extensions** tab is now a two-pane "Catalog" view: **Browse** (marketplace packages with Install buttons) and **Installed** (your local set with Uninstall + "Update available" badges).  The same `@<name>` install flow runs in the background, so `kt install @kt-biome` from the CLI and clicking Install in the app land on the same code path.

## Discovery commands

```bash
kt list                         # installed packages + local agents
kt info path/or/@pkg/creature   # details of one config
kt extension list               # all tools/plugins/presets from all packages
kt extension info my-pack       # package metadata + what it ships
kt marketplace                  # configured marketplace sources
kt marketplace search           # browse the marketplace (all packages)
kt marketplace search biome     # substring + tag filter
kt marketplace info @kt-biome   # detail view for a marketplace entry
```

`kt extension list` is the easiest way to see what's installed locally; `kt marketplace search` is the equivalent for what's available to install.

## Editing installed configs

```bash
kt edit @my-pack/creatures/researcher
```

Opens `config.yaml` in `$EDITOR` (falls back to `$VISUAL`, then `nano`). For editable installs this edits the source; for regular installs it edits the copy under `~/.kohakuterrarium/packages/`.

## Publishing

1. Push the repo to git (GitHub, GitLab, self-hosted — anything `git clone` handles).
2. Tag a version: `git tag v0.1.0 && git push --tags`.
3. Bump `version:` in `kohaku.yaml` for each release.
4. **Optional but recommended**: list your package on TerrariumMarket so users can install with `kt install @your-package`.  Open a PR adding `entries/<your-package>/entry.yaml` + `entries/<your-package>/README.md` to [Kohaku-Lab/TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket); CI validates the schema + tag existence; a maintainer merges.  See [the contributing guide](https://github.com/Kohaku-Lab/TerrariumMarket/blob/main/CONTRIBUTING.md) for the walkthrough.
5. Otherwise, just share the URL: `kt install https://your/repo.git`.

Listing on TerrariumMarket is **not required** — packages are still just git repos with a `kohaku.yaml`, and the direct-URL install path is unchanged.  The marketplace is a discovery layer over that, not a replacement.

### Versioning

Keep `version:` in sync with git tags. `kt update` does `git pull` under the hood; consumers pinned to a tag can check it out manually:

```bash
cd ~/.kohakuterrarium/packages/my-pack
git checkout v0.1.0
```

## Extension discovery at runtime

When the framework loads a creature, the loader looks up tool/plugin names first in the creature's local config, then in installed packages' manifests. Package-declared tools are surfaced through `type: package` in config:

```yaml
tools:
  - name: my_tool
    type: package          # resolved through the `tools:` list in kohaku.yaml
```

The same pattern now applies to package-declared I/O and triggers:

```yaml
input:
  type: package
  name: discord_input

triggers:
  - type: package
    name: webhook
```

Prompt fragments are resolved from Jinja includes:

```md
{% include "git-safety" %}
```

and controller/user commands are discovered from the package manifest rather
than the creature folder.

This lets a creature inside one package reference extensions declared in another, as long as both are installed.

## Troubleshooting

- **`@my-pack/...` fails to resolve.** `kt list` to confirm the package is installed. For editable installs, check the `.link` file points at an existing directory.
- **`kt update my-pack` says "skipped".** Editable and non-git packages can't be updated through `kt update`. Edit the source (editable) or reinstall (copy).
- **`python_dependencies` didn't install.** Confirm `kt install` had permission to install packages in the current environment (use a virtualenv or `pip install --user`).
- **Package tool shadows a builtin.** Built-in tools are resolved first. Rename the package tool if you want yours to win.

## See also

- [Creatures](creatures.md) — packaging a creature.
- [Custom Modules](custom-modules.md) — writing tools/plugins to ship.
- [Reference / CLI](../reference/cli.md) — `kt install`, `kt list`, `kt extension`.
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) — reference package.
