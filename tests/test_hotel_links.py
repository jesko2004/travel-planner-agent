from travel_planner.services.hotel_links import build_hotel_platform_links


def test_hotel_links_are_homepages_with_copyable_search_text():
    links = build_hotel_platform_links("北京", "示例酒店", "2026-10-02", "2026-10-04")
    assert {link.platform for link in links} == {"携程", "同程", "飞猪", "美团"}
    assert all("示例酒店" in link.search_text for link in links)
    assert all(link.url.startswith("https://") for link in links)

