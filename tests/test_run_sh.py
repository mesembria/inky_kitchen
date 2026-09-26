import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SH = os.path.join(REPO, "run.sh")

pytestmark = pytest.mark.skipif(
    not shutil.which("git") or not shutil.which("bash"),
    reason="git and bash required")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _make_origin_and_clone(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "requirements.txt").write_text("requests\n")
    shutil.copy(RUN_SH, origin / "run.sh")
    os.chmod(origin / "run.sh", 0o755)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "init")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    return origin, clone


def _run(clone, **env):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(["bash", str(clone / "run.sh")], cwd=str(clone),
                          capture_output=True, text=True, env=e)


def test_updates_to_new_origin_commit_and_runs(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    (origin / "marker.txt").write_text("v2\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "v2")
    ran = clone / "ran.marker"
    r = _run(clone, INKY_RUN_CMD="touch {}".format(ran))
    assert ran.exists()                     # run command exec'd last
    assert (clone / "marker.txt").exists()  # clone hard-reset to new origin commit
    assert "updated" in r.stdout


def test_up_to_date_when_no_new_commit(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    ran = clone / "ran.marker"
    r = _run(clone, INKY_RUN_CMD="touch {}".format(ran))
    assert ran.exists()
    assert "up to date" in r.stdout


def test_offline_still_runs(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    ran = clone / "ran.marker"
    r = _run(clone, INKY_REMOTE=str(tmp_path / "does-not-exist"),
             INKY_RUN_CMD="touch {}".format(ran))
    assert ran.exists()                     # ran despite fetch failure
    assert "fetch failed" in r.stdout
    assert "up to date" not in r.stdout   # offline must not claim up-to-date
    assert "updated" not in r.stdout


def test_pushed_tag_is_picked_up_in_version(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    # tag a new origin commit, as if `git push --tags`
    _git(origin, "commit", "-q", "--allow-empty", "-m", "release")
    _git(origin, "tag", "v1.0.0")
    ran = clone / "ran.marker"
    r = _run(clone, INKY_RUN_CMD="touch {}".format(ran))
    # the clone must fetch the tag so `git describe --tags` resolves it, not a SHA
    desc = subprocess.run(["git", "describe", "--tags", "--always"],
                          cwd=str(clone), capture_output=True, text=True)
    assert desc.stdout.strip() == "v1.0.0"   # tag was fetched to the clone
    assert "v1.0.0" in r.stdout              # version shows in the run.sh log line


def test_requirements_change_triggers_reinstall(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    (origin / "requirements.txt").write_text("requests\nPillow\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add dep")
    pip_marker = clone / "pip.marker"
    pip_stub = tmp_path / "pip-stub.sh"
    pip_stub.write_text("#!/usr/bin/env bash\ntouch {}\n".format(pip_marker))
    os.chmod(pip_stub, 0o755)
    ran = clone / "ran.marker"
    r = _run(clone, INKY_PIP=str(pip_stub),
             INKY_RUN_CMD="touch {}".format(ran))
    assert "reinstalling" in r.stdout
    assert pip_marker.exists()              # stub pip was invoked
    assert ran.exists()


def _fake_timedatectl(tmp_path, answers):
    """A timedatectl that answers NTPSynchronized from `answers` in order,
    repeating the last one, and counts its calls."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (tmp_path / "answers").write_text("\n".join(answers) + "\n")
    script = bindir / "timedatectl"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "{c}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{c}"\n'
        'sed -n "${{n}}p" "{a}" | grep . || tail -n1 "{a}"\n'.format(
            c=tmp_path / "calls", a=tmp_path / "answers"))
    os.chmod(script, 0o755)
    return str(bindir) + os.pathsep + os.environ["PATH"]


def test_waits_for_clock_sync_before_running(tmp_path):
    # Review finding: a Pi Zero has no RTC and a user unit can't wait for the
    # network, so after a power cut the first fetch raced the network and the
    # panel could show a stale date. run.sh waits (bounded) for NTP sync.
    origin, clone = _make_origin_and_clone(tmp_path)
    path = _fake_timedatectl(tmp_path, ["no", "no", "yes"])
    ran = clone / "ran.marker"
    _run(clone, PATH=path, INKY_SYNC_POLL_S="0", INKY_RUN_CMD="touch {}".format(ran))
    assert ran.exists()
    assert (tmp_path / "calls").read_text().strip() == "3"


def test_clock_sync_wait_is_bounded(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    path = _fake_timedatectl(tmp_path, ["no"])
    ran = clone / "ran.marker"
    r = _run(clone, PATH=path, INKY_SYNC_TRIES="4", INKY_SYNC_POLL_S="0",
             INKY_RUN_CMD="touch {}".format(ran))
    assert ran.exists()                          # runs anyway: stale beats dark
    assert "clock not synced" in r.stdout


def _counting_pip(tmp_path, exit_code):
    """A pip stub that counts its calls and exits with exit_code."""
    count = tmp_path / "pip.count"
    stub = tmp_path / "pip-count.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "{c}" 2>/dev/null || echo 0); echo $((n+1)) > "{c}"\n'
        "exit {code}\n".format(c=count, code=exit_code))
    os.chmod(stub, 0o755)
    return str(stub), count


def test_a_failed_reinstall_is_retried_on_the_next_start(tmp_path):
    # A pull that brings a new dependency, then a pip failure (PyPI hiccup,
    # low disk): the next start must try again, or the daemon can never
    # import the new code and the panel freezes until someone SSHes in.
    origin, clone = _make_origin_and_clone(tmp_path)
    (origin / "requirements.txt").write_text("requests\nicalendar\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add dep")
    pip, count = _counting_pip(tmp_path, 1)
    env = dict(INKY_PIP=pip, INKY_RUN_CMD="true",
               INKY_REQ_STAMP=str(tmp_path / "stamp"))
    _run(clone, **env)
    _run(clone, **env)
    assert count.read_text().strip() == "2"


def test_a_successful_reinstall_is_not_repeated(tmp_path):
    origin, clone = _make_origin_and_clone(tmp_path)
    (origin / "requirements.txt").write_text("requests\nicalendar\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add dep")
    pip, count = _counting_pip(tmp_path, 0)
    env = dict(INKY_PIP=pip, INKY_RUN_CMD="true",
               INKY_REQ_STAMP=str(tmp_path / "stamp"))
    _run(clone, **env)
    r = _run(clone, **env)
    assert count.read_text().strip() == "1"
    assert "reinstalling" not in r.stdout
