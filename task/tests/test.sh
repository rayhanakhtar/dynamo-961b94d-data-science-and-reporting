#!/bin/bash
#
# Runs inside the SHARED environment image (environment/Dockerfile) — canonical TB2 has no
# separate verifier image. pytest is baked into environment/Dockerfile, so do NOT install or
# download anything here — verify-time setup is rejected by the static checks.
#
# Verifier modules live in tests/ and run against the agent's /app/output artifacts.
# Harbor overlays tests/ at /tests only at verify time, so ground truth / expected outputs
# stay in tests/ (never in environment/, where the agent could read them).
#
# Execution hardening (verifier integrity):
#   * Run from a fresh, empty working directory so no agent-controlled file in /app
#     (conftest.py, sitecustomize.py, pytest.ini, pyproject.toml, setup.cfg, tox.ini,
#     .pytest_cache) can be discovered from cwd.
#   * Pin --rootdir to /tests so conftest collection and config discovery stay inside
#     /tests (agent cannot write there; Harbor overlay).
#   * -c /dev/null disables any config-file discovery outside /tests.
#   * -p no:cacheprovider disables the pytest cache (no .pytest_cache reads/writes).
#   * PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 blocks auto-discovery of every installed plugin;
#     only the CTRF reporter is re-enabled explicitly via -p pytest_json_ctrf.
#   * PYTHONNOUSERSITE=1 blocks user-site startup hooks (usercustomize/sitecustomize,
#     user-installed plugins). PYTHONSAFEPATH=1 keeps cwd off sys.path.
#   * PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTEST_ADDOPTS are cleared so no injected
#     import/startup path can alter collection or exit status.
#   * --import-mode=importlib avoids sys.path insertion of test dirs and import
#     collisions with any agent-controlled module.
#
# Reward contract: pytest exit 0 -> reward 1, anything else -> reward 0.
# --ctrf writes a standard JSON report; write 1/0 to /logs/verifier/reward.txt.

cd "$(mktemp -d)" || exit 1

export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONNOUSERSITE=1
export PYTHONSAFEPATH=1
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTEST_ADDOPTS

pytest -p no:cacheprovider -p ctrf \
  --ctrf /logs/verifier/ctrf.json \
  --rootdir=/tests -c /dev/null \
  --import-mode=importlib \
  /tests -rA

rc=$?
if [ "$rc" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$rc"
