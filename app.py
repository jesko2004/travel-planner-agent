from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from travel_planner.config import load_settings
from travel_planner.models import Itinerary, TripRequest, UserProfile, VerificationStatus
from travel_planner.services.calendar_export import generate_ics
from travel_planner.services.input_validation import SensitiveDataError
from travel_planner.storage.database import SQLiteRepository
from travel_planner.workflow import TravelPlannerWorkflow, check_health


st.set_page_config(page_title="我的国内旅行助手", page_icon="🧭", layout="wide")


def get_secrets() -> dict[str, str]:
    try:
        return dict(st.secrets)
    except Exception:
        return {}


@st.cache_resource
def get_repository(database_path: str) -> SQLiteRepository:
    return SQLiteRepository(Path(database_path))


def csv_list(value: str) -> list[str]:
    return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]


def parse_child_ages(value: str) -> list[int]:
    return [int(item) for item in csv_list(value)] if value.strip() else []


def validation_error_text(error: ValidationError) -> str:
    fields = sorted(
        {".".join(str(part) for part in item["loc"]) for item in error.errors()}
    )
    return "输入字段未通过校验：" + "、".join(fields)


def status_text(status: VerificationStatus) -> str:
    return {
        VerificationStatus.VERIFIED: "✅ 已验证",
        VerificationStatus.UNVERIFIED: "⚠️ 未验证草案",
        VerificationStatus.EXPIRED: "🟠 证据已过期，请重新查询",
        VerificationStatus.DRAFT: "📝 草案",
    }[status]


def evidence_text(label: str, evidence) -> str:
    return (
        f"{label} ｜ {evidence.source}/{evidence.tool_name} ｜ "
        f"查询 {evidence.checked_at:%Y-%m-%d %H:%M %Z} ｜ "
        f"过期 {evidence.expires_at:%Y-%m-%d %H:%M %Z} ｜ "
        f"{evidence.status.value}"
    )


def itinerary_evidences(itinerary: Itinerary) -> list:
    values = []
    for day_plan in itinerary.days:
        values.extend(activity.poi.evidence for activity in day_plan.activities)
        values.extend(
            route.evidence for route in day_plan.route_legs if route.evidence is not None
        )
    values.extend(hotel.poi.evidence for hotel in itinerary.hotels)
    if itinerary.weather and itinerary.weather.evidence:
        values.append(itinerary.weather.evidence)
    return values


