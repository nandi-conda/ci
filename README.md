# nandi-conda/ci

Shared CI for every nandi-conda repo. One reusable workflow,
[`conda-package.yml`](.github/workflows/conda-package.yml), builds a repo's conda
package and publishes it to [prefix.dev/nandi-testing](https://prefix.dev/channels/nandi-testing).

It finds the package itself: a `pixi.toml` with a `[package]` table is built with
`pixi build`, otherwise `recipe.yaml` or `recipe/recipe.yaml` is built with
`rattler-build`. A repo with neither passes green.

## Use it in a repo

Copy [`templates/package.yml`](templates/package.yml) to `.github/workflows/package.yml`.
Change behaviour for every repo by editing the reusable workflow here.

Inputs (all optional): `channel` (default `nandi-testing`), `recipe` (override the
detected path), `build-channels` (for rattler-build recipes, default `conda-forge`),
`pre-build` (shell commands run before packaging; by default `ci/pre-package.sh` runs if it exists), `flutter` (install stable Flutter and GTK build deps first), `extra-packages` (more pixi package paths to build), `cached-package` (one slow package path, rebuilt only when its files change), `timeout-minutes` (default 60), `copy-deps` (also upload conda-forge runtime deps, default `true`).

## Secrets

Publishing uses the org secret `PREFIX_API_KEY` (a prefix.dev API key with upload
access to the channel), passed through by `secrets: inherit`. Without it,
rattler-build falls back to prefix.dev trusted publishing (OIDC).

Uploads use `--skip-existing`: bump the version or build number to publish a new build.

## Mirrored conda-forge packages

With `copy-deps`, conda-forge runtime deps are copied into the channel so it installs
on its own. conda-forge corrects package metadata later through repodata patches that
raw copies don't carry, so [`scripts/sync-mirror.py`](scripts/sync-mirror.py) writes
conda-forge's current `depends`/`constrains` into each copy before upload. The
[`sync-mirror.yml`](.github/workflows/sync-mirror.yml) workflow re-syncs copies already
on the channel (weekly, or run it by hand) and checks that packages still solve.
