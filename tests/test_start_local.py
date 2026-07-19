import os
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = REPOSITORY_ROOT / "scripts" / "start-local.sh"


def _run_with_fake_uv(tmp_path: Path, *arguments: str) -> str:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation_log = tmp_path / "uv-invocations.log"
    schedule_ready = tmp_path / "schedule-ready"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ " $* " == *" schedule "* ]]; then\n'
        '  printf "%s\\n" "$*" >> "$WAT_TEST_LOG"\n'
        '  : > "$WAT_TEST_SCHEDULE_READY"\n'
        "else\n"
        '  until [[ -e "$WAT_TEST_SCHEDULE_READY" ]]; do sleep 0.01; done\n'
        '  printf "%s\\n" "$*" >> "$WAT_TEST_LOG"\n'
        "fi\n"
    )
    fake_uv.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("WAT_INTERVAL_MINUTES", None)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["WAT_TEST_LOG"] = str(invocation_log)
    environment["WAT_TEST_SCHEDULE_READY"] = str(schedule_ready)

    subprocess.run(
        ["bash", str(START_SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return invocation_log.read_text()


def test_start_local_defaults_to_ten_minute_interval(tmp_path: Path) -> None:
    invocations = _run_with_fake_uv(tmp_path)

    assert "schedule --interval-minutes 10" in invocations


def test_start_local_accepts_an_interval_override(tmp_path: Path) -> None:
    invocations = _run_with_fake_uv(tmp_path, "--interval-minutes", "7")

    assert "schedule --interval-minutes 7" in invocations


def test_start_local_help_documents_ten_minute_default() -> None:
    result = subprocess.run(
        ["bash", str(START_SCRIPT), "--help"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Default: 10." in result.stdout
