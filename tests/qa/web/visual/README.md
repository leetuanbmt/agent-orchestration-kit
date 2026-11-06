Checks 13 dashboard screenshots: main views and the review drawer in light/dark, plus phone Home.
Update baselines: `QA_E2E=1 QA_VISUAL_UPDATE=1 uv run --frozen pytest -q tests/qa/test_e2e_web.py::test_web_visual`
Baselines are per platform (`process.platform`) because fonts differ.