def render_itinerary(itinerary: Itinerary) -> bool:
    st.header(itinerary.title)
    if itinerary.status == VerificationStatus.EXPIRED:
        st.warning(status_text(itinerary.status))
    elif itinerary.status == VerificationStatus.UNVERIFIED:
        st.error(status_text(itinerary.status))
    else:
        st.subheader(status_text(itinerary.status))
    expired_count = sum(
        evidence.status == VerificationStatus.EXPIRED
        for evidence in itinerary_evidences(itinerary)
    )
    if expired_count and itinerary.status != VerificationStatus.EXPIRED:
        st.warning(f"🟠 当前有 {expired_count} 项证据已过期，请重新查询后再作为出行依据。")
    st.write(itinerary.overview)

    if itinerary.warnings:
        for warning in itinerary.warnings:
            st.warning(warning)

    overview_tab, days_tab, hotel_tab, budget_tab, evidence_tab = st.tabs(
        ["概览", "每日行程", "酒店候选", "预算", "验证与来源"]
    )
    with overview_tab:
        request = itinerary.request
        st.write(
            f"**{request.origin_city} → {request.destination}** ｜ "
            f"{request.start_date} 至 {request.end_date} ｜ "
            f"{request.adults} 位成人、{request.children} 位儿童"
        )
        if request.intercity_transport:
            st.info(f"用户录入的城际交通：{request.intercity_transport}")
        if itinerary.weather:
            st.subheader("天气与装备")
            st.write(itinerary.weather.summary)
            for advice in itinerary.weather.packing_advice:
                st.write(f"- {advice}")

    with days_tab:
        for day_plan in itinerary.days:
            with st.expander(f"{day_plan.day} · {day_plan.title}", expanded=True):
                if not day_plan.activities:
                    st.warning("当天没有通过结构校验的活动。")
                for index, activity in enumerate(day_plan.activities):
                    st.markdown(
                        f"### {activity.start_time.strftime('%H:%M')}–"
                        f"{activity.end_time.strftime('%H:%M')} · {activity.poi.name}"
                    )
                    st.write(activity.poi.address)
                    if activity.notes:
                        st.caption(activity.notes)
                    activity.locked = st.checkbox(
                        "重新规划时锁定此活动",
                        value=activity.locked,
                        key=f"lock-{itinerary.itinerary_id}-{activity.activity_id}",
                    )
                    if index < len(day_plan.route_legs):
                        route = day_plan.route_legs[index]
                        distance = (
                            f"{route.distance_meters / 1000:.1f} km"
                            if route.distance_meters is not None
                            else "距离未验证"
                        )
                        duration = (
                            f"约 {route.duration_minutes} 分钟"
                            if route.duration_minutes is not None
                            else "时间未验证"
                        )
                        st.info(f"下一程：{route.transport_mode}，{distance}，{duration}。{route.summary}")
                        if route.navigation_url:
                            st.link_button("在高德中查看", route.navigation_url)

    with hotel_tab:
        st.warning("所有酒店价格、库存、房型、发票和取消政策均需在平台确认；本应用不会代为下单。")
        if not itinerary.hotels:
            st.info("本次未获得高德酒店候选，请在官方平台手工搜索。")
        for hotel in itinerary.hotels:
            st.subheader(hotel.poi.name)
            st.write(hotel.poi.address)
            st.caption(hotel.confirmation_notice)
            st.code(hotel.platform_links[0].search_text if hotel.platform_links else hotel.poi.name)
            columns = st.columns(4)
            for column, platform in zip(columns, hotel.platform_links):
                column.link_button(platform.platform, platform.url, use_container_width=True)

    with budget_tab:
        budget = itinerary.budget
        st.metric("程序估算总额", f"¥{budget.total:,.0f}", f"预算 ¥{itinerary.request.total_budget:,.0f}")
        st.write(
            {
                "活动": budget.activities,
                "住宿估算（非实时）": budget.accommodation_estimate,
                "市内交通估算": budget.local_transport_estimate,
                "餐饮估算": budget.food_estimate,
                "其他": budget.other,
            }
        )

    with evidence_tab:
        if itinerary.validation_issues:
            for issue in itinerary.validation_issues:
                renderer = st.error if issue.severity == "error" else st.warning
                renderer(f"[{issue.code}] {issue.message}")
        else:
            st.success("确定性校验未发现问题。")
        for day_plan in itinerary.days:
            for activity in day_plan.activities:
                evidence = activity.poi.evidence
                renderer = (
                    st.warning
                    if evidence.status == VerificationStatus.EXPIRED
                    else st.caption
                )
                renderer(evidence_text(activity.poi.name, evidence))
            for route in day_plan.route_legs:
                if route.evidence:
                    evidence = route.evidence
                    renderer = (
                        st.warning
                        if evidence.status == VerificationStatus.EXPIRED
                        else st.caption
                    )
                    renderer(
                        evidence_text(
                            f"路线 {route.origin_activity_id} → {route.destination_activity_id}",
                            evidence,
                        )
                    )
        for hotel in itinerary.hotels:
            evidence = hotel.poi.evidence
            renderer = (
                st.warning
                if evidence.status == VerificationStatus.EXPIRED
                else st.caption
            )
            renderer(evidence_text(f"酒店 {hotel.poi.name}", evidence))
        if itinerary.weather and itinerary.weather.evidence:
            evidence = itinerary.weather.evidence
            renderer = (
                st.warning
                if evidence.status == VerificationStatus.EXPIRED
                else st.caption
            )
            renderer(evidence_text("天气", evidence))

    st.download_button(
        "下载分时日历（ICS）",
        data=generate_ics(itinerary),
        file_name=f"{itinerary.request.destination}-行程.ics",
        mime="text/calendar",
    )
    return st.button(
        "按已锁定活动重新规划",
        key=f"replan-{itinerary.itinerary_id}",
        help="锁定活动的日期、时间和地点不会被无提示替换。",
    )


settings = load_settings(get_secrets())
repository = get_repository(str(settings.database_path))
repository.set_forbidden_values(settings.sensitive_values)
health = check_health(settings, repository)

if "itinerary" not in st.session_state:
    st.session_state.itinerary = None
if "copied_request" not in st.session_state:
    st.session_state.copied_request = None

try:
    profile = repository.load_profile()
except Exception:
    profile = UserProfile()

st.title("🧭 我的国内旅行助手")
st.caption("仅规划中国大陆行程；高德提供只读查询，可信状态由程序校验，酒店由你在官方平台确认和下单。")

