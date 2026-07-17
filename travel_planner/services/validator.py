from __future__ import annotations

from datetime import datetime

from travel_planner.models import Itinerary, ValidationIssue, VerificationStatus


def _minutes(start, end) -> int:
    anchor = datetime(2000, 1, 1)
    return int(
        (
            datetime.combine(anchor.date(), end)
            - datetime.combine(anchor.date(), start)
        ).total_seconds()
        // 60
    )


class ItineraryValidator:
    def validate(self, itinerary: Itinerary) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        request = itinerary.request
        activity_total = 0.0

        for day_plan in itinerary.days:
            if not request.start_date <= day_plan.day <= request.end_date:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="DATE_OUT_OF_RANGE",
                        message="日程日期超出旅行范围",
                        day=day_plan.day,
                    )
                )
            activities = sorted(day_plan.activities, key=lambda item: item.start_time)
            for index, activity in enumerate(activities):
                activity_total += activity.estimated_cost
                if activity.end_time <= activity.start_time:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="INVALID_ACTIVITY_TIME",
                            message=f"{activity.poi.name} 的结束时间不晚于开始时间",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if not (request.daily_start_time <= activity.start_time < request.daily_end_time):
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="OUTSIDE_DAILY_WINDOW",
                            message=f"{activity.poi.name} 超出每日活动时间范围",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if activity.poi.evidence.status != VerificationStatus.VERIFIED:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="UNVERIFIED_POI",
                            message=f"{activity.poi.name} 缺少高德验证证据",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )
                if index and activities[index - 1].end_time > activity.start_time:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="TIME_OVERLAP",
                            message=f"{activities[index - 1].poi.name} 与 {activity.poi.name} 时间重叠",
                            day=activity.day,
                            activity_id=activity.activity_id,
                        )
                    )

            expected_legs = max(0, len(activities) - 1)
            if len(day_plan.route_legs) < expected_legs:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="MISSING_ROUTE",
                        message="相邻活动之间缺少经过高德验证的路线",
                        day=day_plan.day,
                    )
                )
            for leg in day_plan.route_legs:
                if not leg.evidence or leg.evidence.status != VerificationStatus.VERIFIED:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            code="UNVERIFIED_ROUTE",
                            message="存在未验证路线",
                            day=day_plan.day,
                        )
                    )

            walking_meters = sum(
                leg.distance_meters or 0
                for leg in day_plan.route_legs
                if "步行" in leg.transport_mode
            )
            if walking_meters > request.max_daily_walk_km * 1000:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="WALK_LIMIT_EXCEEDED",
                        message=f"预计步行 {walking_meters / 1000:.1f} km，超过设置上限",
                        day=day_plan.day,
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
                ValidationIssue(
                    severity="warning",
                    code="BUDGET_EXCEEDED",
                    message=f"当前估算 ¥{itinerary.budget.total:.0f} 超出总预算 ¥{request.total_budget}",
                )
            )
        return issues

    def apply_status(self, itinerary: Itinerary, amap_available: bool) -> None:
        itinerary.validation_issues = self.validate(itinerary)
        has_errors = any(issue.severity == "error" for issue in itinerary.validation_issues)
        itinerary.status = (
            VerificationStatus.VERIFIED
            if amap_available and not has_errors
            else VerificationStatus.UNVERIFIED
        )

