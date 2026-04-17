# Packages

For readers sharing creatures, terrariums, tools, or plugins across projects.

A KohakuTerrarium package is a directory with a `kohaku.yaml` manifest. It can contain creatures, terrariums, custom tools, plugins, and LLM presets. `kt install` puts it under `~/.kohakuterrarium/packages/<name>/` and the `@<name>/path` syntax references anything inside it.

Concept primer: [boundaries](../concepts/boundaries.md) — packages are how the framework makes "share reusable pieces" cheap.

## The official pack: `kt-defaults`

The first package most people install is `kt-defaults` — the showcase pack containing `swe`, `reviewer`, `researcher`, `ops`, `creative`, `general`, `root` creatures, terrariums like `swe_team` and `deep_research`, and a handful of plugins.

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
kt run @kt-defaults/creatures/swe
```

Study `kt-defaults` as a reference when you build your own pack.

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
  my_pack/                     # installable python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
```

Python modules resolve by dotted path (`my_pack.tools.my_tool:MyTool`). Configs resolve via `@my-pack/creatures/researcher`.

`python_dependencies` are installed by `kt install` when Python deps are declared.

## Install modes

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

Used by `kt run`, `kt terrarium run`, `kt edit`, `kt update`, `base_config:` inheritance, and programmatic `Agent.from_path(...)`.

## Discovery commands

```bash
kt list                         # installed packages + local agents
kt info path/or/@pkg/creature   # details of one config
kt extension list               # all tools/plugins/presets from all packages
kt extension info my-pack       # package metadata + what it ships
```

`kt extension list` is the easiest way to see what's available across your install base.

## Editing installed configs

```bash
kt edit @my-pack/creatures/researcher
```

Opens `config.yaml` in `$EDITOR` (falls back to `$VISUAL`, then `nano`). For editable installs this edits the source; for regular installs it edits the copy under `~/.kohakuterrarium/packages/`.

## Publishing

1. Push the repo to git (GitHub, GitLab, self-hosted — anything `git clone` handles).
2. Tag a version: `git tag v0.1.0 && git push --tags`.
3. Bump `version:` in `kohaku.yaml` for each release.
4. Share the URL: `kt install https://your/repo.git`.

There is no central registry. Packages are just git repos with a `kohaku.yaml`.

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

This lets a creature inside one package reference tools declared in another, as long as both are installed.

## Troubleshooting

- **`@my-pack/...` fails to resolve.** `kt list` to confirm the package is installed. For editable installs, check the `.link` file points at an existing directory.
- **`kt update my-pack` says "skipped".** Editable and non-git packages can't be updated through `kt update`. Edit the source (editable) or reinstall (copy).
- **`python_dependencies` didn't install.** Confirm `kt install` had permission to install packages in the current environment (use a virtualenv or `pip install --user`).
- **Package tool shadows a builtin.** Built-in tools are resolved first. Rename the package tool if you want yours to win.

## See also

- [Creatures](creatures.md) — packaging a creature.
- [Custom Modules](custom-modules.md) — writing tools/plugins to ship.
- [Reference / CLI](../reference/cli.md) — `kt install`, `kt list`, `kt extension`.
- [`kt-defaults`](../../kt-defaults/README.md) — reference package.
