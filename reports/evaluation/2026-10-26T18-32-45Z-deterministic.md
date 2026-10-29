# Evaluation: deterministic

- Run at: 2026-10-26T18:32:45+00:00
- Code: `aec8a29cf5c22577eacfe92e416d8c2066b0a17d-dirty`
- Dataset: `46780fe464d9` (46 examples)
- Options: {'sandbox': False, 'network': False, 'timeout': 120.0, 'line_tolerance': 3}
- Prompt versions: none
- Tool versions: ruff 0.17.2, mypy 2.4.0 (compiled: yes), bandit 1.9.4, semgrep 1.182.1, pip-audit 2.10.1

| Metric | Value |
|---|---|
| Precision (matches a human) | 5.3% (1/19) |
| Recall against humans | 9.1% (1/11) |
| Noise rate | 0.44 findings per 100 changed lines (19/4301) |
| Tool overlap | n/a |
| Location accuracy | 0.0% |
| Silent PRs with findings | 6/36 |

| Example | Changed lines | Issues | Findings | Matched | Tools not OK |
|---|---|---|---|---|---|
| [Textualize__rich__3347](https://github.com/Textualize/rich/pull/3347) | 63 | 0 | 0 | 0 | - |
| [Textualize__rich__3350](https://github.com/Textualize/rich/pull/3350) | 19 | 1 | 0 | 0 | - |
| [Textualize__rich__3845](https://github.com/Textualize/rich/pull/3845) | 16 | 1 | 0 | 0 | - |
| [Textualize__rich__4070](https://github.com/Textualize/rich/pull/4070) | 114 | 1 | 0 | 0 | - |
| [Textualize__rich__4079](https://github.com/Textualize/rich/pull/4079) | 24 | 0 | 0 | 0 | - |
| [aio-libs__aiohttp__14012](https://github.com/aio-libs/aiohttp/pull/14012) | 2 | 0 | 0 | 0 | - |
| [encode__httpx__2278](https://github.com/encode/httpx/pull/2278) | 82 | 1 | 0 | 0 | - |
| [encode__httpx__3371](https://github.com/encode/httpx/pull/3371) | 98 | 1 | 0 | 0 | - |
| [encode__httpx__3440](https://github.com/encode/httpx/pull/3440) | 14 | 0 | 0 | 0 | - |
| [encode__httpx__3445](https://github.com/encode/httpx/pull/3445) | 4 | 0 | 0 | 0 | - |
| [encode__httpx__3773](https://github.com/encode/httpx/pull/3773) | 5 | 0 | 0 | 0 | - |
| [pallets__click__3695](https://github.com/pallets/click/pull/3695) | 177 | 1 | 0 | 0 | - |
| [pallets__click__3780](https://github.com/pallets/click/pull/3780) | 32 | 0 | 0 | 0 | - |
| [pallets__click__3805](https://github.com/pallets/click/pull/3805) | 60 | 0 | 2 | 0 | - |
| [pallets__click__3818](https://github.com/pallets/click/pull/3818) | 384 | 1 | 0 | 0 | - |
| [pallets__click__3884](https://github.com/pallets/click/pull/3884) | 64 | 0 | 0 | 0 | - |
| [pallets__flask__4935](https://github.com/pallets/flask/pull/4935) | 78 | 0 | 0 | 0 | - |
| [pallets__flask__4992](https://github.com/pallets/flask/pull/4992) | 21 | 2 | 1 | 1 | - |
| [pallets__flask__6168](https://github.com/pallets/flask/pull/6168) | 500 | 0 | 0 | 0 | - |
| [pallets__werkzeug__2981](https://github.com/pallets/werkzeug/pull/2981) | 238 | 1 | 0 | 0 | - |
| [pallets__werkzeug__3081](https://github.com/pallets/werkzeug/pull/3081) | 78 | 0 | 0 | 0 | - |
| [pallets__werkzeug__3128](https://github.com/pallets/werkzeug/pull/3128) | 79 | 0 | 0 | 0 | - |
| [pallets__werkzeug__3166](https://github.com/pallets/werkzeug/pull/3166) | 8 | 0 | 0 | 0 | - |
| [pallets__werkzeug__3332](https://github.com/pallets/werkzeug/pull/3332) | 51 | 0 | 0 | 0 | - |
| [psf__black__5487](https://github.com/psf/black/pull/5487) | 105 | 0 | 0 | 0 | - |
| [psf__black__5492](https://github.com/psf/black/pull/5492) | 67 | 0 | 9 | 0 | - |
| [psf__black__5494](https://github.com/psf/black/pull/5494) | 11 | 0 | 0 | 0 | - |
| [psf__black__5530](https://github.com/psf/black/pull/5530) | 57 | 0 | 0 | 0 | - |
| [psf__black__5568](https://github.com/psf/black/pull/5568) | 20 | 0 | 2 | 0 | - |
| [pypa__pip__14235](https://github.com/pypa/pip/pull/14235) | 48 | 0 | 0 | 0 | - |
| [pypa__pip__14347](https://github.com/pypa/pip/pull/14347) | 120 | 0 | 0 | 0 | - |
| [pytest-dev__pytest__14670](https://github.com/pytest-dev/pytest/pull/14670) | 123 | 0 | 0 | 0 | - |
| [pytest-dev__pytest__15011](https://github.com/pytest-dev/pytest/pull/15011) | 340 | 0 | 0 | 0 | - |
| [pytest-dev__pytest__15040](https://github.com/pytest-dev/pytest/pull/15040) | 102 | 1 | 0 | 0 | - |
| [pytest-dev__pytest__15044](https://github.com/pytest-dev/pytest/pull/15044) | 34 | 0 | 0 | 0 | - |
| [pytest-dev__pytest__15192](https://github.com/pytest-dev/pytest/pull/15192) | 2 | 0 | 0 | 0 | - |
| [python-attrs__attrs__1463](https://github.com/python-attrs/attrs/pull/1463) | 53 | 0 | 0 | 0 | - |
| [python-attrs__attrs__1509](https://github.com/python-attrs/attrs/pull/1509) | 310 | 0 | 3 | 0 | - |
| [python-attrs__attrs__1541](https://github.com/python-attrs/attrs/pull/1541) | 105 | 0 | 0 | 0 | - |
| [python-attrs__attrs__1592](https://github.com/python-attrs/attrs/pull/1592) | 255 | 0 | 0 | 0 | - |
| [python-attrs__attrs__1636](https://github.com/python-attrs/attrs/pull/1636) | 3 | 0 | 0 | 0 | - |
| [scrapy__scrapy__8272](https://github.com/scrapy/scrapy/pull/8272) | 190 | 0 | 0 | 0 | - |
| [scrapy__scrapy__8314](https://github.com/scrapy/scrapy/pull/8314) | 46 | 0 | 1 | 0 | - |
| [scrapy__scrapy__8332](https://github.com/scrapy/scrapy/pull/8332) | 53 | 0 | 0 | 0 | - |
| [scrapy__scrapy__8335](https://github.com/scrapy/scrapy/pull/8335) | 37 | 0 | 1 | 0 | - |
| [scrapy__scrapy__8389](https://github.com/scrapy/scrapy/pull/8389) | 9 | 0 | 0 | 0 | - |
