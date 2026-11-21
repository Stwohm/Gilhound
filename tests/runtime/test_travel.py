import pytest

from gilhound.runtime.travel import Travel, command


def test_route_command() -> None:
    assert command("Raiden") == "/li raiden market"
    with pytest.raises(ValueError):
        command("Raiden\n/extra")


def test_window_not_found() -> None:
    class NoGame:
        def find_game(self) -> None:
            return None

    travel = Travel()
    assert not travel.go("Cerberus", 120, NoGame)
    with pytest.raises(BlockingIOError):
        travel.go("Cerberus", 120, NoGame)
