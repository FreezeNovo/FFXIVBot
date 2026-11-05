from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from ff14_core.storage import StateStore
from ff14_core.source_models import CalendarCategoryOption
from ff14_web.app import (
    ActivityPolicyUpdate,
    ManualQueryPolicyUpdate,
    DeliveryCloseRequest,
    MaintenancePolicyUpdate,
    OwnerUpdate,
    TargetUpdate,
    _render_category_option,
    create_app,
)


class WebAccessTests(unittest.TestCase):
    def test_calendar_category_controls_have_unique_dom_ids(self) -> None:
        labels = ("运营活动", "维护", "季节活动", "其他运营活动")
        rendered = [
            _render_category_option(
                CalendarCategoryOption(
                    classification_label=label,
                    candidate_count=1,
                    included=True,
                )
            )
            for label in labels
        ]
        ids = [item.split('id="', 1)[1].split('"', 1)[0] for item in rendered]
        self.assertEqual(len(ids), len(set(ids)))
        for item, option_id in zip(rendered, ids):
            self.assertIn(f'for="{option_id}"', item)

    def test_access_routes_manage_owner_and_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            app = create_app(store)
            routes = {
                (route.path, next(iter(route.methods or ()))): route.endpoint
                for route in app.routes
                if hasattr(route, "methods")
            }

            update_owner = routes[("/api/access-settings/owner", "PUT")]
            save_group = routes[("/api/access-settings/groups/{group_id}", "PUT")]
            update_policy = routes[("/api/access-settings/groups/{group_id}/activity-policy", "PUT")]
            update_maintenance = routes[("/api/access-settings/groups/{group_id}/maintenance-policy", "PUT")]
            delete_group = routes[("/api/access-settings/groups/{group_id}", "DELETE")]
            access_settings = routes[("/api/access-settings", "GET")]
            access_page = routes[("/access", "GET")]

            self.assertEqual(update_owner(OwnerUpdate(owner_user_id="12345")), {"owner_user_id": "12345"})
            self.assertEqual(
                save_group("67890", TargetUpdate(enabled=True))["group_id"], "67890"
            )
            self.assertEqual(access_settings()["owner_user_id"], "12345")
            self.assertEqual(access_settings()["targets"][0]["enabled"], True)

            policy = update_policy(
                "67890",
                ActivityPolicyUpdate(
                    enabled=True,
                    region="cn",
                    classification_codes=["seasonal_event", "other_operational"],
                    digest_hour=20,
                    digest_minute=0,
                    upcoming_window_minutes=14 * 24 * 60,
                    ending_window_minutes=60,
                ),
            )
            self.assertEqual(policy["classification_codes"], ["seasonal_event", "other_operational"])
            self.assertEqual(policy["ending_window_minutes"], 60)
            maintenance = update_maintenance(
                "67890",
                MaintenancePolicyUpdate(
                    enabled=True,
                    region="cn",
                    notify_announcements=False,
                    reminder_window_minutes=360,
                    notify_updates=True,
                ),
            )
            self.assertEqual(maintenance["reminder_window_minutes"], 360)
            self.assertTrue(maintenance["notify_updates"])
            self.assertFalse(maintenance["notify_announcements"])
            self.assertEqual(
                access_settings()["targets"][0]["activity_digest"]["classification_codes"],
                ["seasonal_event", "other_operational"],
            )
            self.assertEqual(
                access_settings()["targets"][0]["maintenance"]["reminder_window_minutes"],
                360,
            )
            page = access_page().body.decode("utf-8")
            self.assertIn('data-digest-classification value="seasonal_event" checked', page)
            self.assertIn('data-digest-classification value="collaboration_event"', page)
            self.assertNotIn('data-digest-classifications', page)
            self.assertIn('开始前<select data-digest-upcoming>', page)
            self.assertIn('value="20160" selected>14 天内', page)
            self.assertIn('value="60" selected>1 小时内', page)
            self.assertIn('data-maintenance-enabled checked', page)
            self.assertIn('data-maintenance-announcements', page)
            self.assertIn('value="360" selected>开始前 6 小时', page)
            self.assertIn('data-maintenance-updates checked', page)
            self.assertNotIn('value="1440" selected>1440 分钟内', page)
            self.assertIn('全部勾选或全部取消都表示全部分类', page)

            with self.assertRaises(HTTPException):
                update_policy(
                    "67890",
                    ActivityPolicyUpdate(
                        enabled=True,
                        upcoming_window_minutes=720,
                        ending_window_minutes=60,
                    ),
                )
            with self.assertRaises(HTTPException):
                update_maintenance(
                    "67890",
                    MaintenancePolicyUpdate(
                        enabled=True,
                        reminder_window_minutes=720,
                    ),
                )

            save_group("67890", TargetUpdate(enabled=False))
            self.assertFalse(access_settings()["targets"][0]["enabled"])
            self.assertEqual(delete_group("67890"), {"deleted": True})
            self.assertEqual(access_settings()["targets"], [])

    def test_access_routes_reject_non_numeric_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            app = create_app(store)
            routes = {
                (route.path, next(iter(route.methods or ()))): route.endpoint
                for route in app.routes
                if hasattr(route, "methods")
            }

            update_owner = routes[("/api/access-settings/owner", "PUT")]
            save_group = routes[("/api/access-settings/groups/{group_id}", "PUT")]

            with self.assertRaises(HTTPException):
                update_owner(OwnerUpdate(owner_user_id="owner"))
            with self.assertRaises(HTTPException):
                save_group("group-test", TargetUpdate(enabled=True))

    def test_review_page_is_reachable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(Path(temporary_directory) / "state.sqlite3")
            store.initialize()
            app = create_app(store)
            review_routes = [
                route
                for route in app.routes
                if getattr(route, "path", None) == "/review"
                and "GET" in (getattr(route, "methods", set()) or set())
            ]

            self.assertEqual(len(review_routes), 1)
            response = review_routes[0].endpoint(page=1)
            self.assertEqual(response.status_code, 200)
            self.assertIn("待审核活动", response.body.decode("utf-8"))

    def test_query_policy_and_uncertain_delivery_review(self):
        from ff14_core.source_models import ActivityDigestPolicy
        from ff14_core.messaging import SendResult
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / 'test.sqlite3')
            store.initialize()
            target = store.save_onebot_group('67890', enabled=True)
            store.set_activity_digest_policy(target.target_id, ActivityDigestPolicy(enabled=True))
            app = create_app(store)
            routes = {getattr(route, 'path', ''): getattr(route, 'endpoint', None) for route in app.routes}
            update = routes['/api/access-settings/groups/{group_id}/query-policy']
            self.assertEqual(update('67890', ManualQueryPolicyUpdate(activity_enabled=True, maintenance_enabled=False)),
                             {'activity_enabled':True, 'maintenance_enabled':False})
            settings = routes['/api/access-settings']()
            self.assertTrue(settings['targets'][0]['manual_queries']['activity_enabled'])
            self.assertIn('data-query-activity checked', routes['/access']().body.decode())
            with self.assertRaises(HTTPException):
                update('11111', ManualQueryPolicyUpdate(activity_enabled=True, maintenance_enabled=True))
            notification = store.create_notification_candidate(business_key='web-review', target_id=target.target_id,
                stream='activity_digest', window_key='fixture', message_text='<script>fixture</script>')
            claim = store.claim_notification(notification)
            store.finish_delivery_attempt(claim.attempt_id, SendResult(success=False, uncertain=True))
            body = routes['/deliveries']().body.decode()
            self.assertIn('结果未知，需核查', body)
            self.assertIn('&lt;script&gt;fixture&lt;/script&gt;', body)
            close = routes['/api/deliveries/{notification_id}/close']
            with self.assertRaises(HTTPException):
                close(notification, DeliveryCloseRequest(confirm=False))
            self.assertEqual(close(notification, DeliveryCloseRequest(confirm=True)), {'closed':True})
            self.assertEqual(store.list_deliveries()[0].status, 'failed')
            with self.assertRaises(HTTPException):
                close(notification, DeliveryCloseRequest(confirm=True))


if __name__ == "__main__":
    unittest.main()
