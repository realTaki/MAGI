from magi_asp.asp.k8s import KubernetesSpawner, magi_pod, magi_pod_name
from magi_asp.asp.spawn import default_spawner, in_kubernetes


def test_pod_name_and_one_command_container() -> None:
    assert magi_pod_name("@eva-000.magi") == "magi-eva-000"
    pod = magi_pod(
        handle="@eva-000.magi",
        base="http://magi-asp:42069",
        token="tok",
    )
    container = pod["spec"]["containers"][0]
    assert pod["metadata"]["name"] == "magi-eva-000"
    assert container["command"] == ["magi"]
    assert container["args"] == ["@eva-000.magi", "http://magi-asp:42069", "tok"]


def test_kubernetes_spawner_creates_one_pod_per_magi() -> None:
    created: list[dict] = []
    spawner = KubernetesSpawner(create_pod=created.append)
    spawned = spawner.spawn(
        handle="@eva-001.magi",
        base="http://magi-asp:42069",
        token="tok",
    )
    assert spawned.spawned is True
    assert spawned.pid is None
    assert created[0]["metadata"]["name"] == "magi-eva-001"
    assert spawner.pods == ["magi-eva-001"]


def test_default_spawner_is_k8s_inside_a_cluster(monkeypatch) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    assert in_kubernetes() is False
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    assert in_kubernetes() is True
    spawner = default_spawner()
    assert type(spawner).__name__ == "KubernetesSpawner"