with st.sidebar:
    st.header("运行状态")
    st.write("✅ DeepSeek" if health.deepseek else "❌ DeepSeek Key")
    st.write("✅ 高德 Key" if health.amap_key else "❌ 高德 Key")
    st.write("✅ Node/npm" if health.node and health.npm else "❌ Node/npm")
    st.write("✅ SQLite" if health.database else "❌ SQLite")
    for message in health.messages:
        st.caption(message)
    st.divider()
    st.subheader("本地默认偏好")
    home_city = st.text_input("常住城市", value=profile.home_city)
    profile_pace = st.selectbox(
        "默认节奏", ["休闲", "适中", "紧凑"], index=["休闲", "适中", "紧凑"].index(profile.travel_pace)
    )
    profile_hotel = st.text_input("酒店偏好（逗号分隔）", value=", ".join(profile.hotel_preferences))
    profile_food = st.text_input("饮食禁忌（逗号分隔）", value=", ".join(profile.food_restrictions))
    profile_walk = st.number_input("每日最大步行 km", 1.0, 50.0, profile.max_daily_walk_km)
    if st.button("保存本地偏好"):
        try:
            repository.save_profile(
                UserProfile.model_validate(
                    {
                        **profile.model_dump(),
                        "home_city": home_city,
                        "travel_pace": profile_pace,
                        "hotel_preferences": csv_list(profile_hotel),
                        "food_restrictions": csv_list(profile_food),
                        "max_daily_walk_km": profile_walk,
                    }
                )
            )
            st.success("偏好已保存在本机")
        except SensitiveDataError as exc:
            st.error(str(exc))
        except ValidationError as exc:
            st.error(validation_error_text(exc))
        except Exception as exc:
            st.error(f"保存失败：{type(exc).__name__}")

plan_tab, history_tab = st.tabs(["新建行程", "历史行程"])

