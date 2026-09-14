import tempfile
import unittest
from pathlib import Path

from helper import AreaResult, CSULibrary, SeatPreference


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.posts = []
        self.target_day = None

    def post(self, url, json, headers, timeout):
        self.posts.append((url, json))
        if url.endswith("/v4/space/confirm"):
            raise AssertionError("dry-run 不得调用预约确认接口")
        if url.endswith("/v4/space/index"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "premises": [{"id": "1", "name": "潇湘校区馆"}],
                        "date": [self.target_day],
                    },
                }
            )
        if url.endswith("/v4/Space/map"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "date": {
                            "reserveType": 1,
                            "list": [
                                {
                                    "day": self.target_day,
                                    "times": [
                                        {
                                            "id": "segment-1",
                                            "start": "07:30",
                                            "end": "22:00",
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                }
            )
        if url.endswith("/v4/Space/seat"):
            return FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "list": [{"id": "9297", "no": "XF5A132", "status": 1}]
                    },
                }
            )
        raise AssertionError(f"未预期请求：{url}")


class HelperTests(unittest.TestCase):
    def make_helper(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "config.ini").write_text(
            "[DATABASE]\nCAMPUS = '新校区'\nSEAT = ['XF5A132']\nDAYS_AHEAD = 1\n",
            encoding="utf-8",
        )
        (root / "新校区座位表.csv").write_text(
            "ID,NO,AREA,CATEGORY,STATUS,AREA NAME\n"
            "9297,XF5A132,59,12,可预约,A区\n",
            encoding="utf-8",
        )
        session = FakeSession()
        helper = CSULibrary("user", "password", root / "config.ini", session=session)
        session.target_day = helper.target_day
        helper.login = lambda: setattr(helper, "token", "fake") or {"name": "测试用户"}
        return temp, helper, session

    def test_dry_run_queries_but_never_confirms(self):
        temp, helper, session = self.make_helper()
        self.addCleanup(temp.cleanup)
        inspected = helper.test()
        self.assertEqual(inspected[0]["seat"]["no"], "XF5A132")
        self.assertEqual(inspected[0]["area"].display_time, "07:30–22:00")
        self.assertFalse(any(url.endswith("/v4/space/confirm") for url, _ in session.posts))
        seat_query = next(body for url, body in session.posts if url.endswith("/v4/Space/seat"))
        self.assertEqual(seat_query["start_time"], "07:30")
        self.assertEqual(seat_query["end_time"], "22:00")

    def test_fixed_segment_confirm_payload_uses_server_segment(self):
        preference = SeatPreference("XF5A132", "59", "9297")
        area = AreaResult(
            area_id="59",
            reserve_type=1,
            day_info={"times": [{"id": "segment-1"}]},
            seats=[],
            display_time="07:30–22:00",
        )
        payload = CSULibrary._confirm_payload(
            {"preference": preference, "seat": {"id": "9297"}, "area": area},
            "2026-09-15",
        )
        self.assertEqual(payload["segment"], "segment-1")
        self.assertEqual(payload["start_time"], "")
        self.assertEqual(payload["end_time"], "")


if __name__ == "__main__":
    unittest.main()
