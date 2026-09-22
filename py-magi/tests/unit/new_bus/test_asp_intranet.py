from channels.asp.intranet import is_intranet_origin, should_join_on_invite


def test_loopback_asp_is_intranet() -> None:
    assert is_intranet_origin("http://127.0.0.1:42069")
    assert is_intranet_origin("http://localhost:42069")
    assert is_intranet_origin("http://10.0.0.8:42069")
    assert not is_intranet_origin("https://asp.example.com")


def test_intranet_invite_joins_without_approval_or_wizard() -> None:
    assert should_join_on_invite(
        origin="http://127.0.0.1:42069",
        invitee="@bot-001.magi",
        handle="@bot-001.magi",
    )
    assert not should_join_on_invite(
        origin="http://127.0.0.1:42069",
        invitee="@other.magi",
        handle="@bot-001.magi",
    )
