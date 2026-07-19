from __future__ import annotations

from datetime import datetime
from collections.abc import Iterable

from travel_planner.models import (
    Activity,
    EvidenceKind,
    Itinerary,
    SourceEvidence,
    ValidationContext,
    ValidationIssue,
    VerificationStatus,
)


ROUTE_BUFFER_MINUTES = 15
_EXPIRY_CODES = {
    "EXPIRED_POI_EVIDENCE",
    "EXPIRED_ROUTE_EVIDENCE",
    "EXPIRED_HOTEL_EVIDENCE",
    "EXPIRED_WEATHER_EVIDENCE",
}


def _minutes_between(start, end) -> int:
    anchor = datetime(2000, 1, 1)
    return int(
        (
            datetime.combine(anchor.date(), end)
            - datetime.combine(anchor.date(), start)
        ).total_seconds()
        // 60
    )


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    day=None,
    activity_id: str | None = None,
    suggestion: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        day=day,
        activity_id=activity_id,
        suggestion=suggestion,
    )


class ItineraryValidator:
    @staticmethod
    def _validate_evidence(
        evidence: SourceEvidence | None,
        *,
        context: ValidationContext,
        expected_kinds: Iterable[EvidenceKind],
        label: str,
        unverified_code: str,
        expired_code: str,
        invalid_kind_code: str,
        day=None,
        activity_id: str | None = None,
        raw_identifier: str | None = None,
    ) -> list[ValidationIssue]:
        if evidence is None:
            return [
                _issue(
                    unverified_code,
                    f"{label}缺少可审计的来源证据",
                    day=day,
                    activity_id=activity_id,
                )
            ]

        issues: list[ValidationIssue] = []
        expected = set(expected_kinds)
        if evidence.kind not in expected:
            issues.append(
                _issue(
                    invalid_kind_code,
                    f"{label}的证据类型不匹配",
                    day=day,
                    activity_id=activity_id,
                )
            )
        if evidence.coordinate_system != "GCJ-02":
            issues.append(
                _issue(
                    invalid_kind_code,
                    f"{label}没有使用 GCJ-02 坐标系",
                    day=day,
                    activity_id=activity_id,
                )
            )
        if raw_identifier is not None and evidence.raw_identifier != raw_identifier:
            issues.append(
                _issue(
                    invalid_kind_code,
                    f"{label}的原始标识符与业务对象不一致",
                    day=day,
                    activity_id=activity_id,
                )
            )

        effective_status = evidence.status_at(context.now)
        evidence.status = effective_status
        if effective_status == VerificationStatus.EXPIRED:
            issues.append(
                _issue(
                    expired_code,
                    f"{label}的来源证据已过期",
                    day=day,
                    activity_id=activity_id,
                    suggestion="使用前重新查询官方来源",
                )
            )
        elif effective_status != VerificationStatus.VERIFIED:
            issues.append(
                _issue(
                    unverified_code,
                    f"{label}缺少当前有效的真实工具调用记录",
                    day=day,
                    activity_id=activity_id,
                )
            )
        return issues

    def _validate_activity_poi(
        self,
        activity: Activity,
        *,
        context: ValidationContext,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        poi = activity.poi
        if poi.poi_id not in context.approved_poi_ids:
            issues.append(
                _issue(
                    "UNKNOWN_POI",
                    f"{poi.name} 不在批准候选列表中",
                    day=activity.day,
                    activity_id=activity.activity_id,
                )
            )
        if (
            poi.longitude is None
            or poi.latitude is None
            or not 73.0 <= poi.longitude <= 135.0
            or not 3.0 <= poi.latitude <= 54.0
        ):
            issues.append(
                _issue(
                    "INVALID_POI_COORDINATES",
                    f"{poi.name} 缺少有效的中国大陆 GCJ-02 坐标",
                    day=activity.day,
                    activity_id=activity.activity_id,
                )
            )
        issues.extend(
            self._validate_evidence(
                poi.evidence,
                context=context,
                expected_kinds={EvidenceKind.POI_LOCATION},
                label=poi.name,
                unverified_code="UNVERIFIED_POI",
                expired_code="EXPIRED_POI_EVIDENCE",
                invalid_kind_code="INVALID_POI_EVIDENCE",
                day=activity.day,
                activity_id=activity.activity_id,
                raw_identifier=poi.poi_id,
            )
        )
        return issues

    def validate(
        self,
        itinerary: Itinerary,
        context: ValidationContext,
    ) -> list[ValidationIssue]:
        # Post-model mapping can reject an unknown candidate before it reaches
        # the final activity list. Preserve those upstream deterministic errors
        # so status derivation cannot accidentally erase the rejection.
        issues: list[ValidationIssue] = list(itinerary.validation_issues)
        request = itinerary.request
        activity_total = 0.0

        for stage in sorted(context.required_stage_names):
            if not context.required_stages.get(stage, False):
                issues.append(
                    _issue(
                        "REQUIRED_STAGE_INCOMPLETE",
                        f"必需阶段 {stage} 尚未完成",
                    )
                )

        if not context.destination_confirmed:
            issues.append(
                _issue(
                    "DESTINATION_UNCONFIRMED",
                    "目的地尚未由批准来源确认",
                )
            )

        total_activities = sum(len(day_plan.activities) for day_plan in itinerary.days)
        if not itinerary.days or total_activities == 0:
            issues.append(_issue("EMPTY_ITINERARY", "行程至少需要一个活动"))

        seen_days = set()
        seen_activity_ids: set[str] = set()
        seen_poi_ids: set[str] = set()

        for day_plan in itinerary.days:
            if day_plan.day in seen_days:
                issues.append(
                    _issue(
                        "DUPLICATE_DAY_PLAN",
                        "同一日期存在重复日程",
                        day=day_plan.day,
                    )
                )
            seen_days.add(day_plan.day)

            if not request.start_date <= day_plan.day <= request.end_date:
                issues.append(
                    _issue(
                        "DATE_OUT_OF_RANGE",
                        "日程日期超出旅行范围",
                        day=day_plan.day,
                    )
                )

            activities = sorted(day_plan.activities, key=lambda item: item.start_time)
            for index, activity in enumerate(activities):
                activity_total += activity.estimated_cost
                if activity.activity_id in seen_activity_ids:
                    issues.append(
                        _issue(
                            "DUPLICATE_ACTIVITY_ID",
                            "活动 ID 重复",
                            day=day_plan.day,
                            activity_id=activity.activity_id,
                        )
                    )
                seen_activity_ids.add(activity.activity_id)

                if activity.day != day_plan.day:
                    issues.append(
                        _issue(
                            "ACTIVITY_DAY_MISMATCH",
                            f"{activity.poi.name} 的日期与所属日程不一致",
                            day=day_plan.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if not request.start_date <= activity.day <= request.end_date:
                    issues.append(
                        _issue(
                            "ACTIVITY_DATE_OUT_OF_RANGE",
                            f"{activity.poi.name} 的日期超出旅行范围",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if activity.end_time <= activity.start_time:
                    issues.append(
                        _issue(
                            "INVALID_ACTIVITY_TIME",
                            f"{activity.poi.name} 的结束时间不晚于开始时间",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if (
                    activity.start_time < request.daily_start_time
                    or activity.end_time > request.daily_end_time
                ):
                    issues.append(
                        _issue(
                            "OUTSIDE_DAILY_WINDOW",
                            f"{activity.poi.name} 超出每日活动时间范围",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )

                if activity.poi.poi_id in seen_poi_ids:
                    issues.append(
                        _issue(
                            "DUPLICATE_POI",
                            f"{activity.poi.name} 在行程中重复出现",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                seen_poi_ids.add(activity.poi.poi_id)
                issues.extend(self._validate_activity_poi(activity, context=context))

                if index and activities[index - 1].end_time > activity.start_time:
                    issues.append(
                        _issue(
                            "TIME_OVERLAP",
                            f"{activities[index - 1].poi.name} 与 {activity.poi.name} 时间重叠",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )

            expected_pairs = [
                (activities[index].activity_id, activities[index + 1].activity_id)
                for index in range(max(0, len(activities) - 1))
            ]
            expected_pair_set = set(expected_pairs)
            legs_by_pair: dict[tuple[str, str], list] = {}
            for leg in day_plan.route_legs:
                pair = (leg.origin_activity_id, leg.destination_activity_id)
                legs_by_pair.setdefault(pair, []).append(leg)
                if pair not in expected_pair_set:
                    issues.append(
                        _issue(
                            "ROUTE_ENDPOINT_MISMATCH",
                            "路线没有精确连接对应的相邻活动",
                            day=day_plan.day,
                        )
                    )

            activity_by_id = {activity.activity_id: activity for activity in activities}
            for pair in expected_pairs:
                matching_legs = legs_by_pair.get(pair, [])
                if not matching_legs:
                    issues.append(
                        _issue(
                            "MISSING_ROUTE",
                            "相邻活动之间缺少经过高德验证的路线",
                            day=day_plan.day,
                            activity_id=pair[1],
                        )
                    )
                    continue
                if len(matching_legs) > 1:
                    issues.append(
                        _issue(
                            "DUPLICATE_ROUTE",
                            "同一对相邻活动存在重复路线",
                            day=day_plan.day,
                            activity_id=pair[1],
                        )
                    )

                leg = matching_legs[0]
                if not leg.distance_meters or not leg.duration_minutes:
                    issues.append(
                        _issue(
                            "INVALID_ROUTE_METRICS",
                            "路线必须包含正距离和正时长",
                            day=day_plan.day,
                            activity_id=pair[1],
                        )
                    )
                issues.extend(
                    self._validate_evidence(
                        leg.evidence,
                        context=context,
                        expected_kinds={EvidenceKind.ROUTE},
                        label="相邻活动路线",
                        unverified_code="UNVERIFIED_ROUTE",
                        expired_code="EXPIRED_ROUTE_EVIDENCE",
                        invalid_kind_code="INVALID_ROUTE_EVIDENCE",
                        day=day_plan.day,
                        activity_id=pair[1],
                    )
                )

                origin = activity_by_id[pair[0]]
                destination = activity_by_id[pair[1]]
                if leg.duration_minutes is not None:
                    available_minutes = _minutes_between(
                        origin.end_time, destination.start_time
                    )
                    if available_minutes < leg.duration_minutes + ROUTE_BUFFER_MINUTES:
                        issues.append(
                            _issue(
                                "INSUFFICIENT_ROUTE_BUFFER",
                                "相邻活动之间无法容纳路线时长和 15 分钟缓冲",
                                day=day_plan.day,
                                activity_id=destination.activity_id,
                            )
                        )

            walking_meters = sum(
                leg.distance_meters or 0
                for leg in day_plan.route_legs
                if "步行" in leg.transport_mode
            )
            if walking_meters > request.max_daily_walk_km * 1000:
                issues.append(
                    _issue(
                        "WALK_LIMIT_EXCEEDED",
                        f"预计步行 {walking_meters / 1000:.1f} km，超过设置上限",
                        severity="warning",
                        day=day_plan.day,
                    )
                )

        for hotel in itinerary.hotels:
            poi = hotel.poi
            if poi.poi_id not in context.approved_poi_ids:
                issues.append(_issue("UNKNOWN_HOTEL_POI", f"{poi.name} 不在批准候选列表中"))
            if (
                poi.longitude is None
                or poi.latitude is None
                or not 73.0 <= poi.longitude <= 135.0
                or not 3.0 <= poi.latitude <= 54.0
            ):
                issues.append(_issue("INVALID_HOTEL_COORDINATES", f"{poi.name} 缺少有效坐标"))
            issues.extend(
                self._validate_evidence(
                    poi.evidence,
                    context=context,
                    expected_kinds={EvidenceKind.HOTEL_LOCATION},
                    label=poi.name,
                    unverified_code="UNVERIFIED_HOTEL",
                    expired_code="EXPIRED_HOTEL_EVIDENCE",
                    invalid_kind_code="INVALID_HOTEL_EVIDENCE",
                    raw_identifier=poi.poi_id,
                )
            )

        if itinerary.weather is not None:
            issues.extend(
                self._validate_evidence(
                    itinerary.weather.evidence,
                    context=context,
                    expected_kinds={
                        EvidenceKind.CURRENT_WEATHER,
                        EvidenceKind.WEATHER_FORECAST,
                    },
                    label="天气",
                    unverified_code="UNVERIFIED_WEATHER",
                    expired_code="EXPIRED_WEATHER_EVIDENCE",
                    invalid_kind_code="INVALID_WEATHER_EVIDENCE",
                )
            )

        activities_by_id = {
            activity.activity_id: activity
            for day_plan in itinerary.days
            for activity in day_plan.activities
        }
        for snapshot in context.locked_activities:
            if not snapshot.locked:
                issues.append(
                    _issue(
                        "INVALID_LOCKED_SNAPSHOT",
                        "锁定活动快照必须保持锁定状态",
                        activity_id=snapshot.activity_id,
                    )
                )
                continue
            current = activities_by_id.get(snapshot.activity_id)
            if current is None:
                issues.append(
                    _issue(
                        "LOCKED_ACTIVITY_MISSING",
                        "重新规划删除了锁定活动",
                        day=snapshot.day,
                        activity_id=snapshot.activity_id,
                    )
                )
                continue
            if (
                current.day != snapshot.day
                or current.start_time != snapshot.start_time
                or current.end_time != snapshot.end_time
                or current.poi.poi_id != snapshot.poi_id
                or not current.locked
            ):
                issues.append(
                    _issue(
                        "LOCKED_ACTIVITY_CHANGED",
                        "重新规划改变了锁定活动的日期、时间、地点或锁定状态",
                        day=snapshot.day,
                        activity_id=snapshot.activity_id,
                    )
                )

        itinerary.budget.activities = round(activity_total, 2)
        itinerary.budget.total = round(
            itinerary.budget.activities
            + itinerary.budget.accommodation_estimate
            + itinerary.budget.local_transport_estimate
            + itinerary.budget.food_estimate
            + itinerary.budget.other,
            2,
        )
        if itinerary.budget.total > request.total_budget:
            issues.append(
                _issue(
                    "BUDGET_EXCEEDED",
                    f"当前估算 ¥{itinerary.budget.total:.0f} 超出总预算 ¥{request.total_budget}",
                    severity="warning",
                )
            )
        unique: dict[tuple, ValidationIssue] = {}
        for issue in issues:
            key = (
                issue.severity,
                issue.code,
                issue.message,
                issue.day,
                issue.activity_id,
                issue.suggestion,
            )
            unique.setdefault(key, issue)
        return list(unique.values())

    def apply_status(self, itinerary: Itinerary, context: ValidationContext) -> None:
        itinerary.validation_issues = self.validate(itinerary, context)
        if not context.required_stages_complete:
            itinerary.status = VerificationStatus.DRAFT
            return

        errors = [
            issue for issue in itinerary.validation_issues if issue.severity == "error"
        ]
        non_expiry_errors = [issue for issue in errors if issue.code not in _EXPIRY_CODES]
        if non_expiry_errors:
            itinerary.status = VerificationStatus.UNVERIFIED
        elif errors:
            itinerary.status = VerificationStatus.EXPIRED
        else:
            itinerary.status = VerificationStatus.VERIFIED
