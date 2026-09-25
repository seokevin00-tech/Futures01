# Your desk folder

This is a ready-made project folder for the risk console. Point the application
at it, or copy its contents next to the packaged executable.

```bash
python -m futures_agents.ui --home desk
```

`desk-config.json` here is the documented $50,000 default, written out in full
so every knob is visible in one place. Edit it in any text editor — the console
re-reads it on every request, so a change is live on your next click.

`desk-account.json` is not included: it is created the first time you save the
account, and it holds your real equity and drawdown history. It is deliberately
never written automatically, so a mistyped `--home` gives you an empty folder
rather than a silently fresh account.

To edit the UI itself, copy the assets out of the package and they take
precedence over the built-in copy:

```bash
cp -r futures_agents/ui/assets desk/assets
```
