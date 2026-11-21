from pathlib import Path

from gilhound.ingest.common import item_ref
from gilhound.market.items import ItemCatalog
from gilhound.sources.fixtures import install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "market"


def test_name_cache_retry_and_links(tmp_path: Path) -> None:
    items = [item_ref(5056, False), item_ref(5333, True)]
    catalog = ItemCatalog(tmp_path)
    try:
        with install("replay", FIXTURES / "retainer"):
            catalog.decorate(items, "Cerberus", wait=True)
            assert items[0].name == "Item 5056"
        with install("replay", FIXTURES / "items") as replay:
            catalog.decorate(items, "Cerberus", wait=True)
            assert replay.used == replay.index["requests"].keys()
            assert [i.name for i in items] == ["Bronze Ingot", "Hempen Yarn"]
    finally:
        catalog.close()
    reopened = ItemCatalog(tmp_path)
    try:
        with install("replay", FIXTURES / "retainer") as replay:
            refs = [item_ref(5056, True), item_ref(3, False)]
            reopened.decorate(refs, "Cerberus", wait=True)
            assert refs[0].name == "Bronze Ingot"
            assert refs[0].universalis_url == "https://universalis.app/market/5056?server=Cerberus"
            assert refs[0].saddlebag_url is None
            assert refs[1].saddlebag_url == "https://saddlebagexchange.com/queries/item-data/3"
    finally:
        reopened.close()
