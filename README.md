# tornspector

![tests](https://github.com/sqlinjected/tornspector/actions/workflows/tests.yml/badge.svg)

Ranked war intel on enemy factions in Torn. Someone was selling these for 5 xanax each so I made my own lol.

![sample](docs/sample-report.png)

[example report](docs/sample-report.html)

## setup

Python 3.10+, no extra packages needed. If your system uses `python3`, use that in the commands below.

```sh
git clone https://github.com/sqlinjected/tornspector.git
cd tornspector
python -m tornspector demo
```

The demo runs without an API key. Open the HTML file it prints in `reports/`.

## usage

For a real report, copy `.env.example` to `.env`.

- Windows PowerShell — `Copy-Item .env.example .env`
- macOS/Linux — `cp .env.example .env`

Put your public Torn API key after `TORN_API_KEY=` in `.env`. Replace `12345` with the number after `ID=` in the target faction's Torn profile URL.

```sh
python -m tornspector report 12345
python -m tornspector report 12345 --wars 20
```

Open the generated HTML in `reports/`. Use your browser's Print menu to save it as a PDF.

For optional battle-stat estimates, add `--ffscouter`.

```sh
python -m tornspector report 12345 --ffscouter
```

Set `FFSCOUTER_KEY` in `.env` to a key registered with FFScouter. If you leave it blank, `--ffscouter` uses `TORN_API_KEY`, which must then be registered with FFScouter. Reports skip FFScouter by default.

For the activity pages, run `python -m tornspector watch 12345` and leave it on for a week or so. It checks the roster every 15 minutes. Run the report again to include the activity data.

## notes

- only counts wars a member was actually in the faction for, so new people aren't no-shows
- loss persistence = avg hits in losses / avg hits in wins
- threat score weights are in `THREAT_WEIGHTS` in `analysis.py` if you want to mess with them
- finished wars get cached so reruns are fast

## API ToS

| Item | Details |
|---|---|
| Data storage | Reports, cache, and activity data are saved locally in `reports/`, `cache/`, and `data/`. |
| Data sharing | With `--ffscouter`, target player IDs are sent to FFScouter. |
| Purpose of use | Faction war planning |
| Key storage & sharing | Keys are read from `.env` or environment variables. Torn receives `TORN_API_KEY`. With `--ffscouter`, FFScouter receives `FFSCOUTER_KEY` if set, otherwise `TORN_API_KEY`. |
| Key access level | Public |

## Reporting bugs

If you find a bug, open an issue and include the steps to reproduce it.

[MIT](LICENSE)
