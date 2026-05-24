# Fork Workflow

This repository is our public fork of `browser-use/video-use`.

## Remotes

- `origin` points to our fork.
- `upstream` points to `https://github.com/browser-use/video-use`.

Check them with:

```bash
git remote -v
```

## Daily Development

Keep our custom work as normal commits on `main` or short-lived feature branches.
Do not keep important behavior only in local scripts, chat notes, or untracked
files. If the skill needs to remember an editing convention, add it to
`SKILL.md` or a helper in this repository.

Never commit `.env`, API keys, generated videos, or per-session edit folders.
They are ignored locally and should stay outside version control.

## Updating From Upstream

Before starting an update:

```bash
git status -sb
```

If local work is clean or intentionally committed, pull upstream into our fork:

```bash
git fetch upstream
git merge upstream/main
```

Resolve conflicts by preserving both upstream fixes and our local behavior. Pay
special attention to:

- `SKILL.md`
- `helpers/transcribe.py`
- `helpers/transcribe_batch.py`
- `helpers/render.py`
- `helpers/timeline_view.py`

Then verify the helper CLIs:

```bash
python helpers/transcribe.py --help
python helpers/transcribe_batch.py --help
python helpers/render.py --help
python helpers/timeline_view.py --help
git diff --check
```

Push the merged result back to our fork:

```bash
git push origin main
```
