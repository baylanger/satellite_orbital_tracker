"""Tests for parsing the ISS crew when NASA's astronaut page does not have the expected layout."""
from custom_components.satellite_orbital_tracker.coordinator import SatelliteDataUpdateCoordinator

CARD = '<div class="hds-meet-the-card"><h3>{name}</h3><p>NASA astronaut</p></div>'
OTHER_ASTRONAUTS = "".join(CARD.format(name=f"Astronaut {i}") for i in range(40))


def test_only_cards_in_the_station_section_are_crew() -> None:
    html = (
        '<div class="hds-module"><h2>At the Space Station</h2>'
        + CARD.format(name="On board")
        + "</div>"
        + '<div class="hds-module"><h2>Astronaut Corps</h2>' + OTHER_ASTRONAUTS + "</div>"
    )
    assert [m["name"] for m in SatelliteDataUpdateCoordinator._parse_crew_html(html)] == ["On board"]


def test_missing_station_heading_gives_no_crew() -> None:
    """A renamed heading must not turn every astronaut on the page into ISS crew."""
    html = '<div class="hds-module"><h2>Currently in Space</h2>' + OTHER_ASTRONAUTS + "</div>"
    assert SatelliteDataUpdateCoordinator._parse_crew_html(html) == []


def test_heading_outside_a_section_gives_no_crew() -> None:
    """Without its section container the station cards cannot be told apart from the others."""
    html = "<h2>At the Space Station</h2>" + OTHER_ASTRONAUTS
    assert SatelliteDataUpdateCoordinator._parse_crew_html(html) == []