with plan_tab:
    copied = st.session_state.copied_request
    left, right = st.columns(2)
    with left:
        origin_city = st.text_input("出发城市", value=(copied.origin_city if copied else profile.home_city))
        destination = st.text_input("目的地", value=(copied.destination if copied else ""))
        start_date = st.date_input("出发日期", value=(copied.start_date if copied else date.today()))
        end_date = st.date_input("返程日期", value=(copied.end_date if copied else date.today()))
        adults = st.number_input("成人", 1, 20, copied.adults if copied else 1)
        children = st.number_input("儿童", 0, 10, copied.children if copied else 0)
        child_ages = st.text_input("儿童年龄（逗号分隔，可留空）")
        rooms = st.number_input("房间数", 1, 10, copied.rooms if copied else 1)
    with right:
        total_budget = st.number_input("总预算（元）", 100, 1000000, copied.total_budget if copied else 5000, 100)
        hotel_min = st.number_input("每晚住宿预算下限", 0, 100000, copied.hotel_budget_min if copied else profile.hotel_budget_min, 50)
        hotel_max = st.number_input("每晚住宿预算上限", 0, 100000, copied.hotel_budget_max if copied else profile.hotel_budget_max, 50)
        pace = st.selectbox("行程节奏", ["休闲", "适中", "紧凑"], index=["休闲", "适中", "紧凑"].index(copied.pace if copied else profile.travel_pace))
        transport = st.selectbox("市内交通", ["公共交通优先", "步行优先", "自驾优先"])
        max_walk = st.number_input("每日最大步行 km", 1.0, 50.0, copied.max_daily_walk_km if copied else profile.max_daily_walk_km)
        daily_start = st.time_input("每日开始", value=(copied.daily_start_time if copied else profile.daily_start_time))
        daily_end = st.time_input("每日结束", value=(copied.daily_end_time if copied else profile.daily_end_time))

    must_visit = st.text_input("必去地点（逗号分隔）", value=", ".join(copied.must_visit) if copied else "")
    avoid = st.text_input("避开地点或体验（逗号分隔）", value=", ".join(copied.avoid) if copied else "")
    food_preferences = st.text_input("饮食偏好（逗号分隔）", value=", ".join(copied.food_preferences) if copied else "")
    intercity = st.text_area("已知城际交通班次（可留空，不查询票价余票）", value=copied.intercity_transport if copied else "")

    if st.button("生成结构化行程", type="primary", disabled=not health.deepseek):
        try:
            request = TripRequest(
                origin_city=origin_city,
                destination=destination,
                start_date=start_date,
                end_date=end_date,
                adults=adults,
                children=children,
                child_ages=parse_child_ages(child_ages),
                rooms=rooms,
                total_budget=total_budget,
                hotel_budget_min=hotel_min,
                hotel_budget_max=hotel_max,
                pace=pace,
                local_transport=transport,
                must_visit=csv_list(must_visit),
                avoid=csv_list(avoid),
                food_preferences=csv_list(food_preferences),
                food_restrictions=profile.food_restrictions,
                hotel_preferences=profile.hotel_preferences,
                intercity_transport=intercity,
                daily_start_time=daily_start,
                daily_end_time=daily_end,
                max_daily_walk_km=max_walk,
            )
            progress_box = st.status("正在执行分阶段工作流…", expanded=True)

            def progress(stage: str, message: str) -> None:
                progress_box.write(f"**{stage}**：{message}")

            result = asyncio.run(
                TravelPlannerWorkflow(settings, repository).generate(request, progress)
            )
            st.session_state.itinerary = result.itinerary
            progress_box.update(
                label="行程已生成" if result.saved else "行程已生成但未保存",
                state="complete",
            )
        except SensitiveDataError as exc:
            st.error(str(exc))
        except ValidationError as exc:
            st.error(validation_error_text(exc))
        except ValueError:
            st.error("输入格式或目的地校验失败，请检查对应字段后重试。")
        except Exception as exc:
            st.error(f"生成失败：{type(exc).__name__}。请检查 DeepSeek 配置和服务状态。")

    if st.session_state.itinerary:
        replan = render_itinerary(st.session_state.itinerary)
        if replan:
            current = st.session_state.itinerary
            locked = [
                activity
                for day_plan in current.days
                for activity in day_plan.activities
                if activity.locked
            ]
            replan_succeeded = False
            with st.status("正在围绕锁定活动重新规划…", expanded=True) as replan_status:
                def replan_progress(stage: str, message: str) -> None:
                    replan_status.write(f"**{stage}**：{message}")

                try:
                    result = asyncio.run(
                        TravelPlannerWorkflow(settings, repository).generate(
                            current.request,
                            replan_progress,
                            locked_activities=locked,
                        )
                    )
                    st.session_state.itinerary = result.itinerary
                    replan_status.update(label="重新规划完成", state="complete")
                    replan_succeeded = True
                except SensitiveDataError as exc:
                    replan_status.update(label="重新规划已拒绝", state="error")
                    st.error(str(exc))
                except ValidationError as exc:
                    replan_status.update(label="重新规划失败", state="error")
                    st.error(validation_error_text(exc))
                except ValueError:
                    replan_status.update(label="重新规划失败", state="error")
                    st.error("锁定活动或目的地未通过校验，请检查后重试。")
                except Exception as exc:
                    replan_status.update(label="重新规划失败", state="error")
                    st.error(f"重新规划失败：{type(exc).__name__}")
            if replan_succeeded:
                st.rerun()

with history_tab:
    try:
        history = repository.list_itineraries()
    except Exception as exc:
        history = []
        st.error(f"无法读取历史记录：{type(exc).__name__}")
    if not history:
        st.info("暂无已保存行程。")
    for item in history:
        with st.expander(f"{item['start_date']} · {item['title']} · {item['status']}"):
            view_col, copy_col, delete_col = st.columns(3)
            if view_col.button("查看", key=f"view-{item['itinerary_id']}"):
                try:
                    st.session_state.itinerary = repository.get_itinerary(item["itinerary_id"])
                    st.rerun()
                except Exception as exc:
                    st.error(f"读取失败：{type(exc).__name__}")
            if copy_col.button("复制为新行程", key=f"copy-{item['itinerary_id']}"):
                try:
                    selected = repository.get_itinerary(item["itinerary_id"])
                    if selected:
                        st.session_state.copied_request = selected.request
                        st.rerun()
                except Exception as exc:
                    st.error(f"复制失败：{type(exc).__name__}")
            confirm = delete_col.checkbox("确认删除", key=f"confirm-{item['itinerary_id']}")
            if delete_col.button("删除本地记录", key=f"delete-{item['itinerary_id']}", disabled=not confirm):
                try:
                    repository.delete_itinerary(item["itinerary_id"])
                    st.rerun()
                except Exception as exc:
                    st.error(f"删除失败：{type(exc).__name__}")
