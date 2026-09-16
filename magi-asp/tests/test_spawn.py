from magi_asp.asp.spawn import ProcessSpawner, magi_cli


def test_magi_cli_starts_the_intranet_runtime() -> None:
    command = magi_cli("/venv/bin/python", "@eva-000.magi", "http://127.0.0.1:42069", "tok")
    assert command == [
        "/venv/bin/python",
        "-m",
        "magi",
        "@eva-000.magi",
        "http://127.0.0.1:42069",
        "tok",
    ]


def test_process_spawner_runs_python_m_magi(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Proc:
        pid = 99

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            seen["terminated"] = True

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        return Proc()

    monkeypatch.setattr("magi_asp.asp.spawn.subprocess.Popen", fake_popen)
    monkeypatch.setattr("magi_asp.asp.spawn._spawn_disabled", lambda: False)
    monkeypatch.setattr("magi_asp.asp.spawn._resolve_magi_python", lambda: "/venv/bin/python")
    spawner = ProcessSpawner()
    spawned = spawner.spawn(
        handle="@eva-000.magi",
        base="http://127.0.0.1:42069",
        token="tok",
    )
    assert spawned.spawned is True
    assert spawned.pid == 99
    assert seen["cmd"] == magi_cli(
        "/venv/bin/python", "@eva-000.magi", "http://127.0.0.1:42069", "tok"
    )
    spawner.close()
    assert seen["terminated"] is True
