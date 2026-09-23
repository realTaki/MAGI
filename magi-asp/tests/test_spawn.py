from server.spawn import ProcessSpawner, magi_cli, ts_magi_cli


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

    monkeypatch.setattr("server.spawn.subprocess.Popen", fake_popen)
    monkeypatch.setattr("server.spawn._spawn_disabled", lambda: False)
    monkeypatch.setattr("server.spawn._resolve_magi_python", lambda: "/venv/bin/python")
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


def test_process_spawner_can_run_typescript_magi(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class Proc:
        pid = 100
        def poll(self): return None
        def terminate(self): return None

    monkeypatch.setenv("MAGI_RUNTIME", "typescript")
    monkeypatch.setattr("server.spawn.subprocess.Popen", lambda cmd, **kwargs: seen.update(cmd=cmd, cwd=kwargs.get("cwd")) or Proc())
    monkeypatch.setattr("server.spawn._spawn_disabled", lambda: False)
    monkeypatch.setattr("server.spawn._resolve_magi_bun", lambda: "/runtime/bin/bun")
    spawner = ProcessSpawner()
    spawned = spawner.spawn(handle="@eva-001.magi", base="http://127.0.0.1:42069", token="tok")
    assert spawned.spawned is True
    assert seen["cmd"] == ts_magi_cli("/runtime/bin/bun", "@eva-001.magi", "http://127.0.0.1:42069", "tok")
    assert str(seen["cwd"]).endswith("ts-magi")
    spawner.close()
