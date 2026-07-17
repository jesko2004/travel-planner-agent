from __future__ import annotations

from travel_planner.models import PlatformLink


PLATFORM_HOMEPAGES = {
    "携程": "https://hotels.ctrip.com/",
    "同程": "https://www.ly.com/hotel/",
    "飞猪": "https://www.fliggy.com/",
    "美团": "https://hotel.meituan.com/",
}


def build_hotel_platform_links(
    city: str,
    hotel_name: str,
    start_date: str,
    end_date: str,
) -> list[PlatformLink]:
    """提供稳定主页和可复制搜索词，不伪造未经验证的深链。"""
    search_text = f"{city} {hotel_name} 入住{start_date} 离店{end_date}"
    return [
        PlatformLink(
            platform=platform,
            url=url,
            search_text=search_text,
        )
        for platform, url in PLATFORM_HOMEPAGES.items()
    ]
