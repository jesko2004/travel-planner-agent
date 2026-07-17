from travel_planner.models import UserProfile
from travel_planner.storage.database import SQLiteRepository


def test_database_round_trip(tmp_path, verified_itinerary):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    repository.save_profile(UserProfile(home_city="上海"))
    assert repository.load_profile().home_city == "上海"

    repository.save_itinerary(verified_itinerary)
    loaded = repository.get_itinerary(verified_itinerary.itinerary_id)
    assert loaded is not None
    assert loaded.title == verified_itinerary.title
    assert len(repository.list_itineraries()) == 1

    repository.delete_itinerary(verified_itinerary.itinerary_id)
    assert repository.get_itinerary(verified_itinerary.itinerary_id) is None


def test_database_payload_does_not_contain_api_key(tmp_path, verified_itinerary):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    repository.save_itinerary(verified_itinerary)
    raw = (tmp_path / "travel.db").read_bytes()
    assert b"DEEPSEEK_API_KEY" not in raw
    assert b"AMAP_MAPS_API_KEY" not in raw

